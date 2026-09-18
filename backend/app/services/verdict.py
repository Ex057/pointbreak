"""The verdict layer: WAIT / ENTER.

WAIT remains the default and the celebrated case, but it now carries the real
planner output instead of a placeholder 2R projection. The verdict is a thin
mapping layer on top of the four-gate trade planner in the repo root.
"""
from __future__ import annotations

import sys

import pandas as pd

from ..config import ACCOUNT_BALANCE, MIN_RR, RISK_PCT, ROOT_DIR
from . import ai_analysis, execution, news, storyteller, topdown

if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from plan import build_trade_plan  # noqa: E402


def position_size(entry: float, stop: float, balance: float, risk_pct: float) -> float:
    """Fixed-fractional sizing: risk a set % of the account per trade."""
    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0:
        return 0.0
    return round((balance * risk_pct / 100.0) / risk_per_unit, 4)


def build(
    scan_result: dict,
    balance: float = ACCOUNT_BALANCE,
    risk_pct: float = RISK_PCT,
    min_rr: float = MIN_RR,
    topdown_context: dict | None = None,
    ai_provider: str | None = None,
) -> dict:
    pair = scan_result["pair"]
    structure = scan_result.get("structure")

    def _structure_line() -> list[str]:
        if not structure or structure["state"] == "UNDETERMINED":
            return []
        label = structure["state"].capitalize()
        if structure["state"] == "RANGE" and structure.get("range_high") is not None:
            return [
                f"Structure: {label} — oscillating between "
                f"{structure['range_low']:.2f} and {structure['range_high']:.2f}."
            ]
        return [f"Structure: {label} — {structure['reason']}"]

    macro = news.context_for(pair)
    event = macro.get("event")

    if topdown_context and topdown_context.get("feed_blocked"):
        roles = topdown_context.get("roles", topdown.timeframe_roles())
        entry_feed = topdown_context.get("feed", {}).get(roles["entry_tf"], {})
        return {
            "state": "WAIT",
            "headline": "Verdict: WAIT — feed unresolved",
            "reasons": [
                topdown_context.get("feed_error") or "Feed integrity failed.",
                "No new entries until the live price and candle stream agree.",
            ],
            "plan": {
                "status": "WAITING",
                "side": None,
                "entry": None,
                "stop": None,
                "target": None,
                "rr": None,
                "size": None,
                "risk_pct": risk_pct,
                "target_basis": None,
                "notes": [],
                "alternatives": [],
                "checklist": [],
                "trader_mode": roles["trader_mode"],
                "bias_tf": roles["bias_tf"],
                "setup_tf": roles["setup_tf"],
                "entry_tf": roles["entry_tf"],
                "feed_source": entry_feed.get("feed_source"),
                "resolved_symbol": entry_feed.get("resolved_symbol"),
                "price_valid": False,
                "feed_error": topdown_context.get("feed_error"),
                "confirmation_state": "FEED_BLOCKED",
            },
            "news": event,
            "macro": macro,
            "ai_analysis": ai_analysis.build(
                pair,
                "WAIT",
                {
                    "status": "WAITING",
                    "side": None,
                    "entry": None,
                    "stop": None,
                    "target": None,
                    "zone_min": None,
                    "zone_max": None,
                    "notes": [],
                    "alternatives": [],
                    "checklist": [],
                    "feed_error": topdown_context.get("feed_error"),
                },
                [
                    topdown_context.get("feed_error") or "Feed integrity failed.",
                    "No new entries until the live price and candle stream agree.",
                ],
                macro,
                provider=ai_provider,
            ),
            "story": storyteller.build(
                scan_result, None, None, min_rr=min_rr, topdown_context=topdown_context
            ),
        }

    if topdown_context and topdown_context.get("plan"):
        trade_plan = topdown_context["plan"]
        entry = (topdown_context.get("setup_scan", {}).get("future", {}).get("entries") or [None])[0] or {}
    else:
        candles = pd.DataFrame(scan_result["candles"])
        analysis = _analysis_adapter(scan_result)
        entry = analysis.get("entry") or {}
        trade_plan = build_trade_plan(
            candles,
            analysis,
            account_balance=balance,
            risk_pct=risk_pct,
            min_rr=min_rr,
        )
    plan_payload = _plan_payload(pair, trade_plan, risk_pct, entry)
    state = _verdict_state(trade_plan["status"])

    if not scan_result["future"]["entries"] and not (topdown_context and topdown_context.get("plan")):
        reasons = _structure_line() + [
            "No zone met the confluence threshold on this timeframe.",
            "Nothing to plan yet — the scan re-runs on the next bar.",
        ]
        return {
            "state": "WAIT",
            "headline": "Verdict: WAIT",
            "reasons": reasons,
            "plan": None,
            "news": event,
            "macro": macro,
            "ai_analysis": ai_analysis.build(pair, "WAIT", None, reasons, macro, provider=ai_provider),
            "story": storyteller.build(
                scan_result, None, None, min_rr=min_rr, topdown_context=topdown_context
            ),
        }

    reasons = _structure_line() + _reason_lines(trade_plan)
    return {
        "state": state,
        "headline": _headline(state, trade_plan),
        "reasons": reasons,
        "plan": plan_payload,
        "news": event,
        "macro": macro,
        "ai_analysis": ai_analysis.build(pair, state, plan_payload, reasons, macro, provider=ai_provider),
        "story": storyteller.build(
            scan_result, trade_plan, entry, min_rr=min_rr, topdown_context=topdown_context
        ),
    }


def _analysis_adapter(scan_result: dict) -> dict:
    entries = scan_result["future"]["entries"]
    return {
        "structure": scan_result.get("structure", {}).get("state"),
        "bias": scan_result["future"].get("bias"),
        "entry": entries[0] if entries else None,
        "sr_zones": scan_result["layers"].get("sr", []),
        "supply_demand": scan_result["layers"].get("supply_demand", []),
        "fvgs": scan_result["layers"].get("fvg", []),
        "patterns": scan_result["layers"].get("patterns_all", []),
    }


def _verdict_state(plan_status: str) -> str:
    if plan_status == "ENTER_READY":
        return "ENTER"
    return "WAIT"


def _headline(state: str, trade_plan: dict) -> str:
    if state == "ENTER":
        return f"Verdict: {'BUY NOW' if trade_plan.get('side') == 'long' else 'SELL NOW'}"
    if trade_plan["status"] == "DISCARDED":
        return "Verdict: WAIT — trade vetoed"
    return "Verdict: WAIT"


def _reason_lines(trade_plan: dict) -> list[str]:
    lines = []
    if trade_plan.get("reason"):
        lines.append(trade_plan["reason"].capitalize())
    if trade_plan["status"] == "WAITING" and trade_plan.get("entry") is not None:
        lines.append(f"Planned entry stays parked at {trade_plan['entry']:.2f} until a trigger confirms.")
    elif trade_plan.get("target_basis") and trade_plan.get("target") is not None:
        lines.append(f"Target basis: {trade_plan['target_basis']} @ {trade_plan['target']:.2f}.")
    for note in trade_plan.get("notes", [])[:2]:
        lines.append(note[:1].upper() + note[1:])
    return lines


def _plan_payload(pair: str, trade_plan: dict, risk_pct: float, entry: dict) -> dict | None:
    if trade_plan.get("entry") is None and not trade_plan.get("feed_error"):
        return None
    instruction = _instruction(trade_plan)
    stop_metrics = execution.distance_metrics(pair, trade_plan.get("entry"), trade_plan.get("stop"))
    target_metrics = execution.distance_metrics(pair, trade_plan.get("entry"), trade_plan.get("target"))
    return {
        "status": trade_plan["status"],
        "side": trade_plan["side"],
        "source": entry.get("source"),
        "entry": round(trade_plan["entry"], 4) if trade_plan.get("entry") is not None else None,
        "zone_min": round(entry["zone_min"], 4) if entry.get("zone_min") is not None else (round(trade_plan["entry"], 4) if trade_plan.get("entry") is not None else None),
        "zone_max": round(entry["zone_max"], 4) if entry.get("zone_max") is not None else (round(trade_plan["entry"], 4) if trade_plan.get("entry") is not None else None),
        "stop": round(trade_plan["stop"], 4) if trade_plan.get("stop") is not None else None,
        "target": round(trade_plan["target"], 4) if trade_plan.get("target") is not None else None,
        "rr": trade_plan.get("rr"),
        "size": trade_plan.get("size"),
        "risk_pct": risk_pct,
        "trader_mode": trade_plan.get("trader_mode"),
        "target_basis": trade_plan.get("target_basis"),
        "stop_points": stop_metrics["points"],
        "stop_pips": stop_metrics["pips"],
        "target_points": target_metrics["points"],
        "target_pips": target_metrics["pips"],
        "notes": trade_plan.get("notes", []),
        "alternatives": trade_plan.get("alternatives", []),
        "checklist": trade_plan.get("checklist", []),
        "confluence": entry.get("confluence"),
        "score": entry.get("score"),
        "bias_tf": trade_plan.get("bias_tf"),
        "setup_tf": trade_plan.get("setup_tf"),
        "entry_tf": trade_plan.get("entry_tf"),
        "feed_source": trade_plan.get("feed_source"),
        "resolved_symbol": trade_plan.get("resolved_symbol"),
        "price_valid": trade_plan.get("price_valid", True),
        "feed_error": trade_plan.get("feed_error"),
        "confirmation_state": trade_plan.get("confirmation_state"),
        "instruction": instruction,
    }


def _instruction(trade_plan: dict) -> str:
    side = trade_plan.get("side")
    if trade_plan.get("feed_error"):
        return "WAIT — FEED CHECK REQUIRED"
    if trade_plan.get("status") == "ENTER_READY":
        return "BUY NOW" if side == "long" else "SELL NOW"
    if trade_plan.get("status") == "WAITING":
        return "WAIT FOR BUY CONFIRMATION" if side == "long" else "WAIT FOR SELL CONFIRMATION"
    if trade_plan.get("status") == "DISCARDED":
        return "DISCARD LONG SETUP" if side == "long" else "DISCARD SHORT SETUP"
    return "WAIT"
