"""Market-structure classifier regression net.

Hand-built swing sequences that unambiguously form each state, so a regression
in the rules is caught immediately.
"""
import numpy as np
import pytest

from app.services.structure import (
    CONSOLIDATION,
    DOWNTREND,
    RANGE,
    UNDETERMINED,
    UPTREND,
    classify_structure,
)


def _swings(highs, lows):
    return {
        "highs": [{"index": i, "price": p} for i, p in enumerate(highs)],
        "lows": [{"index": i, "price": p} for i, p in enumerate(lows)],
    }


ATR = 1.0  # tol = range_tol(0.6) * ATR = 0.6; moves of ~2+ are unambiguous


def test_uptrend_hh_hl_with_agreeing_slope():
    s = _swings(highs=[100, 104, 108], lows=[95, 99, 103])
    r = classify_structure(s, np.array([100, 108.0]), ATR, "bullish")
    assert r["state"] == UPTREND
    assert r["confidence"] >= 0.8
    assert r["reason"]


def test_downtrend_lh_ll_with_agreeing_slope():
    s = _swings(highs=[108, 104, 100], lows=[103, 99, 95])
    r = classify_structure(s, np.array([108, 95.0]), ATR, "bearish")
    assert r["state"] == DOWNTREND
    assert r["confidence"] >= 0.8


def test_range_flat_boundaries():
    # highs hug ~110, lows hug ~100, both flat within tol
    s = _swings(highs=[110, 110.2, 109.8], lows=[100, 100.1, 99.9])
    r = classify_structure(s, np.array([105, 105.0]), ATR, "neutral")
    assert r["state"] == RANGE
    assert r["range_high"] is not None and r["range_low"] is not None
    assert r["range_high"] > r["range_low"]


def test_consolidation_lh_and_hl_coil():
    # lower highs AND higher lows -> contracting triangle
    s = _swings(highs=[112, 108, 104], lows=[92, 96, 100])
    r = classify_structure(s, np.array([102, 102.0]), ATR, "neutral")
    assert r["state"] == CONSOLIDATION


def test_slope_disagreement_downgrades_trend_to_consolidation():
    # swing sequence says uptrend, but momentum says bearish -> unresolved
    s = _swings(highs=[100, 104, 108], lows=[95, 99, 103])
    r = classify_structure(s, np.array([100, 108.0]), ATR, "bearish")
    assert r["state"] == CONSOLIDATION
    assert "disagree" in r["reason"].lower()


def test_envelope_fallback_contracting_is_consolidation():
    # ambiguous sequence (HH but LL), envelope contracts from 30 -> 6
    s = _swings(highs=[100, 118, 108], lows=[70, 92, 102])
    r = classify_structure(s, np.array([104, 104.0]), ATR, "neutral")
    assert r["state"] in {CONSOLIDATION, RANGE}
    # older span |100-70|=30, recent |108-102|=6 -> 6 < 0.7*30 -> coil
    assert r["state"] == CONSOLIDATION


def test_too_few_swings_is_undetermined():
    s = _swings(highs=[100], lows=[95])
    r = classify_structure(s, np.array([100.0]), ATR, "neutral")
    assert r["state"] == UNDETERMINED
    assert r["confidence"] == 0.0


def test_confidence_and_reason_contract():
    s = _swings(highs=[100, 104, 108], lows=[95, 99, 103])
    r = classify_structure(s, np.array([100, 108.0]), ATR, "bullish")
    assert 0.0 <= r["confidence"] <= 1.0
    assert isinstance(r["reason"], str) and r["reason"]
    assert set(r["swing_summary"]) == {"highs", "lows"}


@pytest.mark.parametrize("bias", ["bullish", "bearish", "neutral"])
def test_range_high_low_only_set_for_range(bias):
    s = _swings(highs=[100, 104, 108], lows=[95, 99, 103])
    r = classify_structure(s, np.array([100, 108.0]), ATR, bias)
    if r["state"] != RANGE:
        assert r["range_high"] is None and r["range_low"] is None
