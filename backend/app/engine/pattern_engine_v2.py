"""
================================================================================
PATTERN ENGINE v2 - auto-detection + FUTURE PROJECTION + LAYER TOGGLES
================================================================================
New in v2 (on top of v1):

1. FUTURE PROJECTION  -> engine.project_future(bars_ahead=30)
   * Trendlines are extended into the future (dashed on the chart).
   * All active zones (S/R, demand/supply, fresh FVGs) are projected right.
   * "Imaginary" FUTURE ENTRY POINTS are computed: for each active zone the
     engine estimates WHERE (price) and roughly WHEN (bars ahead, via recent
     average bar travel) price could tap it, which SIDE to trade (long at
     zones below price when bias is up / short at zones above when bias is
     down), and a CONFLUENCE SCORE (zone + FVG + trendline overlapping =
     higher score). Entries come with suggested stop & target.
   * Core idea you asked for: the drawn zones (S/R and FVG especially) ARE
     the most likely next entry points - so entries are generated FROM the
     zones, ranked by confluence, freshness and touch count.

2. LAYER TOGGLES  -> engine.plot(layers={...})
   Available layers (pass any subset - this is your UI toggle):
     "moving_averages" 1. EMA 9/21/50 + SMA 200 overlay lines
     "sr"              2. support & resistance zones
     "supply_demand"   3. demand & supply zones (base -> impulse logic)
     "fvg"             4. fair value gaps (fresh ones projected forward)
     "patterns_high"   5. high-probability candlestick patterns only
                          (strength >= high_prob_threshold OR at a zone)
     "patterns_all"    6. normal patterns & candlesticks (everything)
     "future_entries"  the projected entry markers in the future region
   Default = all layers on.

3. PER-TIMEFRAME  -> PatternEngine(df, timeframe="4H") just labels output;
   run one engine per timeframe DataFrame and merge in your app:
       analyze_multi({"1D": df_daily, "4H": df_4h, "1H": df_1h})

All outputs are plain dict/list/float -> JSON-ready for your app frontend.

Dependencies: numpy, pandas, scipy, matplotlib (only for .plot()).
================================================================================
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.signal import find_peaks

from . import pattern_meta
from . import chart_patterns as _chart_patterns

BULL = "bullish"
BEAR = "bearish"
NEUTRAL = "neutral"

ALL_LAYERS = {
    "moving_averages", "sr", "supply_demand", "fvg",
    "patterns_high", "patterns_all", "future_entries",
}

# Moving averages that replace the old trendline layer. Order matters for the
# frontend legend/colour assignment. Each is (name, period, kind).
MA_CONFIG = [
    ("EMA 9", 9, "ema"),
    ("EMA 21", 21, "ema"),
    ("EMA 50", 50, "ema"),
    ("SMA 200", 200, "sma"),
]

# -----------------------------------------------------------------------------
# Pair-specific pattern availability. Synthetic momentum indices (Volatility,
# Crash) behave in strong impulsive moves, so we surface momentum / strong-body
# reversals. Forex, metals and the stock index get the classic reversal +
# continuation set. A pattern absent from the relevant set is still DETECTED,
# just marked `available: False` so the frontend checklist hides it for that
# instrument. Pure-indecision candles (plain Doji, Spinning Top, High Wave) are
# in neither set — informational only, never on the checklist.
# -----------------------------------------------------------------------------
_MOMENTUM_PATTERNS = frozenset({
    "White Marubozu", "Black Marubozu",
    "Bullish Engulfing", "Bearish Engulfing",
    "Bullish Kicker", "Bearish Kicker",
    "Hammer", "Hanging Man", "Inverted Hammer", "Shooting Star",
    "Morning Star", "Evening Star",
    "Three White Soldiers", "Three Black Crows",
})
_CLASSIC_PATTERNS = frozenset({
    "Bullish Engulfing", "Bearish Engulfing",
    "Bullish Harami", "Bearish Harami",
    "Piercing Line", "Dark Cloud Cover",
    "Tweezers Top", "Tweezers Bottom",
    "Morning Star", "Evening Star",
    "Three White Soldiers", "Three Black Crows",
    "Hammer", "Hanging Man", "Inverted Hammer", "Shooting Star",
    "Dragonfly Doji", "Gravestone Doji",
    "Bullish Kicker", "Bearish Kicker",
    "White Marubozu", "Black Marubozu",
})


def available_patterns(category: str) -> list[str]:
    """The pattern names that make sense to offer for an instrument category.
    Synthetic -> momentum set; everything else (forex/metals/index) -> classic."""
    names = _MOMENTUM_PATTERNS if (category or "").lower() == "synthetic" else _CLASSIC_PATTERNS
    return sorted(names)


def _pattern(i, name, direction, price, strength=1.0, span=1):
    return {"index": int(i), "span": int(span), "name": name,
            "direction": direction, "price": float(price),
            "strength": float(strength), "at_zone": False}


# =============================================================================
class PatternEngine:
    def __init__(
        self,
        df: pd.DataFrame,
        timeframe: str = "",
        pair_category: str = "",
        swing_distance: int = 5,
        swing_prominence_mult: float = 0.8,
        trend_lookback: int = 7,
        zone_merge_atr: float = 0.5,
        atr_period: int = 14,
        high_prob_threshold: float = 0.7,
        impulse_body_mult: float = 1.6,     # supply/demand impulse definition
    ):
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        if not {"open", "high", "low", "close"}.issubset(df.columns):
            raise ValueError("df must contain open, high, low, close")
        self.df = df.reset_index(drop=False)
        self.timeframe = timeframe
        self.pair_category = pair_category
        self.o = df["open"].to_numpy(float)
        self.h = df["high"].to_numpy(float)
        self.l = df["low"].to_numpy(float)
        self.c = df["close"].to_numpy(float)
        self.n = len(df)

        self.swing_distance = swing_distance
        self.swing_prominence_mult = swing_prominence_mult
        self.trend_lookback = trend_lookback
        self.zone_merge_atr = zone_merge_atr
        self.high_prob_threshold = high_prob_threshold
        self.impulse_body_mult = impulse_body_mult

        self.body = np.abs(self.c - self.o)
        self.upper = self.h - np.maximum(self.c, self.o)
        self.lower = np.minimum(self.c, self.o) - self.l
        self.rng = np.where(self.h - self.l == 0, 1e-12, self.h - self.l)
        self.white = self.c > self.o
        self.black = self.c < self.o
        self.atr = self._atr(atr_period)
        self.avg_body = pd.Series(self.body).rolling(atr_period, min_periods=1).mean().to_numpy()

        # Moving averages (replace trendlines): fast EMAs for momentum, EMA 50
        # for mid-trend, SMA 200 for the major trend. min_periods=1 keeps the
        # series defined early; short series simply yield near-price values.
        close_s = pd.Series(self.c)
        self._ma = {}
        for name, period, kind in MA_CONFIG:
            if kind == "ema":
                self._ma[name] = close_s.ewm(span=period, min_periods=1, adjust=False).mean().to_numpy()
            else:
                self._ma[name] = close_s.rolling(period, min_periods=1).mean().to_numpy()

        self._swings = None
        self._results = None

    # ------------------------------------------------------------------ utils
    def _atr(self, period):
        pc = np.roll(self.c, 1); pc[0] = self.c[0]
        tr = np.maximum.reduce([self.h - self.l, np.abs(self.h - pc), np.abs(self.l - pc)])
        return pd.Series(tr).rolling(period, min_periods=1).mean().to_numpy()

    def _trend(self, i):
        j, k = i - 1, i - 1 - self.trend_lookback
        if k < 0:
            return 0
        m = self.c[j] - self.c[k]
        if m > self.atr[j]:
            return 1
        if m < -self.atr[j]:
            return -1
        return 0

    def _long_body(self, i):
        return self.body[i] > 1.2 * max(self.avg_body[i], 1e-12)

    def _short_body(self, i):
        return self.body[i] < 0.5 * max(self.avg_body[i], 1e-12)

    def _is_doji(self, i, tol=0.1):
        return self.body[i] <= tol * self.rng[i]

    # =========================================================================
    #  SWINGS (scipy.signal.find_peaks)
    # =========================================================================
    def detect_swings(self):
        if self._swings is None:
            prom = self.swing_prominence_mult * np.nanmean(self.atr)
            hi, _ = find_peaks(self.h, distance=self.swing_distance, prominence=prom)
            lo, _ = find_peaks(-self.l, distance=self.swing_distance, prominence=prom)
            self._swings = {
                "highs": [{"index": int(i), "price": float(self.h[i])} for i in hi],
                "lows": [{"index": int(i), "price": float(self.l[i])} for i in lo],
            }
        return self._swings

    # =========================================================================
    #  SUPPORT / RESISTANCE ZONES
    # =========================================================================
    def detect_sr_zones(self):
        swings = self.detect_swings()
        tol = self.zone_merge_atr * np.nanmean(self.atr)
        pts = sorted([s["price"] for s in swings["highs"]] +
                     [s["price"] for s in swings["lows"]])
        clusters = []
        for p in pts:
            for cl in clusters:
                if abs(p - np.mean(cl)) <= tol:
                    cl.append(p); break
            else:
                clusters.append([p])
        out = []
        for cl in clusters:
            if len(cl) < 2:
                continue
            mid = float(np.mean(cl))
            out.append({
                "layer": "sr",
                "level_min": float(min(cl)), "level_max": float(max(cl)),
                "level_mid": mid, "touches": len(cl),
                "kind": "support" if mid < self.c[-1] else "resistance",
            })
        return out

    # =========================================================================
    #  DEMAND / SUPPLY ZONES (base -> impulsive departure)
    #  demand = base candles then strong rally away (buyers left orders there)
    #  supply = base candles then strong drop away
    # =========================================================================
    def detect_supply_demand(self, max_base: int = 4):
        zones = []
        i = 1
        while i < self.n:
            if self.body[i] >= self.impulse_body_mult * max(self.avg_body[i], 1e-12):
                # collect the base: small-bodied candles immediately before impulse
                b0 = i - 1
                while b0 > 0 and (i - 1) - b0 < max_base - 1 and self._short_body(b0 - 1):
                    b0 -= 1
                if self._short_body(b0) or self._is_doji(b0):
                    top = float(np.max(self.h[b0:i]))
                    bot = float(np.min(self.l[b0:i]))
                    kind = "demand" if self.white[i] else "supply"
                    # fresh = price hasn't traded back through the zone since
                    later_l = self.l[i + 1:] if i + 1 < self.n else np.array([])
                    later_h = self.h[i + 1:] if i + 1 < self.n else np.array([])
                    if kind == "demand":
                        tested = bool(np.any(later_l <= top))
                        broken = bool(np.any(later_l < bot))
                    else:
                        tested = bool(np.any(later_h >= bot))
                        broken = bool(np.any(later_h > top))
                    zones.append({
                        "layer": "supply_demand",
                        "index": int(b0), "end_index": int(i),
                        "kind": kind, "level_min": bot, "level_max": top,
                        "level_mid": (top + bot) / 2.0,
                        "fresh": not tested, "broken": broken,
                    })
                    i += 1
            i += 1
        return [z for z in zones if not z["broken"]]

    # =========================================================================
    #  MOVING AVERAGES  (replace the old trendlines layer)
    # =========================================================================
    def moving_averages(self):
        """One series per configured MA. Each point is {index, value}; NaNs are
        dropped so the frontend line series starts where the MA is defined."""
        out = []
        for name, period, kind in MA_CONFIG:
            vals = self._ma[name]
            points = [{"index": int(i), "value": float(v)}
                      for i, v in enumerate(vals) if not np.isnan(v)]
            out.append({"layer": "moving_averages", "name": name,
                        "period": int(period), "kind": kind, "points": points})
        return out

    def _ma_bias(self):
        """Trend bias sourced from the moving averages (replaces trendline slope).
        +1 bullish / -1 bearish / 0 neutral, from price vs EMA50/SMA200 and the
        EMA 9/21 cross."""
        if self.n == 0:
            return 0
        px = self.c[-1]
        ema9 = self._ma["EMA 9"][-1]
        ema21 = self._ma["EMA 21"][-1]
        ema50 = self._ma["EMA 50"][-1]
        sma200 = self._ma["SMA 200"][-1]
        score = 0.0
        if not np.isnan(ema50):
            score += 1.0 if px > ema50 else -1.0
        if not (np.isnan(ema9) or np.isnan(ema21)):
            score += 1.0 if ema9 > ema21 else -1.0
        if not np.isnan(sma200):
            score += 0.5 if px > sma200 else -0.5
        return 1 if score > 0 else -1 if score < 0 else 0

    # =========================================================================
    #  FAIR VALUE GAPS
    # =========================================================================
    def detect_fvgs(self, min_gap_atr=0.15):
        out = []
        for i in range(2, self.n):
            g = min_gap_atr * self.atr[i]
            if self.l[i] - self.h[i - 2] > g:                    # bullish FVG
                top, bot = float(self.l[i]), float(self.h[i - 2])
                # fill = the first later bar that trades back down into the gap
                hits = np.where(self.l[i + 1:] <= bot)[0] if i + 1 < self.n else np.array([], int)
                fill_index = int(i + 1 + hits[0]) if hits.size else None
                out.append({"layer": "fvg", "index": int(i - 1),
                            "start_index": int(i - 2), "end_index": int(i),
                            "fill_index": fill_index, "kind": BULL,
                            "top": top, "bottom": bot,
                            "level_mid": (top + bot) / 2, "filled": fill_index is not None})
            elif self.l[i - 2] - self.h[i] > g:                  # bearish FVG
                top, bot = float(self.l[i - 2]), float(self.h[i])
                hits = np.where(self.h[i + 1:] >= top)[0] if i + 1 < self.n else np.array([], int)
                fill_index = int(i + 1 + hits[0]) if hits.size else None
                out.append({"layer": "fvg", "index": int(i - 1),
                            "start_index": int(i - 2), "end_index": int(i),
                            "fill_index": fill_index, "kind": BEAR,
                            "top": top, "bottom": bot,
                            "level_mid": (top + bot) / 2, "filled": fill_index is not None})
        return out

    # =========================================================================
    #  CANDLESTICK PATTERNS (same detectors as v1, condensed)
    # =========================================================================
    def detect_patterns(self):
        p = []
        o, h, l, c = self.o, self.h, self.l, self.c
        body, up, lo, rng = self.body, self.upper, self.lower, self.rng

        for i in range(self.n):
            t = self._trend(i)

            if self._is_doji(i):
                if lo[i] > 2 * up[i] and lo[i] > 0.6 * rng[i]:
                    p.append(_pattern(i, "Dragonfly Doji", BULL if t < 0 else NEUTRAL, l[i], 0.6))
                elif up[i] > 2 * lo[i] and up[i] > 0.6 * rng[i]:
                    p.append(_pattern(i, "Gravestone Doji", BEAR if t > 0 else NEUTRAL, h[i], 0.6))
                elif up[i] > 0.3 * rng[i] and lo[i] > 0.3 * rng[i]:
                    p.append(_pattern(i, "Long-Legged Doji", NEUTRAL, c[i], 0.4))
                else:
                    p.append(_pattern(i, "Doji", NEUTRAL, c[i], 0.3))

            if body[i] > 0 and lo[i] >= 2 * body[i] and up[i] <= 0.25 * body[i]:
                if t < 0:
                    p.append(_pattern(i, "Hammer", BULL, l[i], 0.8 if self.white[i] else 0.65))
                elif t > 0:
                    p.append(_pattern(i, "Hanging Man", BEAR, h[i], 0.8 if self.black[i] else 0.65))

            if body[i] > 0 and up[i] >= 2 * body[i] and lo[i] <= 0.25 * body[i]:
                if t < 0:
                    p.append(_pattern(i, "Inverted Hammer", BULL, l[i], 0.55))
                elif t > 0:
                    p.append(_pattern(i, "Shooting Star", BEAR, h[i], 0.8))

            if self._long_body(i) and up[i] < 0.05 * rng[i] and lo[i] < 0.05 * rng[i]:
                d = BULL if self.white[i] else BEAR
                p.append(_pattern(i, f"{'White' if self.white[i] else 'Black'} Marubozu", d, c[i], 0.7))

            if self._short_body(i) and not self._is_doji(i) and up[i] > 1.5 * body[i] and lo[i] > 1.5 * body[i]:
                name = "High Wave" if (up[i] + lo[i]) > 2.5 * self.atr[i] else "Spinning Top"
                p.append(_pattern(i, name, NEUTRAL, c[i], 0.3))

            if i >= 1:
                mid_prev = (o[i - 1] + c[i - 1]) / 2.0
                if (t < 0 and self.black[i - 1] and self.white[i]
                        and o[i] <= c[i - 1] and c[i] >= o[i - 1] and body[i] > body[i - 1]):
                    p.append(_pattern(i, "Bullish Engulfing", BULL, l[i], 0.85, 2))
                if (t > 0 and self.white[i - 1] and self.black[i]
                        and o[i] >= c[i - 1] and c[i] <= o[i - 1] and body[i] > body[i - 1]):
                    p.append(_pattern(i, "Bearish Engulfing", BEAR, h[i], 0.85, 2))
                if (t < 0 and self.black[i - 1] and self.white[i]
                        and o[i] < l[i - 1] and mid_prev < c[i] < o[i - 1]):
                    p.append(_pattern(i, "Piercing Line", BULL, l[i], 0.75, 2))
                if (t > 0 and self.white[i - 1] and self.black[i]
                        and o[i] > h[i - 1] and o[i - 1] < c[i] < mid_prev):
                    p.append(_pattern(i, "Dark Cloud Cover", BEAR, h[i], 0.75, 2))
                if (self._long_body(i - 1) and self._short_body(i)
                        and max(o[i], c[i]) < max(o[i - 1], c[i - 1])
                        and min(o[i], c[i]) > min(o[i - 1], c[i - 1])):
                    if t < 0 and self.black[i - 1] and self.white[i]:
                        p.append(_pattern(i, "Bullish Harami", BULL, l[i], 0.6, 2))
                    elif t > 0 and self.white[i - 1] and self.black[i]:
                        p.append(_pattern(i, "Bearish Harami", BEAR, h[i], 0.6, 2))
                tol = 0.1 * self.atr[i]
                if t > 0 and abs(h[i] - h[i - 1]) <= tol:
                    p.append(_pattern(i, "Tweezers Top", BEAR, h[i], 0.55, 2))
                if t < 0 and abs(l[i] - l[i - 1]) <= tol:
                    p.append(_pattern(i, "Tweezers Bottom", BULL, l[i], 0.55, 2))
                if self.black[i - 1] and self.white[i] and o[i] > h[i - 1]:
                    p.append(_pattern(i, "Bullish Kicker", BULL, l[i], 0.9, 2))
                if self.white[i - 1] and self.black[i] and o[i] < l[i - 1]:
                    p.append(_pattern(i, "Bearish Kicker", BEAR, h[i], 0.9, 2))

            if i >= 2:
                if (self._trend(i - 1) < 0 and self.black[i - 2] and self._long_body(i - 2)
                        and self._short_body(i - 1) and self.white[i]
                        and c[i] > (o[i - 2] + c[i - 2]) / 2.0):
                    p.append(_pattern(i, "Morning Star", BULL, l[i - 1], 0.85, 3))
                if (self._trend(i - 1) > 0 and self.white[i - 2] and self._long_body(i - 2)
                        and self._short_body(i - 1) and self.black[i]
                        and c[i] < (o[i - 2] + c[i - 2]) / 2.0):
                    p.append(_pattern(i, "Evening Star", BEAR, h[i - 1], 0.85, 3))
                if (all(self.white[i - k] for k in range(3))
                        and c[i] > c[i - 1] > c[i - 2] and o[i] > o[i - 1] > o[i - 2]
                        and all(not self._short_body(i - k) for k in range(3))
                        and all(self.upper[i - k] <= 0.25 * self.body[i - k] + 1e-12 for k in range(3))):
                    p.append(_pattern(i, "Three White Soldiers", BULL, l[i - 2], 0.8, 3))
                if (all(self.black[i - k] for k in range(3))
                        and c[i] < c[i - 1] < c[i - 2] and o[i] < o[i - 1] < o[i - 2]
                        and all(not self._short_body(i - k) for k in range(3))):
                    p.append(_pattern(i, "Three Black Crows", BEAR, h[i - 2], 0.8, 3))
        return p

    # -------------------------------------------------------------------------
    #  Tag patterns that printed AT a zone -> upgrades them to high-probability
    # -------------------------------------------------------------------------
    def _tag_patterns_at_zones(self, patterns, sr, sd, fvgs):
        zones = []
        for z in sr:
            zones.append((z["level_min"], z["level_max"]))
        for z in sd:
            zones.append((z["level_min"], z["level_max"]))
        for g in fvgs:
            zones.append((g["bottom"], g["top"]))
        pad = 0.25 * np.nanmean(self.atr)
        for pat in patterns:
            i = pat["index"]
            for zmin, zmax in zones:
                if self.l[i] <= zmax + pad and self.h[i] >= zmin - pad:
                    pat["at_zone"] = True
                    pat["strength"] = min(1.0, pat["strength"] + 0.15)
                    break
        return patterns

    # -------------------------------------------------------------------------
    #  Confirmation + pair availability
    #  A pattern is CONFIRMED only after its full candle group has closed AND the
    #  next candle closes in the pattern's direction (follow-through). The last
    #  bar (index n-1) is treated as the still-forming bar, so its own patterns —
    #  and any whose confirmation candle would be that forming bar — stay pending.
    # -------------------------------------------------------------------------
    def _finalize_patterns(self, patterns):
        last_closed = self.n - 2      # n-1 is the live/forming bar
        avail = set(available_patterns(self.pair_category))
        for pat in patterns:
            i = pat["index"]
            span = pat.get("span", 1)
            pat["start_index"] = int(i - span + 1)
            pat["end_index"] = int(i)
            pat["available"] = pat["name"] in avail
            pattern_meta.stamp(pat)

            ci = i + 1                # follow-through / confirmation candle
            if i > last_closed or ci > last_closed:
                # pattern completes on (or is confirmed by) the forming bar
                pat["confirmed"] = False
                pat["status"] = "pending"
                continue
            d = pat["direction"]
            if d == BULL:
                ok = self.c[ci] > self.c[i]
            elif d == BEAR:
                ok = self.c[ci] < self.c[i]
            else:  # neutral/doji: confirmation = a decisive break of its range
                ok = self.c[ci] > self.h[i] or self.c[ci] < self.l[i]
            pat["confirmed"] = bool(ok)
            pat["status"] = "confirmed" if ok else "pending"
        return patterns

    # =========================================================================
    #  FUTURE PROJECTION  ->  imaginary entry points in the future region
    # =========================================================================
    def project_future(self, bars_ahead: int = 30, max_entries: int = 6):
        """
        Returns
        -------
        {
          "bars_ahead": int,
          "bias": "bullish"/"bearish"/"neutral",   (from moving averages + trend)
          "entries": [
             {
               "side": "long"/"short",
               "source": "sr"/"supply_demand"/"fvg",
               "zone_min", "zone_max", "entry_price",
               "eta_bars",        # rough bars until price could reach zone
               "x_future",        # plotting x  (last_index + eta_bars)
               "confluence",      # how many layers overlap this price area
               "score",           # ranking 0..1
               "stop", "target",  # suggested (structure-based, R:R ~ auto)
               "note"
             }, ...
          ]
        }
        Logic: zones already drawn (S/R, demand/supply, fresh FVG, trendline
        value in the future) are the candidate NEXT entry points. We rank them
        by confluence + touches + freshness + distance, and estimate arrival
        time from the average absolute bar-to-bar close change.
        """
        sr = self.detect_sr_zones()
        sd = self.detect_supply_demand()
        fvgs = [g for g in self.detect_fvgs() if not g["filled"]]

        last_i = self.n - 1
        px = self.c[-1]
        atr = float(np.nanmean(self.atr[-20:]))
        # average bar travel -> ETA estimator
        travel = float(np.nanmean(np.abs(np.diff(self.c[-30:])))) or atr

        # ---- overall bias (now sourced from moving averages) -----------------
        t = self._trend(self.n)
        ma_bias = self._ma_bias()
        bias_score = t + ma_bias
        bias = BULL if bias_score > 0 else BEAR if bias_score < 0 else NEUTRAL

        # ---- collect candidate zones -----------------------------------------
        candidates = []
        for z in sr:
            candidates.append({"source": "sr", "zmin": z["level_min"],
                               "zmax": z["level_max"], "mid": z["level_mid"],
                               "quality": min(1.0, 0.3 + 0.15 * z["touches"])})
        for z in sd:
            q = 0.7 if z["fresh"] else 0.45
            candidates.append({"source": "supply_demand", "zmin": z["level_min"],
                               "zmax": z["level_max"], "mid": z["level_mid"],
                               "quality": q, "kind": z["kind"]})
        for g in fvgs:
            candidates.append({"source": "fvg", "zmin": g["bottom"],
                               "zmax": g["top"], "mid": g["level_mid"],
                               "quality": 0.6, "kind": g["kind"]})

        # ---- confluence: overlap counting -------------------------------------
        def overlaps(a, b, pad=0.25 * atr):
            return a["zmin"] <= b["zmax"] + pad and b["zmin"] <= a["zmax"] + pad

        # a moving average sitting inside a zone is a confluence factor (a level
        # defended by a dynamic MA), replacing the old trendline-through-zone test
        ma_levels = [self._ma[name][-1] for name, _, _ in MA_CONFIG
                     if not np.isnan(self._ma[name][-1])]
        for cnd in candidates:
            cnd["confluence"] = 1
            for other in candidates:
                if other is not cnd and overlaps(cnd, other):
                    cnd["confluence"] += 1
            for y in ma_levels:
                if cnd["zmin"] - 0.25 * atr <= y <= cnd["zmax"] + 0.25 * atr:
                    cnd["confluence"] += 1
                    break

        # ---- turn zones into future entries -----------------------------------
        entries = []
        for cnd in candidates:
            dist = cnd["mid"] - px
            side = None
            if dist < -0.1 * atr:                       # zone below price
                if cnd["source"] == "supply_demand" and cnd.get("kind") == "supply":
                    continue                            # supply below price = broken idea
                side = "long"                           # buy the dip into it
            elif dist > 0.1 * atr:                      # zone above price
                if cnd["source"] == "supply_demand" and cnd.get("kind") == "demand":
                    continue
                side = "short"                          # sell the rally into it
            else:
                continue

            # bias filter: with-trend entries score higher; counter-trend kept
            # but penalized (user may still want them displayed)
            with_bias = (side == "long" and bias == BULL) or (side == "short" and bias == BEAR)

            eta = int(np.clip(abs(dist) / max(travel, 1e-9), 1, bars_ahead))
            entry_price = cnd["zmax"] if side == "long" else cnd["zmin"]  # front of zone
            zone_h = cnd["zmax"] - cnd["zmin"]
            stop = cnd["zmin"] - 0.5 * atr if side == "long" else cnd["zmax"] + 0.5 * atr
            risk = abs(entry_price - stop)
            target = entry_price + 2 * risk if side == "long" else entry_price - 2 * risk

            score = (0.35 * min(cnd["confluence"], 4) / 4.0
                     + 0.35 * cnd["quality"]
                     + 0.20 * (1.0 if with_bias else 0.35)
                     + 0.10 * max(0.0, 1.0 - abs(dist) / (10 * atr)))

            entries.append({
                "side": side, "source": cnd["source"],
                "zone_min": float(cnd["zmin"]), "zone_max": float(cnd["zmax"]),
                "entry_price": float(entry_price),
                "eta_bars": eta, "x_future": int(last_i + eta),
                "confluence": int(cnd["confluence"]),
                "with_bias": bool(with_bias),
                "score": round(float(score), 3),
                "stop": float(stop), "target": float(target),
                "note": f"{cnd['source']} zone, confluence x{cnd['confluence']}, "
                        f"{'with' if with_bias else 'against'} bias",
            })

        entries.sort(key=lambda e: e["score"], reverse=True)
        # de-duplicate overlapping entries (keep best per price area)
        pruned = []
        for e in entries:
            if all(not (e["zone_min"] <= q["zone_max"] and q["zone_min"] <= e["zone_max"])
                   for q in pruned):
                pruned.append(e)
        return {"timeframe": self.timeframe, "bars_ahead": bars_ahead,
                "bias": bias, "entries": pruned[:max_entries]}

    # =========================================================================
    #  MASTER ANALYZE
    # =========================================================================
    def analyze(self, bars_ahead: int = 30):
        sr = self.detect_sr_zones()
        sd = self.detect_supply_demand()
        fvgs = self.detect_fvgs()
        patterns = self._finalize_patterns(
            self._tag_patterns_at_zones(self.detect_patterns(), sr, sd, fvgs))
        hp = [p for p in patterns
              if p["strength"] >= self.high_prob_threshold or p["at_zone"]]
        self._results = {
            "timeframe": self.timeframe,
            "swings": self.detect_swings(),
            "sr_zones": sr,
            "supply_demand": sd,
            "fvgs": fvgs,
            "moving_averages": self.moving_averages(),
            "patterns_all": patterns,
            "patterns_high": hp,
            "chart_patterns": _chart_patterns.detect_chart_patterns(self),
            "future": self.project_future(bars_ahead),
        }
        return self._results

    # =========================================================================
    #  PLOT with layer toggles + future region
    # =========================================================================
    def plot(self, path=None, layers=None, last_n=None, bars_ahead=30,
             figsize=(17, 9), show=False):
        """
        layers : set/list of layer names to draw (your UI toggle).
                 Any subset of:
                 {"trendlines","sr","supply_demand","fvg",
                  "patterns_high","patterns_all","future_entries"}
                 None -> all layers.
        """
        import matplotlib
        if path and not show:
            matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle

        layers = ALL_LAYERS if layers is None else set(layers)
        res = self._results or self.analyze(bars_ahead)
        fut = res["future"]
        start = 0 if last_n is None else max(0, self.n - last_n)
        end_x = self.n - 1 + bars_ahead
        atr = float(np.nanmean(self.atr))

        fig, ax = plt.subplots(figsize=figsize)
        w = 0.6

        # ---- candles ---------------------------------------------------------
        for i in range(start, self.n):
            col = "#26a69a" if self.white[i] else ("#ef5350" if self.black[i] else "#888")
            ax.vlines(i, self.l[i], self.h[i], color=col, lw=0.8, zorder=2)
            ax.add_patch(Rectangle((i - w / 2, min(self.o[i], self.c[i])), w,
                                   max(self.body[i], 1e-9),
                                   facecolor=col, edgecolor=col, zorder=3))

        # ---- future region shading --------------------------------------------
        ax.axvspan(self.n - 0.5, end_x + 0.5, color="#9e9e9e", alpha=0.08, zorder=0)
        ax.axvline(self.n - 0.5, color="#9e9e9e", ls=":", lw=1)
        ax.text(self.n + 0.5, ax.get_ylim()[1], "", fontsize=8)

        # ---- S/R zones (projected across the future) --------------------------
        if "sr" in layers:
            for z in res["sr_zones"]:
                col = "#2962ff" if z["kind"] == "support" else "#ff6d00"
                ax.add_patch(Rectangle((start - 1, z["level_min"]),
                                       end_x - start + 2,
                                       z["level_max"] - z["level_min"] or 0.05 * atr,
                                       facecolor=col, alpha=0.08, zorder=1))
                ax.hlines(z["level_mid"], start - 1, end_x + 1, color=col,
                          alpha=0.45, lw=1, ls="--")
                ax.text(start, z["level_mid"], f' {z["kind"]} x{z["touches"]}',
                        va="bottom", fontsize=8, color=col)

        # ---- demand / supply zones --------------------------------------------
        if "supply_demand" in layers:
            for z in res["supply_demand"]:
                col = "#00bfa5" if z["kind"] == "demand" else "#ff1744"
                x0 = z["index"] - 0.5
                ax.add_patch(Rectangle((x0, z["level_min"]), end_x - x0 + 0.5,
                                       z["level_max"] - z["level_min"],
                                       facecolor=col, alpha=0.13, zorder=1,
                                       edgecolor=col, lw=0.6))
                tag = f'{z["kind"]}{" *fresh" if z["fresh"] else ""}'
                ax.text(x0 + 0.5, z["level_max"], tag, fontsize=7, color=col, va="bottom")

        # ---- FVGs (fresh ones projected forward) ------------------------------
        if "fvg" in layers:
            for g in res["fvgs"]:
                if g["filled"]:
                    continue
                col = "#26a69a" if g["kind"] == BULL else "#ef5350"
                ax.add_patch(Rectangle((g["index"] - 0.5, g["bottom"]),
                                       end_x - g["index"] + 1, g["top"] - g["bottom"],
                                       facecolor=col, alpha=0.15, zorder=1))
                ax.text(g["index"], (g["top"] + g["bottom"]) / 2, "FVG",
                        fontsize=7, color=col, va="center")

        # ---- moving averages --------------------------------------------------
        if "moving_averages" in layers:
            ma_colors = {"EMA 9": "#42a5f5", "EMA 21": "#ab47bc",
                         "EMA 50": "#ffa726", "SMA 200": "#26a69a"}
            for ser in res["moving_averages"]:
                xs = [p["index"] for p in ser["points"]]
                ys = [p["value"] for p in ser["points"]]
                ax.plot(xs, ys, color=ma_colors.get(ser["name"], "#888"),
                        lw=1.2, zorder=4, label=ser["name"])

        # ---- pattern markers ---------------------------------------------------
        pat_layer = ("patterns_all" if "patterns_all" in layers
                     else "patterns_high" if "patterns_high" in layers else None)
        if pat_layer:
            pad = 0.4 * atr
            for pat in res[pat_layer]:
                i = pat["index"]
                if i < start:
                    continue
                if pat_layer == "patterns_all" and pat["strength"] < 0.4:
                    continue
                star = "*" if pat["at_zone"] else ""
                if pat["direction"] == BULL:
                    ax.annotate(pat["name"] + star, (i, self.l[i] - pad),
                                xytext=(i, self.l[i] - 3 * pad), ha="center",
                                fontsize=7, color="green", rotation=45,
                                arrowprops=dict(arrowstyle="->", color="green", lw=0.8))
                elif pat["direction"] == BEAR:
                    ax.annotate(pat["name"] + star, (i, self.h[i] + pad),
                                xytext=(i, self.h[i] + 3 * pad), ha="center",
                                fontsize=7, color="red", rotation=45,
                                arrowprops=dict(arrowstyle="->", color="red", lw=0.8))

        # ---- FUTURE ENTRY MARKERS ---------------------------------------------
        if "future_entries" in layers:
            for e in fut["entries"]:
                col = "#1b5e20" if e["side"] == "long" else "#b71c1c"
                mk = "^" if e["side"] == "long" else "v"
                ax.scatter([e["x_future"]], [e["entry_price"]], marker=mk,
                           s=140, color=col, edgecolor="white", zorder=6)
                ax.hlines(e["entry_price"], self.n - 0.5, e["x_future"],
                          color=col, lw=1, ls=":", alpha=0.7)
                ax.hlines(e["stop"], e["x_future"] - 1.5, e["x_future"] + 1.5,
                          color="#616161", lw=1.2)
                ax.hlines(e["target"], e["x_future"] - 1.5, e["x_future"] + 1.5,
                          color=col, lw=1.2, ls="--")
                ax.annotate(f'{e["side"].upper()} {e["source"]}\n'
                            f'~{e["eta_bars"]} bars | cfl x{e["confluence"]} | s={e["score"]}',
                            (e["x_future"], e["entry_price"]),
                            xytext=(e["x_future"] + 1, e["entry_price"]),
                            fontsize=7, color=col, va="center")

        seg_lo = min(self.l[start:].min(),
                     min([e["stop"] for e in fut["entries"]] or [self.l[start:].min()]))
        seg_hi = max(self.h[start:].max(),
                     max([e["target"] for e in fut["entries"]] or [self.h[start:].max()]))
        m = 0.08 * (seg_hi - seg_lo)
        ax.set_xlim(start - 1, end_x + 6)
        ax.set_ylim(seg_lo - m, seg_hi + m)
        tf = f" [{self.timeframe}]" if self.timeframe else ""
        on = ", ".join(sorted(layers))
        ax.set_title(f"PatternEngine v2{tf} - bias: {fut['bias']} | layers: {on}", fontsize=10)
        ax.grid(alpha=0.2)
        if "moving_averages" in layers and res["moving_averages"]:
            ax.legend(loc="upper left", fontsize=8)
        fig.tight_layout()
        if path:
            fig.savefig(path, dpi=130)
        if show:
            plt.show()
        plt.close(fig)
        return path


# =============================================================================
#  Multi-timeframe helper
# =============================================================================
def analyze_multi(dfs: dict, bars_ahead: int = 30, **kwargs):
    """
    dfs = {"1D": df_daily, "4H": df_4h, ...}
    Returns {tf: results-dict}. Each results dict contains its own
    future-entry list, so your app can show entries PER TIMEFRAME.
    """
    out = {}
    for tf, df in dfs.items():
        eng = PatternEngine(df, timeframe=tf, **kwargs)
        out[tf] = eng.analyze(bars_ahead=bars_ahead)
    return out


# =============================================================================
#  Demo
# =============================================================================
if __name__ == "__main__":
    rng = np.random.default_rng(7)
    n = 220
    drift = np.concatenate([np.full(60, -0.35), np.full(50, 0.05),
                            np.full(70, 0.45), np.full(40, -0.15)])
    steps = drift + rng.normal(0, 1.0, n)
    close = 100 + np.cumsum(steps)
    open_ = close - steps + rng.normal(0, 0.15, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0.6, 0.5, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0.6, 0.5, n))
    open_[130] += 3.0; close[130] += 3.5; high[130] += 3.8; low[130] += 2.8
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close})

    eng = PatternEngine(df, timeframe="1D")
    res = eng.analyze(bars_ahead=30)

    print("bias:", res["future"]["bias"])
    print("future entries:")
    for e in res["future"]["entries"]:
        print(f'  {e["side"]:5s} {e["source"]:14s} @ {e["entry_price"]:.2f} '
              f'eta~{e["eta_bars"]:>2d} bars  cfl x{e["confluence"]}  '
              f'score {e["score"]}  stop {e["stop"]:.2f}  tgt {e["target"]:.2f}')

    # 1) everything on
    eng.plot("v2_all_layers.png", bars_ahead=30)
    # 2) toggle example: only S/R + FVG + future entries
    eng.plot("v2_sr_fvg_entries.png",
             layers={"sr", "fvg", "future_entries"}, bars_ahead=30)
    # 3) toggle example: supply/demand + high-probability patterns only
    eng.plot("v2_sd_highprob.png",
             layers={"supply_demand", "patterns_high", "future_entries"},
             bars_ahead=30)
    print("charts saved.")
