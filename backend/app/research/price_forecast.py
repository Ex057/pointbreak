"""Fixed one-hour-ahead XAU/USD forecast replay for the Research Lab."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import math
from threading import Lock
from time import time

import numpy as np

from ..db import repo
from .data import MIN_FORECAST_CANDLES, PAIR_SCOPE, TIMEFRAME, history_status

LOOKBACK_BARS = 30
_INDICATOR_FEATURES = ("rsi14", "bollinger_high", "bollinger_low", "bollinger_width", "ma20_level", "ma20_slope")
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tradeguard-forecast")
_LOCK = Lock()


def _feature_rows(rows: list[dict]) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """Flatten 30 closed candles into past-only LightGBM inputs and next log return."""
    opens = np.asarray([row["open"] for row in rows], dtype=float)
    highs = np.asarray([row["high"] for row in rows], dtype=float)
    lows = np.asarray([row["low"] for row in rows], dtype=float)
    closes = np.asarray([row["close"] for row in rows], dtype=float)
    X, y, target_epochs = [], [], []
    for i in range(LOOKBACK_BARS - 1, len(rows) - 1):
        current = closes[i]
        if current <= 0 or closes[i + 1] <= 0:
            continue
        start = i - LOOKBACK_BARS + 1
        vector: list[float] = []
        # Preserve candle shape and order like the notebook's 30-bar sequence,
        # expressed relative to the latest close to avoid price-scale drift.
        for j in range(start, i + 1):
            vector.extend((
                opens[j] / current - 1,
                highs[j] / current - 1,
                lows[j] / current - 1,
                closes[j] / current - 1,
            ))
        recent = closes[max(0, i - 19):i + 1]
        mean20 = float(np.mean(recent))
        std20 = float(np.std(recent))
        gains = np.maximum(np.diff(closes[max(0, i - 14):i + 1]), 0).mean() if i >= 14 else 0.0
        losses = np.maximum(-np.diff(closes[max(0, i - 14):i + 1]), 0).mean() if i >= 14 else 0.0
        rsi = 100 - 100 / (1 + gains / losses) if losses else (100.0 if gains else 50.0)
        ma_slope = (mean20 - float(np.mean(closes[max(0, i - 39):max(0, i - 19)]))) / current
        vector.extend((
            rsi / 100,
            (mean20 + 2 * std20) / current - 1,
            (mean20 - 2 * std20) / current - 1,
            (4 * std20 / mean20) if mean20 else 0.0,
            mean20 / current - 1,
            ma_slope,
        ))
        X.append(vector)
        y.append(math.log(closes[i + 1] / current))
        target_epochs.append(int(rows[i + 1]["epoch"]))
    return np.asarray(X, dtype=float), np.asarray(y, dtype=float), target_epochs


def _regression_metrics(actual: np.ndarray, predicted: np.ndarray, actual_returns: np.ndarray, previous_close: np.ndarray) -> dict:
    errors = predicted - actual
    return {
        "mse": round(float(np.mean(errors ** 2)), 6),
        "mae": round(float(np.mean(np.abs(errors))), 6),
        "rmse": round(float(np.sqrt(np.mean(errors ** 2))), 6),
        "directional_accuracy": round(float(np.mean(((predicted / previous_close - 1) >= 0) == (actual_returns >= 0))), 6),
        "test_points": int(len(actual)),
    }


def _price_intervals(last_close: float, predicted_return: float, calibration_errors: np.ndarray) -> dict:
    """Empirical central prediction zones, calibrated only on validation residuals."""
    errors = np.asarray(calibration_errors, dtype=float)
    if not len(errors):
        errors = np.asarray([0.0])
    quantiles = {
        "50": (0.25, 0.75),
        "90": (0.05, 0.95),
    }
    intervals = {}
    for name, (lo_q, hi_q) in quantiles.items():
        lo, hi = np.quantile(errors, [lo_q, hi_q])
        intervals[name] = {
            "lower": round(float(last_close * math.exp(predicted_return + lo)), 2),
            "upper": round(float(last_close * math.exp(predicted_return + hi)), 2),
        }
    return intervals


def _probability_up(predicted_return: float, calibration_errors: np.ndarray) -> float:
    errors = np.asarray(calibration_errors, dtype=float)
    if not len(errors):
        return 0.5
    # Laplace smoothing prevents a small calibration set from claiming certainty.
    wins = int(np.count_nonzero(predicted_return + errors > 0))
    return float((wins + 1) / (len(errors) + 2))


def _direction_metrics(actual_returns: np.ndarray, probabilities_up: np.ndarray) -> dict:
    y = (actual_returns > 0).astype(float)
    probability = np.clip(probabilities_up.astype(float), 1e-6, 1 - 1e-6)
    metrics = {
        "up_probability_brier": round(float(np.mean((probability - y) ** 2)), 6),
        "up_probability_log_loss": round(float(-np.mean(y * np.log(probability) + (1 - y) * np.log(1 - probability))), 6),
        "probability_test_points": int(len(y)),
    }
    if len(set(y)) == 2:
        try:
            from sklearn.metrics import roc_auc_score
            metrics["up_probability_roc_auc"] = round(float(roc_auc_score(y, probability)), 6)
        except (ImportError, ValueError):
            metrics["up_probability_roc_auc"] = None
    else:
        metrics["up_probability_roc_auc"] = None
    return metrics


def evaluate(pair: str = PAIR_SCOPE, source: str | None = None) -> dict:
    data = history_status(pair)
    selected = source or data["selected_source"]
    if selected not in {"deriv", "yahoo"}:
        return {"status": "insufficient_data", "error": f"Need at least {MIN_FORECAST_CANDLES} 1H candles from one source."}
    source_status = data["sources"][selected]
    # The last provider bar may still be forming. A projection is made only
    # from fully closed candles, with the next expected bar as its horizon.
    rows = [
        row for row in repo.read_research_candles(pair, TIMEFRAME, selected)
        if int(row["epoch"]) + 3600 <= int(time())
    ]
    X, y, target_epochs = _feature_rows(rows)
    if len(X) < MIN_FORECAST_CANDLES - LOOKBACK_BARS:
        return {"status": "insufficient_data", "error": f"Need at least {MIN_FORECAST_CANDLES} clean 1H candles; {len(rows)} available from {selected}.", "source": selected}

    n = len(X)
    train_end = int(n * 0.70)
    valid_end = int(n * 0.85)
    if n - valid_end < 50:
        return {"status": "insufficient_data", "error": "The chronological test segment needs at least 50 forecast points.", "source": selected}
    train_x, train_y = X[:train_end], y[:train_end]
    valid_x, valid_y = X[train_end:valid_end], y[train_end:valid_end]
    test_x, test_y = X[valid_end:], y[valid_end:]
    test_epochs = target_epochs[valid_end:]
    previous_close = np.asarray([rows[LOOKBACK_BARS - 1 + i + valid_end]["close"] for i in range(len(test_y))], dtype=float)
    actual_close = previous_close * np.exp(test_y)
    baseline_close = previous_close.copy()
    predicted_close = None
    test_model_returns = None
    validation_model_returns = None
    future_model_return = 0.0
    model_error = None
    model_name = "lightgbm"
    feature_importance = []
    try:
        from lightgbm import LGBMRegressor, early_stopping
        model = LGBMRegressor(
            objective="regression_l1", max_depth=3, num_leaves=7,
            min_child_samples=20, learning_rate=0.03, n_estimators=500,
            subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
            importance_type="gain", random_state=42, verbosity=-1,
        )
        model.fit(train_x, train_y, eval_set=[(valid_x, valid_y)], callbacks=[early_stopping(40, verbose=False)])
        validation_model_returns = model.predict(valid_x)
        test_model_returns = model.predict(test_x)
        predicted_close = previous_close * np.exp(test_model_returns)
        best_iterations = int(getattr(model, "best_iteration_", 0) or getattr(model, "n_estimators", 500))
        future_model = LGBMRegressor(
            objective="regression_l1", max_depth=3, num_leaves=7,
            min_child_samples=20, learning_rate=0.03, n_estimators=best_iterations,
            subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
            importance_type="gain", random_state=42, verbosity=-1,
        )
        future_model.fit(X, y)
        future_model_return = float(future_model.predict(X[-1:])[0])
        importance_total = float(np.sum(model.feature_importances_))
        if importance_total:
            feature_labels = [
                f"lag_{LOOKBACK_BARS - 1 - index // 4}_{('open', 'high', 'low', 'close')[index % 4]}"
                for index in range(LOOKBACK_BARS * 4)
            ] + list(_INDICATOR_FEATURES)
            feature_importance = [
                {"feature": feature_labels[i], "gain": round(float(value / importance_total), 6)}
                for i, value in sorted(enumerate(model.feature_importances_), key=lambda item: item[1], reverse=True)[:12]
            ]
    except (ImportError, OSError) as exc:
        reason = (
            "LightGBM native runtime unavailable (macOS may need libomp)."
            if isinstance(exc, OSError) else f"LightGBM unavailable: {exc}"
        )
        try:
            from sklearn.ensemble import HistGradientBoostingRegressor
            model = HistGradientBoostingRegressor(
                max_iter=150, max_leaf_nodes=7, min_samples_leaf=20,
                learning_rate=0.05, l2_regularization=1.0,
                early_stopping=False, random_state=42,
            )
            model.fit(train_x, train_y)
            validation_model_returns = model.predict(valid_x)
            test_model_returns = model.predict(test_x)
            predicted_close = previous_close * np.exp(test_model_returns)
            future_model = HistGradientBoostingRegressor(
                max_iter=150, max_leaf_nodes=7, min_samples_leaf=20,
                learning_rate=0.05, l2_regularization=1.0,
                early_stopping=False, random_state=42,
            )
            future_model.fit(X, y)
            future_model_return = float(future_model.predict(X[-1:])[0])
            model_name = "hist_gradient_boosting"
            model_error = f"{reason} Used scikit-learn histogram gradient boosting as the compatible fallback."
        except ImportError as fallback_error:
            model_error = f"{reason} No compatible fallback is installed: {fallback_error}"

    # Direction is computed from each forecast's own previous close, not from
    # adjacent rows of the test slice.
    actual_returns = test_y
    models = {"last_close": _regression_metrics(actual_close, baseline_close, actual_returns, previous_close)}
    if predicted_close is not None:
        models[model_name] = _regression_metrics(actual_close, predicted_close, actual_returns, previous_close)
    calibration_errors = valid_y - validation_model_returns if validation_model_returns is not None else valid_y
    baseline_probability_up = _probability_up(0.0, valid_y)
    baseline_probabilities = np.full(len(test_y), baseline_probability_up)
    models["last_close"].update(_direction_metrics(actual_returns, baseline_probabilities))
    model_probabilities = None
    if test_model_returns is not None:
        model_probabilities = np.asarray([_probability_up(float(value), calibration_errors) for value in test_model_returns])
        models[model_name].update(_direction_metrics(actual_returns, model_probabilities))
    points = []
    for i, epoch in enumerate(test_epochs):
        point = {"epoch": epoch, "actual": float(actual_close[i]), "last_close": float(baseline_close[i])}
        if predicted_close is not None:
            point[model_name] = float(predicted_close[i])
        point["last_close_probability_up"] = baseline_probability_up
        if model_probabilities is not None:
            point["probability_up"] = float(model_probabilities[i])
        points.append(point)
    last_close = float(rows[-1]["close"])
    if predicted_close is None:
        model_name = "last_close"
        future_model_return = 0.0
    projected_price = float(last_close * math.exp(future_model_return))
    probability_up = _probability_up(future_model_return, calibration_errors)
    probability_down = 1.0 - probability_up
    bias = "BUY BIAS" if probability_up >= 0.55 else "SELL BIAS" if probability_down >= 0.55 else "NO CLEAR EDGE"
    future_forecast = {
        "epoch": int(rows[-1]["epoch"]) + 3600,
        "as_of_epoch": int(rows[-1]["epoch"]) + 3600,
        "last_close": last_close,
        "prediction": projected_price,
        "direction": "up" if probability_up > 0.5 else "down" if probability_down > 0.5 else "flat",
        "bias": bias,
        "probability_up": round(probability_up, 4),
        "probability_down": round(probability_down, 4),
        "model": model_name,
        "intervals": _price_intervals(last_close, future_model_return, calibration_errors),
        "calibration_samples": int(len(calibration_errors)),
        "stale_hours": round(max(0.0, time() - (int(rows[-1]["epoch"]) + 3600)) / 3600, 2),
    }
    yahoo_note = "GC=F is a gold-futures proxy, not XAU/USD spot." if selected == "yahoo" else None
    return {
        "status": "completed" if predicted_close is not None else "inconclusive",
        "pair": pair, "timeframe": TIMEFRAME, "source": selected,
        "source_note": yahoo_note, "lookback_bars": LOOKBACK_BARS,
        "metrics": models, "points": points, "feature_importance": feature_importance,
        "model_error": model_error, "model_name": model_name if predicted_close is not None else None,
        "future_forecast": future_forecast,
        "data": {
            "source_rows": source_status["rows"], "source_first_epoch": source_status["first_epoch"],
            "source_last_epoch": source_status["last_epoch"], "train_points": train_end,
            "validation_points": valid_end - train_end, "test_points": len(test_y),
            "test_start_epoch": test_epochs[0], "test_end_epoch": test_epochs[-1],
        },
    }


def _run(run_id: int, pair: str, source: str) -> None:
    repo.update_research_forecast_run(run_id, status="running")
    try:
        result = evaluate(pair, source)
        repo.update_research_forecast_run(
            run_id, status=result["status"], metrics_json=result.get("metrics", {}),
            points_json=result.get("points", []),
            data_json={key: value for key, value in result.items() if key not in {"metrics", "points"}},
            error=result.get("error") or result.get("model_error"), completed_at=int(time()),
        )
    except Exception as exc:  # noqa: BLE001 - keep worker failures visible in run history
        repo.update_research_forecast_run(run_id, status="failed", error=str(exc), completed_at=int(time()))


def start_run(pair: str = PAIR_SCOPE) -> dict:
    if pair != PAIR_SCOPE:
        raise ValueError("price forecast is scoped to XAU/USD")
    status = history_status(pair)
    source = status["selected_source"]
    if source is None:
        raise ValueError(f"No single-source 1H dataset has {MIN_FORECAST_CANDLES} candles yet. Fetch history first.")
    with _LOCK:
        if repo.active_research_data_job(pair):
            raise ValueError("historical data fetch is running; wait for it to finish before forecasting")
        active = repo.active_research_forecast_run(pair)
        if active:
            return active
        run_id = repo.create_research_forecast_run(pair, TIMEFRAME, source)
        _EXECUTOR.submit(_run, run_id, pair, source)
    return repo.get_research_forecast_run(run_id) or {"id": run_id, "status": "queued"}


def list_runs(pair: str = PAIR_SCOPE, limit: int = 20) -> list[dict]:
    return repo.list_research_forecast_runs(pair, limit)


def get_run(run_id: int) -> dict | None:
    return repo.get_research_forecast_run(run_id)
