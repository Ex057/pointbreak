"""TradeGuard FastAPI gateway.

Client-agnostic by design: the Android app in Phase 3 is a client of this same
surface, so no response is shaped for the web.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timezone
import logging

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import DATA_SOURCE, DEFAULT_PAIR_ID, MIN_RR, RISK_PCT, TIMEFRAMES
from .db import repo
from .db.database import init_schema
from .engine.deriv_feed import stop_deriv_feed
from .services.markets import (
    build_catalog,
    fallback_catalog,
    feed_snapshot,
    get_feed_status,
    get_live_tick,
    latest_ohlc,
    live_price_value,
    load_candles,
)
from .services.monitor import monitor
from .services import ai_analysis, execution, learning, topdown, verdict as verdict_service
from .services.scan import scan as run_scan

# The pair the monitor watches on startup.
FAVOURITE_PAIR = "xauusd"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("tradeguard.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema()
    try:
        catalog, feed_status = build_catalog()
    except Exception as exc:  # pragma: no cover - exercised in API tests
        log.exception("catalog bootstrap failed, starting with fallback catalog")
        catalog, feed_status = fallback_catalog(str(exc))
    app.state.catalog = {item.id: item for item in catalog}
    app.state.feed_status = feed_status
    log.info(
        "startup complete data_source=%s feed_connected=%s resolved=%s requested=%s error=%s",
        DATA_SOURCE,
        feed_status.get("connected"),
        feed_status.get("resolved"),
        feed_status.get("requested"),
        feed_status.get("error"),
    )
    # start the always-on monitor on the favourite pair
    fav = app.state.catalog.get(FAVOURITE_PAIR) or next(iter(app.state.catalog.values()), None)
    if fav is not None:
        settings = repo.get_user_settings()
        monitor.set_favourite(fav, topdown.timeframe_roles(settings.get("trader_mode"))["entry_tf"], settings.get("trader_mode"))
        monitor.start()
    try:
        yield
    finally:
        monitor.stop()
        if DATA_SOURCE == "deriv":
            stop_deriv_feed()


app = FastAPI(title="TradeGuard API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
def _pair_entry(pair: str):
    item = app.state.catalog.get(pair)
    if item is not None:
        return item
    for market in app.state.catalog.values():
        if pair in {market.display, market.code}:
            return market
    raise HTTPException(
        404,
        {
            "code": "unknown_pair",
            "message": f"Unknown pair '{pair}'.",
        },
    )


def _load_candles(pair: str, timeframe: str):
    market = _pair_entry(pair)
    df = load_candles(market, timeframe)
    snap = feed_snapshot(market, timeframe, df)
    if df.empty or not snap["price_valid"]:
        raise HTTPException(
            503,
            {
                "code": "feed_not_ready",
                "message": snap["feed_error"] or f"Live candle feed is not ready for {market.display} {timeframe}.",
                "feed": snap,
            },
        )
    return market, df

def _market_price(market, timeframe: str) -> float:
    px = live_price_value(market)
    if px is not None:
        return round(px, 4)
    _, df = _load_candles(market.id, timeframe)
    return round(float(df["close"].iloc[-1]), 4)


def _epoch_bounds(date_from: date | None, date_to: date | None) -> tuple[int | None, int | None]:
    since = (
        int(datetime.combine(date_from, time.min, tzinfo=timezone.utc).timestamp())
        if date_from is not None
        else None
    )
    until = (
        int(datetime.combine(date_to, time.max, tzinfo=timezone.utc).timestamp())
        if date_to is not None
        else None
    )
    return since, until


def _validate_timeframe(tf: str):
    if tf not in TIMEFRAMES:
        raise HTTPException(
            404,
            {
                "code": "unknown_timeframe",
                "message": f"Unsupported timeframe '{tf}'.",
                "supported": list(TIMEFRAMES),
            },
        )


@app.get("/health")
def health():
    settings = repo.get_user_settings()
    return {
        "status": "ok",
        "data_source": DATA_SOURCE,
        "feed_status": get_feed_status(),
        "timeframes": list(TIMEFRAMES),
        "default_pair": DEFAULT_PAIR_ID,
        "default_setup_tf": topdown.timeframe_roles(settings.get("trader_mode"))["setup_tf"],
        "default_trader_mode": settings.get("trader_mode"),
        "default_ai_provider": settings.get("ai_provider"),
        "trader_modes": topdown.TRADER_MODES,
        "ai_providers": ai_analysis.provider_catalog(),
        "pairs": [
            {
                "id": market.id,
                "display": market.display,
                "code": market.code,
                "category": market.category,
                "live": market.live,
                "sanity_min": market.sanity_min,
            }
            for market in app.state.catalog.values()
        ],
    }


@app.get("/scan")
def scan_query(pair: str = Query(...), tf: str = Query(...), min_rr: float = Query(MIN_RR, ge=1.0, le=10.0)):
    _validate_timeframe(tf)
    market, df = _load_candles(pair, tf)
    return run_scan(df, pair=market.id, pair_display=market.display, timeframe=tf, min_rr=min_rr,
                    pair_category=market.category)


@app.get("/scan/{pair}/{tf}")
def scan_endpoint(pair: str, tf: str, min_rr: float = Query(MIN_RR, ge=1.0, le=10.0)):
    _validate_timeframe(tf)
    market, df = _load_candles(pair, tf)
    return run_scan(df, pair=market.id, pair_display=market.display, timeframe=tf, min_rr=min_rr,
                    pair_category=market.category)


@app.get("/verdict")
def verdict_query(pair: str = Query(...), tf: str = Query(...), min_rr: float = Query(MIN_RR, ge=1.0, le=10.0), trader_mode: str | None = Query(None), recheck_reason: str | None = Query(None)):
    _validate_timeframe(tf)
    market, df = _load_candles(pair, tf)
    result = run_scan(df, pair=market.id, pair_display=market.display, timeframe=tf, min_rr=min_rr,
                    pair_category=market.category)
    settings = repo.get_user_settings()
    selected_mode = trader_mode or settings.get("trader_mode")
    context = topdown.load_context(market, min_rr=min_rr, risk_pct=RISK_PCT, trader_mode=selected_mode,
                                   counter_trend_ok=bool(repo.get_param_overrides(market.id).get("counter_trend", 0)))
    return {
        **result,
        "timeframe_roles": topdown.timeframe_roles(selected_mode),
        "rescan": topdown.rescan_policy(
            int(result.get("generated_at") or datetime.now(timezone.utc).timestamp()),
            selected_mode,
            (context.get("plan") or {}).get("status"),
            recheck_reason,
        ),
        "feed": feed_snapshot(market, tf, df),
        "verdict": verdict_service.build(result, min_rr=min_rr, topdown_context=context, ai_provider=settings.get("ai_provider")),
    }


@app.get("/verdict/{pair}/{tf}")
def verdict_endpoint(pair: str, tf: str, min_rr: float = Query(MIN_RR, ge=1.0, le=10.0), trader_mode: str | None = Query(None), recheck_reason: str | None = Query(None)):
    _validate_timeframe(tf)
    market, df = _load_candles(pair, tf)
    result = run_scan(df, pair=market.id, pair_display=market.display, timeframe=tf, min_rr=min_rr,
                    pair_category=market.category)
    settings = repo.get_user_settings()
    selected_mode = trader_mode or settings.get("trader_mode")
    context = topdown.load_context(market, min_rr=min_rr, risk_pct=RISK_PCT, trader_mode=selected_mode,
                                   counter_trend_ok=bool(repo.get_param_overrides(market.id).get("counter_trend", 0)))
    return {
        **result,
        "timeframe_roles": topdown.timeframe_roles(selected_mode),
        "rescan": topdown.rescan_policy(
            int(result.get("generated_at") or datetime.now(timezone.utc).timestamp()),
            selected_mode,
            (context.get("plan") or {}).get("status"),
            recheck_reason,
        ),
        "feed": feed_snapshot(market, tf, df),
        "verdict": verdict_service.build(result, min_rr=min_rr, topdown_context=context, ai_provider=settings.get("ai_provider")),
    }


# ---------------------------------------------------------------- live price (tick)
@app.get("/price")
def price(pair: str = Query(...), tf: str | None = Query(None)):
    """The real-time streamed TICK for a pair — distinct from the candle close
    that /scan reports. Poll this for a live-updating price. Returns
    source='tick' with the quote, or source='none' when no live tick is
    available (fixture mode, market closed, or the feed is on another pair)."""
    market = _pair_entry(pair)
    tick = get_live_tick(market)
    active_tf = tf or monitor.status().get("timeframe") or topdown.timeframe_roles(repo.get_user_settings().get("trader_mode"))["entry_tf"]
    if tick is None:
        return {"pair": market.id, "source": "none", "price": None, "ohlc": latest_ohlc(market, active_tf)}
    return {"pair": market.id, **tick, "ohlc": latest_ohlc(market, active_tf)}  # tick carries its own source (deriv|yahoo)


@app.websocket("/ws/price")
async def price_stream(ws: WebSocket, pair: str, tf: str | None = None):
    try:
        market = _pair_entry(pair)
        await ws.accept()
        last_payload = None
        active_tf = tf or monitor.status().get("timeframe") or topdown.timeframe_roles(repo.get_user_settings().get("trader_mode"))["entry_tf"]
        while True:
            tick = get_live_tick(market) or {"pair": market.id, "source": "none", "price": None}
            payload = {"pair": market.id, **tick, "ohlc": latest_ohlc(market, active_tf)}
            if payload != last_payload:
                await ws.send_json(payload)
                last_payload = payload
            await asyncio.sleep(0.35)
    except WebSocketDisconnect:
        return
    except HTTPException:
        await ws.close(code=1008)
        return
    except Exception:
        await ws.close(code=1011)
        return


# ---------------------------------------------------------------- monitor / alerts / stats
@app.get("/monitor")
def monitor_status():
    try:
        return monitor.status()
    except Exception as exc:
        log.exception("monitor status endpoint failed")
        roles = topdown.timeframe_roles(repo.get_user_settings().get("trader_mode"))
        return {
            "pair": None,
            "pair_display": None,
            "trader_mode": roles["trader_mode"],
            "timeframe": roles["entry_tf"],
            "bias_tf": roles["bias_tf"],
            "setup_tf": roles["setup_tf"],
            "entry_tf": roles["entry_tf"],
            "running": False,
            "active_alerts": 0,
            "error": f"monitor status degraded: {exc}",
        }


@app.put("/monitor")
def set_monitor(pair: str = Query(...), tf: str = Query(...), trader_mode: str | None = Query(None)):
    _validate_timeframe(tf)
    market = _pair_entry(pair)
    selected_mode = trader_mode or repo.get_user_settings().get("trader_mode")
    monitor.set_favourite(market, tf, selected_mode)
    monitor.start()
    return monitor.status()


@app.get("/settings")
def settings():
    current = repo.get_user_settings()
    return {"settings": current, "trader_modes": topdown.TRADER_MODES, "ai_providers": ai_analysis.provider_catalog()}


class UpdateSettings(BaseModel):
    trader_mode: str | None = None
    ai_provider: str | None = None
    auto_apply: bool | None = None


@app.put("/settings")
def update_settings(body: UpdateSettings):
    fields = {}
    if body.trader_mode is not None:
        fields["trader_mode"] = topdown.normalize_mode(body.trader_mode)
    if body.ai_provider is not None:
        fields["ai_provider"] = ai_analysis.normalize_provider(body.ai_provider)
    if body.auto_apply:
        # The analyst is propose-only. It once auto-applied a +112.8R hindsight
        # estimate that realised -139R, and could only ever loosen filters.
        raise HTTPException(400, {
            "code": "auto_apply_removed",
            "message": "Recommendations are propose-only. Approve them individually "
                       "via POST /recommendations/{id}/apply.",
        })
    if body.auto_apply is not None:
        fields["auto_apply"] = 0
    settings = repo.update_user_settings(**fields)
    return {"settings": settings, "trader_modes": topdown.TRADER_MODES, "ai_providers": ai_analysis.provider_catalog()}


@app.get("/ai-analysis")
def ai_analyst(
    pair: str = Query(...),
    tf: str = Query(...),
    trader_mode: str | None = Query(None),
    force_refresh: bool = Query(False),
):
    _validate_timeframe(tf)
    market, df = _load_candles(pair, tf)
    settings = repo.get_user_settings()
    selected_mode = trader_mode or settings.get("trader_mode")
    context = topdown.load_context(market, min_rr=MIN_RR, risk_pct=RISK_PCT, trader_mode=selected_mode,
                                   counter_trend_ok=bool(repo.get_param_overrides(market.id).get("counter_trend", 0)))
    result = run_scan(df, pair=market.id, pair_display=market.display, timeframe=tf, min_rr=MIN_RR, pair_category=market.category)
    verdict = verdict_service.build(result, min_rr=MIN_RR, topdown_context=context, ai_provider=settings.get("ai_provider"))
    journal = repo.list_setup_journal(market.id, limit=80)
    closed = repo.closed_ledger(market.id)
    learning_state = learning.ensure_current_model(market.id, force_refresh=force_refresh)
    learned_score = learning.score_plan(market.id, verdict.get("plan"), learning_state)
    analysis = ai_analysis.analyze(
        market.id,
        verdict["state"],
        verdict.get("plan"),
        verdict.get("reasons", []),
        verdict.get("macro"),
        provider=settings.get("ai_provider"),
        recent_closed=closed,
        recent_journal=journal,
        learned_score=learned_score,
        learning_status=learning_state,
    )
    # Surface the most recent end-of-day detection blind spots so the analyst can
    # flag "moves seen too late / never proposed" right in the live panel.
    latest_review = repo.latest_eod_review(market.id)
    if latest_review:
        rv = latest_review.get("review") or {}
        blind = [m for m in (rv.get("missed_moves") or []) if m.get("coverage_kind") == "none"]
        analysis["detection_gaps"] = {
            "day": latest_review.get("day"),
            "blind_count": len(blind),
            "biggest_blind_r": rv.get("biggest_blind_r", 0.0),
            "top": blind[:3],
        }
    return {
        "pair": market.id,
        "pair_display": market.display,
        "timeframe": tf,
        "trader_mode": selected_mode,
        "generated_at": int(datetime.now(timezone.utc).timestamp()),
        "force_refresh": force_refresh,
        "analysis": analysis,
    }


@app.get("/learning/status")
def learning_status(pair: str = Query(...), force_refresh: bool = Query(False)):
    market = _pair_entry(pair)
    return learning.ensure_current_model(market.id, force_refresh=force_refresh)


# ---- end-of-day what-if review + strategy recommendations -----------------
@app.get("/eod-review")
def eod_review(pair: str = Query(...), history: int = Query(5, ge=0, le=30)):
    market = _pair_entry(pair)
    latest = repo.latest_eod_review(market.id)
    return {
        "pair": market.id,
        "latest": latest,
        "history": repo.eod_review_history(market.id, limit=history) if history else [],
        "recommendations": repo.list_recommendations(market.id, status="proposed"),
    }


@app.post("/eod-review")
def run_eod_review(pair: str = Query(...), day: str | None = Query(None)):
    """Run the end-of-day counterfactual review + strategy analyst on demand.
    Replays the day's rejected setups against real prices, attributes profit left
    on the table per veto gate, and stores advisory (not applied) recommendations."""
    market = _pair_entry(pair)
    try:
        result = monitor.run_eod_review(market.id, day=day)
    except Exception as exc:  # noqa: BLE001 - surface a clean error to the UI
        raise HTTPException(500, {"code": "eod_review_failed", "message": str(exc)})
    return result


@app.post("/eod-review/scan-history")
def scan_history(pair: str = Query(...), days: int = Query(60, ge=1, le=365)):
    """Backfill: replay ALL recorded history for the pair, day by day, and return
    every missed opportunity (no-candidate blind spots + seen-then-vetoed) with its
    formation and reason. Works off stored candles — no live feed required. Also
    runs the strategy analyst over the combined history and (when auto-apply is on)
    tunes the gates in one pass."""
    from .services import strategy_review, whatif
    market = _pair_entry(pair)
    settings = repo.get_user_settings()
    min_rr = float(repo.get_param_overrides(market.id).get("min_rr", settings.get("min_rr") or MIN_RR))
    try:
        result = whatif.scan_history(market.id, days=days, min_rr=min_rr)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, {"code": "scan_history_failed", "message": str(exc)})
    # analyse the combined history and persist/apply recommendations
    combined = result.pop("combined_review", None)
    result["recommendations"] = []
    if combined:
        analyst = strategy_review.run(market.id, combined)
        result["recommendations"] = strategy_review.persist_recommendations(
            market.id, analyst["recommendations"], None
        )
        result["provider"] = analyst["provider"]
    result["auto_apply"] = False
    # fold the freshly-scanned counterfactuals into the learner
    try:
        learning.ensure_current_model(market.id, force_refresh=True)
    except Exception:  # pragma: no cover
        pass
    return result


@app.get("/overrides")
def get_overrides(pair: str = Query(...)):
    """Currently-active parameter overrides (the live tuning) for a pair, plus the
    recommendations that produced them — the visibility/audit surface."""
    market = _pair_entry(pair)
    applied = [
        r for r in repo.list_recommendations(market.id)
        if r.get("status") in ("applied", "auto_applied")
    ]
    return {"pair": market.id, "overrides": repo.get_param_overrides(market.id), "applied": applied}


@app.post("/overrides/reset")
def reset_overrides(pair: str = Query(...)):
    """Kill switch: clear every override for the pair, reverting the planner/monitor
    to config defaults on the next bar."""
    market = _pair_entry(pair)
    cleared = repo.clear_all_overrides(market.id)
    return {"reset": True, "cleared": cleared, "overrides": repo.get_param_overrides(market.id)}


@app.get("/recommendations")
def recommendations(pair: str | None = Query(None), status: str | None = Query(None)):
    target = pair
    if target:
        target = _pair_entry(target).id
    return {"recommendations": repo.list_recommendations(target, status=status)}


@app.post("/recommendations/{rec_id}/apply")
def apply_recommendation(rec_id: int):
    """Approve a recommendation: write a parameter override the planner reads.
    This is the ONLY path by which a what-if recommendation changes a live gate,
    and only on this explicit human action."""
    rec = repo.get_recommendation(rec_id)
    if rec is None:
        raise HTTPException(404, {"code": "unknown_recommendation", "message": f"No recommendation {rec_id}"})
    if rec.get("status") == "applied":
        raise HTTPException(409, {"code": "already_applied", "message": "Recommendation already applied"})
    repo.set_param_override(rec["pair"], rec["param"], float(rec["proposed_value"]), source_recommendation_id=rec_id)
    repo.update_recommendation_status(rec_id, "applied")
    return {
        "applied": True,
        "recommendation": repo.get_recommendation(rec_id),
        "effective_params": repo.get_param_overrides(rec["pair"]),
    }


@app.post("/recommendations/{rec_id}/dismiss")
def dismiss_recommendation(rec_id: int):
    rec = repo.get_recommendation(rec_id)
    if rec is None:
        raise HTTPException(404, {"code": "unknown_recommendation", "message": f"No recommendation {rec_id}"})
    repo.update_recommendation_status(rec_id, "dismissed")
    return {"dismissed": True, "recommendation": repo.get_recommendation(rec_id)}


@app.get("/alerts")
def alerts(
    pair: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=100),
):
    paged = repo.list_alerts_page(pair, page, per_page)
    return {
        "alerts": [
            {**a, "events": repo.alert_events(a["id"])[-8:]}
            for a in paged["items"]
        ],
        "pagination": {
            "page": paged["page"],
            "per_page": paged["per_page"],
            "total": paged["total"],
            "total_pages": paged["total_pages"],
        },
    }


@app.get("/alerts/{alert_id}")
def alert_detail(alert_id: int):
    events = repo.alert_events(alert_id)
    if not events:
        raise HTTPException(404, {"code": "unknown_alert", "message": f"No alert {alert_id}"})
    return {"id": alert_id, "events": events}


@app.get("/stats")
def stats(
    pair: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
):
    target_pair = pair or monitor.status().get("pair")
    since, until = _epoch_bounds(date_from, date_to)
    account = repo.get_account(target_pair) if target_pair else None
    open_pos = repo.open_positions(target_pair) if target_pair else []
    # floating P&L of open positions, in the same 1%-risk units as closed trades
    floating_r = round(sum(p.get("unrealized_r") or 0.0 for p in open_pos), 3)
    risk_amount = (account["balance"] * RISK_PCT / 100.0) if account else 0.0
    floating_cash = round(floating_r * risk_amount, 2)
    return {
        "stats": repo.stats(target_pair, since, until),
        "account": account,
        "equity": repo.equity_curve(target_pair) if target_pair else [],
        "open": open_pos,
        "closed": repo.closed_ledger(target_pair, since, until) if target_pair else [],
        "journal": repo.list_setup_journal(target_pair, since, until, limit=60) if target_pair else [],
        "floating_r": floating_r,
        "floating_cash": floating_cash,
        "equity_float": round((account["balance"] + floating_cash), 2) if account else None,
    }


@app.get("/journal")
def journal(
    pair: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    target_pair = pair or monitor.status().get("pair")
    since, until = _epoch_bounds(date_from, date_to)
    return {
        "pair": target_pair,
        "items": repo.list_setup_journal(target_pair, since, until, limit=limit) if target_pair else [],
    }


@app.get("/account")
def account(pair: str | None = Query(None)):
    target_pair = pair or monitor.status().get("pair")
    if not target_pair:
        raise HTTPException(404, {"code": "no_pair", "message": "No monitored pair"})
    return {
        "account": repo.get_account(target_pair),
        "equity": repo.equity_curve(target_pair),
    }


# ---------------------------------------------------------------- paper trades
class TakeTrade(BaseModel):
    pair: str
    tf: str
    side: str            # "long" | "short"
    stop: float
    target: float
    source: str = "manual"


class CreateConnector(BaseModel):
    name: str
    url: str
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    enabled: bool = True


@app.post("/trades")
def take_trade(body: TakeTrade):
    """Open a PAPER position at the current market price. TradeGuard never places
    a real order — this only moves the demo ledger and hands the position to the
    monitor's turtle-soup state machine to track to TARGET/STOPPED."""
    _validate_timeframe(body.tf)
    if body.side not in ("long", "short"):
        raise HTTPException(422, {"code": "bad_side", "message": "side must be 'long' or 'short'"})
    market = _pair_entry(body.pair)
    entry = _market_price(market, body.tf)

    # sanity: the plan must be a real trade at the current price
    ok = (body.stop < entry < body.target) if body.side == "long" else (body.target < entry < body.stop)
    if not ok:
        raise HTTPException(422, {
            "code": "invalid_levels",
            "message": f"For a {body.side} at {entry}, need "
                       + ("stop < entry < target" if body.side == "long" else "target < entry < stop")
                       + f" (got stop={body.stop}, target={body.target}).",
        })

    risk = abs(entry - body.stop)
    rr = round(abs(body.target - entry) / risk, 2) if risk else 0.0
    balance = repo.get_account(market.id).get("balance", 0.0)
    size = execution.execution_size(
        market.id,
        verdict_service.position_size(entry, body.stop, balance, RISK_PCT),
    )

    try:
        alert_id = monitor.take_trade(
            market, body.tf, body.side, entry=entry, stop=body.stop, target=body.target,
            size=size, rr=rr, source=body.source)
    except ValueError as exc:
        raise HTTPException(409, {"code": "invalid_trade_levels", "message": str(exc)})
    return {
        "id": alert_id, "pair": market.id, "timeframe": body.tf, "side": body.side,
        "entry": entry, "stop": body.stop, "target": body.target,
        "size": size, "rr": rr, "state": "FILLED", "monitor": monitor.status(),
    }


@app.post("/trades/{alert_id}/close")
def close_trade(alert_id: int):
    """Close a live paper position at market, booking the actual R."""
    status = monitor.status()
    target_pair = status.get("pair")
    if not target_pair:
        raise HTTPException(409, {"code": "no_pair", "message": "Monitor is idle"})
    market = _pair_entry(target_pair)
    price = _market_price(market, status.get("timeframe") or FAVOURITE_TF)
    try:
        return monitor.close_trade(alert_id, price)
    except KeyError:
        raise HTTPException(404, {"code": "unknown_alert",
                                  "message": f"No live paper position {alert_id} on {target_pair}"})
    except ValueError as exc:
        raise HTTPException(409, {"code": "already_closed", "message": str(exc)})


@app.get("/connectors")
def connectors():
    return {"connectors": repo.list_connectors()}


@app.post("/connectors", status_code=201)
def create_connector(body: CreateConnector):
    connector_id = repo.create_connector(body.model_dump())
    row = next((c for c in repo.list_connectors() if c["id"] == connector_id), None)
    return row or {"id": connector_id}


@app.delete("/connectors/{connector_id}", status_code=204)
def delete_connector(connector_id: int):
    deleted = repo.delete_connector(connector_id)
    if not deleted:
        raise HTTPException(404, {"code": "unknown_connector", "message": f"No connector {connector_id}"})
