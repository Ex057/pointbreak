"""Execution assumptions for paper trading."""
from __future__ import annotations

from ..config import XAUUSD_PAPER_LOT, XAUUSD_SPREAD

POINT_VALUE_PER_LOT = {
    "xauusd": 100.0,  # standard gold lot: 100 oz, so $1 move = $100 per 1.00 lot
}

PIP_SIZE_BY_PAIR = {
    "xauusd": 0.01,
    "eurusd": 0.0001,
    "gbpusd": 0.0001,
    "usdjpy": 0.01,
    "us30": 1.0,
}


def spread_for(pair: str) -> float:
    return XAUUSD_SPREAD if pair == "xauusd" else 0.0


def execution_size(pair: str, suggested_size: float) -> float:
    if pair == "xauusd":
        return XAUUSD_PAPER_LOT
    return suggested_size


def distance_metrics(pair: str, start: float | None, end: float | None) -> dict[str, float | None]:
    if start is None or end is None:
        return {"points": None, "pips": None}
    points = abs(float(end) - float(start))
    pip_size = PIP_SIZE_BY_PAIR.get(pair)
    pips = None if not pip_size or pip_size <= 0 else round(points / pip_size)
    return {
        "points": round(points, 5 if pair in {"eurusd", "gbpusd", "usdjpy"} else 2),
        "pips": pips,
    }


def r_multiple(pair: str, side: str, entry: float, exit_price: float, risk: float) -> float:
    """R earned, net of the spread — the same convention `cash_delta` uses.

    R and cash must never disagree about whether a trade won, so the half-spread
    is applied on both legs here exactly as it is below. On a tight stop this is
    not a rounding detail: a 0.30 spread against a 1-point stop is 30% of risk,
    and ignoring it is what let the ledger show +$449 while net R was -136.
    """
    if risk <= 0:
        return 0.0
    spread = spread_for(pair)
    entry_exec = entry + (spread / 2.0 if side == "long" else -spread / 2.0)
    exit_exec = exit_price - (spread / 2.0 if side == "long" else -spread / 2.0)
    direction = 1.0 if side == "long" else -1.0
    return (exit_exec - entry_exec) * direction / risk


def cash_delta(pair: str, side: str, entry: float, exit_price: float, size: float) -> float | None:
    point_value = POINT_VALUE_PER_LOT.get(pair)
    if point_value is None or size <= 0:
        return None
    spread = spread_for(pair)
    entry_exec = entry + (spread / 2.0 if side == "long" else -spread / 2.0)
    exit_exec = exit_price - (spread / 2.0 if side == "long" else -spread / 2.0)
    direction = 1.0 if side == "long" else -1.0
    pnl = (exit_exec - entry_exec) * point_value * size * direction
    return round(pnl, 2)
