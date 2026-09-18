"""Deterministic synthetic candles, so the whole stack is testable with no network.

Mirrors the random-walk generator in pattern_engine_v2's __main__ block, but
seeded per (pair, timeframe) and given a real DatetimeIndex — the engine only
needs open/high/low/close, but everything downstream needs the timestamps.
"""
import numpy as np
import pandas as pd

from ..config import TIMEFRAMES


def make_candles(pair: str = "R_100", timeframe: str = "1H", n: int = 240) -> pd.DataFrame:
    """Return a DataFrame indexed by UTC timestamps with open/high/low/close.

    Same (pair, timeframe) always yields the same series, so scans are
    reproducible and snapshot tests are stable.
    """
    step = TIMEFRAMES[timeframe]
    seed = abs(hash((pair, timeframe))) % (2**32)
    rng = np.random.default_rng(seed)

    # Random walk with a little drift and volatility clustering, so the engine
    # has actual structure to find rather than pure noise.
    drift = rng.normal(0, 0.0004, n)
    shock = rng.normal(0, 1, n) * (0.004 + 0.003 * np.abs(np.sin(np.arange(n) / 18)))
    close = 100 * np.exp(np.cumsum(drift + shock))

    open_ = np.concatenate([[close[0]], close[:-1]])
    spread = np.abs(rng.normal(0, 0.0035, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread

    # Bar epoch is the OPEN time, matching deriv_feed's _norm_candle.
    end = pd.Timestamp.now("UTC").tz_localize(None).floor(f"{step}s")
    idx = pd.date_range(end=end, periods=n, freq=f"{step}s")

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close}, index=idx
    )
