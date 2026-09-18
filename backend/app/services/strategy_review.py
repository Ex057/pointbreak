"""End-of-day strategy analyst.

Takes the structured what-if `EodReview` (per-gate profit-left-on-the-table),
hands the *facts* to a local Claude agent, and asks it to (a) name the concrete
strategy flaws and (b) propose bounded parameter adjustments as strict JSON. Each
proposal is then **shadow-tested** against the same day's deterministic replays
so we can show its expected impact before anyone approves it.

Nothing here changes a gate. Recommendations are advisory; the planner only reads
a parameter override once a human clicks *Approve* (see main.py).
"""
from __future__ import annotations

import json
import logging

from ..config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, ARM_SCORE_MIN, MIN_RR
from ..db import repo

log = logging.getLogger("tradeguard.strategy_review")

# Tunable parameters, the veto gate each one governs, and the SAFE bounds a
# recommendation must fall within. A proposal outside its bounds is rejected.
PARAM_SPECS = {
    "min_rr": {
        "gate": "rr_below_min",
        "bounds": (1.0, 3.0),
        "label": "Minimum R:R",
        "desc": "Lower it to admit setups whose nearest obstacle caps reward below the current floor.",
    },
    "arm_score_min": {
        "gate": "blocked_score",
        "bounds": (0.30, 0.72),
        "label": "Minimum arm score",
        "desc": "Lower it to admit lower-scored setups the monitor currently blocks.",
    },
    "max_positions": {
        "gate": "blocked_max_active",
        "bounds": (1.0, 6.0),
        "label": "Max concurrent positions",
        "desc": "Raise it to admit setups blocked because the active-position cap was full.",
    },
    "impulse_body_mult": {
        "gate": None,
        "bounds": (1.2, 2.2),
        "label": "Impulse body multiplier",
        "desc": "Lower it to detect more supply/demand bases (upstream of the gates; not shadow-testable).",
    },
    "counter_trend": {
        # a toggle (0/1) that governs BOTH the structure and MA-bias vetoes; set it
        # to 1 to admit counter-trend reversals that still clear a real trigger.
        "gate": "_counter_trend",  # special: aggregates structure_conflict + ma_bias_conflict
        "bounds": (0.0, 1.0),
        "label": "Allow counter-trend reversals",
        "desc": "Set to 1 to stop vetoing swept-level reversals against the trend (they still need a confirmed trigger).",
    },
    "sweep_reclaim": {
        # a toggle (0/1) that turns on live detection + auto-arming of liquidity
        # sweep-reclaim setups — the fix for the NO-CANDIDATE blind spots.
        "gate": "_detection_blind",  # special: aggregates the missed-move blind spots
        "bounds": (0.0, 1.0),
        "label": "Auto-arm sweep-reclaim reversals",
        "desc": "Set to 1 to detect and paper-arm liquidity-sweep-reclaim setups the planner never proposed (the no-candidate blind spots).",
    },
}

# The counter-trend toggle unlocks these two gates at once.
_COUNTER_TREND_GATES = ("structure_conflict", "ma_bias_conflict")


def current_params(pair: str) -> dict:
    """Effective current values (config defaults, overridden by any approved
    param override for this pair or 'global')."""
    settings = repo.get_user_settings()
    overrides = repo.get_param_overrides(pair)
    values = {
        "min_rr": float(settings.get("min_rr") or MIN_RR),
        "arm_score_min": float(ARM_SCORE_MIN),
        "max_positions": float(settings.get("max_positions") or 3),
        "impulse_body_mult": 1.6,
        "counter_trend": 0.0,
        "sweep_reclaim": 0.0,  # opt-in; the analyst may recommend enabling it
    }
    values.update({k: float(v) for k, v in overrides.items() if k in values})
    return values


def _shadow_test(review: dict, param: str, proposed: float, current: float) -> tuple[float | None, int | None]:
    """Estimate the net-R impact of a proposed change from the day's deterministic
    replays. For a gate-mapped param, admitting that gate's rejected-but-filled
    setups nets `sum(+rr winners) - sum(1R losers)` = missed_r - would_have_lost.
    Returns (shadow_delta_r, shadow_added_trades); (None, None) when not mapped."""
    spec = PARAM_SPECS.get(param) or {}
    gate = spec.get("gate")
    if not gate:
        return None, None

    gates_index = {g.get("gate"): g for g in review.get("gates", [])}

    # sweep_reclaim is a detection toggle scored against the no-candidate blind spots.
    #
    # This used to return sum(blind_move["rr"]) as a *forecast*. Those rr values
    # are (peak_price - entry) / risk — the best price the move ever reached,
    # with no stop test and no target. They are what you would have made with
    # perfect foresight, and they are typically an order of magnitude above what
    # is reachable. Reported as +112.8R here once; realised -139R.
    #
    # There is no honest shadow number for a detection toggle without replaying
    # each formation path-dependently, so we return None and let the UI say so.
    if gate == "_detection_blind":
        return None, None

    # counter_trend is a toggle over two gates at once.
    if gate == "_counter_trend":
        if not (proposed >= 1 and current < 1):
            return 0.0, 0
        net = 0.0
        added = 0
        for gname in _COUNTER_TREND_GATES:
            g = gates_index.get(gname)
            if g:
                net += float(g.get("missed_r") or 0.0) - float(g.get("would_have_lost") or 0)
                added += int(g.get("filled") or 0)
        return round(net, 3), added

    # Only a *loosening* move admits more trades, so this estimator can only ever
    # score one direction. Returning 0.0 for a tightening move made "no evidence"
    # indistinguishable from "no benefit" and helped ratchet min_rr 2.0 -> 1.5 with
    # nothing able to push back. None means "not shadow-testable" — say that.
    loosening = proposed > current if param == "max_positions" else proposed < current
    if not loosening:
        return None, None
    g = gates_index.get(gate)
    if g:
        net = round(float(g.get("missed_r") or 0.0) - float(g.get("would_have_lost") or 0), 3)
        return net, int(g.get("filled") or 0)
    return 0.0, 0


def _deterministic_recommendations(pair: str, review: dict, params: dict) -> list[dict]:
    """Fallback analyst (no LLM): propose a bounded loosening for every flagged
    gate straight from the counterfactual stats."""
    recs: list[dict] = []
    gate_to_param = {
        spec["gate"]: name for name, spec in PARAM_SPECS.items()
        if spec["gate"] and spec["gate"] != "_counter_trend"
    }

    # counter-trend: one toggle covers both structure and MA-bias vetoes
    ct_flaw = any(
        g.get("flaw") and g.get("gate") in _COUNTER_TREND_GATES for g in review.get("gates", [])
    )
    if ct_flaw and params.get("counter_trend", 0) < 1:
        ct_missed = sum(
            float(g.get("missed_r") or 0.0)
            for g in review.get("gates", []) if g.get("gate") in _COUNTER_TREND_GATES
        )
        recs.append({
            "param": "counter_trend",
            "current_value": params["counter_trend"],
            "proposed_value": 1.0,
            "rationale": "Swept-level reversals against the trend were vetoed but would have won — allow counter-trend reversals (they still need a confirmed trigger).",
            "expected_added_trades": None,
            "expected_r_impact": round(ct_missed, 2),
            "confidence": "medium",
        })

    # detection blind spots (no candidate) → recommend turning on sweep-reclaim auto-arm
    blind = [m for m in review.get("missed_moves", []) if m.get("coverage_kind") == "none"]
    if blind and params.get("sweep_reclaim", 0) < 1:
        recs.append({
            "param": "sweep_reclaim",
            "current_value": params["sweep_reclaim"],
            "proposed_value": 1.0,
            "rationale": f"{len(blind)} tradable move(s) got no candidate at all — all liquidity-sweep-reclaims. Enable live sweep-reclaim detection so these are proposed and paper-armed as they form.",
            "expected_added_trades": len(blind),
            "expected_r_impact": round(sum(float(m.get("rr") or 0.0) for m in blind), 2),
            "confidence": "medium",
        })

    for g in review.get("gates", []):
        if not g.get("flaw"):
            continue
        param = gate_to_param.get(g.get("gate"))
        if not param:
            continue
        current = params[param]
        lo, hi = PARAM_SPECS[param]["bounds"]
        if param == "max_positions":
            proposed = min(hi, current + 1)
        elif param == "min_rr":
            proposed = max(lo, round(current - 0.25, 2))
        else:  # arm_score_min
            proposed = max(lo, round(current - 0.05, 3))
        if proposed == current:
            continue
        recs.append({
            "param": param,
            "current_value": current,
            "proposed_value": proposed,
            "rationale": g.get("note") or f"{PARAM_SPECS[param]['label']} looks too strict today.",
            "expected_added_trades": int(g.get("filled") or 0),
            "expected_r_impact": float(g.get("missed_r") or 0.0),
            "confidence": "medium",
        })
    return recs


def _claude_recommendations(pair: str, review: dict, params: dict) -> list[dict] | None:
    if not ANTHROPIC_API_KEY:
        return None
    import anthropic  # lazy import

    schema = {
        "type": "object",
        "properties": {
            "flaws_summary": {"type": "string"},
            "recommendations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "param": {"type": "string", "enum": list(PARAM_SPECS.keys())},
                        "proposed_value": {"type": "number"},
                        "rationale": {"type": "string"},
                        "expected_added_trades": {"type": "integer"},
                        "expected_r_impact": {"type": "number"},
                        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    },
                    "required": ["param", "proposed_value", "rationale", "confidence"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["flaws_summary", "recommendations"],
        "additionalProperties": False,
    }
    payload = {
        "pair": pair,
        "review": {k: review[k] for k in ("day", "realized_r", "missed_r", "avoided_loss_r",
                                          "rejections_total", "rejections_scored", "gates",
                                          "missed_winners", "flaws", "missed_moves",
                                          "missed_moves_blind", "biggest_blind_r") if k in review},
        "detection_note": (
            "missed_moves are real swings that made >= min R; coverage_kind='none' means the "
            "planner never proposed a candidate there (a DETECTION blind spot, not a veto). "
            "These can't be fixed by loosening a gate — call them out in flaws_summary and, if "
            "they cluster on reversals/swept lows, say the zone detection or trend filter is the cause."
        ),
        "current_params": params,
        "param_specs": {
            name: {"bounds": spec["bounds"], "label": spec["label"], "desc": spec["desc"]}
            for name, spec in PARAM_SPECS.items()
        },
    }
    system = (
        "You are a trading-strategy analyst reviewing an end-of-day counterfactual "
        "backtest of a deterministic setup planner. The planner vetoed setups today; "
        "we replayed each rejection against real prices to measure profit left on the "
        "table per veto gate. Identify the strategy's real flaws and propose ONLY "
        "bounded parameter adjustments (each within its stated bounds). Never invent "
        "prices. Never propose trades. Prefer fewer, higher-confidence changes. If no "
        "gate is systematically too strict, return an empty recommendations list."
    )
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=2048,
        system=system,
        thinking={"type": "adaptive"},
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": json.dumps(payload)}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    data = json.loads(text)
    recs: list[dict] = []
    for item in data.get("recommendations", []):
        param = item.get("param")
        if param not in PARAM_SPECS:
            continue
        recs.append({
            "param": param,
            "current_value": params[param],
            "proposed_value": float(item["proposed_value"]),
            "rationale": item.get("rationale"),
            "expected_added_trades": item.get("expected_added_trades"),
            "expected_r_impact": item.get("expected_r_impact"),
            "confidence": item.get("confidence", "medium"),
        })
    return recs


def run(pair: str, review: dict) -> dict:
    """Produce shadow-tested recommendations for the given EodReview. Uses Claude
    when a key is configured, else a deterministic fallback analyst. Recommendations
    are validated against bounds and shadow-tested; nothing is applied."""
    params = current_params(pair)
    provider = "builtin"
    recs: list[dict] | None = None
    try:
        recs = _claude_recommendations(pair, review, params)
        if recs is not None:
            provider = "claude"
    except Exception:  # noqa: BLE001 - analyst must degrade to the deterministic path
        recs = None
    if recs is None:
        recs = _deterministic_recommendations(pair, review, params)

    validated: list[dict] = []
    for rec in recs:
        param = rec["param"]
        lo, hi = PARAM_SPECS[param]["bounds"]
        proposed = rec["proposed_value"]
        if proposed is None or not (lo <= proposed <= hi):
            continue  # reject out-of-bounds proposals
        current = params[param]
        delta_r, added = _shadow_test(review, param, proposed, current)
        rec["shadow_delta_r"] = delta_r
        rec["shadow_added_trades"] = added
        validated.append(rec)

    return {"provider": provider, "recommendations": validated}


def persist_recommendations(pair: str, recs: list[dict], review_id: int | None) -> list[dict]:
    """Persist each recommendation and return it with its DB id + status, so the
    UI's Approve/Dismiss buttons have something to act on.

    THIS FUNCTION DOES NOT APPLY ANYTHING, BY DESIGN. It used to, whenever the
    `auto_apply` setting was on, gated by nothing but a bounds check — it never
    even read `shadow_delta_r`. On 2026-07-31 that applied `sweep_reclaim 0->1`
    on a +112.8R shadow estimate derived from perfect-hindsight peak prices; the
    realised result was -139R across 145 trades, and because `_shadow_test` only
    scores *loosening* moves, nothing could ever propose the reverse.

    The only write path is now `POST /recommendations/{id}/apply` — a human
    looking at the numbers and clicking. Do not re-add an automatic branch here
    without a realised-performance gate and the ability to tighten."""
    out: list[dict] = []
    for rec in recs:
        rid = repo.create_recommendation({**rec, "pair": pair, "eod_review_id": review_id})
        out.append({**rec, "id": rid, "pair": pair, "eod_review_id": review_id,
                    "status": "proposed"})
    return out
