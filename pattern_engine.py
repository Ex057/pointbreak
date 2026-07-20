"""
================================================================================
PATTERN ENGINE - Candlestick / S&R / Trendline / FVG auto-detection & plotting
================================================================================
Drop-in module for your trading app.

Core ideas
----------
* scipy.signal.find_peaks locates swing highs / swing lows.
* Swings are clustered into horizontal SUPPORT / RESISTANCE zones.
* Trendlines are fitted (least squares) through recent swing highs (down line)
  and swing lows (up line), then validated by touch count.
* Fair Value Gaps (FVG) use the classic 3-candle non-overlap definition.
* Candlestick patterns follow the validity rules from the reference book:
  prior-trend requirement, shadow-to-body ratios, midpoint penetration, etc.

Usage
-----
    import pandas as pd
    from pattern_engine import PatternEngine

    df = pd.DataFrame(...)   # columns: open, high, low, close (volume optional)
                             # index: DatetimeIndex or RangeIndex

    engine  = PatternEngine(df)
    results = engine.analyze()          # dict of everything detected
    engine.plot("chart.png")            # auto-plots candles + all overlays

    results["patterns"]     -> list of {index, name, direction, price, strength}
    results["sr_zones"]     -> list of {level_min, level_max, touches, kind}
    results["trendlines"]   -> list of {kind, x0, y0, x1, y1, slope, touches}
    results["fvgs"]         -> list of {index, kind, top, bottom, filled}
    results["swings"]       -> {"highs": [...], "lows": [...]}

Everything returned is plain python (ints/floats/str) so it serializes to
JSON directly for a web front-end / charting library (e.g. lightweight-charts,
plotly, TradingView widget markers).

Dependencies: numpy, pandas, scipy, matplotlib (matplotlib only for .plot()).
================================================================================
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.signal import find_peaks

# ------------------------------------------------------------------ constants
BULL = "bullish"
BEAR = "bearish"
NEUTRAL = "neutral"


# =============================================================================
#  Helper dataclass-ish dict builders (kept as dicts for easy JSON export)
# =============================================================================
def _pattern(i, name, direction, price, strength=1.0, span=1):
    return {
        "index": int(i),              # integer bar index of the LAST candle
        "span": int(span),            # how many candles the pattern covers
        "name": name,
        "direction": direction,       # bullish / bearish / neutral
        "price": float(price),        # anchor price for plotting the marker
        "strength": float(strength),  # 0..1 heuristic confidence
    }


# =============================================================================
#  Main engine
# =============================================================================
class PatternEngine:
    """
    Parameters
    ----------
    df : pd.DataFrame
        Must contain columns open, high, low, close (case-insensitive).
    swing_distance : int
        Minimum bars between swing points (find_peaks `distance`).
    swing_prominence_mult : float
        Prominence = mult * ATR. Larger = fewer, more significant swings.
    trend_lookback : int
        Bars used to define the "prior trend" for pattern validity.
    zone_merge_atr : float
        Swings closer than this many ATRs are merged into one S/R zone.
    """

    def __init__(
        self,
        df: pd.DataFrame,
        swing_distance: int = 5,
        swing_prominence_mult: float = 0.8,
        trend_lookback: int = 7,
        zone_merge_atr: float = 0.5,
        atr_period: int = 14,
    ):
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        required = {"open", "high", "low", "close"}
        if not required.issubset(df.columns):
            raise ValueError(f"df must contain columns {required}")
        self.df = df.reset_index(drop=False)
        self.o = df["open"].to_numpy(float)
        self.h = df["high"].to_numpy(float)
        self.l = df["low"].to_numpy(float)
        self.c = df["close"].to_numpy(float)
        self.n = len(df)

        self.swing_distance = swing_distance
        self.swing_prominence_mult = swing_prominence_mult
        self.trend_lookback = trend_lookback
        self.zone_merge_atr = zone_merge_atr
        self.atr_period = atr_period

        # ---- derived series -------------------------------------------------
        self.body = np.abs(self.c - self.o)
        self.upper = self.h - np.maximum(self.c, self.o)
        self.lower = np.minimum(self.c, self.o) - self.l
        self.rng = self.h - self.l
        self.rng[self.rng == 0] = 1e-12          # avoid div-by-zero
        self.white = self.c > self.o             # bullish body
        self.black = self.c < self.o             # bearish body
        self.atr = self._atr(atr_period)
        self.avg_body = pd.Series(self.body).rolling(atr_period).mean().to_numpy()

        # results cache
        self._swings = None
        self._results = None

    # ------------------------------------------------------------------ utils
    def _atr(self, period):
        prev_c = np.roll(self.c, 1)
        prev_c[0] = self.c[0]
        tr = np.maximum.reduce(
            [self.h - self.l, np.abs(self.h - prev_c), np.abs(self.l - prev_c)]
        )
        return pd.Series(tr).rolling(period, min_periods=1).mean().to_numpy()

    def _trend(self, i):
        """
        Prior-trend filter (the book's 'must be in a definite up/downtrend').
        Simple & fast: compare close[i-1] with close[i-1-lookback] scaled by ATR.
        Returns +1 uptrend, -1 downtrend, 0 sideways.
        """
        lb = self.trend_lookback
        j = i - 1
        k = j - lb
        if k < 0:
            return 0
        move = self.c[j] - self.c[k]
        if move > 1.0 * self.atr[j]:
            return 1
        if move < -1.0 * self.atr[j]:
            return -1
        return 0

    def _long_body(self, i):
        return self.body[i] > 1.2 * (self.avg_body[i] if self.avg_body[i] > 0 else self.body[i])

    def _short_body(self, i):
        return self.body[i] < 0.5 * (self.avg_body[i] if self.avg_body[i] > 0 else self.body[i])

    def _is_doji(self, i, tol=0.1):
        return self.body[i] <= tol * self.rng[i]

    # =========================================================================
    #  1. SWING DETECTION  (scipy.signal.find_peaks)
    # =========================================================================
    def detect_swings(self):
        if self._swings is not None:
            return self._swings

        prom = self.swing_prominence_mult * np.nanmean(self.atr)
        peak_idx, _ = find_peaks(self.h, distance=self.swing_distance, prominence=prom)
        trough_idx, _ = find_peaks(-self.l, distance=self.swing_distance, prominence=prom)

        self._swings = {
            "highs": [{"index": int(i), "price": float(self.h[i])} for i in peak_idx],
            "lows": [{"index": int(i), "price": float(self.l[i])} for i in trough_idx],
        }
        return self._swings

    # =========================================================================
    #  2. SUPPORT / RESISTANCE ZONES (cluster the swing prices)
    # =========================================================================
    def detect_sr_zones(self):
        swings = self.detect_swings()
        atr = np.nanmean(self.atr)
        tol = self.zone_merge_atr * atr

        points = (
            [(s["price"], "resistance") for s in swings["highs"]]
            + [(s["price"], "support") for s in swings["lows"]]
        )
        points.sort(key=lambda p: p[0])

        zones = []
        for price, kind in points:
            placed = False
            for z in zones:
                if abs(price - z["_center"]) <= tol:
                    z["prices"].append(price)
                    z["kinds"].append(kind)
                    z["_center"] = float(np.mean(z["prices"]))
                    placed = True
                    break
            if not placed:
                zones.append({"prices": [price], "kinds": [kind], "_center": price})

        out = []
        last_close = self.c[-1]
        for z in zones:
            if len(z["prices"]) < 2:          # need >= 2 touches to be a zone
                continue
            lo, hi = min(z["prices"]), max(z["prices"])
            kind = "support" if z["_center"] < last_close else "resistance"
            out.append(
                {
                    "level_min": float(lo),
                    "level_max": float(hi),
                    "level_mid": float(z["_center"]),
                    "touches": int(len(z["prices"])),
                    "kind": kind,
                }
            )
        return out

    # =========================================================================
    #  3. TRENDLINES (least-squares fit through recent swing highs / lows)
    # =========================================================================
    def detect_trendlines(self, max_points: int = 4, min_touch: int = 2):
        swings = self.detect_swings()
        atr = np.nanmean(self.atr)
        lines = []

        def fit(points, kind):
            if len(points) < min_touch:
                return
            pts = points[-max_points:]                     # most recent swings
            xs = np.array([p["index"] for p in pts], float)
            ys = np.array([p["price"] for p in pts], float)
            slope, intercept = np.polyfit(xs, ys, 1)
            # count touches: swings within 0.5 ATR of the line
            touches = int(np.sum(np.abs(ys - (slope * xs + intercept)) < 0.5 * atr))
            if touches < min_touch:
                return
            x0, x1 = int(xs[0]), self.n - 1
            lines.append(
                {
                    "kind": kind,                          # "up" (support) / "down" (resistance)
                    "x0": x0,
                    "y0": float(slope * x0 + intercept),
                    "x1": int(x1),
                    "y1": float(slope * x1 + intercept),
                    "slope": float(slope),
                    "touches": touches,
                }
            )

        # up trendline through swing LOWS, down trendline through swing HIGHS
        fit(swings["lows"], "up")
        fit(swings["highs"], "down")
        return lines

    # =========================================================================
    #  4. FAIR VALUE GAPS (3-candle non-overlap)
    # =========================================================================
    def detect_fvgs(self, min_gap_atr: float = 0.15):
        fvgs = []
        for i in range(2, self.n):
            gap_min = min_gap_atr * self.atr[i]
            # bullish FVG: candle[i-2].high < candle[i].low
            if self.l[i] - self.h[i - 2] > gap_min:
                top, bottom = self.l[i], self.h[i - 2]
                filled = bool(np.any(self.l[i + 1 :] <= bottom)) if i + 1 < self.n else False
                fvgs.append(
                    {"index": int(i - 1), "kind": BULL, "top": float(top),
                     "bottom": float(bottom), "filled": filled}
                )
            # bearish FVG: candle[i-2].low > candle[i].high
            elif self.l[i - 2] - self.h[i] > gap_min:
                top, bottom = self.l[i - 2], self.h[i]
                filled = bool(np.any(self.h[i + 1 :] >= top)) if i + 1 < self.n else False
                fvgs.append(
                    {"index": int(i - 1), "kind": BEAR, "top": float(top),
                     "bottom": float(bottom), "filled": filled}
                )
        return fvgs

    # =========================================================================
    #  5. CANDLESTICK PATTERNS
    #     Each detector honors the book's validity rules:
    #       - prior trend requirement (self._trend)
    #       - shadow >= 2x body for hammer family
    #       - midpoint penetration for piercing / dark cloud
    # =========================================================================
    def detect_patterns(self):
        p = []
        o, h, l, c = self.o, self.h, self.l, self.c
        body, up, lo, rng = self.body, self.upper, self.lower, self.rng

        for i in range(self.n):
            t = self._trend(i)

            # ---------------- single candle ----------------
            # Doji family
            if self._is_doji(i):
                if lo[i] > 2 * up[i] and lo[i] > 0.6 * rng[i]:
                    p.append(_pattern(i, "Dragonfly Doji", BULL if t < 0 else NEUTRAL, l[i], 0.6))
                elif up[i] > 2 * lo[i] and up[i] > 0.6 * rng[i]:
                    p.append(_pattern(i, "Gravestone Doji", BEAR if t > 0 else NEUTRAL, h[i], 0.6))
                elif up[i] > 0.3 * rng[i] and lo[i] > 0.3 * rng[i]:
                    p.append(_pattern(i, "Long-Legged Doji", NEUTRAL, c[i], 0.4))
                else:
                    p.append(_pattern(i, "Doji", NEUTRAL, c[i], 0.3))

            # Hammer / Hanging Man (lower shadow >= 2x body, tiny upper shadow)
            if (body[i] > 0 and lo[i] >= 2 * body[i] and up[i] <= 0.25 * body[i]):
                if t < 0:
                    s = 0.8 if self.white[i] else 0.65     # white body stronger (book)
                    p.append(_pattern(i, "Hammer", BULL, l[i], s))
                elif t > 0:
                    s = 0.8 if self.black[i] else 0.65     # black body stronger (book)
                    p.append(_pattern(i, "Hanging Man", BEAR, h[i], s))

            # Inverted Hammer / Shooting Star (upper shadow >= 2x body)
            if (body[i] > 0 and up[i] >= 2 * body[i] and lo[i] <= 0.25 * body[i]):
                if t < 0:
                    p.append(_pattern(i, "Inverted Hammer", BULL, l[i], 0.55))
                elif t > 0:
                    p.append(_pattern(i, "Shooting Star", BEAR, h[i], 0.8))

            # Marubozu (no/negligible shadows, long body)
            if self._long_body(i) and up[i] < 0.05 * rng[i] and lo[i] < 0.05 * rng[i]:
                d = BULL if self.white[i] else BEAR
                p.append(_pattern(i, f"{'White' if self.white[i] else 'Black'} Marubozu", d, c[i], 0.7))

            # Spinning top / High wave
            if self._short_body(i) and not self._is_doji(i):
                if up[i] > 1.5 * body[i] and lo[i] > 1.5 * body[i]:
                    name = "High Wave" if (up[i] + lo[i]) > 2.5 * self.atr[i] else "Spinning Top"
                    p.append(_pattern(i, name, NEUTRAL, c[i], 0.3))

            # ---------------- two candle ----------------
            if i >= 1:
                # Bullish / Bearish Engulfing (body engulfs prior body)
                if (t < 0 and self.black[i - 1] and self.white[i]
                        and o[i] <= c[i - 1] and c[i] >= o[i - 1]
                        and body[i] > body[i - 1]):
                    p.append(_pattern(i, "Bullish Engulfing", BULL, l[i], 0.85, span=2))
                if (t > 0 and self.white[i - 1] and self.black[i]
                        and o[i] >= c[i - 1] and c[i] <= o[i - 1]
                        and body[i] > body[i - 1]):
                    p.append(_pattern(i, "Bearish Engulfing", BEAR, h[i], 0.85, span=2))

                mid_prev = (o[i - 1] + c[i - 1]) / 2.0

                # Piercing Line: opens below prior LOW, closes above midpoint
                if (t < 0 and self.black[i - 1] and self.white[i]
                        and o[i] < l[i - 1] and mid_prev < c[i] < o[i - 1]):
                    p.append(_pattern(i, "Piercing Line", BULL, l[i], 0.75, span=2))

                # Dark Cloud Cover: opens above prior HIGH, closes below midpoint
                if (t > 0 and self.white[i - 1] and self.black[i]
                        and o[i] > h[i - 1] and o[i - 1] < c[i] < mid_prev):
                    p.append(_pattern(i, "Dark Cloud Cover", BEAR, h[i], 0.75, span=2))

                # Harami (small body inside prior long body)
                if (self._long_body(i - 1) and self._short_body(i)
                        and max(o[i], c[i]) < max(o[i - 1], c[i - 1])
                        and min(o[i], c[i]) > min(o[i - 1], c[i - 1])):
                    if t < 0 and self.black[i - 1] and self.white[i]:
                        p.append(_pattern(i, "Bullish Harami", BULL, l[i], 0.6, span=2))
                    elif t > 0 and self.white[i - 1] and self.black[i]:
                        p.append(_pattern(i, "Bearish Harami", BEAR, h[i], 0.6, span=2))

                # Homing Pigeon: both black, 2nd body inside 1st, downtrend
                if (t < 0 and self.black[i - 1] and self.black[i]
                        and self._long_body(i - 1)
                        and o[i] < o[i - 1] and c[i] > c[i - 1]):
                    p.append(_pattern(i, "Homing Pigeon", BULL, l[i], 0.5, span=2))

                # Tweezers top / bottom (matching extremes within 0.1 ATR)
                tol = 0.1 * self.atr[i]
                if t > 0 and abs(h[i] - h[i - 1]) <= tol:
                    p.append(_pattern(i, "Tweezers Top", BEAR, h[i], 0.55, span=2))
                if t < 0 and abs(l[i] - l[i - 1]) <= tol:
                    p.append(_pattern(i, "Tweezers Bottom", BULL, l[i], 0.55, span=2))

                # Kicker: gap open beyond prior high/low with color flip
                if (self.black[i - 1] and self.white[i] and o[i] > h[i - 1]):
                    p.append(_pattern(i, "Bullish Kicker", BULL, l[i], 0.9, span=2))
                if (self.white[i - 1] and self.black[i] and o[i] < l[i - 1]):
                    p.append(_pattern(i, "Bearish Kicker", BEAR, h[i], 0.9, span=2))

            # ---------------- three candle ----------------
            if i >= 2:
                # Morning Star
                if (self._trend(i - 1) < 0
                        and self.black[i - 2] and self._long_body(i - 2)
                        and self._short_body(i - 1)
                        and self.white[i] and c[i] > (o[i - 2] + c[i - 2]) / 2.0):
                    p.append(_pattern(i, "Morning Star", BULL, l[i - 1], 0.85, span=3))

                # Evening Star
                if (self._trend(i - 1) > 0
                        and self.white[i - 2] and self._long_body(i - 2)
                        and self._short_body(i - 1)
                        and self.black[i] and c[i] < (o[i - 2] + c[i - 2]) / 2.0):
                    p.append(_pattern(i, "Evening Star", BEAR, h[i - 1], 0.85, span=3))

                # Three White Soldiers
                if (all(self.white[i - k] for k in range(3))
                        and c[i] > c[i - 1] > c[i - 2]
                        and o[i] > o[i - 1] and o[i - 1] > o[i - 2]
                        and all(self.upper[i - k] < 0.25 * self.body[i - k] + 1e-12 for k in range(3))
                        and all(not self._short_body(i - k) for k in range(3))):
                    p.append(_pattern(i, "Three White Soldiers", BULL, l[i - 2], 0.8, span=3))

                # Three Black Crows
                if (all(self.black[i - k] for k in range(3))
                        and c[i] < c[i - 1] < c[i - 2]
                        and o[i] < o[i - 1] and o[i - 1] < o[i - 2]
                        and all(not self._short_body(i - k) for k in range(3))):
                    p.append(_pattern(i, "Three Black Crows", BEAR, h[i - 2], 0.8, span=3))

                # Tri-Star Doji
                if (self._is_doji(i) and self._is_doji(i - 1) and self._is_doji(i - 2)
                        and self._trend(i - 2) < 0):
                    p.append(_pattern(i, "Tri-Star Doji", BULL, l[i - 1], 0.4, span=3))

        return p

    # =========================================================================
    #  MASTER ANALYZE
    # =========================================================================
    def analyze(self):
        self._results = {
            "swings": self.detect_swings(),
            "sr_zones": self.detect_sr_zones(),
            "trendlines": self.detect_trendlines(),
            "fvgs": self.detect_fvgs(),
            "patterns": self.detect_patterns(),
        }
        return self._results

    # =========================================================================
    #  PLOTTING (matplotlib) - candles + zones + trendlines + FVGs + markers
    # =========================================================================
    def plot(self, path: str = None, last_n: int = None, figsize=(16, 9), show=False):
        import matplotlib
        if path and not show:
            matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle

        res = self._results or self.analyze()
        start = 0 if last_n is None else max(0, self.n - last_n)
        xs = np.arange(self.n)

        fig, ax = plt.subplots(figsize=figsize)
        w = 0.6

        # ---- candles -------------------------------------------------------
        for i in range(start, self.n):
            color = "#26a69a" if self.white[i] else ("#ef5350" if self.black[i] else "#888888")
            ax.vlines(i, self.l[i], self.h[i], color=color, linewidth=0.8, zorder=2)
            bottom, height = min(self.o[i], self.c[i]), max(self.body[i], 1e-9)
            ax.add_patch(Rectangle((i - w / 2, bottom), w, height,
                                   facecolor=color, edgecolor=color, zorder=3))

        # ---- S/R zones -----------------------------------------------------
        for z in res["sr_zones"]:
            col = "#2962ff" if z["kind"] == "support" else "#ff6d00"
            ax.axhspan(z["level_min"], z["level_max"], color=col, alpha=0.10, zorder=1)
            ax.axhline(z["level_mid"], color=col, alpha=0.45, linewidth=1, linestyle="--")
            ax.text(start, z["level_mid"], f' {z["kind"]} x{z["touches"]}',
                    va="bottom", fontsize=8, color=col)

        # ---- trendlines ----------------------------------------------------
        for tl in res["trendlines"]:
            col = "#00c853" if tl["kind"] == "up" else "#d50000"
            ax.plot([tl["x0"], tl["x1"]], [tl["y0"], tl["y1"]],
                    color=col, linewidth=1.6, zorder=4,
                    label=f'{tl["kind"]} trendline ({tl["touches"]} touches)')

        # ---- FVGs ----------------------------------------------------------
        for g in res["fvgs"]:
            if g["filled"]:
                continue                       # draw only fresh gaps
            col = "#26a69a" if g["kind"] == BULL else "#ef5350"
            ax.add_patch(Rectangle((g["index"] - 0.5, g["bottom"]),
                                   (self.n - 1) - g["index"] + 0.5,
                                   g["top"] - g["bottom"],
                                   facecolor=col, alpha=0.15, zorder=1))
            ax.text(g["index"], (g["top"] + g["bottom"]) / 2, "FVG",
                    fontsize=7, color=col, va="center")

        # ---- pattern markers ------------------------------------------------
        pad = 0.4 * np.nanmean(self.atr)
        for pat in res["patterns"]:
            i = pat["index"]
            if i < start or pat["strength"] < 0.5:      # plot the meaningful ones
                continue
            if pat["direction"] == BULL:
                ax.annotate(pat["name"], (i, self.l[i] - pad),
                            xytext=(i, self.l[i] - 3 * pad),
                            ha="center", fontsize=7, color="green", rotation=45,
                            arrowprops=dict(arrowstyle="->", color="green", lw=0.8))
            elif pat["direction"] == BEAR:
                ax.annotate(pat["name"], (i, self.h[i] + pad),
                            xytext=(i, self.h[i] + 3 * pad),
                            ha="center", fontsize=7, color="red", rotation=45,
                            arrowprops=dict(arrowstyle="->", color="red", lw=0.8))

        ax.set_xlim(start - 1, self.n + 1)
        seg_lo = self.l[start:].min()
        seg_hi = self.h[start:].max()
        margin = 0.08 * (seg_hi - seg_lo)
        ax.set_ylim(seg_lo - margin, seg_hi + margin)
        ax.set_title("PatternEngine - candlesticks, S/R zones, trendlines, FVGs")
        ax.grid(alpha=0.2)
        if res["trendlines"]:
            ax.legend(loc="upper left", fontsize=8)
        fig.tight_layout()

        if path:
            fig.savefig(path, dpi=130)
        if show:
            plt.show()
        plt.close(fig)
        return path


# =============================================================================
#  Quick self-test / demo when run directly
# =============================================================================
if __name__ == "__main__":
    # synthetic OHLC walk with regimes so patterns/zones/FVGs actually appear
    rng = np.random.default_rng(7)
    n = 220
    drift = np.concatenate([
        np.full(60, -0.35),   # downtrend
        np.full(50, 0.05),    # base
        np.full(70, 0.45),    # uptrend
        np.full(40, -0.15),   # pullback
    ])
    steps = drift + rng.normal(0, 1.0, n)
    close = 100 + np.cumsum(steps)
    open_ = close - steps + rng.normal(0, 0.15, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0.6, 0.5, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0.6, 0.5, n))
    # inject a gap so an FVG shows up
    open_[130] += 3.0; close[130] += 3.5; high[130] += 3.8; low[130] += 2.8

    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close})
    eng = PatternEngine(df)
    res = eng.analyze()

    print(f"swing highs : {len(res['swings']['highs'])}")
    print(f"swing lows  : {len(res['swings']['lows'])}")
    print(f"S/R zones   : {len(res['sr_zones'])}")
    print(f"trendlines  : {len(res['trendlines'])}")
    print(f"FVGs        : {len(res['fvgs'])}")
    print(f"patterns    : {len(res['patterns'])}")
    for pat in res["patterns"][:12]:
        print("  ", pat["index"], pat["name"], pat["direction"], round(pat["strength"], 2))
    eng.plot("demo_chart.png")
    print("chart saved -> demo_chart.png")
