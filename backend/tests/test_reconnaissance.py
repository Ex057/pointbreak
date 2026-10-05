from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.config import PREFERRED_MARKETS
from app.main import app
from app.services import reconnaissance
from app.services.fixtures import make_candles
from app.services.markets import market_by_id


def test_stance_boundaries():
    assert reconnaissance._stance(40)[0] == "bullish_alignment"
    assert reconnaissance._stance(15)[0] == "bullish_lean"
    assert reconnaissance._stance(14.9)[0] == "mixed"
    assert reconnaissance._stance(-15)[0] == "bearish_lean"
    assert reconnaissance._stance(-40)[0] == "bearish_alignment"


def test_completed_bars_excludes_open_interval():
    now_ts = 1_800_000_000
    step = reconnaissance.TF_SECONDS["15m"]
    end = pd.Timestamp(now_ts, unit="s", tz="UTC").floor("15min")
    frame = make_candles("xauusd", "15m", n=40)
    frame.index = pd.date_range(end=end, periods=40, freq=f"{step}s")

    completed = reconnaissance._completed(frame, "15m", now_ts)

    assert len(completed) == 39
    assert reconnaissance._epoch(completed.index[-1]) + step <= now_ts


def test_liquidity_uses_latest_completed_day_and_week():
    daily_index = pd.date_range("2026-09-01", periods=2, freq="D", tz="UTC")
    weekly_index = pd.date_range("2026-08-24", periods=2, freq="7D", tz="UTC")
    daily = pd.DataFrame(
        {"open": [10, 11], "high": [14, 16], "low": [8, 9], "close": [12, 15]},
        index=daily_index,
    )
    weekly = pd.DataFrame(
        {"open": [9, 12], "high": [15, 20], "low": [7, 10], "close": [13, 18]},
        index=weekly_index,
    )

    levels = {item["id"]: item for item in reconnaissance._liquidity_levels(daily, weekly)}

    assert levels["PDH"]["price"] == 16
    assert levels["PDL"]["price"] == 9
    assert levels["PWH"]["price"] == 20
    assert levels["PWL"]["price"] == 10
    assert levels["PDH"]["source_epoch"] == reconnaissance._epoch(daily_index[-1])


def test_partial_report_keeps_other_timeframes(monkeypatch):
    market = market_by_id("xauusd")
    original = reconnaissance.markets.load_candles

    def load(selected, timeframe):
        if timeframe == "1W":
            return pd.DataFrame(columns=["open", "high", "low", "close"])
        return original(selected, timeframe)

    monkeypatch.setattr(reconnaissance.markets, "load_candles", load)
    reconnaissance.clear_cache()
    report = reconnaissance.build(market, force_refresh=True)

    assert report["status"] == "partial"
    assert report["summary"]["stance"] != "unavailable"
    assert report["timeframes"][0]["timeframe"] == "1W"
    assert report["timeframes"][0]["available"] is False
    assert all(row["available"] for row in report["timeframes"][1:])


@pytest.mark.parametrize("pair", [item["id"] for item in PREFERRED_MARKETS])
def test_every_catalog_pair_can_be_reconnoitered(pair):
    reconnaissance.clear_cache()
    report = reconnaissance.build(market_by_id(pair), force_refresh=True)

    assert report["pair"] == pair
    assert report["status"] == "ready"
    assert [row["timeframe"] for row in report["timeframes"]] == list(reconnaissance.TIMEFRAMES)
    assert all(row["available"] for row in report["timeframes"])
    assert -100 <= report["summary"]["score"] <= 100


def test_reconnaissance_endpoint_and_cache():
    reconnaissance.clear_cache()
    with TestClient(app) as client:
        first = client.get("/reconnaissance", params={"pair": "eurusd"})
        second = client.get("/reconnaissance", params={"pair": "eurusd"})

    assert first.status_code == 200
    assert first.json()["status"] == "ready"
    assert {level["id"] for level in first.json()["liquidity"]["levels"]} == {"PDH", "PDL", "PWH", "PWL"}
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True


def test_reconnaissance_endpoint_rejects_unknown_pair():
    with TestClient(app) as client:
        response = client.get("/reconnaissance", params={"pair": "not-a-market"})
    assert response.status_code == 404
