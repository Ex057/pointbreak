"""Engine -> JSON adapter.

The pattern engine speaks in POSITIONAL BAR INDICES (0..n-1). It never emits a
timestamp. lightweight-charts needs epochs. Every index/x0/x1/x_future the
engine returns must be translated here, or the chart renders looking entirely
plausible while being completely wrong. That translation is this module's whole
reason to exist.

Two other engine quirks are corrected here rather than in the engine itself, so
pattern_engine_v2.py stays the untouched reference implementation:

  * project_future() hardcodes a 2R target. We recompute it from min_rr so the
    Settings "minimum R:R" control can actually mean something.
  * detect_supply_demand() filters broken zones before returning, so `broken` is
    always False. We drop the dead field.
"""
from __future__ import annotations

import pandas as pd

from ..engine import pattern_meta
from ..config import TIMEFRAMES
from ..engine.pattern_engine_v2 import PatternEngine, available_patterns
from .structure import classify_structure


class BarClock:
    """Maps positional bar indices to epoch seconds, including into the future.

    Indices past the end of the series are projected forward at one granularity
    step per bar — that is how the engine's x_future / trendline extensions are
    meant to be read.
    """

    def __init__(self, index: pd.DatetimeIndex, step_seconds: int):
        self._times = [int(ts.timestamp()) for ts in index]
        self._step = step_seconds
        self._last_i = len(self._times) - 1

    def at(self, i: float | int | None) -> int | None:
        if i is None:
            return None
        i = int(i)
        if 0 <= i <= self._last_i:
            return self._times[i]
        if i < 0:
            return self._times[0] + i * self._step
        return self._times[-1] + (i - self._last_i) * self._step


def _candles_payload(df: pd.DataFrame) -> list[dict]:
    return [
        {
            "time": int(ts.timestamp()),
            "open": float(r.open),
            "high": float(r.high),
            "low": float(r.low),
            "close": float(r.close),
        }
        for ts, r in zip(df.index, df.itertuples())
    ]


def _retarget(entry: dict, min_rr: float) -> dict:
    """Replace the engine's hardcoded 2R target with a min_rr-derived one."""
    risk = abs(entry["entry_price"] - entry["stop"])
    if entry["side"] == "long":
        entry["target"] = round(entry["entry_price"] + min_rr * risk, 5)
    else:
        entry["target"] = round(entry["entry_price"] - min_rr * risk, 5)
    entry["rr"] = min_rr
    entry["risk_per_unit"] = round(risk, 5)
    return entry


def _priority_bucket(score: float) -> str:
    if score >= 0.72:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"


def _zone_overlap_count(zone: dict, all_zones: list[dict], atr: float) -> int:
    count = 1
    for other in all_zones:
        if other is zone:
            continue
        if zone["level_min"] <= other["level_max"] + 0.25 * atr and other["level_min"] <= zone["level_max"] + 0.25 * atr:
            count += 1
    return count


def _score_zones(sr: list[dict], supply_demand: list[dict], fvgs: list[dict], last_close: float, atr: float):
    all_zones = sr + supply_demand + fvgs

    for zone in sr:
        width = max(zone["level_max"] - zone["level_min"], 1e-9)
        proximity = max(0.0, 1.0 - abs(zone["level_mid"] - last_close) / max(6 * atr, 1e-9))
        width_score = max(0.0, 1.0 - width / max(2.5 * atr, 1e-9))
        confluence = min(_zone_overlap_count(zone, all_zones, atr), 4) / 4.0
        score = 0.4 * min(zone.get("touches", 1), 5) / 5.0 + 0.25 * proximity + 0.2 * width_score + 0.15 * confluence
        zone["priority_score"] = round(score, 3)
        zone["priority"] = _priority_bucket(score)
        zone["priority_reason"] = f"{zone.get('touches', 1)} touches, proximity {proximity:.2f}, confluence {confluence:.2f}"

    for zone in supply_demand:
        width = max(zone["level_max"] - zone["level_min"], 1e-9)
        proximity = max(0.0, 1.0 - abs(zone["level_mid"] - last_close) / max(8 * atr, 1e-9))
        width_score = max(0.0, 1.0 - width / max(3 * atr, 1e-9))
        freshness = 1.0 if zone.get("fresh") else 0.45
        confluence = min(_zone_overlap_count(zone, all_zones, atr), 4) / 4.0
        score = 0.4 * freshness + 0.25 * proximity + 0.2 * width_score + 0.15 * confluence
        zone["priority_score"] = round(score, 3)
        zone["priority"] = _priority_bucket(score)
        zone["priority_reason"] = f"{'fresh' if zone.get('fresh') else 'tested'}, proximity {proximity:.2f}, confluence {confluence:.2f}"

    for zone in fvgs:
        width = max(zone["top"] - zone["bottom"], 1e-9)
        proximity = max(0.0, 1.0 - abs(zone["level_mid"] - last_close) / max(8 * atr, 1e-9))
        width_score = min(width / max(1.5 * atr, 1e-9), 1.0)
        fill_score = 0.2 if zone.get("filled") else 1.0
        confluence = min(_zone_overlap_count(zone, all_zones, atr), 4) / 4.0
        score = 0.35 * fill_score + 0.25 * width_score + 0.25 * proximity + 0.15 * confluence
        zone["priority_score"] = round(score, 3)
        zone["priority"] = _priority_bucket(score)
        zone["priority_reason"] = f"{'unfilled' if not zone.get('filled') else 'filled'}, width {width_score:.2f}, confluence {confluence:.2f}"


def scan(
    df: pd.DataFrame,
    pair: str,
    timeframe: str,
    bars_ahead: int = 30,
    min_rr: float = 2.0,
    pair_display: str | None = None,
    pair_category: str = "",
) -> dict:
    """Run the engine and return a JSON-ready, epoch-stamped analysis."""
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unknown timeframe {timeframe!r}")

    engine = PatternEngine(df, timeframe=timeframe, pair_category=pair_category)
    raw = engine.analyze(bars_ahead=bars_ahead)
    clock = BarClock(df.index, TIMEFRAMES[timeframe])

    swings = {
        side: [{"time": clock.at(s["index"]), "price": s["price"]} for s in pts]
        for side, pts in raw["swings"].items()
    }

    supply_demand = []
    for z in raw["supply_demand"]:
        z = {k: v for k, v in z.items() if k != "broken"}  # always False, dead field
        z["time"] = clock.at(z.pop("index"))
        z["end_time"] = clock.at(z.pop("end_index"))
        supply_demand.append(z)

    fvgs = []
    for z in raw["fvgs"]:
        z = dict(z)
        z["time"] = clock.at(z.pop("index"))
        # the gap's 3-candle formation span...
        z["start_time"] = clock.at(z.pop("start_index"))
        z["end_time"] = clock.at(z.pop("end_index"))
        # ...and where it got filled (None if still open) — the box extends
        # forward from formation to here, or ahead if unfilled.
        z["fill_time"] = clock.at(z.pop("fill_index"))
        z["level_min"] = z["bottom"]
        z["level_max"] = z["top"]
        fvgs.append(z)

    # EMA/SMA overlay series: map each point's index -> epoch for the chart
    moving_averages = []
    for ser in raw["moving_averages"]:
        ser = dict(ser)
        ser["points"] = [{"time": clock.at(p["index"]), "value": p["value"]}
                         for p in ser["points"]]
        moving_averages.append(ser)

    def _patterns(items):
        out = []
        for p in items:
            p = dict(p)
            p["index"] = int(p["index"])
            p["time"] = clock.at(p["index"])
            p["start_time"] = clock.at(p.pop("start_index"))
            p["end_time"] = clock.at(p.pop("end_index"))
            out.append(p)
        return out

    def _chart_patterns(items):
        """Map every geometry endpoint (bar index) of a chart pattern to an epoch
        so lightweight-charts can draw its lines / region / pivots correctly."""
        out = []
        for cp in items:
            cp = dict(cp)
            cp["pivots"] = [{"time": clock.at(pv["index"]), "price": pv["price"],
                             "label": pv.get("label", "")} for pv in cp.get("pivots", [])]
            cp["lines"] = [{"t0": clock.at(ln["x0"]), "y0": ln["y0"],
                            "t1": clock.at(ln["x1"]), "y1": ln["y1"],
                            "kind": ln.get("kind", "")} for ln in cp.get("lines", [])]
            region = cp.get("region")
            cp["region"] = None if not region else {
                "start_time": clock.at(region["start_index"]),
                "end_time": clock.at(region["end_index"]),
                "top": region["top"], "bottom": region["bottom"],
            }
            cp["breakout_time"] = clock.at(cp.pop("breakout_index", None))
            pattern_meta.stamp_chart(cp)
            out.append(cp)
        return out

    fut = raw["future"]

    entries = []
    for e in fut["entries"]:
        e = _retarget(dict(e), min_rr)
        e["t_future"] = clock.at(e["x_future"])
        entries.append(e)

    last_close = float(df["close"].iloc[-1])
    prev_close = float(df["close"].iloc[-2]) if len(df) > 1 else last_close
    atr = float((df["high"] - df["low"]).tail(20).mean()) if not df.empty else 0.0
    atr_safe = atr or max(last_close * 0.01, 1e-9)
    _score_zones(raw["sr_zones"], supply_demand, fvgs, last_close, atr_safe)

    # Market structure: the richer read layered on top of the engine's 3-way
    # bias. Reuses the swings the engine already found and the slope-based bias
    # as the confirmation lens — no engine edit, no recompute.
    structure = classify_structure(
        raw["swings"],
        df["close"].to_numpy(float),
        atr_safe,
        fut["bias"],
    )

    return {
        "pair": pair,
        "pair_display": pair_display or pair,
        "timeframe": timeframe,
        "generated_at": int(pd.Timestamp.now("UTC").timestamp()),
        "price": last_close,
        "change_pct": round((last_close / prev_close - 1) * 100, 3),
        "structure": structure,
        "candles": _candles_payload(df),
        # the patterns worth offering on this instrument's checklist (per category)
        "pattern_catalog": pattern_meta.grouped_catalog(
            available_patterns(pair_category),
            [p for p in raw["patterns_all"] if p.get("confirmed") and p.get("available")],
        ),
        "pattern_catalog_flat": available_patterns(pair_category),
        # chart/structure patterns catalog (grouped bullish/bearish/range)
        "chart_pattern_catalog": pattern_meta.grouped_chart_catalog(
            [c for c in raw["chart_patterns"] if c.get("status") == "confirmed"]),
        "layers": {
            "swings": swings,
            "sr": raw["sr_zones"],
            "supply_demand": supply_demand,
            "fvg": fvgs,
            "moving_averages": moving_averages,
            "patterns_high": _patterns(raw["patterns_high"]),
            "patterns_all": _patterns(raw["patterns_all"]),
            "chart_patterns": _chart_patterns(raw["chart_patterns"]),
        },
        "future": {
            "bars_ahead": fut["bars_ahead"],
            "bias": fut["bias"],
            "entries": entries,
        },
    }
