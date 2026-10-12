"""Free live-market fallback via Yahoo Finance's public chart endpoint.

No API key, no auth. Used when the primary (Deriv) feed can't serve a symbol —
e.g. from a geo-restricted network. Covers REAL markets only (forex, metals,
stock indices); Deriv synthetic indices have no Yahoo equivalent and are handled
by returning an empty frame upstream.

The chart endpoint returns OHLC arrays aligned to a timestamp array:
  https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=..&range=..
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
import urllib.request

import pandas as pd

log = logging.getLogger("tradeguard.yahoo")

_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
_UA = {"User-Agent": "Mozilla/5.0"}

# Our timeframe -> (Yahoo interval, Yahoo range). Yahoo has no native 4H, so we
# pull 60m and resample. 1m history is capped at ~7 days by Yahoo.
_INTERVAL = {
    "1m": ("1m", "1d"),
    "5m": ("5m", "5d"),
    "15m": ("15m", "5d"),
    "30m": ("30m", "1mo"),
    "1H": ("60m", "1mo"),
    "4H": ("60m", "3mo"),
    "1D": ("1d", "1y"),
    "1W": ("1wk", "5y"),
}

# Short-lived price cache so a 1.5s frontend poll doesn't hammer Yahoo.
_price_cache: dict[str, dict] = {}
_PRICE_TTL = 5.0
_CANDLE_TTL = 15.0
_candle_cache: dict[tuple[str, str], tuple[pd.DataFrame, float]] = {}
_refreshing: set[tuple[str, str]] = set()
_cache_lock = threading.RLock()


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.load(resp)


def _fetch_candles_uncached(symbol: str, timeframe: str) -> pd.DataFrame:
    interval, rng = _INTERVAL.get(timeframe, ("1d", "1y"))
    url = f"{_BASE}{urllib.parse.quote(symbol)}?interval={interval}&range={rng}"
    try:
        data = _get(url)
        result = data["chart"]["result"][0]
        ts = result["timestamp"]
        q = result["indicators"]["quote"][0]
        meta = result.get("meta", {})
        df = pd.DataFrame(
            {"open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"]},
            index=pd.to_datetime(ts, unit="s", utc=True),
        ).dropna()
        if timeframe == "4H" and not df.empty:
            df = (df.resample("4h")
                    .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
                    .dropna())
        # cache the freshest price for /price
        price = meta.get("regularMarketPrice")
        if price is None and not df.empty:
            price = float(df["close"].iloc[-1])
        received_at = time.time()
        provider_epoch = int(meta.get("regularMarketTime") or df.index[-1].timestamp())
        df.attrs.update({
            "source": "yahoo",
            "resolved_symbol": symbol,
            "received_at": received_at,
            "provider_epoch": provider_epoch,
            "realtime": False,
        })
        if price is not None:
            _price_cache[symbol] = {
                "price": float(price), "epoch": provider_epoch,
                "received_at": received_at,
            }
        return df.tail(300)
    except Exception as exc:  # network / shape / rate-limit
        log.warning("yahoo fetch failed symbol=%s tf=%s: %s", symbol, timeframe, exc)
        return pd.DataFrame(columns=["open", "high", "low", "close"])


def _refresh_candles(symbol: str, timeframe: str) -> None:
    key = (symbol, timeframe)
    try:
        frame = _fetch_candles_uncached(symbol, timeframe)
        if not frame.empty:
            with _cache_lock:
                _candle_cache[key] = (frame, time.time())
    finally:
        with _cache_lock:
            _refreshing.discard(key)


def fetch_candles(symbol: str, timeframe: str) -> pd.DataFrame:
    """Return Yahoo OHLC with a short stale-while-revalidate cache."""
    key = (symbol, timeframe)
    now = time.time()
    with _cache_lock:
        cached = _candle_cache.get(key)
        if cached and now - cached[1] < _CANDLE_TTL:
            return cached[0].copy()
        if cached:
            if key not in _refreshing:
                _refreshing.add(key)
                threading.Thread(
                    target=_refresh_candles,
                    args=(symbol, timeframe),
                    name=f"yahoo-refresh-{symbol}-{timeframe}",
                    daemon=True,
                ).start()
            stale = cached[0].copy()
            stale.attrs["fallback_reason"] = "stale_while_revalidate"
            return stale
    frame = _fetch_candles_uncached(symbol, timeframe)
    if not frame.empty:
        with _cache_lock:
            _candle_cache[key] = (frame, now)
    return frame.copy()


def fetch_candles_window(symbol: str, timeframe: str, start: int, end: int) -> list[dict]:
    """Historical candles for a Yahoo symbol filtered to [start, end] epochs.

    Yahoo's chart endpoint only takes coarse `range` presets, so we fetch the
    recent window for the timeframe and filter to the requested epochs. Adequate
    for a same-day end-of-day replay; older windows may fall outside Yahoo's
    intraday retention (~7d for 1m, ~60d for 5-30m). Returns candle dicts
    (epoch/open/high/low/close) ascending, or [] on failure/empty.
    """
    df = fetch_candles(symbol, timeframe)
    if df.empty:
        return []
    out: list[dict] = []
    for idx, row in df.iterrows():
        epoch = int(idx.timestamp()) if hasattr(idx, "timestamp") else None
        if epoch is None or epoch < int(start) or epoch > int(end):
            continue
        out.append({
            "epoch": epoch,
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        })
    out.sort(key=lambda r: r["epoch"])
    return out


def fetch_research_hourly_window(symbol: str, start: int, end: int) -> list[dict]:
    """Fetch an untruncated Yahoo 1H research window (GC=F for XAU fallback).

    The normal live-candle cache is deliberately capped at 300 bars. Research
    asks for a larger bounded 3-month chart response so a chronological holdout
    can be formed; Yahoo may still return less history, which coverage reports.
    """
    url = f"{_BASE}{urllib.parse.quote(symbol)}?interval=60m&range=3mo"
    try:
        data = _get(url)
        result = data["chart"]["result"][0]
        timestamps = result.get("timestamp") or []
        quote = result["indicators"]["quote"][0]
        rows = []
        for index, epoch in enumerate(timestamps):
            if int(epoch) < int(start) or int(epoch) > int(end):
                continue
            values = [quote[key][index] for key in ("open", "high", "low", "close")]
            if any(value is None for value in values):
                continue
            rows.append({"epoch": int(epoch), "open": values[0], "high": values[1], "low": values[2], "close": values[3]})
        return rows
    except Exception as exc:  # network, provider response, or retention failure
        log.warning("yahoo research fetch failed symbol=%s: %s", symbol, exc)
        return []


def fetch_price_record(symbol: str) -> dict | None:
    """Latest Yahoo quote with honest provider and receipt timestamps."""
    cached = _price_cache.get(symbol)
    now = time.time()
    if cached and now - cached["received_at"] < _PRICE_TTL:
        record = dict(cached)
        record["age"] = max(0.0, now - record["epoch"])
        record["received_age"] = max(0.0, now - record["received_at"])
        return record
    try:
        data = _get(f"{_BASE}{urllib.parse.quote(symbol)}?interval=1m&range=1d")
        result = data["chart"]["result"][0]
        meta = result.get("meta", {})
        price = meta.get("regularMarketPrice")
        if price is not None:
            timestamps = result.get("timestamp") or []
            provider_epoch = int(meta.get("regularMarketTime") or (timestamps[-1] if timestamps else now))
            record = {
                "price": float(price), "epoch": provider_epoch,
                "received_at": now, "age": max(0.0, now - provider_epoch),
                "received_age": 0.0,
            }
            _price_cache[symbol] = dict(record)
            return record
    except Exception as exc:
        log.warning("yahoo price failed symbol=%s: %s", symbol, exc)
    if cached:
        record = dict(cached)
        record["age"] = max(0.0, now - record["epoch"])
        record["received_age"] = max(0.0, now - record["received_at"])
        record["fallback_reason"] = "provider_error_cached_quote"
        return record
    return None


def fetch_price(symbol: str) -> float | None:
    """Backwards-compatible float-only quote helper."""
    record = fetch_price_record(symbol)
    return None if record is None else float(record["price"])
