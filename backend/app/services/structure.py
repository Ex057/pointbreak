"""Market-structure classifier — swing-structure first (Dow / price-action).

The engine only computes a 3-way directional bias (bullish/bearish/neutral) and
never reads swing SEQUENCING, so it cannot tell a clean uptrend from a choppy
range. This service reads the HH/HL/LH/LL sequence returned by
PatternEngine.detect_swings() and classifies the chart into one of four states:

    UPTREND        Higher Highs AND Higher Lows
    DOWNTREND      Lower Highs  AND Lower Lows
    RANGE          both boundaries roughly flat — oscillation between two levels
    CONSOLIDATION  Lower Highs AND Higher Lows — a contracting coil

The difference between RANGE and CONSOLIDATION is the one most tools miss: a
range oscillates between two stable horizontal bounds; consolidation *compresses*
toward an apex (energy building toward a breakout). We separate them by whether
the swing envelope is stable or contracting.

Pure functions over data the scan already computed — no engine edit, no network,
no new indicators. Same discipline as scan.py's _retarget / _score_zones.
"""
from __future__ import annotations

import numpy as np

# Slope-confirmation lens, matching the engine's own bias vocabulary.
BULL, BEAR, NEUTRAL = "bullish", "bearish", "neutral"

UPTREND = "UPTREND"
DOWNTREND = "DOWNTREND"
RANGE = "RANGE"
CONSOLIDATION = "CONSOLIDATION"
UNDETERMINED = "UNDETERMINED"


def _slope_to_int(slope_bias) -> int:
    """Accept either the engine's bias string or a raw signed number."""
    if isinstance(slope_bias, str):
        return {BULL: 1, BEAR: -1, NEUTRAL: 0}.get(slope_bias, 0)
    return int(np.sign(slope_bias))


def classify_structure(
    swings: dict,
    closes: np.ndarray,
    atr: float,
    slope_bias,
    *,
    min_swings: int = 3,
    range_tol: float = 0.6,       # ×ATR — how flat a boundary must be to count
    compression_ratio: float = 0.7,  # recent span < ratio × older span => coil
) -> dict:
    """Classify market structure from swing sequence + slope confirmation.

    Returns a JSON-ready dict:
        {state, confidence, reason,
         range_high, range_low,          # set only when RANGE
         swing_summary: {highs, lows}}
    """
    highs = [float(h["price"]) for h in swings.get("highs", [])]
    lows = [float(l["price"]) for l in swings.get("lows", [])]
    atr = max(float(atr), 1e-9)
    slope = _slope_to_int(slope_bias)

    summary = {"highs": highs[-min_swings:], "lows": lows[-min_swings:]}

    if len(highs) < 2 or len(lows) < 2:
        return {
            "state": UNDETERMINED,
            "confidence": 0.0,
            "reason": "Too few confirmed swings to read structure.",
            "range_high": None,
            "range_low": None,
            "swing_summary": summary,
        }

    tol = range_tol * atr
    rh, rl = highs[-min_swings:], lows[-min_swings:]

    # --- swing-sequence reads (primary lens) ---------------------------------
    higher_highs = highs[-1] > highs[-2] + tol
    lower_highs = highs[-1] < highs[-2] - tol
    higher_lows = lows[-1] > lows[-2] + tol
    lower_lows = lows[-1] < lows[-2] - tol

    # "Roughly flat" = every recent same-type swing within tol of the previous.
    flat_highs = all(abs(rh[i] - rh[i - 1]) <= tol for i in range(1, len(rh)))
    flat_lows = all(abs(rl[i] - rl[i - 1]) <= tol for i in range(1, len(rl)))

    range_high = max(rh)
    range_low = min(rl)

    # --- decide ---------------------------------------------------------------
    state = UNDETERMINED
    confidence = 0.4
    reason = ""

    if higher_highs and higher_lows:
        state = UPTREND
        reason = (f"Higher highs ({rh[-2]:.2f}→{rh[-1]:.2f}) and higher lows "
                  f"({rl[-2]:.2f}→{rl[-1]:.2f}).")
        # slope must agree; if it contradicts, we're not really trending
        confidence = 0.85 if slope >= 0 else 0.5
        if slope < 0:
            state = CONSOLIDATION
            reason += " Momentum disagrees — treated as unresolved."
    elif lower_highs and lower_lows:
        state = DOWNTREND
        reason = (f"Lower highs ({rh[-2]:.2f}→{rh[-1]:.2f}) and lower lows "
                  f"({rl[-2]:.2f}→{rl[-1]:.2f}).")
        confidence = 0.85 if slope <= 0 else 0.5
        if slope > 0:
            state = CONSOLIDATION
            reason += " Momentum disagrees — treated as unresolved."
    elif lower_highs and higher_lows:
        # contracting by sequence — a coil
        state = CONSOLIDATION
        reason = (f"Lower highs ({rh[-2]:.2f}→{rh[-1]:.2f}) and higher lows "
                  f"({rl[-2]:.2f}→{rl[-1]:.2f}) — a contracting triangle.")
        confidence = 0.75
    elif flat_highs and flat_lows:
        state = RANGE
        reason = (f"Highs flat near {range_high:.2f}, lows flat near "
                  f"{range_low:.2f} — oscillating between the two.")
        confidence = 0.8
    else:
        # mixed signals: fall back to the envelope test to split range vs coil
        state, confidence, reason = _envelope_fallback(
            rh, rl, compression_ratio, range_high, range_low
        )

    out = {
        "state": state,
        "confidence": round(float(confidence), 2),
        "reason": reason,
        "range_high": None,
        "range_low": None,
        "swing_summary": summary,
    }
    if state == RANGE:
        out["range_high"] = round(range_high, 5)
        out["range_low"] = round(range_low, 5)
    return out


def _envelope_fallback(rh, rl, compression_ratio, range_high, range_low):
    """When the sequence is ambiguous, split RANGE vs CONSOLIDATION by envelope.

    Stable swing envelope -> RANGE; contracting envelope -> CONSOLIDATION.
    Compares the most recent high-low span against the oldest in the window.
    """
    if len(rh) < 2 or len(rl) < 2:
        return RANGE, 0.45, (f"Sideways between {range_low:.2f} and "
                             f"{range_high:.2f}.")
    recent_span = abs(rh[-1] - rl[-1])
    older_span = abs(rh[0] - rl[0])
    if older_span > 0 and recent_span < compression_ratio * older_span:
        return CONSOLIDATION, 0.6, (
            f"Swing envelope contracting ({older_span:.2f}→{recent_span:.2f}) "
            f"— coiling toward a breakout.")
    return RANGE, 0.55, (f"Swing envelope stable — ranging between "
                         f"{range_low:.2f} and {range_high:.2f}.")
