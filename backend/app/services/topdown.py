"""Top-down trade planning helpers."""
from __future__ import annotations

import pandas as pd
import sys

from ..config import ACCOUNT_BALANCE, MIN_RR, RISK_PCT, ROOT_DIR
from . import execution, markets
from .scan import scan as run_scan

if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from plan import build_trade_plan

TRADER_MODES: dict[str, dict[str, str]] = {
    "Scalper": {"bias_tf": "1H", "setup_tf": "15m", "entry_tf": "5m"},
    "Day Trader": {"bias_tf": "4H", "setup_tf": "1H", "entry_tf": "15m"},
    "Swing Trader": {"bias_tf": "1D", "setup_tf": "4H", "entry_tf": "1H"},
    "Position Trader": {"bias_tf": "1W", "setup_tf": "1D", "entry_tf": "4H"},
}
DEFAULT_TRADER_MODE = "Swing Trader"
TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1H": 3600,
    "4H": 14400,
    "1D": 86400,
    "1W": 604800,
}
LOWER_TF_WAKEUP = {
    "Scalper": "1m",
    "Day Trader": "5m",
    "Swing Trader": "15m",
    "Position Trader": None,
}


def normalize_mode(mode: str | None) -> str:
    return mode if mode in TRADER_MODES else DEFAULT_TRADER_MODE


def timeframe_roles(mode: str | None = None) -> dict[str, str]:
    selected = normalize_mode(mode)
    return {"trader_mode": selected, **TRADER_MODES[selected]}


def timeframe_seconds(tf: str) -> int:
    return TIMEFRAME_SECONDS[tf]


def _next_bar_close(epoch: int, tf: str) -> int:
    size = timeframe_seconds(tf)
    return ((epoch // size) + 1) * size


def rescan_policy(generated_at: int, mode: str | None, plan_status: str | None = None, last_reason: str | None = None) -> dict[str, int | str | bool | None]:
    roles = timeframe_roles(mode)
    entry_tf = roles["entry_tf"]
    entry_next = _next_bar_close(generated_at, entry_tf)
    stale_after = entry_next + timeframe_seconds(entry_tf)
    wakeup_tf = LOWER_TF_WAKEUP.get(roles["trader_mode"])
    wakeup_enabled = bool(wakeup_tf and plan_status == "WAITING")
    wakeup_next = _next_bar_close(generated_at, wakeup_tf) if wakeup_enabled and wakeup_tf else None
    return {
        "last_recheck_reason": last_reason or "manual_rescan",
        "next_recheck_at": entry_next,
        "stale_after": stale_after,
        "entry_tf_seconds": timeframe_seconds(entry_tf),
        "lower_tf_wakeup_enabled": wakeup_enabled,
        "lower_tf_wakeup_tf": wakeup_tf if wakeup_enabled else None,
        "lower_tf_next_recheck_at": wakeup_next,
    }


def load_context(
    market: markets.Market,
    *,
    trader_mode: str | None = None,
    account_balance: float = ACCOUNT_BALANCE,
    risk_pct: float = RISK_PCT,
    min_rr: float = MIN_RR,
    counter_trend_ok: bool = False,
) -> dict:
    roles = timeframe_roles(trader_mode)
    bias_tf = roles["bias_tf"]
    setup_tf = roles["setup_tf"]
    entry_tf = roles["entry_tf"]
    # Configure the complete top-down feed once. This prevents bias/setup/entry
    # loads from successively replacing the active Deriv subscription bundle.
    markets.prepare_live_feed(market, [bias_tf, setup_tf, entry_tf])
    scans: dict[str, dict] = {}
    frames: dict[str, pd.DataFrame] = {}
    feed: dict[str, dict] = {}

    for tf in (bias_tf, setup_tf, entry_tf):
        df = markets.load_candles(market, tf)
        frames[tf] = df
        feed[tf] = markets.feed_snapshot(market, tf, df)
        if df.empty:
            return {
                "market": market,
                "frames": frames,
                "feed": feed,
                "roles": roles,
                "feed_blocked": True,
                "feed_error": f"Live candle feed is not ready for {market.display} {tf}.",
            }
        if not feed[tf]["price_valid"]:
            return {
                "market": market,
                "frames": frames,
                "feed": feed,
                "roles": roles,
                "feed_blocked": True,
                "feed_error": feed[tf]["feed_error"],
            }
        scans[tf] = run_scan(
            df,
            pair=market.id,
            pair_display=market.display,
            timeframe=tf,
            min_rr=min_rr,
            pair_category=market.category,
        )

    setup_scan = scans[setup_tf]
    entry_scan = scans[entry_tf]
    bias_scan = scans[bias_tf]
    analysis = {
        "structure": bias_scan.get("structure", {}).get("state"),
        "bias": bias_scan.get("future", {}).get("bias"),
        "entry": setup_scan.get("future", {}).get("entries", [None])[0],
        "sr_zones": setup_scan.get("layers", {}).get("sr", []),
        "supply_demand": setup_scan.get("layers", {}).get("supply_demand", []),
        "fvgs": setup_scan.get("layers", {}).get("fvg", []),
        "patterns": entry_scan.get("layers", {}).get("patterns_all", []),
        "pattern_candles": frames[entry_tf],
    }
    plan = build_trade_plan(
        frames[setup_tf],
        analysis,
        account_balance=account_balance,
        risk_pct=risk_pct,
        min_rr=min_rr,
        ltf_candles=frames[entry_tf],
        counter_trend_ok=counter_trend_ok,
    )
    plan["trader_mode"] = roles["trader_mode"]
    plan["bias_tf"] = bias_tf
    plan["setup_tf"] = setup_tf
    plan["entry_tf"] = entry_tf
    plan["feed_source"] = feed[entry_tf]["feed_source"]
    plan["resolved_symbol"] = feed[entry_tf]["resolved_symbol"]
    plan["price_valid"] = feed[entry_tf]["price_valid"]
    plan["feed_error"] = feed[entry_tf]["feed_error"]
    plan["confirmation_state"] = plan["status"]
    plan["size"] = execution.execution_size(market.id, float(plan.get("size") or 0.0))
    return {
        "market": market,
        "frames": frames,
        "feed": feed,
        "scans": scans,
        "setup_scan": setup_scan,
        "entry_scan": entry_scan,
        "bias_scan": bias_scan,
        "analysis": analysis,
        "plan": plan,
        "roles": roles,
        "feed_blocked": False,
        "feed_error": None,
    }
