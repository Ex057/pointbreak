from datetime import datetime, timezone

from app.db import repo
from app.db.database import init_schema
from app.research import setup_quality


def _epoch(day: int) -> int:
    return int(datetime(2026, 1, day, tzinfo=timezone.utc).timestamp())


def test_candle_ingress_rejects_off_grid_and_bad_ohlc():
    init_schema()
    base = _epoch(1)
    written = repo.upsert_candles(
        "quality_pair",
        "1H",
        [
            {"epoch": base, "open": 100, "high": 102, "low": 99, "close": 101},
            {"epoch": base + 17, "open": 101, "high": 102, "low": 100, "close": 101},
            {"epoch": base + 3600, "open": 100, "high": 99, "low": 101, "close": 100},
        ],
        source="deriv",
    )
    assert written == 1
    rows = repo.candle_quality_rows("quality_pair", "1H")
    assert len(rows) == 1
    assert rows[0]["source"] == "deriv"
    assert rows[0]["quality_status"] == "valid"


def test_research_candles_keep_provider_series_isolated():
    init_schema()
    epoch = _epoch(1)
    repo.upsert_research_candles("source_test", "1H", "deriv", [
        {"epoch": epoch, "open": 100, "high": 102, "low": 99, "close": 101},
    ])
    # Yahoo exchange-hour bars can sit on a half-hour UTC offset.
    repo.upsert_research_candles("source_test", "1H", "yahoo", [
        {"epoch": epoch + 1800, "open": 200, "high": 202, "low": 199, "close": 201},
    ])
    assert repo.read_research_candles("source_test", "1H", "deriv")[0]["close"] == 101
    assert repo.read_research_candles("source_test", "1H", "yahoo")[0]["close"] == 201
    from app.research.data import history_status
    status = history_status("source_test")
    assert status["sources"]["deriv"]["rows"] == 1
    assert status["sources"]["yahoo"]["rows"] == 1
    assert status["selected_source"] is None


def test_historical_fallback_is_persisted_as_yahoo(monkeypatch):
    from app.services import markets

    init_schema()
    epoch = 1_900_000_000 // 3600 * 3600
    monkeypatch.setattr(markets, "fetch_deriv_history", lambda *args: [])
    monkeypatch.setattr(markets.yahoo, "fetch_candles_window", lambda *args: [
        {"epoch": epoch, "open": 2000, "high": 2002, "low": 1999, "close": 2001},
    ])
    assert markets.load_candle_window("xauusd", "1H", epoch, epoch)
    with repo.cursor() as con:
        row = con.execute("SELECT source FROM research_candles WHERE pair='xauusd' AND timeframe='1H' AND epoch=?", (epoch,)).fetchone()
        canonical = con.execute("SELECT 1 FROM candles WHERE pair='xauusd' AND timeframe='1H' AND epoch=?", (epoch,)).fetchone()
    assert row["source"] == "yahoo"
    assert canonical is None


def test_historical_candidate_replay_imports_real_scanner(monkeypatch):
    from app.services import topdown
    import app.services.scan

    monkeypatch.setattr(setup_quality, "SCAN_EVERY_ENTRY_BARS", 10_000)
    monkeypatch.setattr(app.services.scan, "scan", lambda frame, **kwargs: {
        "future": {"entries": [{"source": "test"}], "bias": "up"},
        "structure": {"state": "UPTREND"},
        "layers": {"sr": [], "supply_demand": [], "fvg": [], "patterns_all": []},
    })
    monkeypatch.setattr(topdown, "build_trade_plan", lambda *args, **kwargs: {
        "side": "long", "entry": 100.0, "stop": 99.0, "target": 102.0, "rr": 2.0,
    })
    base = 1_700_000_000 - (400 * 4 * 3600)
    frame_rows = {}
    for timeframe, seconds, count in (("4H", 14400, 400), ("1H", 3600, 400)):
        frame_rows[timeframe] = [
            {"epoch": base + index * seconds, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5}
            for index in range(count)
        ]
    entry_start = 1_700_000_000 - 240 * 900
    frame_rows["15m"] = [
        {"epoch": entry_start + index * 900, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5}
        for index in range(242)
    ]
    candidates, errors = setup_quality._historical_candidates("xauusd", "Day Trader", frame_rows)
    assert errors == 0
    assert candidates and candidates[0]["source"] == "test"


def test_price_forecast_reports_baseline_and_aligned_test_points(monkeypatch):
    import sys
    from types import ModuleType
    import numpy as np
    from app.research import price_forecast

    class DeterministicRegressor:
        def __init__(self, **kwargs):
            self.feature_importances_ = np.ones(126)

        def fit(self, x, y, **kwargs):
            return self

        def predict(self, x):
            return np.full(len(x), 0.0002)

    fake_lightgbm = ModuleType("lightgbm")
    fake_lightgbm.LGBMRegressor = DeterministicRegressor
    fake_lightgbm.early_stopping = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "lightgbm", fake_lightgbm)

    init_schema()
    rows = []
    start = _epoch(1)
    for index in range(700):
        close = 2000 + index * 0.04 + np.sin(index / 8) * 2
        rows.append({"epoch": start + index * 3600, "open": close - 0.2, "high": close + 0.8,
                     "low": close - 0.9, "close": close})
    repo.upsert_research_candles("forecast_test", "1H", "deriv", rows)
    result = price_forecast.evaluate("forecast_test")
    assert result["status"] == "completed"
    assert set(result["metrics"]) == {"last_close", "lightgbm"}
    assert result["metrics"]["lightgbm"]["test_points"] == len(result["points"])
    assert all(point["epoch"] > rows[0]["epoch"] for point in result["points"])
    forecast = result["future_forecast"]
    assert forecast["bias"] in {"BUY BIAS", "SELL BIAS", "NO CLEAR EDGE"}
    assert 0 <= forecast["probability_up"] <= 1
    assert forecast["probability_up"] + forecast["probability_down"] == 1
    assert forecast["intervals"]["90"]["lower"] <= forecast["intervals"]["90"]["upper"]
    assert forecast["intervals"]["50"]["lower"] <= forecast["intervals"]["50"]["upper"]
    assert "up_probability_brier" in result["metrics"]["lightgbm"]
    assert forecast["epoch"] > rows[-1]["epoch"]


def test_price_forecast_uses_cpu_fallback_when_lightgbm_runtime_is_missing(monkeypatch):
    import sys
    from types import ModuleType
    import numpy as np
    from app.research import price_forecast

    broken_lightgbm = ModuleType("lightgbm")
    def missing_runtime(_name):
        raise OSError("libomp.dylib unavailable")
    broken_lightgbm.__getattr__ = missing_runtime
    monkeypatch.setitem(sys.modules, "lightgbm", broken_lightgbm)

    init_schema()
    rows = []
    start = _epoch(1)
    for index in range(700):
        close = 2000 + index * 0.02 + np.sin(index / 9)
        rows.append({"epoch": start + index * 3600, "open": close - 0.1, "high": close + 0.6,
                     "low": close - 0.7, "close": close})
    repo.upsert_research_candles("forecast_fallback", "1H", "deriv", rows)
    result = price_forecast.evaluate("forecast_fallback")
    assert result["status"] == "completed"
    assert result["model_name"] == "hist_gradient_boosting"
    assert "lightgbm" in result["model_error"].lower()
    assert "hist_gradient_boosting" in result["metrics"]


def test_walk_forward_folds_are_chronological_and_purged():
    samples = []
    for day in range(1, 13):
        ts = _epoch(day)
        samples.append(setup_quality.ResearchSample(
            ts=ts,
            day=f"2026-01-{day:02d}",
            mode="Day Trader",
            side="long",
            source="plan",
            label=day % 2,
            outcome="target" if day % 2 else "stopped",
            rr=2.0,
            risk_distance=1.0,
            features={"planned_rr": 2.0},
        ))
    folds = setup_quality._walk_forward_folds(samples, 900)
    assert folds
    purge = setup_quality.LABEL_HORIZON_BARS * 900
    for train, test in folds:
        test_start = min(samples[i].ts for i in test)
        assert max(samples[i].ts for i in train) < test_start - purge


def test_research_run_round_trip_decodes_json():
    init_schema()
    run_id = repo.create_research_run("xauusd", ["Day Trader"], {"seed": 42})
    repo.update_research_run(run_id, status="running", started_at=123)
    repo.replace_research_mode_result(run_id, {
        "trader_mode": "Day Trader",
        "status": "insufficient_data",
        "sample_count": 2,
        "independent_days": 2,
        "positive_count": 1,
        "negative_count": 1,
        "censored_count": 3,
        "metrics": {"constant": {"brier": 0.25}},
        "folds": [],
        "feature_importance": [],
        "blockers": ["needs more history"],
        "predictions": [{"epoch": 123, "day": "2026-01-02", "fold": 1, "label": 1, "outcome": "target", "predictions": {"constant": 0.6}}],
    })
    row = repo.get_research_run(run_id)
    assert row is not None
    assert row["modes"] == ["Day Trader"]
    assert row["config"]["seed"] == 42
    assert row["results"][0]["blockers"] == ["needs more history"]
    assert row["results"][0]["predictions"][0]["predictions"]["constant"] == 0.6


def test_metric_values_use_probability_scores():
    y = setup_quality.np.asarray([0, 0, 1, 1])
    strong = setup_quality._metric_values(y, setup_quality.np.asarray([0.1, 0.2, 0.8, 0.9]))
    weak = setup_quality._metric_values(y, setup_quality.np.asarray([0.5, 0.5, 0.5, 0.5]))
    assert strong["brier"] < weak["brier"]
    assert strong["log_loss"] < weak["log_loss"]


def test_lightgbm_walk_forward_benchmark_and_day_bootstrap(monkeypatch):
    from datetime import timedelta
    import sys
    from types import ModuleType

    import numpy as np

    class DeterministicTree:
        def __init__(self, **kwargs):
            self.feature_importances_ = np.asarray([0.1, 1.0])

        def fit(self, x, y, **kwargs):
            return self

        def predict_proba(self, x):
            # Feature columns are sorted as noise, signal.
            positive = 0.1 + 0.8 * x[:, 1]
            return np.column_stack([1 - positive, positive])

    fake_lightgbm = ModuleType("lightgbm")
    fake_lightgbm.LGBMClassifier = DeterministicTree
    fake_lightgbm.early_stopping = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "lightgbm", fake_lightgbm)

    samples = []
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for day_index in range(150):
        day = start + timedelta(days=day_index)
        for within_day in range(4):
            label = (day_index + within_day) % 2
            ts = int(day.timestamp()) + within_day * 900
            samples.append(setup_quality.ResearchSample(
                ts=ts,
                day=day.date().isoformat(),
                mode="Day Trader",
                side="long" if label else "short",
                source="plan",
                label=label,
                outcome="target" if label else "stopped",
                rr=2.0,
                risk_distance=1.0,
                features={"signal": float(label), "noise": float((day_index * 7 + within_day) % 11) / 11.0},
            ))
    monkeypatch.setattr(setup_quality, "build_samples", lambda pair, mode: (samples, {"selected_source": "deriv"}))
    result = setup_quality.evaluate_mode("xauusd", "Day Trader")
    assert len(result["folds"]) == 3
    assert result["metrics"]["lightgbm"]["brier"] < result["metrics"]["constant"]["brier"]
    assert result["metrics"]["qualification"]["lightgbm_minus_baseline_brier_day_bootstrap_95ci"] is not None
    assert result["metrics"]["lightgbm_net_r_after_spread"] > 0
