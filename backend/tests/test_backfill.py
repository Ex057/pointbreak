"""Backfill pagination, tested against a synthetic fetcher (no socket)."""
from app.db.database import init_schema
from app.db import repo
from app.research import backfill as bf


def _synthetic_fetcher(oldest_epoch: int, tf_seconds: int, per_window: int = 4800):
    """A fetcher with a finite history that starts at `oldest_epoch`."""
    def fetch(code, granularity, start, end):
        lo = max(int(start), oldest_epoch)
        hi = int(end)
        if lo > hi:
            return []
        epochs = list(range(lo - lo % tf_seconds, hi + 1, tf_seconds))[:per_window]
        return [{"epoch": e, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5}
                for e in epochs]
    return fetch


def test_backfill_pages_backwards_until_history_runs_out():
    init_schema()
    tf_seconds = 3600
    now = 1_800_000_000
    oldest = now - 300 * 86400          # 300 days of history available

    res = bf.backfill(
        "xauusd", "1H", years=2.0, now_ts=now, sleep_between=0,
        fetcher=_synthetic_fetcher(oldest, tf_seconds),
    )

    assert res.written > 0
    assert res.windows > 1, "must have paginated, not stopped after one window"
    # It should stop shortly after exhausting real history rather than grinding
    # through the entire requested 2 years of empty windows.
    assert res.empty_windows <= 2
    rows = repo.read_candles("xauusd", "1H", limit=100000)
    assert len(rows) > 1000
    assert rows == sorted(rows, key=lambda r: r["epoch"])
    assert len(repo.read_research_candles("xauusd", "1H", "deriv")) >= res.written


def test_backfill_reports_day_coverage_change():
    """Days, not rows, are the statistical currency — the result must say so."""
    init_schema()
    now = 1_800_000_000
    before = bf._distinct_days("xauusd", "4H")
    res = bf.backfill(
        "xauusd", "4H", years=0.25, now_ts=now, sleep_between=0,
        fetcher=_synthetic_fetcher(now - 90 * 86400, 14400),
    )
    assert res.days_before == before
    assert res.days_after > res.days_before
    assert "distinct days" in res.summary()


def test_a_transient_failure_is_retried_not_skipped():
    """Skipping a window silently discards its whole span — up to ~200 days on 1H."""
    init_schema()
    calls = {"n": 0}
    good = _synthetic_fetcher(1_800_000_000 - 40 * 86400, 3600)

    def flaky(code, granularity, start, end):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("deriv timed out")
        return good(code, granularity, start, end)

    res = bf.backfill("xauusd", "1H", years=0.5, now_ts=1_800_000_000,
                      sleep_between=0, fetcher=flaky)
    assert not res.errors, "a retried window should not be reported as an error"
    assert res.written > 0, "the window's history must be recovered, not lost"


def test_an_exhausted_window_is_recorded_and_the_run_continues():
    init_schema()

    def always_fails(code, granularity, start, end):
        raise TimeoutError("deriv is down")

    res = bf.backfill("xauusd", "1H", years=0.5, now_ts=1_800_000_000,
                      sleep_between=0, max_retries=1, fetcher=always_fails)
    assert res.errors and "deriv is down" in res.errors[0]
    assert res.written == 0


def test_stops_when_the_feed_replays_the_same_recent_slice():
    """Past its history limit Deriv ignores `start` and re-sends recent candles.

    Without a non-advancing guard the loop keeps paying for round trips that
    return data it already has, all the way down to the requested floor.
    """
    init_schema()
    now = 1_800_000_000
    tf_seconds = 900
    calls = {"n": 0}

    def stuck(code, granularity, start, end):
        calls["n"] += 1
        # Always the same recent slice, regardless of what was asked for.
        base = now - 500 * tf_seconds
        return [{"epoch": base + i * tf_seconds, "open": 1.0, "high": 2.0,
                 "low": 0.5, "close": 1.5} for i in range(500)]

    res = bf.backfill("xauusd", "15m", years=2.0, now_ts=now,
                      sleep_between=0, fetcher=stuck)
    assert calls["n"] <= 4, f"burned {calls['n']} round trips on a stuck feed"
    assert res.windows <= 4


def test_unknown_timeframe_and_pair_are_rejected_loudly():
    init_schema()
    for kwargs in ({"pair": "xauusd", "timeframe": "3s"}, {"pair": "nope", "timeframe": "1H"}):
        try:
            bf.backfill(**kwargs, years=0.1, fetcher=lambda *a: [])
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {kwargs}")
