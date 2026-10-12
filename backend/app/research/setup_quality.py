"""Offline XAU/USD setup-quality experiments.

This module is deliberately isolated from the verdict and monitor paths. It
labels deduplicated setup episodes against clean, forward candles and compares
simple baselines with logistic regression and LightGBM using date-grouped
walk-forward folds. Nothing here can arm, block, size, or execute a trade.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from threading import Lock
from time import time
from typing import Any

import numpy as np
import pandas as pd

from ..config import ACCOUNT_BALANCE, MIN_RR, RISK_PCT, XAUUSD_SPREAD
from ..db import repo
from ..services import topdown
from ..services.whatif import replay_setup

PAIR_SCOPE = "xauusd"
LABEL_HORIZON_BARS = 48
MIN_RESOLVED = 200
MIN_DAYS = 60
MIN_CLASS = 30
RANDOM_SEED = 42
MODEL_KIND = "setup_quality_research_v1"
SCAN_EVERY_ENTRY_BARS = 4
CANDLE_CONTEXT_LIMIT = 600

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tradeguard-research")
_LOCK = Lock()


def _mode_timeframes(mode: str) -> list[str]:
    roles = topdown.timeframe_roles(mode)
    return list(dict.fromkeys(roles[key] for key in ("bias_tf", "setup_tf", "entry_tf")))


@dataclass
class ResearchSample:
    ts: int
    day: str
    mode: str
    side: str
    source: str
    label: int | None
    outcome: str
    rr: float
    risk_distance: float
    features: dict[str, float]


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _clean_candles(pair: str, timeframe: str) -> tuple[list[dict], dict]:
    rows = repo.candle_quality_rows(pair, timeframe)
    seconds = topdown.timeframe_seconds(timeframe)
    clean: list[dict] = []
    off_grid = invalid = 0
    sources: dict[str, int] = {}
    for row in rows:
        source = str(row.get("source") or "legacy")
        sources[source] = sources.get(source, 0) + 1
        # Weekly candles may be anchored to Monday in the provider's exchange
        # timezone, so Unix-epoch modulus is not a meaningful weekly grid test.
        if timeframe != "1W" and int(row["epoch"]) % seconds:
            off_grid += 1
            continue
        o, h, l, c = (_finite(row.get(k), float("nan")) for k in ("open", "high", "low", "close"))
        if not all(math.isfinite(v) for v in (o, h, l, c)) or h < l or not (l <= o <= h and l <= c <= h):
            invalid += 1
            continue
        clean.append({"epoch": int(row["epoch"]), "open": o, "high": h, "low": l, "close": c, "source": source})
    # Prefer canonical Deriv rows when they have enough continuity. Until a
    # backfill lands, aligned legacy rows remain usable but visibly provisional.
    deriv = [row for row in clean if row["source"] == "deriv"]
    selected = deriv if len(deriv) >= 240 else clean
    selected_source = "deriv" if selected is deriv else "legacy_or_mixed"
    gaps = 0
    for left, right in zip(selected, selected[1:]):
        if right["epoch"] - left["epoch"] > seconds * 3:
            gaps += 1
    days = len({datetime.fromtimestamp(row["epoch"], timezone.utc).date().isoformat() for row in selected})
    return selected, {
        "rows": len(rows), "valid_rows": len(selected), "off_grid": off_grid,
        "invalid_ohlc": invalid, "gaps": gaps, "days": days,
        "sources": sources, "selected_source": selected_source,
        "first_epoch": selected[0]["epoch"] if selected else None,
        "last_epoch": selected[-1]["epoch"] if selected else None,
    }


def data_status(pair: str) -> dict:
    modes = []
    for mode, roles in topdown.TRADER_MODES.items():
        frames = {}
        blockers = []
        for tf in _mode_timeframes(mode):
            _, quality = _clean_candles(pair, tf)
            frames[tf] = quality
            if quality["valid_rows"] < 240:
                blockers.append(f"{tf} needs at least 240 clean candles")
            if quality["selected_source"] != "deriv":
                blockers.append(f"{tf} is not yet a canonical Deriv-only series")
        modes.append({"trader_mode": mode, "frames": frames, "blockers": blockers})
    journal = repo.list_setup_journal(pair, limit=5000)
    return {
        "pair": pair,
        "model_kind": MODEL_KIND,
        "research_only": True,
        "label_horizon_bars": LABEL_HORIZON_BARS,
        "journal_rows": len(journal),
        "journal_days": len({datetime.fromtimestamp(int(r.get("first_seen_at") or r.get("created_at") or 0), timezone.utc).date().isoformat() for r in journal if r.get("first_seen_at") or r.get("created_at")}),
        "modes": modes,
    }


def _atr(rows: list[dict], period: int = 14) -> float:
    if len(rows) < 2:
        return 0.0
    trs = []
    for previous, row in zip(rows[-period - 1:-1], rows[-period:]):
        trs.append(max(row["high"] - row["low"], abs(row["high"] - previous["close"]), abs(row["low"] - previous["close"])))
    return float(np.mean(trs)) if trs else 0.0


def _rsi(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    changes = np.diff(closes[-period - 1:])
    gains = np.maximum(changes, 0).mean()
    losses = np.maximum(-changes, 0).mean()
    if losses == 0:
        return 100.0 if gains else 50.0
    return float(100 - 100 / (1 + gains / losses))


def _ema(values: np.ndarray, span: int) -> float:
    if len(values) == 0:
        return 0.0
    alpha = 2.0 / (span + 1.0)
    result = float(values[0])
    for value in values[1:]:
        result = alpha * float(value) + (1 - alpha) * result
    return result


def _market_features(rows: list[dict]) -> dict[str, float]:
    if not rows:
        return {"atr_pct": 0.0, "rsi14": 0.5}
    history = rows[-240:]
    closes = np.asarray([row["close"] for row in history], dtype=float)
    close = float(closes[-1])
    atr = _atr(history)
    out: dict[str, float] = {"atr_pct": atr / close if close else 0.0, "rsi14": _rsi(closes) / 100.0}
    for lag in (1, 3, 6, 12):
        out[f"return_{lag}"] = close / float(closes[-lag - 1]) - 1 if len(closes) > lag else 0.0
    ema12, ema26 = _ema(closes[-80:], 12), _ema(closes[-120:], 26)
    out["macd_atr"] = (ema12 - ema26) / atr if atr else 0.0
    for span in (9, 21, 50, 200):
        ema = _ema(closes[-max(span * 3, span):], span)
        out[f"close_ema{span}_atr"] = (close - ema) / atr if atr else 0.0
    window = closes[-20:]
    mean, std = float(window.mean()), float(window.std())
    out["bollinger_width"] = (4 * std / mean) if mean else 0.0
    high = max(row["high"] for row in history[-20:])
    low = min(row["low"] for row in history[-20:])
    out["range_position"] = (close - low) / (high - low) if high > low else 0.5
    out["realized_vol"] = float(np.std(np.diff(np.log(closes[-21:])))) if len(closes) > 2 else 0.0
    return out


def _market_features_asof(rows: list[dict], epochs: list[int], ts: int) -> dict[str, float]:
    index = int(np.searchsorted(epochs, ts, side="right") - 1)
    if index < 0:
        return {}
    return _market_features(rows[max(0, index - 239):index + 1])


def _candidate_features(row: dict, history: list[dict]) -> dict[str, float]:
    entry, stop, target = (_finite(row.get(k)) for k in ("entry", "stop", "target"))
    atr = _atr(history)
    plan = row.get("plan_json") or {}
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except json.JSONDecodeError:
            plan = {}
    checklist = plan.get("checklist") or []
    passed = sum(1 for item in checklist if item.get("passed") is True)
    failed = sum(1 for item in checklist if item.get("passed") is False)
    pending = max(0, len(checklist) - passed - failed)
    ts = int(row.get("first_seen_at") or row.get("created_at") or 0)
    dt = datetime.fromtimestamp(ts, timezone.utc)
    features = {
        "side_long": 1.0 if row.get("side") == "long" else 0.0,
        "planned_rr": _finite(row.get("rr"), abs(target - entry) / max(abs(entry - stop), 1e-9)),
        "risk_atr": abs(entry - stop) / atr if atr else 0.0,
        "target_atr": abs(target - entry) / atr if atr else 0.0,
        "check_passed": float(passed), "check_failed": float(failed), "check_pending": float(pending),
        "status_waiting": 1.0 if row.get("status") == "waiting" else 0.0,
        "status_discarded": 1.0 if row.get("status") == "discarded" else 0.0,
        "source_sweep_reclaim": 1.0 if row.get("source") == "sweep_reclaim" else 0.0,
        "hour_sin": math.sin(2 * math.pi * dt.hour / 24),
        "hour_cos": math.cos(2 * math.pi * dt.hour / 24),
        "weekday": dt.weekday() / 6.0,
    }
    features.update(_market_features(history))
    return features


def _as_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame.index = pd.to_datetime(frame.pop("epoch"), unit="s", utc=True)
    return frame[["open", "high", "low", "close"]]


def _historical_candidates(pair: str, mode: str, frame_rows: dict[str, list[dict]]) -> tuple[list[dict], int]:
    """Replay the deterministic scanner on candle prefixes, never future bars.

    A cadence of four entry bars limits redundant scans; the later episode
    dedupe still collapses same-level candidates across the 48-bar label horizon.
    """
    # The deterministic scanner belongs to services; importing this lazily keeps
    # the research module decoupled while still allowing replay once data exists.
    from ..services.scan import scan as run_scan

    roles = topdown.timeframe_roles(mode)
    unique_tfs = _mode_timeframes(mode)
    full_frames = {tf: _as_frame(frame_rows[tf]) for tf in unique_tfs}
    entry_tf = roles["entry_tf"]
    entry_rows = frame_rows[entry_tf]
    entry_epochs = [row["epoch"] for row in entry_rows]
    generated = []
    scan_errors = 0

    entry_seconds = topdown.timeframe_seconds(entry_tf)
    for entry_idx in range(240, len(entry_rows), SCAN_EVERY_ENTRY_BARS):
        # Provider epochs identify candle opens. Make the decision at its close
        # and admit only higher-timeframe bars whose own closes have passed.
        ts = entry_epochs[entry_idx] + entry_seconds
        frames = {}
        ready = True
        for tf, full in full_frames.items():
            last_closed_open = ts - topdown.timeframe_seconds(tf)
            prefix = full.loc[full.index <= pd.to_datetime(last_closed_open, unit="s", utc=True)].tail(CANDLE_CONTEXT_LIMIT)
            if len(prefix) < 240:
                ready = False
                break
            frames[tf] = prefix
        if not ready:
            continue
        try:
            scans = {
                tf: run_scan(
                    frame, pair=pair, pair_display="XAU/USD", timeframe=tf,
                    min_rr=MIN_RR, pair_category="metals",
                )
                for tf, frame in frames.items()
            }
            setup_scan, bias_scan, entry_scan = scans[roles["setup_tf"]], scans[roles["bias_tf"]], scans[entry_tf]
            entries = setup_scan.get("future", {}).get("entries", [])
            if not entries:
                continue
            analysis = {
                "structure": bias_scan.get("structure", {}).get("state"),
                "bias": bias_scan.get("future", {}).get("bias"),
                "entry": entries[0],
                "sr_zones": setup_scan.get("layers", {}).get("sr", []),
                "supply_demand": setup_scan.get("layers", {}).get("supply_demand", []),
                "fvgs": setup_scan.get("layers", {}).get("fvg", []),
                "patterns": entry_scan.get("layers", {}).get("patterns_all", []),
                "pattern_candles": frames[entry_tf],
            }
            plan = topdown.build_trade_plan(
                frames[roles["setup_tf"]], analysis,
                account_balance=ACCOUNT_BALANCE, risk_pct=RISK_PCT, min_rr=MIN_RR,
                ltf_candles=frames[entry_tf],
            )
            if plan.get("side") not in {"long", "short"} or any(plan.get(key) is None for key in ("entry", "stop", "target")):
                continue
            plan["trader_mode"] = mode
            source = entries[0].get("source") or entries[0].get("kind") or "zone"
            generated.append({
                "pair": pair, "status": plan.get("status", "DISCARDED"),
                "side": plan["side"], "source": str(source), "entry": plan["entry"],
                "stop": plan["stop"], "target": plan["target"], "rr": plan.get("rr"),
                "trader_mode": mode, "feed_source": "deriv", "first_seen_at": ts,
                "created_at": ts, "plan_json": plan,
            })
        except Exception:  # noqa: BLE001 - one malformed point-in-time scan must not abort a research run
            scan_errors += 1
    return generated, scan_errors


def build_samples(pair: str, mode: str) -> tuple[list[ResearchSample], dict]:
    roles = topdown.timeframe_roles(mode)
    entry_tf = roles["entry_tf"]
    frame_rows = {}
    frame_quality = {}
    frame_epochs = {}
    for tf in _mode_timeframes(mode):
        frame_rows[tf], frame_quality[tf] = _clean_candles(pair, tf)
        frame_epochs[tf] = [c["epoch"] for c in frame_rows[tf]]
    candles, quality = frame_rows[entry_tf], frame_quality[entry_tf]
    epochs = [row["epoch"] for row in candles]
    if any(frame_quality[tf].get("selected_source") != "deriv" or frame_quality[tf]["valid_rows"] < 240 for tf in _mode_timeframes(mode)):
        quality["candidate_rows"] = 0
        quality["deduplicated_episodes"] = 0
        quality["resolved_episodes"] = 0
        quality["frames"] = {tf: dict(item) for tf, item in frame_quality.items()}
        return [], quality
    journal, scan_errors = _historical_candidates(pair, mode, frame_rows)
    journal.sort(key=lambda row: int(row.get("first_seen_at") or row.get("created_at") or 0))
    samples: list[ResearchSample] = []
    seen: dict[str, int] = {}
    seconds = topdown.timeframe_seconds(entry_tf)
    for row in journal:
        ts = int(row.get("first_seen_at") or row.get("created_at") or 0)
        if not ts or not row.get("side") or any(row.get(key) is None for key in ("entry", "stop", "target")):
            continue
        # ATR-tolerant-enough quantisation for XAU removes rapid rescans whose
        # raw levels differ only by feed noise.
        key = "|".join(str(value) for value in (
            mode, row.get("side"), row.get("source"), round(_finite(row.get("entry")), 1),
            round(_finite(row.get("stop")), 1), round(_finite(row.get("target")), 1),
        ))
        if ts - seen.get(key, -10**18) < LABEL_HORIZON_BARS * seconds:
            continue
        seen[key] = ts
        idx = int(np.searchsorted(epochs, ts, side="right") - 1)
        if idx < 30:
            continue
        history = candles[max(0, idx - 239):idx + 1]
        future = candles[idx + 1:idx + 1 + LABEL_HORIZON_BARS]
        if any(frame_quality[tf].get("selected_source") != "deriv" for tf in _mode_timeframes(mode)):
            # Provisional rows can be inspected in the UI but are excluded from
            # any model run until every timeframe in this mode is source-clean.
            continue
        result = replay_setup(row, future)
        label = 1 if result["outcome"] == "target" else 0 if result["outcome"] == "stopped" else None
        samples.append(ResearchSample(
            ts=ts,
            day=datetime.fromtimestamp(ts, timezone.utc).date().isoformat(),
            mode=mode, side=str(row.get("side")), source=str(row.get("source") or "unknown"),
            label=label, outcome=result["outcome"], rr=_finite(result.get("rr_target"), _finite(row.get("rr"))),
            risk_distance=abs(_finite(row.get("entry")) - _finite(row.get("stop"))),
            features={
                **_candidate_features(row, history),
                **{
                    f"{prefix}_{name}": value
                    for prefix, tf in (("bias", roles["bias_tf"]), ("setup", roles["setup_tf"]))
                    if tf != entry_tf
                    for name, value in _market_features_asof(frame_rows[tf], frame_epochs[tf], ts).items()
                },
            },
        ))
    quality["candidate_rows"] = len(journal)
    quality["scan_errors"] = scan_errors
    quality["frames"] = {tf: dict(item) for tf, item in frame_quality.items()}
    quality["deduplicated_episodes"] = len(samples)
    quality["resolved_episodes"] = sum(sample.label is not None for sample in samples)
    return samples, quality


def _walk_forward_folds(samples: list[ResearchSample], tf_seconds: int) -> list[tuple[list[int], list[int]]]:
    days = sorted({sample.day for sample in samples})
    if len(days) < 4:
        return []
    cut_points = sorted(set(max(1, int(len(days) * frac)) for frac in (0.50, 0.66, 0.82)))
    folds = []
    purge = LABEL_HORIZON_BARS * tf_seconds
    for number, cut in enumerate(cut_points):
        if cut >= len(days):
            continue
        end = cut_points[number + 1] if number + 1 < len(cut_points) else len(days)
        test_days = set(days[cut:end])
        if not test_days:
            continue
        test_idx = [i for i, sample in enumerate(samples) if sample.day in test_days]
        test_start = min(samples[i].ts for i in test_idx)
        train_idx = [i for i, sample in enumerate(samples) if sample.day < days[cut] and sample.ts < test_start - purge]
        if train_idx and test_idx:
            folds.append((train_idx, test_idx))
    return folds


def _metric_values(y: np.ndarray, probability: np.ndarray) -> dict:
    probability = np.clip(probability.astype(float), 1e-6, 1 - 1e-6)
    metrics = {
        "brier": round(float(np.mean((probability - y) ** 2)), 6),
        "log_loss": round(float(-np.mean(y * np.log(probability) + (1 - y) * np.log(1 - probability))), 6),
    }
    bins = np.linspace(0.0, 1.0, 11)
    calibration = 0.0
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (probability >= lo) & ((probability < hi) if hi < 1 else (probability <= hi))
        if np.any(mask):
            calibration += float(mask.mean()) * abs(float(probability[mask].mean()) - float(y[mask].mean()))
    metrics["calibration_error"] = round(calibration, 6)
    try:
        from sklearn.metrics import average_precision_score, roc_auc_score
        metrics["roc_auc"] = round(float(roc_auc_score(y, probability)), 6)
        metrics["pr_auc"] = round(float(average_precision_score(y, probability)), 6)
    except (ImportError, ValueError):
        metrics["roc_auc"] = None
        metrics["pr_auc"] = None
    return metrics


def evaluate_mode(pair: str, mode: str) -> dict:
    samples, quality = build_samples(pair, mode)
    resolved = [sample for sample in samples if sample.label is not None]
    positives = sum(sample.label == 1 for sample in resolved)
    negatives = len(resolved) - positives
    days = len({sample.day for sample in resolved})
    blockers = []
    if len(resolved) < MIN_RESOLVED:
        blockers.append(f"needs {MIN_RESOLVED} resolved episodes; has {len(resolved)}")
    if days < MIN_DAYS:
        blockers.append(f"needs {MIN_DAYS} independent dates; has {days}")
    if min(positives, negatives) < MIN_CLASS:
        blockers.append(f"needs {MIN_CLASS} examples of each class; has {positives} target / {negatives} stopped")
    if quality.get("selected_source") != "deriv":
        blockers.append("canonical Deriv-only candle backfill is incomplete")

    result = {
        "trader_mode": mode, "status": "insufficient_data", "sample_count": len(resolved),
        "independent_days": days, "positive_count": positives, "negative_count": negatives,
        "censored_count": len(samples) - len(resolved), "metrics": {}, "folds": [],
        "feature_importance": [], "predictions": [], "blockers": blockers, "data_quality": quality,
        "feature_schema_version": 1,
        "dataset_fingerprint": sha256(json.dumps([
            [sample.ts, sample.side, sample.source, sample.label, sample.outcome, sample.rr, sample.features]
            for sample in samples
        ], sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }
    if len(resolved) < 20 or positives < 3 or negatives < 3:
        return result

    feature_names = sorted({name for sample in resolved for name in sample.features})
    X = np.asarray([[sample.features.get(name, 0.0) for name in feature_names] for sample in resolved], dtype=float)
    y = np.asarray([int(sample.label) for sample in resolved], dtype=int)
    folds = _walk_forward_folds(resolved, topdown.timeframe_seconds(topdown.timeframe_roles(mode)["entry_tf"]))
    predictions: dict[str, list[float]] = {name: [] for name in ("constant", "segment", "logistic", "lightgbm")}
    actual: list[int] = []
    actual_days: list[str] = []
    lightgbm_net_r: list[float] = []
    fold_rows = []
    prediction_records = []
    importance = np.zeros(len(feature_names), dtype=float)
    model_error = None
    for fold_no, (train_idx, test_idx) in enumerate(folds, 1):
        y_train, y_test = y[train_idx], y[test_idx]
        if len(set(y_train)) < 2 or len(set(y_test)) < 2:
            continue
        base = float(y_train.mean())
        constant = np.full(len(test_idx), base)
        groups: dict[tuple[str, str], list[int]] = {}
        for idx in train_idx:
            groups.setdefault((resolved[idx].side, resolved[idx].source), []).append(int(y[idx]))
        segment = np.asarray([
            (sum(groups.get((resolved[idx].side, resolved[idx].source), [])) + base * 4)
            / (len(groups.get((resolved[idx].side, resolved[idx].source), [])) + 4)
            for idx in test_idx
        ])
        fold_predictions = {"constant": constant, "segment": segment}
        try:
            from sklearn.impute import SimpleImputer
            from sklearn.linear_model import LogisticRegression
            from sklearn.pipeline import make_pipeline
            from sklearn.preprocessing import StandardScaler
            logistic = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(C=0.3, max_iter=1000, random_state=RANDOM_SEED))
            logistic.fit(X[train_idx], y_train)
            fold_predictions["logistic"] = logistic.predict_proba(X[test_idx])[:, 1]
        except ImportError as exc:
            model_error = f"scikit-learn unavailable: {exc}"
        try:
            from lightgbm import LGBMClassifier, early_stopping
            train_days = sorted({resolved[i].day for i in train_idx})
            validation_days = set(train_days[max(1, int(len(train_days) * 0.8)):])
            validation_start = min((resolved[i].ts for i in train_idx if resolved[i].day in validation_days), default=None)
            purge = LABEL_HORIZON_BARS * topdown.timeframe_seconds(topdown.timeframe_roles(mode)["entry_tf"])
            if validation_start is not None:
                fit_idx = [i for i in train_idx if resolved[i].day not in validation_days and resolved[i].ts < validation_start - purge]
                validation_idx = [i for i in train_idx if resolved[i].day in validation_days]
            else:
                fit_idx, validation_idx = train_idx, []
            if len(set(y[fit_idx])) == 2:
                model = LGBMClassifier(
                    max_depth=3, num_leaves=7, min_child_samples=30, learning_rate=0.03,
                    n_estimators=500 if len(validation_idx) and len(set(y[validation_idx])) == 2 else 120,
                    subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                    importance_type="gain", random_state=RANDOM_SEED, verbosity=-1,
                )
                kwargs = {}
                if len(validation_idx) and len(set(y[validation_idx])) == 2:
                    kwargs = {"eval_set": [(X[validation_idx], y[validation_idx])], "callbacks": [early_stopping(50, verbose=False)]}
                model.fit(X[fit_idx], y[fit_idx], **kwargs)
                lgb_prob = model.predict_proba(X[test_idx])[:, 1]
                fold_predictions["lightgbm"] = lgb_prob
                importance += model.feature_importances_
                for idx, probability in zip(test_idx, lgb_prob):
                    if probability >= 0.5:
                        sample = resolved[idx]
                        reward = sample.rr if sample.label else -1.0
                        spread_r = XAUUSD_SPREAD / max(sample.risk_distance, 1e-9)
                        lightgbm_net_r.append(reward - spread_r)
        except ImportError as exc:
            model_error = f"LightGBM unavailable: {exc}"
        except OSError:
            model_error = "LightGBM native runtime unavailable; install the platform OpenMP runtime (macOS: libomp)."
        for name, values in fold_predictions.items():
            predictions[name].extend(float(value) for value in values)
        for position, idx in enumerate(test_idx):
            sample = resolved[idx]
            prediction_records.append({
                "epoch": sample.ts, "day": sample.day, "fold": fold_no,
                "label": int(y_test[position]), "outcome": sample.outcome,
                "rr": sample.rr, "risk_distance": sample.risk_distance,
                "predictions": {name: float(values[position]) for name, values in fold_predictions.items()},
            })
        # Keep arrays aligned: unavailable challengers get no aggregate metric.
        actual.extend(int(value) for value in y_test)
        actual_days.extend(resolved[i].day for i in test_idx)
        fold_rows.append({
            "fold": fold_no, "train_samples": len(train_idx), "test_samples": len(test_idx),
            "train_end": resolved[train_idx[-1]].day, "test_start": resolved[test_idx[0]].day,
            "test_end": resolved[test_idx[-1]].day,
            "metrics": {name: _metric_values(y_test, np.asarray(values)) for name, values in fold_predictions.items()},
        })
    if len(fold_rows) < 1:
        result["blockers"].append("no valid two-class walk-forward fold")
        return result
    metrics = {}
    actual_array = np.asarray(actual)
    for name, values in predictions.items():
        if len(values) == len(actual):
            metrics[name] = _metric_values(actual_array, np.asarray(values))
    best_baseline = min((metrics[name]["brier"] for name in ("constant", "segment") if name in metrics), default=None)
    challenger = metrics.get("lightgbm")
    improvement = None
    if best_baseline and challenger:
        improvement = round((best_baseline - challenger["brier"]) / best_baseline, 6)
    bootstrap_ci = None
    if challenger and best_baseline is not None and "lightgbm" in predictions:
        baseline_name = min((name for name in ("constant", "segment") if name in metrics), key=lambda name: metrics[name]["brier"], default=None)
        if baseline_name and len(predictions["lightgbm"]) == len(actual):
            cand_p = np.clip(np.asarray(predictions["lightgbm"]), 1e-6, 1 - 1e-6)
            base_p = np.clip(np.asarray(predictions[baseline_name]), 1e-6, 1 - 1e-6)
            y_all = np.asarray(actual)
            deltas = (cand_p - y_all) ** 2 - (base_p - y_all) ** 2
            by_day: dict[str, list[float]] = {}
            for day, delta in zip(actual_days, deltas):
                by_day.setdefault(day, []).append(float(delta))
            day_values = list(by_day.values())
            if len(day_values) >= 2:
                rng = np.random.default_rng(RANDOM_SEED)
                boot = []
                for _ in range(500):
                    selected = rng.integers(0, len(day_values), len(day_values))
                    flat = [value for index in selected for value in day_values[int(index)]]
                    boot.append(float(np.mean(flat)))
                bootstrap_ci = [round(float(np.quantile(boot, 0.025)), 6), round(float(np.quantile(boot, 0.975)), 6)]
    metrics["lightgbm_net_r_after_spread"] = round(float(sum(lightgbm_net_r)), 4)
    metrics["qualification"] = {
        "best_baseline_brier": best_baseline,
        "lightgbm_relative_brier_improvement": improvement,
        "lightgbm_minus_baseline_brier_day_bootstrap_95ci": bootstrap_ci,
        "required_improvement": 0.05,
    }
    if model_error:
        result["blockers"].append(model_error)
    if len(fold_rows) < 3:
        result["blockers"].append(f"needs 3 valid walk-forward folds; has {len(fold_rows)}")
    if improvement is None or improvement < 0.05:
        result["blockers"].append("LightGBM has not beaten the best baseline by 5% Brier")
    if bootstrap_ci is None or bootstrap_ci[0] >= 0:
        result["blockers"].append("day-block bootstrap does not show a reliable Brier improvement")
    result.update({"metrics": metrics, "folds": fold_rows, "predictions": prediction_records})
    if importance.sum() > 0:
        result["feature_importance"] = [
            {"feature": feature_names[i], "gain": round(float(value / importance.sum()), 6)}
            for i, value in sorted(enumerate(importance), key=lambda item: item[1], reverse=True)[:15]
        ]
    result["status"] = "qualified_for_shadow" if not result["blockers"] else "inconclusive"
    return result


def _run(run_id: int, pair: str, modes: list[str]) -> None:
    repo.update_research_run(run_id, status="running", started_at=int(time()))
    try:
        mode_results = []
        for mode in modes:
            result = evaluate_mode(pair, mode)
            artifact_dir = Path(__file__).resolve().parents[2] / ".research"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = artifact_dir / f"run-{run_id}-{mode.lower().replace(' ', '-')}.json"
            artifact_path.write_text(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
            result["artifact_path"] = str(artifact_path)
            repo.replace_research_mode_result(run_id, result)
            mode_results.append(result)
        frame_fingerprints = {}
        for mode in modes:
            for tf in _mode_timeframes(mode):
                if tf in frame_fingerprints:
                    continue
                rows, _ = _clean_candles(pair, tf)
                frame_fingerprints[tf] = sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        fingerprint = sha256(json.dumps(frame_fingerprints, sort_keys=True).encode()).hexdigest()
        summary = {
            "research_only": True,
            "qualified_modes": [r["trader_mode"] for r in mode_results if r["status"] == "qualified_for_shadow"],
            "inconclusive_modes": [r["trader_mode"] for r in mode_results if r["status"] != "qualified_for_shadow"],
        }
        repo.update_research_run(run_id, status="completed", data_fingerprint=fingerprint, summary_json=summary, completed_at=int(time()))
    except Exception as exc:  # noqa: BLE001 - persist a useful failed run
        repo.update_research_run(run_id, status="failed", error=str(exc), completed_at=int(time()))


def start_run(pair: str, modes: list[str]) -> dict:
    if pair != PAIR_SCOPE:
        raise ValueError("The first research benchmark is scoped to XAU/USD.")
    invalid = [mode for mode in modes if mode not in topdown.TRADER_MODES]
    if invalid:
        raise ValueError(f"Unknown trader mode(s): {', '.join(invalid)}")
    config = {"model_kind": MODEL_KIND, "label_horizon_bars": LABEL_HORIZON_BARS, "seed": RANDOM_SEED, "spread": XAUUSD_SPREAD}
    with _LOCK:
        active = repo.active_research_run(pair)
        if active:
            return active
        run_id = repo.create_research_run(pair, modes, config)
        _EXECUTOR.submit(_run, run_id, pair, modes)
    return repo.get_research_run(run_id) or {"id": run_id, "status": "queued"}
