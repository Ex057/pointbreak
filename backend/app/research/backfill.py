"""Candle backfill — the binding constraint on everything else.

Every label this project can build sits on a *price path*. Trades on the same day
share one path, so the currency of statistical power is DAYS, not rows. At the
time of writing the journal spans 7 distinct days and 1H candles span 20: with a
per-trade sigma near 1.3R that is a 95% CI on mean R of roughly +/-1.2R, i.e. the
smallest detectable edge is bigger than any edge that exists in a real market.

Pulling two years of history takes that to ~500 days and the detectable edge to
roughly +/-0.12R, which is finally in the neighbourhood of the 0.02-0.06R cost
hurdle. That is the whole reason this module runs before any modelling.

`deriv_feed.fetch_history` already does the single-window query; this adds the
pagination, the gap accounting, and the honesty about what actually landed.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from ..db import repo
from ..services import markets

log = logging.getLogger(__name__)

# Deriv caps a ticks_history response at 5000 candles. Ask for slightly less so a
# boundary candle repeating across windows can never stall progress.
WINDOW_CANDLES = 4800

TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800,
              "1H": 3600, "4H": 14400, "1D": 86400}


@dataclass
class BackfillResult:
    pair: str
    timeframe: str
    requested_from: int
    requested_to: int
    fetched: int = 0
    written: int = 0
    windows: int = 0
    empty_windows: int = 0
    misaligned_dropped: int = 0
    days_before: int = 0
    days_after: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"{self.pair} {self.timeframe}: {self.written} candles written over "
                f"{self.windows} windows; distinct days {self.days_before} -> {self.days_after}"
                + (f"; dropped {self.misaligned_dropped} misaligned" if self.misaligned_dropped else "")
                + (f"; {len(self.errors)} error(s)" if self.errors else ""))


def _distinct_days(pair: str, timeframe: str) -> int:
    with repo.cursor() as con:
        row = con.execute(
            "SELECT COUNT(DISTINCT date(epoch,'unixepoch')) AS d FROM candles "
            "WHERE pair=? AND timeframe=?",
            (pair, timeframe),
        ).fetchone()
    return int(row["d"] or 0)


def _deriv_fetcher(symbol_code: str):
    """Default fetcher: the running Deriv feed, started on demand.

    `fetch_history` is a stateless request/response, so it works for any symbol
    regardless of what the feed is subscribed to — but it still needs a live
    socket, which `start_deriv_feed` owns.
    """
    from ..engine import deriv_feed

    if deriv_feed._client is None:
        deriv_feed.start_deriv_feed([symbol_code], granularities=(60,), wait=True)

    def fetch(code: str, granularity: int, start: int, end: int) -> list[dict]:
        return deriv_feed.fetch_deriv_history(code, granularity, start, end)

    return fetch


def backfill(
    pair: str,
    timeframe: str,
    *,
    years: float = 2.0,
    now_ts: int | None = None,
    progress=None,
    sleep_between: float = 0.2,
    max_retries: int = 2,
    fetcher=None,
) -> BackfillResult:
    """Page backwards from now, writing every window to the `candles` table.

    Walks backwards rather than forwards so that an interrupted run still leaves
    the most recent — and therefore most useful — history in place.

    `fetcher(code, granularity, start, end) -> list[candle dict]` is injectable so
    this is unit-testable without a socket.
    """
    tf_seconds = TF_SECONDS.get(timeframe)
    if tf_seconds is None:
        raise ValueError(f"unsupported timeframe {timeframe!r}")

    market = markets.market_by_id(pair)
    if market is None:
        raise ValueError(f"unknown pair {pair!r}")

    # Align every boundary to the granularity. Deriv buckets candles from the
    # `start` it is given, so an arbitrary epoch yields bars stamped 10:23, 11:23,
    # ... which then overlap the 10:00, 11:00 grid already in the table. Two
    # griddings of the same hour make every ATR, swing and pattern downstream
    # meaningless, and it is invisible until you check `epoch % tf_seconds`.
    raw_end = int(now_ts if now_ts is not None else time.time())
    end = (raw_end // tf_seconds) * tf_seconds
    start_floor = ((end - int(years * 365.25 * 86400)) // tf_seconds) * tf_seconds
    result = BackfillResult(pair=pair, timeframe=timeframe,
                            requested_from=start_floor, requested_to=end,
                            days_before=_distinct_days(pair, timeframe))

    fetch = fetcher or _deriv_fetcher(market.code)
    window_span = WINDOW_CANDLES * tf_seconds
    cursor_end = end
    # Two consecutive empty windows means we are past the symbol's history; one
    # alone is just a market closure (a weekend on a real FX pair).
    consecutive_empty = 0
    # Past the symbol's history Deriv does NOT return empty — it ignores `start`
    # and hands back the most recent candles again. Without this guard the loop
    # burns a round trip per window re-fetching data it already has, all the way
    # down to the requested floor.
    oldest_seen: int | None = None
    non_advancing = 0

    while cursor_end > start_floor and consecutive_empty < 2 and non_advancing < 2:
        window_start = max(start_floor, ((cursor_end - window_span) // tf_seconds) * tf_seconds)
        result.windows += 1
        # Retry before abandoning a window. Skipping one silently discards its
        # entire span of history — up to ~200 days on 1H — and a transient socket
        # timeout is not a reason to lose that.
        rows = None
        for attempt in range(max_retries + 1):
            try:
                rows = fetch(market.code, tf_seconds, window_start, cursor_end)
                break
            except Exception as exc:  # noqa: BLE001
                if attempt >= max_retries:
                    result.errors.append(f"{window_start}-{cursor_end}: {exc}")
                    log.warning("backfill window failed after %d retries %s %s %s-%s: %s",
                                max_retries, pair, timeframe, window_start, cursor_end, exc)
                elif sleep_between:
                    time.sleep(sleep_between * (attempt + 1))
        if rows is None:
            cursor_end = window_start
            continue

        if not rows:
            result.empty_windows += 1
            consecutive_empty += 1
            cursor_end = window_start
            continue

        # Belt and braces: even with aligned windows, refuse to write a bar whose
        # epoch is off the grid. One such row silently overlaps a real one.
        aligned = [r for r in rows if int(r["epoch"]) % tf_seconds == 0]
        result.misaligned_dropped += len(rows) - len(aligned)
        if not aligned:
            result.empty_windows += 1
            consecutive_empty += 1
            cursor_end = window_start
            continue
        rows = aligned

        consecutive_empty = 0
        oldest = int(rows[0]["epoch"])
        if oldest_seen is not None and oldest >= oldest_seen:
            # Same slice again — we are past the symbol's available history.
            non_advancing += 1
        else:
            non_advancing = 0
        oldest_seen = oldest if oldest_seen is None else min(oldest_seen, oldest)

        result.fetched += len(rows)
        result.written += repo.upsert_candles(pair, timeframe, rows)
        # Step to just before the oldest candle we actually received, so a window
        # that returned less than asked for still makes progress.
        cursor_end = min(window_start, oldest - tf_seconds)

        if progress:
            progress(f"{pair} {timeframe}: {result.written} candles, "
                     f"back to {time.strftime('%Y-%m-%d', time.gmtime(rows[0]['epoch']))}")
        if sleep_between:
            time.sleep(sleep_between)

    result.days_after = _distinct_days(pair, timeframe)
    return result


def backfill_many(pairs_and_tfs, *, years: float = 2.0, progress=None,
                  fetcher=None) -> list[BackfillResult]:
    out = []
    for pair, timeframe in pairs_and_tfs:
        res = backfill(pair, timeframe, years=years, progress=progress, fetcher=fetcher)
        log.info("backfill complete: %s", res.summary())
        out.append(res)
    return out


if __name__ == "__main__":  # pragma: no cover - operator entry point
    import argparse

    parser = argparse.ArgumentParser(description="Backfill candle history.")
    parser.add_argument("--pair", default="xauusd")
    parser.add_argument("--timeframes", default="1H,15m")
    parser.add_argument("--years", type=float, default=2.0)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for res in backfill_many(
        [(args.pair, tf) for tf in args.timeframes.split(",")],
        years=args.years,
        progress=lambda msg: print(f"  {msg}", flush=True),
    ):
        print(res.summary())
