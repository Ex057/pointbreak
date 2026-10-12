"""Provider-separated historical data acquisition for offline research."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import time

from ..db import repo
from . import backfill

PAIR_SCOPE = "xauusd"
TIMEFRAME = "1H"
DEFAULT_YEARS = 2.0
MIN_FORECAST_CANDLES = 500

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tradeguard-history")
_LOCK = Lock()


def history_status(pair: str = PAIR_SCOPE) -> dict:
    sources = repo.research_candle_status(pair, TIMEFRAME)
    normalized = {}
    for source in ("deriv", "yahoo"):
        row = sources.get(source, {"rows": 0, "first_epoch": None, "last_epoch": None})
        normalized[source] = {
            "rows": int(row["rows"]),
            "first_epoch": row["first_epoch"],
            "last_epoch": row["last_epoch"],
            "usable_for_forecast": int(row["rows"]) >= MIN_FORECAST_CANDLES,
        }
    selected = next((source for source in ("deriv", "yahoo") if normalized[source]["usable_for_forecast"]), None)
    return {
        "pair": pair,
        "timeframe": TIMEFRAME,
        "default_years": DEFAULT_YEARS,
        "minimum_candles": MIN_FORECAST_CANDLES,
        "selected_source": selected,
        "sources": normalized,
        "yahoo_symbol": "GC=F",
        "note": None if selected != "yahoo" else "Yahoo GC=F is a gold-futures proxy, not XAU/USD spot.",
    }


def _run_backfill(job_id: int, pair: str, years: float) -> None:
    repo.update_research_data_job(job_id, status="running", started_at=int(time()), message="Fetching Deriv history")
    attempts = []
    try:
        for source in ("deriv", "yahoo"):
            before = repo.research_candle_status(pair, TIMEFRAME).get(source, {}).get("rows", 0)

            def progress(message: str, _source=source) -> None:
                current = repo.research_candle_status(pair, TIMEFRAME).get(_source, {}).get("rows", 0)
                repo.update_research_data_job(job_id, source=_source, written=int(current), message=message)

            result = backfill.backfill(
                pair, TIMEFRAME, years=years, progress=progress,
                sleep_between=0.1, source=source,
            )
            after = repo.research_candle_status(pair, TIMEFRAME).get(source, {}).get("rows", 0)
            attempts.append({
                "source": source, "written": result.written, "available_rows": int(after),
                "days": result.days_after, "errors": result.errors,
            })
            repo.update_research_data_job(
                job_id, source=source, written=int(after),
                message=f"{source.title()} history: {after} candles available",
            )
            # Use a complete provider-specific series. Never splice a futures
            # proxy into spot history, or vice versa.
            if after >= MIN_FORECAST_CANDLES:
                break
            # A stale Deriv cache that is already sufficient should not trigger
            # a Yahoo fetch merely because this particular request added none.
            if source == "deriv" and max(int(before), int(after)) >= MIN_FORECAST_CANDLES:
                break

        final = history_status(pair)
        selected = final["selected_source"]
        message = (
            f"Ready for a preliminary forecast using {selected} history"
            if selected else f"History fetched; need {MIN_FORECAST_CANDLES} candles for forecast metrics"
        )
        repo.update_research_data_job(
            job_id, status="completed", source=selected or (attempts[-1]["source"] if attempts else None),
            written=sum(item["written"] for item in attempts), message=message,
            result_json={"attempts": attempts, "history": final}, completed_at=int(time()),
        )
    except Exception as exc:  # noqa: BLE001 - report provider errors in the research UI
        repo.update_research_data_job(
            job_id, status="failed", error=str(exc),
            result_json={"attempts": attempts}, message="Historical data fetch failed", completed_at=int(time()),
        )


def start_backfill(pair: str = PAIR_SCOPE, years: float = DEFAULT_YEARS) -> dict:
    if pair != PAIR_SCOPE:
        raise ValueError("historical research backfill is scoped to XAU/USD")
    if not 0.1 <= years <= 5.0:
        raise ValueError("years must be between 0.1 and 5")
    with _LOCK:
        if repo.active_research_forecast_run(pair):
            raise ValueError("a forecast is running; wait for it to finish before fetching history")
        active = repo.active_research_data_job(pair)
        if active:
            return active
        job_id = repo.create_research_data_job(pair, TIMEFRAME, years)
        _EXECUTOR.submit(_run_backfill, job_id, pair, years)
    return repo.get_research_data_job(job_id) or {"id": job_id, "status": "queued"}


def get_backfill(job_id: int) -> dict | None:
    return repo.get_research_data_job(job_id)


def list_backfills(pair: str = PAIR_SCOPE, limit: int = 20) -> list[dict]:
    return repo.list_research_data_jobs(pair, limit)
