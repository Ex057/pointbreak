"""Multi-bar CHART (structure) pattern detection.

Complements the single/2/3-candle detectors in pattern_engine_v2. These are the
classic price-structure formations traders draw with trendlines and necklines —
double tops, head & shoulders, triangles, wedges, channels, flags, pennants,
rectangles, rounding bottoms and cup & handle.

Method (standard swing-pivot approach):
  * take the engine's swing highs/lows, collapse to an alternating zigzag,
  * run geometric matchers over the recent pivot window with ATR-scaled
    tolerances,
  * a pattern is `forming` until price CLOSES beyond its boundary
    (neckline / trendline / flag rail), then `confirmed`,
  * every geometry endpoint is a POSITIONAL BAR INDEX — scan.py maps them to
    epochs via BarClock (including breakout/target extensions into the future).

Each detection is a plain dict (JSON-ready):
  { name, category (bullish|bearish|range), direction (bullish|bearish|neutral),
    status (forming|confirmed),
    pivots:[{index,price,label}],
    lines:[{x0,y0,x1,y1,kind}],
    region:{start_index,end_index,top,bottom}|None,
    breakout_index|None, target|None, stop|None, score }
"""
from __future__ import annotations

import numpy as np

BULLISH = "bullish"
BEARISH = "bearish"
RANGE = "range"
NEUTRAL = "neutral"

# name -> category, the single source of truth for grouping/availability.
CHART_PATTERN_CATEGORY: dict[str, str] = {
    "Double Bottom": BULLISH,
    "Triple Bottom": BULLISH,
    "Inverse Head & Shoulders": BULLISH,
    "Ascending Triangle": BULLISH,
    "Falling Wedge": BULLISH,
    "Bull Flag": BULLISH,
    "Bullish Pennant": BULLISH,
    "Rounding Bottom": BULLISH,
    "Cup & Handle": BULLISH,
    "Double Top": BEARISH,
    "Triple Top": BEARISH,
    "Head & Shoulders": BEARISH,
    "Descending Triangle": BEARISH,
    "Rising Wedge": BEARISH,
    "Bear Flag": BEARISH,
    "Bearish Pennant": BEARISH,
    "Rounding Top": BEARISH,
    "Symmetrical Triangle": RANGE,
    "Rectangle": RANGE,
    "Ascending Channel": RANGE,
    "Descending Channel": RANGE,
    "Broadening / Megaphone": RANGE,
}

CHART_PATTERN_NAMES = list(CHART_PATTERN_CATEGORY.keys())

# how far back a pattern's last pivot may be and still count as "current"
_RECENCY_BARS = 60
_LOOKBACK_BARS = 160


# ----------------------------------------------------------------- primitives
def _alternating_pivots(engine) -> list[dict]:
    """Swing highs+lows merged into an index-ordered alternating zigzag; runs of
    the same kind collapse to the extreme (highest high / lowest low)."""
    sw = engine.detect_swings()
    pts = [{"i": int(h["index"]), "price": float(h["price"]), "kind": "H"} for h in sw["highs"]]
    pts += [{"i": int(l["index"]), "price": float(l["price"]), "kind": "L"} for l in sw["lows"]]
    pts.sort(key=lambda p: p["i"])
    out: list[dict] = []
    for p in pts:
        if out and out[-1]["kind"] == p["kind"]:
            prev = out[-1]
            if (p["kind"] == "H" and p["price"] > prev["price"]) or (
                p["kind"] == "L" and p["price"] < prev["price"]
            ):
                out[-1] = p
        else:
            out.append(p)
    return out


def _fit_line(xs: list[int], ys: list[float]) -> tuple[float, float]:
    slope, intercept = np.polyfit(np.array(xs, float), np.array(ys, float), 1)
    return float(slope), float(intercept)


def _line(x0: int, x1: int, slope: float, intercept: float, kind: str) -> dict:
    return {"x0": int(x0), "y0": float(slope * x0 + intercept),
            "x1": int(x1), "y1": float(slope * x1 + intercept), "kind": kind}


def _hline(x0: int, x1: int, y: float, kind: str) -> dict:
    return {"x0": int(x0), "y0": float(y), "x1": int(x1), "y1": float(y), "kind": kind}


def _atr(engine) -> float:
    a = float(np.nanmean(engine.atr[-20:])) if engine.n else 0.0
    return a or max(float(np.nanmean(engine.c)) * 0.005, 1e-9)


def _score(base: float, *bonuses: float) -> float:
    return round(min(1.0, base + sum(bonuses)), 3)


# ----------------------------------------------------------------- reversals
def _double_or_triple(engine, pivots, atr):
    """Double/Triple Top (equal highs) and Bottom (equal lows)."""
    out = []
    n = engine.n
    close = engine.c[-1]
    tol = 0.6 * atr

    # tops: two/three highs at a similar level with a trough between
    highs = [p for p in pivots if p["kind"] == "H"]
    lows = [p for p in pivots if p["kind"] == "L"]

    def peaks_pattern(peaks, troughs, is_top):
        res = []
        if len(peaks) < 2:
            return res
        recent = peaks[-3:]
        for count, name in ((3, "Triple"), (2, "Double")):
            if len(recent) < count:
                continue
            grp = recent[-count:]
            level = np.mean([p["price"] for p in grp])
            if max(abs(p["price"] - level) for p in grp) > tol:
                continue
            if grp[-1]["i"] < n - 1 - _RECENCY_BARS:
                continue
            # neckline = the extreme trough between the first and last peak
            inner = [t for t in troughs if grp[0]["i"] < t["i"] < grp[-1]["i"]]
            if not inner:
                continue
            neck = (min(t["price"] for t in inner) if is_top
                    else max(t["price"] for t in inner))
            height = abs(level - neck)
            if height < 0.8 * atr:
                continue
            if is_top:
                confirmed = close < neck
                direction = BEARISH
                target = neck - height
                stop = level + 0.3 * atr
                label = name + " Top"
            else:
                confirmed = close > neck
                direction = BULLISH
                target = neck + height
                stop = level - 0.3 * atr
                label = name + " Bottom"
            pivots_out = [{"index": p["i"], "price": p["price"], "label": str(k + 1)}
                          for k, p in enumerate(grp)]
            res.append({
                "name": label,
                "category": BEARISH if is_top else BULLISH,
                "direction": direction,
                "status": "confirmed" if confirmed else "forming",
                "pivots": pivots_out,
                "lines": [_hline(grp[0]["i"], n - 1, neck, "neckline"),
                          _hline(grp[0]["i"], grp[-1]["i"], level,
                                 "resistance" if is_top else "support")],
                "region": None,
                "breakout_index": (n - 1) if confirmed else None,
                "target": float(target),
                "stop": float(stop),
                "score": _score(0.5, 0.1 * len(grp), 0.15 if confirmed else 0.0),
            })
            break  # prefer triple over double, one per side
        return res

    out += peaks_pattern(highs, lows, True)
    out += peaks_pattern(lows, highs, False)
    return out


def _head_and_shoulders(engine, pivots, atr):
    out = []
    n = engine.n
    close = engine.c[-1]
    tol = 0.5 * atr

    highs = [p for p in pivots if p["kind"] == "H"]
    lows = [p for p in pivots if p["kind"] == "L"]

    # bearish H&S: LS < H > RS, LS≈RS, head is the max; neckline through the two lows
    if len(highs) >= 3 and len(lows) >= 2:
        ls, head, rs = highs[-3], highs[-2], highs[-1]
        if (rs["i"] >= n - 1 - _RECENCY_BARS and head["price"] > ls["price"]
                and head["price"] > rs["price"]
                and abs(ls["price"] - rs["price"]) <= 1.2 * atr):
            inner = [t for t in lows if ls["i"] < t["i"] < rs["i"]]
            if len(inner) >= 2:
                l1, l2 = inner[0], inner[-1]
                slope, intercept = _fit_line([l1["i"], l2["i"]], [l1["price"], l2["price"]])
                neck_now = slope * (n - 1) + intercept
                height = head["price"] - (slope * head["i"] + intercept)
                confirmed = close < neck_now
                out.append({
                    "name": "Head & Shoulders",
                    "category": BEARISH, "direction": BEARISH,
                    "status": "confirmed" if confirmed else "forming",
                    "pivots": [
                        {"index": ls["i"], "price": ls["price"], "label": "LS"},
                        {"index": head["i"], "price": head["price"], "label": "H"},
                        {"index": rs["i"], "price": rs["price"], "label": "RS"},
                    ],
                    "lines": [_line(l1["i"], n - 1, slope, intercept, "neckline")],
                    "region": None,
                    "breakout_index": (n - 1) if confirmed else None,
                    "target": float(neck_now - height),
                    "stop": float(head["price"] + 0.3 * atr),
                    "score": _score(0.6, 0.15 if confirmed else 0.0),
                })

    # bullish inverse H&S: LS > H < RS on the lows
    if len(lows) >= 3 and len(highs) >= 2:
        ls, head, rs = lows[-3], lows[-2], lows[-1]
        if (rs["i"] >= n - 1 - _RECENCY_BARS and head["price"] < ls["price"]
                and head["price"] < rs["price"]
                and abs(ls["price"] - rs["price"]) <= 1.2 * atr):
            inner = [t for t in highs if ls["i"] < t["i"] < rs["i"]]
            if len(inner) >= 2:
                h1, h2 = inner[0], inner[-1]
                slope, intercept = _fit_line([h1["i"], h2["i"]], [h1["price"], h2["price"]])
                neck_now = slope * (n - 1) + intercept
                height = (slope * head["i"] + intercept) - head["price"]
                confirmed = close > neck_now
                out.append({
                    "name": "Inverse Head & Shoulders",
                    "category": BULLISH, "direction": BULLISH,
                    "status": "confirmed" if confirmed else "forming",
                    "pivots": [
                        {"index": ls["i"], "price": ls["price"], "label": "LS"},
                        {"index": head["i"], "price": head["price"], "label": "H"},
                        {"index": rs["i"], "price": rs["price"], "label": "RS"},
                    ],
                    "lines": [_line(h1["i"], n - 1, slope, intercept, "neckline")],
                    "region": None,
                    "breakout_index": (n - 1) if confirmed else None,
                    "target": float(neck_now + height),
                    "stop": float(head["price"] - 0.3 * atr),
                    "score": _score(0.6, 0.15 if confirmed else 0.0),
                })
    return out


# --------------------------------------------------- trendline family (one pick)
def _trendline_pattern(engine, pivots, atr):
    n = engine.n
    close = engine.c[-1]
    highs = [p for p in pivots if p["kind"] == "H" and p["i"] >= n - _LOOKBACK_BARS][-3:]
    lows = [p for p in pivots if p["kind"] == "L" and p["i"] >= n - _LOOKBACK_BARS][-3:]
    if len(highs) < 2 or len(lows) < 2:
        return []

    hs, hi = _fit_line([p["i"] for p in highs], [p["price"] for p in highs])
    ls, li = _fit_line([p["i"] for p in lows], [p["price"] for p in lows])

    x0 = min(highs[0]["i"], lows[0]["i"])
    x1 = n - 1
    span = max(x1 - x0, 1)
    rise_h = hs * span
    rise_l = ls * span
    flat_h = abs(rise_h) < 0.6 * atr
    flat_l = abs(rise_l) < 0.6 * atr
    up_h, dn_h = rise_h >= 0.6 * atr, rise_h <= -0.6 * atr
    up_l, dn_l = rise_l >= 0.6 * atr, rise_l <= -0.6 * atr

    gap0 = (hs * x0 + hi) - (ls * x0 + li)
    gap1 = (hs * x1 + hi) - (ls * x1 + li)
    converging = gap1 < 0.7 * gap0
    diverging = gap1 > 1.3 * gap0
    if gap0 <= 0:  # crossed lines — not a clean structure
        return []

    res_now = hs * x1 + hi
    sup_now = ls * x1 + li
    height = gap1

    name = category = direction = None
    if flat_h and up_l:
        name, category, direction = "Ascending Triangle", BULLISH, BULLISH
    elif flat_l and dn_h:
        name, category, direction = "Descending Triangle", BEARISH, BEARISH
    elif converging and dn_h and up_l:
        name, category, direction = "Symmetrical Triangle", RANGE, NEUTRAL
    elif converging and up_h and up_l:
        name, category, direction = "Rising Wedge", BEARISH, BEARISH
    elif converging and dn_h and dn_l:
        name, category, direction = "Falling Wedge", BULLISH, BULLISH
    elif flat_h and flat_l:
        name, category, direction = "Rectangle", RANGE, NEUTRAL
    elif diverging and up_h and dn_l:
        name, category, direction = "Broadening / Megaphone", RANGE, NEUTRAL
    elif up_h and up_l:
        name, category, direction = "Ascending Channel", RANGE, BULLISH
    elif dn_h and dn_l:
        name, category, direction = "Descending Channel", RANGE, BEARISH
    else:
        return []

    # confirmation: close beyond the relevant rail
    confirmed = False
    if direction == BULLISH:
        confirmed = close > res_now
    elif direction == BEARISH:
        confirmed = close < sup_now
    else:  # bilateral — either break confirms and sets direction
        if close > res_now:
            confirmed, direction = True, BULLISH
        elif close < sup_now:
            confirmed, direction = True, BEARISH

    target = None
    if confirmed and direction == BULLISH:
        target = res_now + height
    elif confirmed and direction == BEARISH:
        target = sup_now - height

    pivots_out = ([{"index": p["i"], "price": p["price"], "label": "H"} for p in highs]
                  + [{"index": p["i"], "price": p["price"], "label": "L"} for p in lows])
    return [{
        "name": name, "category": category, "direction": direction,
        "status": "confirmed" if confirmed else "forming",
        "pivots": pivots_out,
        "lines": [_line(x0, x1, hs, hi, "resistance"),
                  _line(x0, x1, ls, li, "support")],
        "region": None,
        "breakout_index": (n - 1) if confirmed else None,
        "target": float(target) if target is not None else None,
        "stop": float(sup_now - 0.3 * atr) if direction == BULLISH
                else float(res_now + 0.3 * atr) if direction == BEARISH else None,
        "score": _score(0.45, 0.1 * (len(highs) + len(lows) - 4), 0.15 if confirmed else 0.0),
    }]


# ----------------------------------------------------------------- flags/pennants
def _flag_or_pennant(engine, pivots, atr):
    n = engine.n
    close = engine.c[-1]
    if len(pivots) < 4:
        return []
    recent = [p for p in pivots if p["i"] >= n - _LOOKBACK_BARS]
    if len(recent) < 4:
        return []

    # find the most recent strong leg (flagpole) between two consecutive pivots
    pole = None
    for a, b in zip(recent, recent[1:]):
        if abs(b["price"] - a["price"]) >= 2.5 * atr:
            pole = (a, b)
    if pole is None:
        return []
    a, b = pole
    up = b["price"] > a["price"]
    consol = [p for p in recent if p["i"] > b["i"]]
    if len(consol) < 2 or consol[-1]["i"] < n - 1 - _RECENCY_BARS:
        return []

    pole_size = abs(b["price"] - a["price"])
    prices = [p["price"] for p in consol]
    consol_range = max(prices) - min(prices)
    if consol_range > 0.6 * pole_size:  # consolidation too big — not a flag
        return []

    ch = [p for p in consol if p["kind"] == "H"]
    cl = [p for p in consol if p["kind"] == "L"]
    lines = []
    converging = False
    if len(ch) >= 2 and len(cl) >= 2:
        hs, hi = _fit_line([p["i"] for p in ch], [p["price"] for p in ch])
        ls, li = _fit_line([p["i"] for p in cl], [p["price"] for p in cl])
        x0, x1 = consol[0]["i"], n - 1
        g0 = (hs * x0 + hi) - (ls * x0 + li)
        g1 = (hs * x1 + hi) - (ls * x1 + li)
        converging = g1 < 0.6 * g0
        lines = [_line(x0, x1, hs, hi, "resistance"), _line(x0, x1, ls, li, "support")]
        upper_now, lower_now = hs * x1 + hi, ls * x1 + li
    else:
        top, bot = max(prices), min(prices)
        upper_now, lower_now = top, bot
        lines = [_hline(consol[0]["i"], n - 1, top, "resistance"),
                 _hline(consol[0]["i"], n - 1, bot, "support")]

    pennant = converging
    if up:
        name = "Bullish Pennant" if pennant else "Bull Flag"
        category = direction = BULLISH
        confirmed = close > upper_now
        target = upper_now + pole_size if confirmed else None
    else:
        name = "Bearish Pennant" if pennant else "Bear Flag"
        category = direction = BEARISH
        confirmed = close < lower_now
        target = lower_now - pole_size if confirmed else None

    return [{
        "name": name, "category": category, "direction": direction,
        "status": "confirmed" if confirmed else "forming",
        "pivots": [{"index": a["i"], "price": a["price"], "label": "pole"},
                   {"index": b["i"], "price": b["price"], "label": "pole"}],
        "lines": lines,
        "region": {"start_index": consol[0]["i"], "end_index": n - 1,
                   "top": float(max(prices)), "bottom": float(min(prices))},
        "breakout_index": (n - 1) if confirmed else None,
        "target": float(target) if target is not None else None,
        "stop": float(min(prices) - 0.3 * atr) if up else float(max(prices) + 0.3 * atr),
        "score": _score(0.5, 0.15 if confirmed else 0.0),
    }]


# ----------------------------------------------------------------- rounding / cup
def _rounding(engine, atr):
    n = engine.n
    win = min(60, n)
    if win < 25:
        return []
    xs = np.arange(win, dtype=float)
    ys = engine.c[-win:]
    a, b, c = np.polyfit(xs, ys, 2)
    yfit = a * xs * xs + b * xs + c
    ss_res = float(np.sum((ys - yfit) ** 2))
    ss_tot = float(np.sum((ys - np.mean(ys)) ** 2)) or 1e-9
    r2 = 1 - ss_res / ss_tot
    vertex = -b / (2 * a) if a != 0 else -1
    if r2 < 0.5 or not (win * 0.2 < vertex < win * 0.8):
        return []
    depth = abs(yfit.max() - yfit.min())
    if depth < 1.5 * atr:
        return []

    start_i = n - win
    close = engine.c[-1]
    if a > 0:  # convex up -> rounding bottom (bullish)
        rim = max(ys[0], ys[-1])
        confirmed = close > rim
        base = {
            "name": "Rounding Bottom", "category": BULLISH, "direction": BULLISH,
            "status": "confirmed" if confirmed else "forming",
            "pivots": [{"index": int(start_i + vertex), "price": float(yfit.min()), "label": "base"}],
            "lines": [_hline(start_i, n - 1, rim, "neckline")],
            "region": {"start_index": start_i, "end_index": n - 1,
                       "top": float(rim), "bottom": float(yfit.min())},
            "breakout_index": (n - 1) if confirmed else None,
            "target": float(rim + depth) if confirmed else None,
            "stop": float(yfit.min() - 0.3 * atr),
            "score": _score(0.4, 0.3 * max(0.0, r2 - 0.5), 0.1 if confirmed else 0.0),
        }
        # cup & handle: a small pullback near the right rim after the cup
        handle_ys = engine.c[-6:]
        if (ys[-1] >= rim - 0.5 * atr and (handle_ys.max() - handle_ys.min()) < 0.4 * depth):
            cup = dict(base)
            cup["name"] = "Cup & Handle"
            cup["score"] = _score(base["score"], 0.05)
            return [cup]
        return [base]
    else:  # concave -> rounding top (bearish)
        rim = min(ys[0], ys[-1])
        confirmed = close < rim
        return [{
            "name": "Rounding Top", "category": BEARISH, "direction": BEARISH,
            "status": "confirmed" if confirmed else "forming",
            "pivots": [{"index": int(start_i + vertex), "price": float(yfit.max()), "label": "peak"}],
            "lines": [_hline(start_i, n - 1, rim, "neckline")],
            "region": {"start_index": start_i, "end_index": n - 1,
                       "top": float(yfit.max()), "bottom": float(rim)},
            "breakout_index": (n - 1) if confirmed else None,
            "target": float(rim - depth) if confirmed else None,
            "stop": float(yfit.max() + 0.3 * atr),
            "score": _score(0.4, 0.3 * max(0.0, r2 - 0.5), 0.1 if confirmed else 0.0),
        }]


# ----------------------------------------------------------------- entry point
def detect_chart_patterns(engine, max_out: int = 6) -> list[dict]:
    if engine.n < 20:
        return []
    atr = _atr(engine)
    pivots = _alternating_pivots(engine)
    found: list[dict] = []
    found += _double_or_triple(engine, pivots, atr)
    found += _head_and_shoulders(engine, pivots, atr)
    found += _trendline_pattern(engine, pivots, atr)
    found += _flag_or_pennant(engine, pivots, atr)
    found += _rounding(engine, atr)

    # de-duplicate by name, prefer confirmed then higher score
    best: dict[str, dict] = {}
    for p in found:
        cur = best.get(p["name"])
        if cur is None or (p["status"] == "confirmed", p["score"]) > (
            cur["status"] == "confirmed", cur["score"]):
            best[p["name"]] = p
    ordered = sorted(best.values(),
                     key=lambda p: (p["status"] == "confirmed", p["score"]), reverse=True)
    return ordered[:max_out]
