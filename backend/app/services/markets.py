"""Market catalog and live candle loading helpers."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
import time

import pandas as pd

from ..config import (
    CANDLE_CACHE_MAXLEN,
    CANDLE_HISTORY_BARS,
    DATA_SOURCE,
    DERIV_API_TOKEN,
    DERIV_APP_ID,
    DERIV_URL,
    PREFERRED_MARKETS,
)
from ..engine.deriv_feed import (
    DERIV_CONFIG,
    fetch_active_symbols,
    fetch_deriv_history,
    get_candles_df,
    start_deriv_feed,
    stop_deriv_feed,
)
from .fixtures import make_candles
from . import yahoo

log = logging.getLogger("tradeguard.markets")

# A live tick is considered stale after this many seconds without an update.
TICK_STALE_SECONDS = 15.0
YAHOO_STALE_SECONDS = 120.0
PRICE_DRIFT_MAX = 0.15
TRADE_PRICE_DRIFT_MAX = 0.35


def get_live_tick(market: "Market") -> dict | None:
    """The freshest quote for a market: Deriv's streamed TICK when available,
    else the free Yahoo quote for real markets. Returns None in fixture mode or
    when nothing is available (e.g. a synthetic with no Deriv access)."""
    if DATA_SOURCE == "fixture":
        return None
    # Deriv streamed tick (most real-time when the feed is live on this pair)
    if DATA_SOURCE == "deriv":
        rec = DERIV_CONFIG["prices"].get(market.code)
        if rec:
            now = time.time()
            received_at = float(rec.get("received_at") or rec.get("ts", 0.0))
            epoch = int(rec.get("epoch", 0))
            age = max(0.0, now - epoch) if epoch else max(0.0, now - received_at)
            received_age = max(0.0, now - received_at)
            return {
                "source": "deriv", "price": float(rec["price"]),
                "epoch": epoch, "received_at": received_at,
                "age": round(age, 2), "received_age": round(received_age, 2),
                "stale": received_age > TICK_STALE_SECONDS,
                "realtime": True, "quote_kind": "last",
            }
    # Free latest-available Yahoo fallback for real markets.
    if market.fallback:
        rec = yahoo.fetch_price_record(market.fallback)
        if rec is not None:
            return {
                "source": "yahoo", "price": float(rec["price"]),
                "epoch": int(rec.get("epoch", 0)),
                "received_at": rec.get("received_at"),
                "age": round(float(rec.get("age", 0.0)), 2),
                "received_age": round(float(rec.get("received_age", 0.0)), 2),
                "stale": float(rec.get("age", 0.0)) > YAHOO_STALE_SECONDS,
                "realtime": False, "quote_kind": "last",
                "fallback_reason": rec.get("fallback_reason") or "deriv_unavailable",
            }
    return None


def live_price_value(market: "Market") -> float | None:
    tick = get_live_tick(market)
    if not tick or tick.get("price") is None or tick.get("stale"):
        return None
    return float(tick["price"])


def latest_ohlc(market: "Market", timeframe: str) -> dict | None:
    source_tf = "1D" if timeframe == "1W" else timeframe
    try:
        granularity = _granularity(source_tf)
    except KeyError:
        return None
    # Must match the SAME source the chart/scan uses, or the header OHLC drifts
    # from the candles (e.g. fixture scan ~100 but a real Yahoo quote leaking in).
    if DATA_SOURCE == "fixture":
        df = make_candles(market.id, timeframe)
    else:
        df = get_candles_df(market.code, granularity=granularity)
        if timeframe == "1W" and not df.empty:
            df = _aggregate_weekly(df)
        if df.empty and market.fallback:
            df = _yahoo_candles(market, timeframe)
    if df.empty:
        return None
    row = df.iloc[-1]
    return {
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "time": int(df.index[-1].timestamp()) if hasattr(df.index[-1], "timestamp") else None,
    }


def validate_trade_levels(
    market: "Market",
    *,
    entry: float | None,
    stop: float | None,
    target: float | None,
    reference_price: float | None = None,
) -> tuple[bool, str | None]:
    values = [float(v) for v in (entry, stop, target) if v is not None]
    if not values:
        return False, "Trade levels are missing."
    sanity_min = getattr(market, "sanity_min", None)
    if sanity_min is not None:
        bad = next((v for v in values if v < sanity_min), None)
        if bad is not None:
            return False, f"{market.display} trade level {bad:.2f} is below the sanity floor."
    if reference_price is not None:
        ref = max(abs(float(reference_price)), 1e-9)
        drift = max(abs(v - float(reference_price)) / ref for v in values)
        if drift > TRADE_PRICE_DRIFT_MAX:
            return False, (
                f"{market.display} trade levels drift too far from the live reference "
                f"({float(reference_price):.2f})."
            )
    return True, None


@dataclass(frozen=True)
class Market:
    id: str
    display: str
    code: str
    category: str
    live: bool
    fallback: str | None = None   # free Yahoo symbol when Deriv can't serve this pair
    sanity_min: float | None = None


_feed_lock = threading.RLock()
_active_pair_id: str | None = None
_active_pair_code: str | None = None
_active_timeframes: list[str] = []
_active_granularities: list[int] = []
_latest_feed_error: str | None = None
_configured_at: float | None = None


def _bundle_for(timeframe: str) -> list[str]:
    bundles = {
        "1m": ["1m", "5m", "15m", "1H"],
        "5m": ["5m", "15m", "1H"],
        "15m": ["15m", "1H", "4H"],
        "30m": ["30m", "1H", "4H"],
        "1H": ["1H", "4H", "1D"],
        "4H": ["4H", "1D"],
        "1D": ["1D"],
        "1W": ["1D"],
    }
    return bundles[timeframe]


def _granularity(tf: str) -> int:
    return {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1H": 3600,
        "4H": 14400,
        "1D": 86400,
    }[tf]


def _aggregate_weekly(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    weekly = (
        df.resample("W-MON", label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    return weekly.tail(240)


def _reset_feed_cache():
    DERIV_CONFIG["prices"].clear()
    DERIV_CONFIG["candles"].clear()
    DERIV_CONFIG["status"].clear()
    DERIV_CONFIG["status"].update({"connected": False, "last_msg": 0.0, "reconnects": 0})


def build_catalog() -> tuple[list[Market], dict]:
    global _active_pair_id, _active_pair_code, _active_timeframes, _active_granularities, _latest_feed_error, _configured_at
    log.info("build_catalog data_source=%s requested_markets=%s", DATA_SOURCE, len(PREFERRED_MARKETS))
    available_symbols: set[str] | None = None
    availability_error: str | None = None
    if DATA_SOURCE == "deriv":
        try:
            available = fetch_active_symbols(app_id=DERIV_APP_ID, url=DERIV_URL or None)
            available_symbols = {
                item.get("underlying_symbol") or item.get("symbol")
                for item in available
                if (item.get("underlying_symbol") or item.get("symbol"))
            }
            log.info("fetched %s active Deriv symbols for validation", len(available_symbols))
        except Exception as exc:
            availability_error = str(exc)
            log.warning("active symbol lookup failed: %s", exc)
    validated_codes: dict[str, bool] = {}
    if DATA_SOURCE == "deriv":
        if not available_symbols:
            availability_error = availability_error or "Deriv active_symbols returned no usable codes."
            log.warning("skipping per-symbol Deriv probes during startup because active_symbols was empty")
            validated_codes = {item["code"]: False for item in PREFERRED_MARKETS}
        else:
            for item in PREFERRED_MARKETS:
                code = item["code"]
                validated_codes[code] = code in available_symbols
    preferred = [
        Market(
            id=item["id"],
            display=item["display"],
            code=item["code"],
            category=item["category"],
            live=DATA_SOURCE != "deriv" or validated_codes.get(item["code"], True),
            fallback=item.get("fallback"),
            sanity_min=item.get("sanity_min"),
        )
        for item in PREFERRED_MARKETS
    ]

    _active_pair_id = None
    _active_pair_code = None
    _active_timeframes = []
    _active_granularities = []
    _latest_feed_error = availability_error
    _configured_at = None

    if DATA_SOURCE != "deriv":
        log.info("using fixture catalog")
        return preferred, get_feed_status()

    with _feed_lock:
        stop_deriv_feed()
        _reset_feed_cache()
    return preferred, get_feed_status()


def fallback_catalog(error: str | None = None) -> tuple[list[Market], dict]:
    """Static catalog used when live catalog bootstrap is unavailable.

    This keeps the API surface up so `/health`, `/settings`, and `/monitor`
    still respond even if Deriv discovery is slow or failing.
    """
    preferred = [
        Market(
            id=item["id"],
            display=item["display"],
            code=item["code"],
            category=item["category"],
            live=DATA_SOURCE != "deriv",
            fallback=item.get("fallback"),
            sanity_min=item.get("sanity_min"),
        )
        for item in PREFERRED_MARKETS
    ]
    status = get_feed_status()
    status["mode"] = DATA_SOURCE
    status["error"] = error or status.get("error")
    status["catalog_fallback"] = True
    return preferred, status


def get_feed_status() -> dict:
    status = dict(DERIV_CONFIG["status"])
    status["mode"] = DATA_SOURCE
    status["requested"] = 1 if _active_pair_id else 0
    status["resolved"] = 1 if status.get("connected") and _active_pair_id else 0
    status["active_pair"] = _active_pair_id
    status["active_timeframes"] = list(_active_timeframes)
    ready_tick = bool(_active_pair_code and _active_pair_code in DERIV_CONFIG["prices"])
    ready_candles = bool(_active_pair_code) and all(
        not get_candles_df(_active_pair_code, granularity=g).empty
        for g in _active_granularities
    )
    status["warming"] = bool(_active_pair_id and not (ready_tick and ready_candles))
    status["configured_at"] = _configured_at
    status["error"] = _latest_feed_error
    return status


def prepare_live_feed(market: Market, timeframes: list[str]) -> None:
    """Configure one persistent feed for a pair and a union of timeframes."""
    global _active_pair_id, _active_pair_code, _active_timeframes, _active_granularities, _latest_feed_error, _configured_at
    if DATA_SOURCE != "deriv":
        return
    wanted = []
    for timeframe in timeframes:
        for bundled in _bundle_for(timeframe):
            if bundled not in wanted:
                wanted.append(bundled)
    granularities = [_granularity(tf) for tf in wanted]

    with _feed_lock:
        same_pair = _active_pair_code == market.code
        if same_pair:
            wanted = list(dict.fromkeys([*_active_timeframes, *wanted]))
            granularities = [_granularity(tf) for tf in wanted]
        if same_pair and granularities == _active_granularities:
            return

        log.info(
            "configuring live feed pair=%s code=%s timeframes=%s",
            market.id, market.code, wanted,
        )
        if not same_pair:
            _reset_feed_cache()
        _latest_feed_error = None
        _active_pair_id = market.id
        _active_pair_code = market.code
        _active_timeframes = wanted
        _active_granularities = granularities
        _configured_at = time.time()

        try:
            start_deriv_feed(
                symbols=[market.code],
                granularities=granularities,
                app_id=DERIV_APP_ID,
                url=DERIV_URL or None,
                api_token=DERIV_API_TOKEN or "",
                wait=False,
                candles_count=CANDLE_HISTORY_BARS,
                candles_maxlen=CANDLE_CACHE_MAXLEN,
            )
        except Exception as exc:
            _latest_feed_error = str(exc)
            log.exception("Failed to configure Deriv feed for %s", market.code)


def _ensure_live_feed(market: Market, timeframe: str) -> None:
    prepare_live_feed(market, [timeframe])


def _yahoo_candles(market: Market, timeframe: str) -> pd.DataFrame:
    """Free real-market fallback. Empty for synthetics (no Yahoo equivalent)."""
    if not market.fallback:
        return pd.DataFrame(columns=["open", "high", "low", "close"])
    df = yahoo.fetch_candles(market.fallback, timeframe)
    if not df.empty:
        log.info("loaded YAHOO fallback pair=%s yahoo=%s timeframe=%s rows=%s",
                 market.id, market.fallback, timeframe, len(df))
    return df


def load_candles(market: Market, timeframe: str) -> pd.DataFrame:
    global _latest_feed_error
    if DATA_SOURCE == "fixture":
        df = make_candles(market.id, timeframe)
        df.attrs.update({"source": "fixture", "resolved_symbol": market.id, "realtime": False})
        return df
    if DATA_SOURCE == "yahoo":
        # free real-market mode; synthetics have no source and return empty
        df = _yahoo_candles(market, timeframe)
        if df.empty and not market.fallback:
            _latest_feed_error = (
                f"{market.display} is a Deriv-only synthetic index — no free "
                f"fallback exists. Use DATA_SOURCE=deriv with a token for this pair.")
        _persist_candles(market.id, timeframe, df)
        return df

    # deriv (primary — required for synthetics), with Yahoo fallback for real markets
    _ensure_live_feed(market, timeframe)
    source_tf = "1D" if timeframe == "1W" else timeframe
    granularity = _granularity(source_tf)

    df = get_candles_df(market.code, granularity=granularity)
    if not df.empty:
        if timeframe == "1W":
            df = _aggregate_weekly(df)
            df.attrs.update({"source": "deriv", "resolved_symbol": market.code, "realtime": True})
        if not df.empty:
            log.info("loaded live candles pair=%s code=%s timeframe=%s rows=%s", market.id, market.code, timeframe, len(df))
            _persist_candles(market.id, timeframe, df)
            return df

    # Deriv produced nothing (not ready / geo-restricted / unsupported symbol).
    # Fall back to the free Yahoo feed for real markets; synthetics stay empty.
    fallback_df = _yahoo_candles(market, timeframe)
    if not fallback_df.empty:
        _persist_candles(market.id, timeframe, fallback_df)
        return fallback_df

    if _latest_feed_error is None:
        _latest_feed_error = f"No candles received yet for {market.display} {timeframe}."
    log.warning("no live candles yet pair=%s code=%s timeframe=%s granularity=%s", market.id, market.code, timeframe, granularity)
    return fallback_df


_MARKET_BY_ID = {item["id"]: item for item in PREFERRED_MARKETS}


def market_by_id(pair: str) -> Market | None:
    item = _MARKET_BY_ID.get(pair)
    if item is None:
        return None
    return Market(
        id=item["id"], display=item["display"], code=item["code"],
        category=item["category"], live=True,
        fallback=item.get("fallback"), sanity_min=item.get("sanity_min"),
    )


def _persist_candles(pair: str, timeframe: str, df: pd.DataFrame) -> None:
    """Best-effort: write loaded candles to the DB so history accumulates for the
    what-if replay. Never let a persistence error affect the live request."""
    if df is None or df.empty:
        return
    try:
        from ..db import repo  # lazy import avoids any import-time cycle
        rows = []
        for idx, row in df.iterrows():
            epoch = int(idx.timestamp()) if hasattr(idx, "timestamp") else None
            if epoch is None:
                continue
            rows.append({
                "epoch": epoch, "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
            })
        repo.upsert_candles(pair, timeframe, rows)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("candle persist failed pair=%s tf=%s: %s", pair, timeframe, exc)


def load_candle_window(pair: str, timeframe: str, start: int, end: int) -> list[dict]:
    """Historical OHLC for [start, end] epochs: persisted DB first, then a Deriv
    `ticks_history` fetch, then the Yahoo fallback for real markets. Fetched
    candles are written back to the DB. Returns candle dicts ascending, or []."""
    from ..db import repo  # lazy import avoids any import-time cycle
    cached = repo.read_candles(pair, timeframe, start, end)
    if cached:
        return cached

    market = market_by_id(pair)
    if market is None:
        return []
    source_tf = "1D" if timeframe == "1W" else timeframe
    try:
        granularity = _granularity(source_tf)
    except KeyError:
        return []

    fetched: list[dict] = []
    if DATA_SOURCE == "deriv":
        fetched = fetch_deriv_history(market.code, granularity, start, end)
    if not fetched and market.fallback:
        fetched = yahoo.fetch_candles_window(market.fallback, source_tf, start, end)

    window = [c for c in fetched if int(start) <= int(c["epoch"]) <= int(end)]
    if window:
        try:
            repo.upsert_candles(pair, timeframe, window)
        except Exception as exc:  # pragma: no cover
            log.debug("history persist failed pair=%s tf=%s: %s", pair, timeframe, exc)
    return sorted(window, key=lambda r: r["epoch"])


def feed_snapshot(market: Market, timeframe: str, df: pd.DataFrame | None = None) -> dict:
    """Return source + sanity metadata for the current pair/timeframe."""
    if df is None or df.empty:
        df = load_candles(market, timeframe)
    tick = get_live_tick(market)
    market_code = getattr(market, "code", market.id)
    candle_source = str(getattr(df, "attrs", {}).get("source") or (
        "fixture" if DATA_SOURCE == "fixture" else "unknown"
    ))
    using_yahoo_fallback = DATA_SOURCE == "deriv" and candle_source == "yahoo"
    candle_price = float(df["close"].iloc[-1]) if df is not None and not df.empty else None
    reference = None if not tick or tick.get("price") is None or tick.get("stale") else float(tick["price"])
    drift = None
    price_valid = True
    error = None
    sanity_min = getattr(market, "sanity_min", None)
    if DATA_SOURCE != "fixture" and sanity_min is not None and candle_price is not None and candle_price < sanity_min:
        price_valid = False
        error = f"{market.display} candle price {candle_price:.2f} is below the sanity floor."
    if reference is not None and candle_price is not None:
        drift = abs(candle_price - reference) / max(abs(reference), 1e-9)
        if drift > PRICE_DRIFT_MAX:
            price_valid = False
            error = (
                f"{market.display} candle/tick drift is {drift * 100:.1f}% "
                f"({candle_price:.2f} vs {reference:.2f})."
            )
    has_live_deriv_data = market_code in DERIV_CONFIG["prices"] or (
        DATA_SOURCE == "deriv" and not df.empty and not using_yahoo_fallback
    )
    if DATA_SOURCE == "deriv" and not getattr(market, "live", True) and not using_yahoo_fallback and not has_live_deriv_data:
        price_valid = False
        error = error or f"{market.display} is not available on this Deriv session."
    return {
        "pair": market.id,
        "timeframe": timeframe,
        "feed_source": candle_source,
        "tick_source": tick.get("source") if tick else None,
        "candle_source": candle_source,
        "resolved_symbol": getattr(df, "attrs", {}).get("resolved_symbol") or getattr(market, "code", market.id),
        "price_valid": price_valid,
        "reference_price": reference,
        "candle_price": candle_price,
        "price_drift": round(drift, 4) if drift is not None else None,
        "feed_error": error or _latest_feed_error,
        "warming": bool(get_feed_status().get("warming")) if DATA_SOURCE == "deriv" else False,
        "realtime": bool(tick and tick.get("realtime")),
        "provider_epoch": tick.get("epoch") if tick else getattr(df, "attrs", {}).get("provider_epoch"),
        "received_at": tick.get("received_at") if tick else getattr(df, "attrs", {}).get("received_at"),
        "age": tick.get("age") if tick else None,
        "stale": tick.get("stale") if tick else None,
        "fallback_reason": (
            tick.get("fallback_reason") if tick else getattr(df, "attrs", {}).get("fallback_reason")
        ) or ("deriv_warming_or_unavailable" if using_yahoo_fallback else None),
    }
