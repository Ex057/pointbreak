"""Explanation-only AI layer for verdict presentation.

The deterministic planner remains authoritative for side, entry, stop, and
targets. This service only turns the current plan into a concise human-readable
trade explanation and can optionally use a free provider such as Ollama or
Gemini. If an external provider fails, it falls back to the built-in explainer.
"""
from __future__ import annotations

import json
import re
from time import time
from typing import Any

import httpx

from ..config import (
    AI_PROVIDER,
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    OLLAMA_MODEL,
    OLLAMA_URL,
)

SUPPORTED_PROVIDERS = ("builtin", "ollama", "gemini", "claude", "none")


def normalize_provider(provider: str | None) -> str:
    selected = (provider or AI_PROVIDER or "builtin").strip().lower()
    return selected if selected in SUPPORTED_PROVIDERS else "builtin"


def provider_catalog() -> dict[str, dict[str, Any]]:
    return {
        "builtin": {
            "label": "Built-in explainer",
            "free": True,
            "configured": True,
            "status": "ready",
            "detail": "Deterministic local explanation.",
        },
        "ollama": {
            "label": "Ollama",
            "free": True,
            "configured": bool(OLLAMA_URL),
            "status": "ready" if OLLAMA_URL else "disabled",
            "detail": f"Local model via {OLLAMA_URL.rstrip('/')}/api/generate",
            "model": OLLAMA_MODEL,
        },
        "gemini": {
            "label": "Gemini free tier",
            "free": True,
            "configured": bool(GEMINI_API_KEY),
            "status": "ready" if GEMINI_API_KEY else "disabled",
            "detail": "Developer free tier via Google AI Studio API key.",
            "model": GEMINI_MODEL,
        },
        "claude": {
            "label": "Claude (Anthropic)",
            "free": False,
            "configured": bool(ANTHROPIC_API_KEY),
            "status": "ready" if ANTHROPIC_API_KEY else "disabled",
            "detail": "Local Anthropic API key; also powers the end-of-day strategy analyst.",
            "model": ANTHROPIC_MODEL,
        },
        "none": {
            "label": "Disabled",
            "free": True,
            "configured": True,
            "status": "disabled",
            "detail": "Turns AI commentary off.",
        },
    }


def build(
    pair: str,
    verdict_state: str,
    trade_plan: dict | None,
    reasons: list[str],
    macro: dict | None = None,
    *,
    provider: str | None = None,
) -> dict:
    selected = normalize_provider(provider)
    if selected == "none":
        return {
            "provider": "none",
            "status": "disabled",
            "trade_type": "AI analysis off",
            "entry_zone": None,
            "stop_loss": None,
            "take_profit_1": None,
            "take_profit_2": None,
            "reasoning": "AI commentary is disabled in settings.",
            "why_now": [],
            "risk_note": "The planner remains active without AI commentary.",
            "generated_at": int(time()),
        }

    fallback = _builtin(pair, verdict_state, trade_plan, reasons, macro)
    if selected == "builtin":
        return fallback

    try:
        if selected == "ollama":
            return _ollama(pair, verdict_state, trade_plan, reasons, macro, fallback)
        if selected == "gemini":
            return _gemini(pair, verdict_state, trade_plan, reasons, macro, fallback)
        if selected == "claude":
            return _claude(pair, verdict_state, trade_plan, reasons, macro, fallback)
    except Exception as exc:  # noqa: BLE001 - explanation layer must degrade safely
        fallback["provider"] = selected
        fallback["status"] = "fallback"
        fallback["error"] = str(exc)
        fallback["reasoning"] = f"{fallback['reasoning']} External AI was unavailable, so the built-in explainer was used."
        return fallback
    return fallback


def analyze(
    pair: str,
    verdict_state: str,
    trade_plan: dict | None,
    reasons: list[str],
    macro: dict | None = None,
    *,
    provider: str | None = None,
    recent_closed: list[dict] | None = None,
    recent_journal: list[dict] | None = None,
    learned_score: dict | None = None,
    learning_status: dict | None = None,
) -> dict:
    base = build(pair, verdict_state, trade_plan, reasons, macro, provider=provider)
    plan = trade_plan or {}
    similar_closed, similar_journal = _similar_history(plan, recent_closed or [], recent_journal or [])
    history = _history_check(similar_closed, similar_journal)
    agreement = _agreement(verdict_state, plan, history, learned_score)
    what_is_missing = _what_is_missing(plan)
    return {
        **base,
        "agreement": agreement,
        "what_is_missing": what_is_missing,
        "history_check": history,
        "learned_score": learned_score,
        "learning_status": learning_status,
        "provider_label": provider_catalog().get(base["provider"], {}).get("label", base["provider"]),
        "trade_framing": {
            "label": base["trade_type"],
            "entry_zone": base.get("entry_zone"),
            "stop_loss": base.get("stop_loss"),
            "take_profit_1": base.get("take_profit_1"),
            "take_profit_2": base.get("take_profit_2"),
        },
        "fallback_used": base.get("status") == "fallback",
    }


def _builtin(pair: str, verdict_state: str, trade_plan: dict | None, reasons: list[str], macro: dict | None) -> dict:
    plan = trade_plan or {}
    side = plan.get("side")
    trade_type = _trade_type(plan)
    entry = _round(plan.get("entry"))
    stop = _round(plan.get("stop"))
    tp1 = _round(plan.get("target"))
    tp2 = _alt_target(plan)
    entry_zone = _entry_zone(plan)

    setup_tf = plan.get("setup_tf")
    bias_tf = plan.get("bias_tf")
    entry_tf = plan.get("entry_tf")
    target_basis = plan.get("target_basis")
    notes = [n.rstrip(".") for n in plan.get("notes", [])[:2]]
    checklist = plan.get("checklist", [])
    waiting_trigger = next((
        item.get("detail") for item in checklist
        if item.get("required", True)
        and item.get("status", "passed" if item.get("passed") else "failed") in {"pending", "failed"}
    ), None)
    macro_sentiment = (((macro or {}).get("sentiment") or {}).get("label") or "neutral").lower()

    if plan.get("status") == "ENTER_READY":
        reasoning = (
            f"{pair.upper()} has aligned {bias_tf} bias, {setup_tf} setup context, and {entry_tf} trigger confirmation. "
            f"The plan is actionable from the current zone with target flow defined by {target_basis or 'the nearest opposing liquidity'}."
        )
    elif plan.get("status") == "WAITING":
        reasoning = (
            f"{pair.upper()} has directional context in place, but the {entry_tf} trigger has not confirmed yet. "
            f"The entry remains parked while price interacts with the active zone."
        )
    elif plan.get("status") == "DISCARDED":
        reasoning = (
            f"{pair.upper()} produced a candidate, but the planner vetoed it before execution. "
            f"The setup does not currently clear the minimum quality filters."
        )
    else:
        reasoning = f"{pair.upper()} has no actionable setup right now."

    why_now = []
    if bias_tf and setup_tf and entry_tf:
        why_now.append(f"Top-down stack: {bias_tf} bias · {setup_tf} setup · {entry_tf} confirmation.")
    if target_basis:
        why_now.append(f"Target basis: {target_basis}.")
    if waiting_trigger and plan.get("status") == "WAITING":
        why_now.append(waiting_trigger[:1].upper() + waiting_trigger[1:])
    why_now.extend(note[:1].upper() + note[1:] for note in notes if note)
    if macro_sentiment != "neutral":
        why_now.append(f"Macro Pulse is {macro_sentiment}, but this remains context only.")
    if not why_now:
        why_now.extend(reasons[:2])

    risk_note = _risk_note(plan, verdict_state, waiting_trigger)

    return {
        "provider": "builtin",
        "status": "ready",
        "trade_type": trade_type,
        "entry_zone": entry_zone,
        "stop_loss": stop,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "reasoning": reasoning,
        "why_now": why_now[:4],
        "risk_note": risk_note,
        "generated_at": int(time()),
    }


def _ollama(pair: str, verdict_state: str, trade_plan: dict | None, reasons: list[str], macro: dict | None, fallback: dict) -> dict:
    prompt = _prompt_payload(pair, verdict_state, trade_plan, reasons, macro, fallback)
    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            f"{OLLAMA_URL.rstrip('/')}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "system": _system_prompt(),
                "prompt": prompt,
                "format": "json",
                "stream": False,
            },
        )
        response.raise_for_status()
        payload = response.json()
    text = payload.get("response", "")
    parsed = _parse_model_json(text)
    return _merge_model_result(parsed, fallback, "ollama")


def _gemini(pair: str, verdict_state: str, trade_plan: dict | None, reasons: list[str], macro: dict | None, fallback: dict) -> dict:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    prompt = _prompt_payload(pair, verdict_state, trade_plan, reasons, macro, fallback)
    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
            headers={
                "x-goog-api-key": GEMINI_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "generationConfig": {"responseMimeType": "application/json"},
                "contents": [
                    {
                        "parts": [
                            {"text": _system_prompt()},
                            {"text": prompt},
                        ]
                    }
                ],
            },
        )
        response.raise_for_status()
        payload = response.json()
    parts = (((payload.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    text = "".join(str(part.get("text") or "") for part in parts)
    parsed = _parse_model_json(text)
    return _merge_model_result(parsed, fallback, "gemini")


def _claude(pair: str, verdict_state: str, trade_plan: dict | None, reasons: list[str], macro: dict | None, fallback: dict) -> dict:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured.")
    import anthropic  # lazy: only required when the Claude provider is selected

    prompt = _prompt_payload(pair, verdict_state, trade_plan, reasons, macro, fallback)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        system=_system_prompt(),
        # Explanation is a light task — keep it snappy; the planner is the truth.
        thinking={"type": "disabled"},
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    parsed = _parse_model_json(text)
    return _merge_model_result(parsed, fallback, "claude")


def _system_prompt() -> str:
    return (
        "You are an explanation-only trading assistant. "
        "Never invent prices, directions, or setups. "
        "Use the provided plan as the source of truth. "
        "Return compact JSON only with keys: trade_type, reasoning, why_now, risk_note."
    )


def _prompt_payload(pair: str, verdict_state: str, trade_plan: dict | None, reasons: list[str], macro: dict | None, fallback: dict) -> str:
    payload = {
        "pair": pair,
        "verdict_state": verdict_state,
        "plan": trade_plan,
        "reasons": reasons,
        "macro": {
            "sentiment": (macro or {}).get("sentiment"),
            "event": (macro or {}).get("event"),
            "provider_status": (macro or {}).get("provider_status"),
        },
        "baseline": {
            "trade_type": fallback["trade_type"],
            "entry_zone": fallback["entry_zone"],
            "stop_loss": fallback["stop_loss"],
            "take_profit_1": fallback["take_profit_1"],
            "take_profit_2": fallback["take_profit_2"],
        },
        "instructions": [
            "Keep reasoning to 2-4 sentences.",
            "Use the exact plan prices already given; do not invent new ones.",
            "If the plan is waiting, explain what confirmation is still missing.",
            "If the plan is discarded, explain why it was vetoed.",
        ],
    }
    return json.dumps(payload, ensure_ascii=True)


def _merge_model_result(parsed: dict[str, Any], fallback: dict, provider: str) -> dict:
    merged = dict(fallback)
    merged["provider"] = provider
    merged["status"] = "ready"
    if isinstance(parsed.get("trade_type"), str) and parsed["trade_type"].strip():
        merged["trade_type"] = parsed["trade_type"].strip()
    if isinstance(parsed.get("reasoning"), str) and parsed["reasoning"].strip():
        merged["reasoning"] = parsed["reasoning"].strip()
    if isinstance(parsed.get("risk_note"), str) and parsed["risk_note"].strip():
        merged["risk_note"] = parsed["risk_note"].strip()
    if isinstance(parsed.get("why_now"), list):
        merged["why_now"] = [str(item).strip() for item in parsed["why_now"] if str(item).strip()][:4]
    return merged


def _similar_history(plan: dict, recent_closed: list[dict], recent_journal: list[dict]) -> tuple[list[dict], list[dict]]:
    side = plan.get("side")
    source = plan.get("source")
    mode = plan.get("trader_mode")

    def matches(row: dict, source_key: str) -> bool:
        if side and row.get("side") not in {side, side.upper()}:
            return False
        if mode and row.get("trader_mode") and row.get("trader_mode") != mode:
            return False
        if source and row.get(source_key) and row.get(source_key) != source:
            return False
        return True

    closed = [row for row in recent_closed if matches(row, "zone_type")][:10]
    journal = [row for row in recent_journal if matches(row, "source")][:20]
    return closed, journal


def _history_check(similar_closed: list[dict], similar_journal: list[dict]) -> dict:
    counts = {
        "closed_sample": len(similar_closed),
        "targets": sum(1 for row in similar_closed if float(row.get("result_r") or 0.0) > 0),
        "stopped": sum(1 for row in similar_closed if float(row.get("result_r") or 0.0) < 0),
        "waiting": sum(1 for row in similar_journal if row.get("status") == "waiting"),
        "retired": sum(1 for row in similar_journal if row.get("status") == "retired"),
        "discarded": sum(1 for row in similar_journal if row.get("status") == "discarded"),
    }
    sample = counts["targets"] + counts["stopped"]
    win_rate = None if sample == 0 else round(100 * counts["targets"] / sample, 1)
    if sample:
        summary = (
            f"Last {sample} similar completed setups: {counts['targets']} targets, "
            f"{counts['stopped']} stopped"
        )
        if counts["retired"]:
            summary += f", {counts['retired']} retired before entry"
        if win_rate is not None:
            summary += f" ({win_rate:.1f}% win rate)."
    elif counts["waiting"] or counts["discarded"] or counts["retired"]:
        summary = (
            f"No completed similar trades yet. Recent journal shows "
            f"{counts['waiting']} waiting, {counts['retired']} retired, {counts['discarded']} discarded."
        )
    else:
        summary = "No similar trade history recorded yet for this pair/mode."
    return {
        "summary": summary,
        "counts": counts,
        "win_rate": win_rate,
        "sample_size": sample,
    }


def _agreement(verdict_state: str, plan: dict, history: dict, learned_score: dict | None = None) -> dict[str, str]:
    status = plan.get("status")
    sample = history.get("sample_size") or 0
    win_rate = history.get("win_rate")
    if status == "DISCARDED":
        state = "disagrees"
        confidence = "high"
    elif status == "WAITING":
        state = "cautious"
        confidence = "medium"
    else:
        state = "agrees"
        confidence = "medium"

    if sample >= 4 and win_rate is not None:
        if win_rate < 35:
            state = "disagrees" if status != "ENTER_READY" else "cautious"
            confidence = "high"
        elif win_rate >= 60 and status == "ENTER_READY":
            state = "agrees"
            confidence = "high"
    # The learned score may TEMPER the verdict but no longer overrides it. It used
    # to force "disagrees" at high confidence below 40, which let an unvalidated
    # six-segment ratio table outrank the deterministic planner — inverting the
    # architecture this module's own docstring states ("the deterministic planner
    # remains authoritative"). It regains that power when it can beat a naive
    # baseline out of sample; see config.QUALITY_GATE_MODE.
    if learned_score and learned_score.get("quality_score") is not None:
        score = int(learned_score["quality_score"])
        if score < 60 and state == "agrees":
            state = "cautious"
        if score < 40:
            # An unproven model's pessimism is itself low-confidence information.
            confidence = "low"
    return {"state": state, "confidence": confidence}


def _what_is_missing(plan: dict) -> list[str]:
    status = plan.get("status")
    checklist = plan.get("checklist") or []
    if status == "WAITING":
        missing = [
            item.get("detail") for item in checklist
            if item.get("required", True)
            and item.get("status", "passed" if item.get("passed") else "failed") in {"pending", "failed"}
            and item.get("detail")
        ]
        return [str(item) for item in missing[:3]]
    if status == "DISCARDED":
        reason = plan.get("reason")
        return [reason] if reason else []
    return []


def _parse_model_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            return {}
        try:
            payload = json.loads(match.group(0))
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}


def _trade_type(plan: dict) -> str:
    side = plan.get("side")
    if side not in {"long", "short"}:
        return "WAIT"
    if plan.get("status") == "ENTER_READY":
        return "BUY NOW" if side == "long" else "SELL NOW"
    if plan.get("status") == "WAITING":
        return "BUY LIMIT" if side == "long" else "SELL LIMIT"
    if plan.get("status") == "DISCARDED":
        return "DISCARDED BUY SETUP" if side == "long" else "DISCARDED SHORT SETUP"
    return "WAIT"


def _entry_zone(plan: dict) -> dict[str, float] | None:
    entry = _round(plan.get("entry"))
    if entry is None:
        return None
    zone_min = _round(plan.get("zone_min"))
    zone_max = _round(plan.get("zone_max"))
    if zone_min is None or zone_max is None:
        zone_min = entry
        zone_max = entry
    return {"min": min(zone_min, zone_max), "max": max(zone_min, zone_max)}


def _alt_target(plan: dict) -> float | None:
    alts = plan.get("alternatives") or []
    if not alts:
        return None
    price = alts[0].get("price")
    return _round(price)


def _risk_note(plan: dict, verdict_state: str, waiting_trigger: str | None) -> str:
    if plan.get("feed_error"):
        return "Do not trust the setup until the live feed is healthy again."
    if plan.get("status") == "WAITING":
        return f"No entry until {waiting_trigger or 'the lower-timeframe trigger confirms'}."
    if plan.get("status") == "DISCARDED":
        return "Stand aside. The planner vetoed this setup before execution."
    if verdict_state == "ENTER":
        return "Use the planned stop as invalidation; do not widen it after entry."
    return "No trade should be taken from this analysis alone."


def _round(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None
