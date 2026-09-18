"""Chart-pattern matcher tests over hand-crafted swing structures."""
import numpy as np
import pandas as pd

from app.engine.pattern_engine_v2 import PatternEngine
from app.engine.chart_patterns import detect_chart_patterns


def _frame(waypoints, seg=8):
    """Build an OHLC frame whose swing highs/lows sit at the given waypoints."""
    closes = []
    for a, b in zip(waypoints, waypoints[1:]):
        closes += list(np.linspace(a, b, seg, endpoint=False))
    closes.append(float(waypoints[-1]))
    closes = np.array(closes, float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) + 0.2
    lows = np.minimum(opens, closes) - 0.2
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="h")
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes}, index=idx)


def _by_name(df):
    eng = PatternEngine(df)
    return {p["name"]: p for p in detect_chart_patterns(eng)}


def test_double_top_confirmed_bearish():
    got = _by_name(_frame([100, 112, 104, 112, 100]))
    assert "Double Top" in got
    p = got["Double Top"]
    assert p["category"] == "bearish" and p["direction"] == "bearish"
    assert p["status"] == "confirmed"
    assert p["target"] < 104  # measured move below the neckline


def test_double_bottom_confirmed_bullish():
    got = _by_name(_frame([100, 88, 96, 88, 100]))
    assert "Double Bottom" in got
    p = got["Double Bottom"]
    assert p["category"] == "bullish"
    assert p["status"] == "confirmed"
    assert p["target"] > 96


def test_head_and_shoulders_bearish():
    got = _by_name(_frame([100, 110, 106, 116, 106, 110, 100]))
    assert "Head & Shoulders" in got
    p = got["Head & Shoulders"]
    assert p["category"] == "bearish"
    labels = [pv["label"] for pv in p["pivots"]]
    assert labels == ["LS", "H", "RS"]


def test_ascending_triangle_confirmed_bullish():
    # flat highs at ~110, rising lows, then a breakout above 110
    got = _by_name(_frame([100, 110, 102, 110, 106, 110, 114]))
    assert "Ascending Triangle" in got
    p = got["Ascending Triangle"]
    assert p["category"] == "bullish"
    assert p["status"] == "confirmed"


def test_forming_not_confirmed_before_breakout():
    # ascending triangle that has NOT broken out — must be forming, not confirmed
    got = _by_name(_frame([100, 110, 102, 110, 106, 109]))
    tri = got.get("Ascending Triangle") or got.get("Symmetrical Triangle")
    assert tri is not None
    assert tri["status"] == "forming"
    assert tri["breakout_time"] is None if "breakout_time" in tri else tri["breakout_index"] is None


def test_geometry_and_scoring_present():
    p = _by_name(_frame([100, 112, 104, 112, 100]))["Double Top"]
    assert p["lines"] and all({"x0", "y0", "x1", "y1"} <= set(ln) for ln in p["lines"])
    assert p["pivots"] and all("index" in pv and "price" in pv for pv in p["pivots"])
    assert 0.0 <= p["score"] <= 1.0
