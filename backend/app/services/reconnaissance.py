"""Deterministic multi-timeframe market reconnaissance.

The service is deliberately advisory: it summarizes the existing scanner on a
fixed timeframe stack and never changes the planner, monitor, or execution
path. Results are cached until the next 15-minute close so the dashboard can
refresh freely without repeatedly running five full scans.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from time import time
from typing import Any

import numpy as np
import pandas as pd

from . import markets
from .scan import scan as run_scan


TIMEFRAMES = ("1W", "4H", "1H", "30m", "15m")
WEIGHTS = {"1W": 0.30, "4H": 0.25, "1H": 0.20, "30m": 0.15, "15m": 0.10}
TF_SECONDS = {"1W": 604800, "4H": 14400, "1H": 3600, "30m": 1800, "15m": 900, "1D": 86400}
MIN_BARS = 30
WARMUP_BARS = 200


@dataclass
class _CacheEntry:
    expires_at: int
    payload: dict[str, Any]


_cache: dict[str, _CacheEntry] = {}
_cache_lock = RLock()


def _epoch(value) -> int:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return int(stamp.timestamp())


def _completed(df: pd.DataFrame, timeframe: str, now_ts: int) -> pd.DataFrame:
    """Return only bars whose full interval has elapsed, retaining attrs."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close"])
    step = TF_SECONDS[timeframe]
    mask = [_epoch(idx) + step <= now_ts for idx in df.index]
    out = df.loc[mask].tail(WARMUP_BARS).copy()
    out.attrs.update(getattr(df, "attrs", {}))
    return out


def _next_refresh(now_ts: int) -> int:
    return ((now_ts // TF_SECONDS["15m"]) + 1) * TF_SECONDS["15m"] + 2


def _bias_sign(value: str | None) -> int:
    return {"bullish": 1, "bearish": -1}.get(str(value or "").lower(), 0)


def _structure_sign(value: str | None) -> int:
    return {"UPTREND": 1, "DOWNTREND": -1}.get(str(value or "").upper(), 0)


def _direction_score(scan: dict) -> float:
    structure = scan.get("structure") or {}
    structural = _structure_sign(structure.get("state")) * float(structure.get("confidence") or 0.0)
    bias = _bias_sign((scan.get("future") or {}).get("bias"))
    return round(100.0 * ((0.70 * structural) + (0.30 * bias)), 1)


def _volatility(df: pd.DataFrame) -> dict[str, float | str | None]:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(14, min_periods=5).mean()
    atr_pct = (atr / df["close"].replace(0, np.nan) * 100.0).dropna().tail(100)
    if atr_pct.empty:
        return {"atr_pct": None, "percentile": None, "regime": "unknown"}
    current = float(atr_pct.iloc[-1])
    percentile = float((atr_pct <= current).mean())
    regime = "low" if percentile < 0.33 else "high" if percentile >= 0.67 else "normal"
    return {"atr_pct": round(current, 3), "percentile": round(percentile, 3), "regime": regime}


def _zone_name(zone: dict) -> str:
    layer = str(zone.get("layer") or "level")
    kind = str(zone.get("kind") or "")
    return f"{layer}:{kind}" if kind else layer


def _nearest_levels(scan: dict) -> dict[str, dict | None]:
    price = float(scan["price"])
    layers = scan.get("layers") or {}
    zones = [*(layers.get("sr") or []), *(layers.get("supply_demand") or []), *(layers.get("fvg") or [])]
    candidates = []
    for zone in zones:
        level = zone.get("level_mid")
        if level is None:
            low = zone.get("level_min")
            high = zone.get("level_max")
            if low is not None and high is not None:
                level = (float(low) + float(high)) / 2.0
        if level is None:
            continue
        value = float(level)
        candidates.append({
            "price": round(value, 5),
            "type": _zone_name(zone),
            "priority": zone.get("priority"),
            "distance_pct": round(abs(value - price) / max(abs(price), 1e-9) * 100.0, 3),
        })
    below = [item for item in candidates if item["price"] <= price]
    above = [item for item in candidates if item["price"] > price]
    support = min(below, key=lambda item: price - item["price"], default=None)
    resistance = min(above, key=lambda item: item["price"] - price, default=None)
    return {"support": support, "resistance": resistance}


def _candidate(scan: dict) -> dict | None:
    entries = (scan.get("future") or {}).get("entries") or []
    if not entries:
        return None
    item = entries[0]
    return {
        "side": item.get("side"),
        "source": item.get("source"),
        "entry": item.get("entry_price"),
        "rr": item.get("rr"),
    }


def _timeframe_result(timeframe: str, df: pd.DataFrame, market: markets.Market) -> dict:
    scanned = run_scan(
        df,
        pair=market.id,
        pair_display=market.display,
        timeframe=timeframe,
        pair_category=market.category,
    )
    structure = scanned.get("structure") or {}
    score = _direction_score(scanned)
    return {
        "timeframe": timeframe,
        "available": True,
        "bars": len(df),
        "sample_confidence": round(min(1.0, len(df) / WARMUP_BARS), 3),
        "last_complete_epoch": _epoch(df.index[-1]),
        "price": round(float(df["close"].iloc[-1]), 5),
        "structure": structure.get("state", "UNDETERMINED"),
        "structure_confidence": float(structure.get("confidence") or 0.0),
        "reason": structure.get("reason") or "Structure is not yet resolved.",
        "bias": (scanned.get("future") or {}).get("bias", "neutral"),
        "direction_score": score,
        "volatility": _volatility(df),
        "levels": _nearest_levels(scanned),
        "candidate": _candidate(scanned),
        "error": None,
    }


def _unavailable(timeframe: str, message: str) -> dict:
    return {
        "timeframe": timeframe,
        "available": False,
        "bars": 0,
        "sample_confidence": 0.0,
        "last_complete_epoch": None,
        "price": None,
        "structure": "UNDETERMINED",
        "structure_confidence": 0.0,
        "reason": message,
        "bias": "neutral",
        "direction_score": 0.0,
        "volatility": {"atr_pct": None, "percentile": None, "regime": "unknown"},
        "levels": {"support": None, "resistance": None},
        "candidate": None,
        "error": message,
    }


def _summary_30d(daily: pd.DataFrame) -> dict:
    if daily.empty:
        return {
            "change_pct_30d": None, "high_30d": None, "low_30d": None,
            "range_position": None, "volatility": "unknown",
        }
    window = daily.tail(30)
    first = float(window["close"].iloc[0])
    last = float(window["close"].iloc[-1])
    high = float(window["high"].max())
    low = float(window["low"].min())
    span = high - low
    realized = float(window["close"].pct_change().dropna().std() * np.sqrt(252) * 100.0) if len(window) > 2 else 0.0
    volatility = "low" if realized < 10 else "high" if realized >= 25 else "normal"
    return {
        "change_pct_30d": round((last / first - 1.0) * 100.0, 2) if first else None,
        "high_30d": round(high, 5),
        "low_30d": round(low, 5),
        "range_position": round((last - low) / span, 3) if span > 0 else 0.5,
        "volatility": volatility,
    }


def _liquidity_levels(daily: pd.DataFrame, weekly: pd.DataFrame) -> list[dict]:
    """Previous completed day/week extremes, expressed as chart-ready levels."""
    levels: list[dict] = []

    def add(frame: pd.DataFrame, period: str, high_id: str, low_id: str) -> None:
        if frame is None or frame.empty:
            return
        row = frame.iloc[-1]
        source_epoch = _epoch(frame.index[-1])
        levels.extend([
            {
                "id": high_id,
                "label": f"Previous {period} high",
                "price": round(float(row["high"]), 5),
                "period": period,
                "side": "high",
                "source_epoch": source_epoch,
            },
            {
                "id": low_id,
                "label": f"Previous {period} low",
                "price": round(float(row["low"]), 5),
                "period": period,
                "side": "low",
                "source_epoch": source_epoch,
            },
        ])

    add(daily, "day", "PDH", "PDL")
    add(weekly, "week", "PWH", "PWL")
    return levels


def _stance(score: float) -> tuple[str, str]:
    if score >= 40:
        return "bullish_alignment", "Bullish alignment"
    if score >= 15:
        return "bullish_lean", "Bullish lean"
    if score <= -40:
        return "bearish_alignment", "Bearish alignment"
    if score <= -15:
        return "bearish_lean", "Bearish lean"
    return "mixed", "Mixed / neutral"


def _aggregate(rows: list[dict]) -> tuple[dict, list[str]]:
    available = [row for row in rows if row["available"]]
    available_weight = sum(WEIGHTS[row["timeframe"]] for row in available)
    if not available or available_weight <= 0:
        return {
            "stance": "unavailable", "score": 0.0, "confidence": 0.0,
            "headline": "Reconnaissance is waiting for completed candles.",
        }, []

    score = sum(WEIGHTS[row["timeframe"]] * row["direction_score"] for row in available) / available_weight
    dominant = 1 if score >= 15 else -1 if score <= -15 else 0
    agreeing_weight = sum(
        WEIGHTS[row["timeframe"]]
        for row in available
        if (_bias_sign("bullish" if row["direction_score"] >= 15 else "bearish" if row["direction_score"] <= -15 else "neutral") == dominant)
    )
    agreement = agreeing_weight / available_weight if dominant else sum(
        WEIGHTS[row["timeframe"]] for row in available if abs(row["direction_score"]) < 15
    ) / available_weight
    evidence = sum(
        WEIGHTS[row["timeframe"]]
        * ((0.7 * row["structure_confidence"]) + (0.3 * row["sample_confidence"]))
        for row in available
    ) / available_weight
    confidence = min(1.0, available_weight * ((0.6 * agreement) + (0.4 * evidence)))
    stance, label = _stance(score)

    conflicts = []
    higher = [row for row in available if row["timeframe"] in {"1W", "4H"}]
    lower = [row for row in available if row["timeframe"] in {"1H", "30m", "15m"}]
    high_score = sum(WEIGHTS[r["timeframe"]] * r["direction_score"] for r in higher) / max(sum(WEIGHTS[r["timeframe"]] for r in higher), 1e-9) if higher else 0
    low_score = sum(WEIGHTS[r["timeframe"]] * r["direction_score"] for r in lower) / max(sum(WEIGHTS[r["timeframe"]] for r in lower), 1e-9) if lower else 0
    if high_score >= 15 and low_score <= -15:
        conflicts.append("Higher timeframes are bullish while execution timeframes are bearish.")
    elif high_score <= -15 and low_score >= 15:
        conflicts.append("Higher timeframes are bearish while execution timeframes are bullish.")

    headline = f"{label} across {len(available)} of {len(TIMEFRAMES)} timeframes."
    if conflicts:
        headline = f"{label}, but higher and execution timeframes conflict."
    return {
        "stance": stance,
        "score": round(score, 1),
        "confidence": round(confidence, 3),
        "headline": headline,
    }, conflicts


def build(market: markets.Market, *, force_refresh: bool = False, now_ts: int | None = None) -> dict:
    now_ts = int(now_ts or time())
    with _cache_lock:
        cached = _cache.get(market.id)
        if cached and not force_refresh and now_ts < cached.expires_at:
            return {**cached.payload, "cached": True}

    markets.prepare_live_feed(market, [*TIMEFRAMES, "1D"])
    rows: list[dict] = []
    completed_frames: dict[str, pd.DataFrame] = {}
    warnings: list[str] = []
    for timeframe in TIMEFRAMES:
        try:
            frame = _completed(markets.load_candles(market, timeframe), timeframe, now_ts)
            completed_frames[timeframe] = frame
            if len(frame) < MIN_BARS:
                message = f"Only {len(frame)} completed {timeframe} bars; at least {MIN_BARS} are required."
                rows.append(_unavailable(timeframe, message))
                warnings.append(message)
                continue
            rows.append(_timeframe_result(timeframe, frame, market))
        except Exception as exc:  # noqa: BLE001 - partial results are intentional
            message = f"{timeframe} analysis unavailable: {exc}"
            rows.append(_unavailable(timeframe, message))
            warnings.append(message)

    try:
        daily = _completed(markets.load_candles(market, "1D"), "1D", now_ts)
    except Exception as exc:  # noqa: BLE001
        daily = pd.DataFrame(columns=["open", "high", "low", "close"])
        warnings.append(f"30-day context unavailable: {exc}")

    aggregate, conflicts = _aggregate(rows)
    available_count = sum(1 for row in rows if row["available"])
    status = "ready" if available_count == len(TIMEFRAMES) else "partial" if available_count else "unavailable"
    refresh_at = _next_refresh(now_ts)
    payload = {
        "pair": market.id,
        "pair_display": market.display,
        "status": status,
        "generated_at": now_ts,
        "next_refresh_at": refresh_at,
        "window_days": 30,
        "cached": False,
        "summary": {**aggregate, **_summary_30d(daily)},
        "liquidity": {
            "levels": _liquidity_levels(daily, completed_frames.get("1W", pd.DataFrame())),
        },
        "timeframes": rows,
        "conflicts": conflicts,
        "warnings": warnings,
    }
    with _cache_lock:
        _cache[market.id] = _CacheEntry(expires_at=refresh_at, payload=payload)
    return payload


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
