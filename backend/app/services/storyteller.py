"""The storyteller: how the identified move should play out, beat by beat.

The verdict says WHAT was decided and the four-gate checklist says WHAT WAS
CHECKED. Neither tells a trader what to *monitor for* next. This module turns the
same engine facts into an ordered narrative — reach the zone, sweep the lows,
change of character, confirmation, enter — with the actual price level to watch
on each beat, plus what kills the thesis, what to do once filled, and what the
opposite move would set up instead.

Rules this module lives by:

1. DETERMINISTIC. No model calls. Every number comes from the plan, the scan, or
   an engine primitive. If a value is missing the sentence is dropped, never
   guessed — see `_px` / `_dist` and the "hard rule" they enforce.
2. NEVER CONTRADICT THE PLANNER. Levels are quoted from the plan's own fields
   (`trigger_level`, `stop_anchor_price`, `zone_min/max`, `atr`) rather than
   recomputed, so the prose and the tiles can never disagree.
3. NO VOLUME. The candle model is OHLC only (see services/scan.py) — there is no
   volume anywhere in this codebase. Copy must never mention it.
4. NEVER RAISE. `build()` is wrapped: a prose bug must not 500 the verdict.

Each beat carries `level` + `comparator` so the frontend can advance it live
between scans (webapp/src/lib/story.ts) without a second endpoint.
"""
from __future__ import annotations

import math

from ..engine import pattern_meta
from . import execution, topdown

VERSION = 1

# Beat kinds, in the order they are told.
REACH_ZONE = "reach_zone"
SWEEP = "sweep"
CHOCH = "choch"
CONFIRMATION = "confirmation"
ENTRY = "entry"
MANAGE = "manage"

DONE, ACTIVE, PENDING, FAILED = "done", "active", "pending", "failed"

AT_OR_BELOW = "at_or_below"
AT_OR_ABOVE = "at_or_above"
CLOSE_ABOVE = "close_above"
CLOSE_BELOW = "close_below"

_PRECISE_PAIRS = {"eurusd", "gbpusd", "usdjpy"}


# --------------------------------------------------------------- formatting
def _finite(value) -> float | None:
    """None unless the value is a real, finite number."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _px(pair: str, value) -> str | None:
    """Format a price, or None when there is no honest number to print.

    Every sentence template in this module is rendered only when all of its
    `_px` calls returned non-None. That is what keeps 'None' and 'nan' out of
    the copy without a single defensive check at the call sites.
    """
    out = _finite(value)
    if out is None:
        return None
    return f"{out:,.5f}" if pair in _PRECISE_PAIRS else f"{out:,.2f}"


def _dist(pair: str, start, end) -> str | None:
    """'46.20 pts' or '46.20 pts / 462 pips' — None if either end is missing."""
    if _finite(start) is None or _finite(end) is None:
        return None
    metrics = execution.distance_metrics(pair, float(start), float(end))
    points = metrics.get("points")
    if points is None:
        return None
    text = f"{points:,.5f} pts" if pair in _PRECISE_PAIRS else f"{points:,.2f} pts"
    if metrics.get("pips"):
        text += f" / {metrics['pips']:,} pips"
    return text


def _atr_multiple(distance, atr) -> str | None:
    d, a = _finite(distance), _finite(atr)
    if d is None or a is None or a <= 0:
        return None
    return f"{d / a:.1f}x ATR"


def _bars_eta(eta_bars, timeframe) -> str | None:
    """'about 6 4H bars (~1 day)' — the wall-clock half is dropped if unknown."""
    bars = _finite(eta_bars)
    if bars is None or bars <= 0:
        return None
    bars = int(round(bars))
    text = f"about {bars} {timeframe} bar{'s' if bars != 1 else ''}" if timeframe else f"about {bars} bars"
    try:
        seconds = topdown.timeframe_seconds(timeframe) * bars
    except (KeyError, TypeError):
        return text
    if seconds >= 172800:
        return f"{text} (~{seconds // 86400} days)"
    if seconds >= 86400:
        return f"{text} (~{round(seconds / 86400)} day)"
    if seconds >= 3600:
        return f"{text} (~{round(seconds / 3600)}h)"
    return text


def _sentence(*parts: str | None) -> str:
    """Join the parts that survived formatting; used for optional clauses."""
    return " ".join(p for p in parts if p)


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def _beat(beat_id, kind, title, detail, status=PENDING, level=None,
          comparator=None, timeframe=None, optional=False) -> dict:
    return {
        "id": beat_id,
        "kind": kind,
        "title": title,
        "detail": detail,
        "status": status,
        "level": _finite(level),
        "comparator": comparator if _finite(level) is not None else None,
        "timeframe": timeframe,
        "optional": bool(optional),
    }


def _mark_active(beats: list[dict]) -> list[dict]:
    """Exactly one ACTIVE beat: the first that is neither settled nor optional.

    webapp/src/lib/story.ts re-runs this same rule after live advancement, so
    the two ends can never disagree about which beat the user is waiting on.
    """
    for beat in beats:
        if beat["status"] in (DONE, FAILED) or beat["optional"]:
            continue
        beat["status"] = ACTIVE
        break
    return beats


def _story(status, state, *, side=None, title="", situation=None, beats=None,
           invalidation=None, management=None, alternate=None) -> dict:
    return {
        "version": VERSION,
        "state": state,
        "status": status,
        "side": side,
        "title": title,
        "situation": situation or [],
        "beats": beats or [],
        "invalidation": invalidation,
        "management": management or [],
        "alternate": alternate,
    }


# --------------------------------------------------------------- entry point
def build(scan_result: dict, trade_plan: dict | None, entry: dict | None, *,
          min_rr: float, topdown_context: dict | None = None) -> dict:
    """Compose the story for whichever branch the verdict landed in."""
    try:
        context = topdown_context or {}
        if context.get("feed_blocked"):
            return _feed_blocked_story(context)
        facts = _facts(scan_result, trade_plan, entry, context, min_rr)
        if not trade_plan or trade_plan.get("side") not in ("long", "short"):
            return _no_setup_story(facts)
        return _setup_story(facts)
    except Exception:  # noqa: BLE001 - narrative must never break the verdict
        import logging

        logging.getLogger(__name__).exception("storyteller failed")
        return _story("NO_SETUP", "no_setup")


# --------------------------------------------------------------- fact layer
def _facts(scan_result: dict, trade_plan: dict | None, entry: dict | None,
           context: dict, min_rr: float) -> dict:
    """Flatten every source into one dict so no builder walks a nested path."""
    plan = trade_plan or {}
    cand = entry or {}
    roles = context.get("roles") or topdown.timeframe_roles(plan.get("trader_mode"))

    bias_scan = context.get("bias_scan") or {}
    setup_scan = context.get("setup_scan") or {}
    structure = bias_scan.get("structure") or scan_result.get("structure") or {}
    setup_structure = setup_scan.get("structure") or scan_result.get("structure") or {}

    zone_min = _finite(cand.get("zone_min")) or _finite(plan.get("zone_min"))
    zone_max = _finite(cand.get("zone_max")) or _finite(plan.get("zone_max"))
    # Mirrors alert_engine._wick_eps so the sweep level in the prose is the level
    # the state machine will actually fire on.
    eps = 0.02 * (zone_max - zone_min) if zone_min is not None and zone_max is not None else None

    trigger_detail = next(
        (row.get("detail") for row in plan.get("checklist", []) if row.get("stage") == "trigger"),
        None,
    )
    # Which gate killed it — "Market structure", "MA bias", "R:R filter", …
    veto_gate = next(
        (row.get("name") for row in plan.get("checklist", []) if not row.get("passed")),
        None,
    )

    return {
        "pair": scan_result.get("pair", ""),
        "pair_display": scan_result.get("pair_display") or scan_result.get("pair", ""),
        "price": _finite(scan_result.get("price")),
        "min_rr": min_rr,
        "trader_mode": roles.get("trader_mode"),
        "bias_tf": plan.get("bias_tf") or roles.get("bias_tf"),
        "setup_tf": plan.get("setup_tf") or roles.get("setup_tf"),
        "entry_tf": plan.get("entry_tf") or roles.get("entry_tf"),
        "structure": structure,
        # The structure string the planner actually gated on. Same source as
        # `structure` in production (topdown feeds bias_scan into both), but
        # reading it from the plan is what stops the prose from ever naming a
        # trend the veto did not use.
        "plan_structure": (plan.get("structure") or "").upper() or None,
        "setup_structure": setup_structure,
        "setup_entries": (setup_scan.get("future") or {}).get("entries")
        or (scan_result.get("future") or {}).get("entries") or [],
        "analysis": context.get("analysis"),
        "setup_frame": (context.get("frames") or {}).get(plan.get("setup_tf") or roles.get("setup_tf")),
        "status": plan.get("status"),
        "side": plan.get("side"),
        "source": cand.get("source") or "zone",
        "zone_min": zone_min,
        "zone_max": zone_max,
        "eps": eps,
        "eta_bars": _finite(cand.get("eta_bars")),
        "confluence": cand.get("confluence"),
        "entry": _finite(plan.get("entry")),
        "stop": _finite(plan.get("stop")),
        "target": _finite(plan.get("target")),
        "rr": _finite(plan.get("rr")),
        "size": _finite(plan.get("size")),
        "target_basis": plan.get("target_basis"),
        "alternatives": plan.get("alternatives", []),
        "reason": plan.get("reason") or "",
        "atr": _finite(plan.get("atr")),
        "trigger_level": _finite(plan.get("trigger_level")),
        "trigger_kind": plan.get("trigger_kind") or "none",
        "trigger_pattern": plan.get("trigger_pattern"),
        "trigger_detail": trigger_detail,
        "veto_gate": veto_gate,
        "stop_anchor": plan.get("stop_anchor"),
        "stop_anchor_price": _finite(plan.get("stop_anchor_price")),
    }


def _zone_label(source: str | None, side: str | None) -> str:
    """What to call the zone, by the layer it came from and the side we trade."""
    long_side = side == "long"
    if source == "sr":
        return "support" if long_side else "resistance"
    if source == "fvg":
        return "fair value gap"
    return "demand" if long_side else "supply"


def _zone_text(f: dict) -> str | None:
    """'the 4H demand at 3,312.00–3,318.00' — the phrase used everywhere."""
    lo, hi = _px(f["pair"], f["zone_min"]), _px(f["pair"], f["zone_max"])
    label = _zone_label(f["source"], f["side"])
    if lo is None or hi is None:
        return f"the {f['setup_tf']} {label}" if f["setup_tf"] else None
    tf = f"{f['setup_tf']} " if f["setup_tf"] else ""
    return f"the {tf}{label} at {lo}–{hi}"


def _price_vs_zone(f: dict) -> str:
    """'above' | 'inside' | 'through' — where price sits relative to the zone."""
    price, lo, hi = f["price"], f["zone_min"], f["zone_max"]
    if price is None or lo is None or hi is None:
        return "unknown"
    if lo <= price <= hi:
        return "inside"
    if f["side"] == "long":
        return "above" if price > hi else "through"
    return "below" if price < lo else "through"


# --------------------------------------------------------------- situation
def _situation(f: dict) -> list[str]:
    lines: list[str] = []

    state = (f["structure"].get("state") or "UNDETERMINED").upper()
    reason = f["structure"].get("reason")
    tf = f["bias_tf"] or "higher timeframe"
    if state == "UNDETERMINED":
        lines.append(
            f"The {tf} swing sequence is too thin to call a trend — treat this as a level trade, "
            "not a trend trade."
        )
    elif reason:
        lines.append(f"On the {tf} the structure reads {state.lower()} — {_lower_first(reason)}")
    else:
        lines.append(f"On the {tf} the structure reads {state.lower()}.")

    zone = _zone_text(f)
    where = _price_vs_zone(f)
    if zone:
        if where == "inside":
            lines.append(
                f"Price is already inside {zone} — this is the decision window, not the waiting room."
            )
        elif where in ("above", "below"):
            edge = f["zone_max"] if f["side"] == "long" else f["zone_min"]
            gap = _dist(f["pair"], f["price"], edge)
            atr_mult = _atr_multiple(abs(f["price"] - edge), f["atr"]) if f["price"] is not None and _finite(edge) is not None else None
            if gap:
                qualifier = f" ({atr_mult})" if atr_mult else ""
                lines.append(f"Price is {gap}{qualifier} {where} {zone}.")
            else:
                lines.append(f"Price has not reached {zone} yet.")
        elif where == "through":
            gap = _dist(f["pair"], f["price"], f["zone_min"] if f["side"] == "long" else f["zone_max"])
            side_word = "below" if f["side"] == "long" else "above"
            lines.append(
                _sentence(
                    f"Price has traded {gap}" if gap else "Price has traded",
                    f"{side_word} {zone} — the zone is under stress and needs a close back inside to stay valid.",
                )
            )

    eta = _bars_eta(f["eta_bars"], f["setup_tf"])
    if eta and where in ("above", "below"):
        lines.append(f"The projection has it reaching the zone in {eta}, so there is time to prepare.")

    return lines


# --------------------------------------------------------------- the beats
def _reach_zone_beat(f: dict) -> dict | None:
    zone = _zone_text(f)
    if not zone:
        return None
    edge = f["zone_max"] if f["side"] == "long" else f["zone_min"]
    where = _price_vs_zone(f)
    reached = where in ("inside", "through")

    gap = _dist(f["pair"], f["price"], edge)
    atr_mult = _atr_multiple(abs(f["price"] - edge), f["atr"]) if f["price"] is not None and _finite(edge) is not None else None
    eta = _bars_eta(f["eta_bars"], f["setup_tf"])

    if where == "through":
        back = "back above" if f["side"] == "long" else "back below"
        detail = (
            f"Price has already traded through the zone, so the first thing it owes you is a close "
            f"{back} the edge. Until it does, this is a broken level, not a setup."
        )
    elif reached:
        detail = "Price is at the zone — the setup is live and everything now hangs on the trigger."
    else:
        distance = _sentence(gap, f"({atr_mult})" if atr_mult else None)
        detail = _sentence(
            f"{distance} away." if distance else None,
            f"{eta[:1].upper()}{eta[1:]} out." if eta else None,
            "Nothing to do until it arrives — chasing here buys the middle of a leg.",
        )

    return _beat(
        REACH_ZONE, REACH_ZONE,
        f"Price comes back to {zone}",
        detail,
        status=DONE if reached else PENDING,
        level=edge,
        comparator=AT_OR_BELOW if f["side"] == "long" else AT_OR_ABOVE,
        timeframe=f["setup_tf"],
    )


def _sweep_beat(f: dict) -> dict | None:
    """The turtle-soup path. Optional: plan.py accepts three different triggers."""
    if f["zone_min"] is None or f["zone_max"] is None or f["eps"] is None:
        return None
    level = f["zone_min"] - f["eps"] if f["side"] == "long" else f["zone_max"] + f["eps"]
    level_text = _px(f["pair"], level)
    if not level_text:
        return None
    under = "under" if f["side"] == "long" else "over"
    stops = "below" if f["side"] == "long" else "above"
    move = "A dip through" if f["side"] == "long" else "A push above"
    return _beat(
        SWEEP, SWEEP,
        f"{move} {level_text} that closes back inside",
        f"Not required, but it is the best version of this trade. A wick {under} the zone takes the "
        f"stops parked {stops} it, then a close back inside proves the other side had no follow-through. "
        f"When that happens the stop re-anchors just {under} the wick — tighter risk for the same idea.",
        status=DONE if f["trigger_kind"] == "sweep_reclaim" else PENDING,
        level=level,
        comparator=AT_OR_BELOW if f["side"] == "long" else AT_OR_ABOVE,
        timeframe=f["entry_tf"],
        optional=True,
    )


def _choch_beat(f: dict) -> dict | None:
    """The change of character — the beat the whole feature exists for."""
    level_text = _px(f["pair"], f["trigger_level"])
    if not level_text or f["trigger_kind"] == "pattern":
        # A pattern trigger already fired; the confirmation beat tells that story.
        return None
    tf = f["entry_tf"] or "lower timeframe"
    if f["side"] == "long":
        swing, owner, direction = "lower high", "sellers", "above"
    else:
        swing, owner, direction = "higher low", "buyers", "below"
    return _beat(
        CHOCH, CHOCH,
        f"Change of character on the {tf}: a close {direction} {level_text}",
        f"{level_text} is the last minor {swing} inside the pull-back. Until a {tf} candle closes "
        f"{direction} it, {owner} still own the short-term structure and every bounce off the zone is "
        f"a pause, not a turn. That close is the first objective evidence control changed hands.",
        status=DONE if f["trigger_kind"] == "micro_bos" else PENDING,
        level=f["trigger_level"],
        comparator=CLOSE_ABOVE if f["side"] == "long" else CLOSE_BELOW,
        timeframe=f["entry_tf"],
    )


def _accepted_triggers(side: str, limit: int = 4) -> list[str]:
    """Trigger-role reversal patterns for this side, straight from the catalog."""
    want = "reversal_bullish" if side == "long" else "reversal_bearish"
    return [
        name
        for name, meta in pattern_meta.PATTERN_META.items()
        if meta.get("role") == "trigger" and meta.get("group") == want
    ][:limit]


def _confirmation_beat(f: dict) -> dict:
    tf = f["entry_tf"] or "lower timeframe"
    fired = f["trigger_kind"] == "pattern" and f["trigger_pattern"]

    if fired:
        edu = pattern_meta.meta_for(f["trigger_pattern"])["education"]
        detail = _sentence(
            edu.get("story"),
            f"Valid when: {_lower_first(edu.get('valid_when', ''))}" if edu.get("valid_when") else None,
            f"Watch out: {_lower_first(edu.get('watch_out', ''))}" if edu.get("watch_out") else None,
        )
        return _beat(
            CONFIRMATION, CONFIRMATION,
            f"{f['trigger_pattern']} confirmed at the zone",
            detail,
            status=DONE,
            timeframe=f["entry_tf"],
        )

    names = _accepted_triggers(f["side"])
    takeover = "bullish takeover" if f["side"] == "long" else "bearish takeover"
    accepted = ", ".join(names[:-1]) + f" or {names[-1]}" if len(names) > 1 else (names[0] if names else "a confirmed reversal candle")
    lead = f["trigger_detail"] if f["trigger_detail"] and "not a valid entry trigger" in f["trigger_detail"] else None
    no_swing = (
        f"No minor swing has formed on the {tf} yet — one has to print before there is a level to break."
        if f["trigger_level"] is None and f["trigger_kind"] == "none"
        else None
    )
    detail = _sentence(
        no_swing,
        f"{lead[:1].upper() + lead[1:]}." if lead and not lead.endswith(".") else (lead[:1].upper() + lead[1:] if lead else None),
        f"What counts is a confirmed {takeover} closed at the zone: {accepted}.",
        "Indecision candles are context, not permission.",
    )
    return _beat(
        CONFIRMATION, CONFIRMATION,
        "A real reversal trigger at the zone",
        detail,
        status=DONE if f["trigger_kind"] not in ("none",) else PENDING,
        timeframe=f["entry_tf"],
    )


def _entry_beat(f: dict) -> dict | None:
    entry_text = _px(f["pair"], f["entry"])
    if not entry_text:
        return None
    stop_text = _px(f["pair"], f["stop"])
    target_text = _px(f["pair"], f["target"])
    risk = _dist(f["pair"], f["entry"], f["stop"])
    size = f"{f['size']:g}" if f["size"] else None

    title = _sentence(
        f"Enter {size} at {entry_text}" if size else f"Enter at {entry_text}",
        f"· stop {stop_text}" if stop_text else None,
    )
    if stop_text:
        detail = _sentence(
            f"Risk {risk} to the stop." if risk else None,
            f"Target {target_text}" + (f" ({f['target_basis']})" if f["target_basis"] else "") +
            (f", {f['rr']:.2f}R." if f["rr"] is not None else ".") if target_text else None,
            "If the sweep path happened instead, the stop rides up under the wick and the same idea "
            "costs less.",
        )
    else:
        # A WAITING plan has an entry parked at the zone but no stop or target
        # yet — plan.py only anchors those once a trigger candle exists.
        anchor = "low" if f["side"] == "long" else "high"
        detail = (
            f"The stop and target are not fixed yet. They get anchored off the trigger candle once it "
            f"prints — beyond its {anchor} plus an ATR buffer — and the planner then checks the reward "
            f"against the nearest obstacle before it will arm anything. Size follows from that distance, "
            f"so there is no position to plan until the trigger exists."
        )
    return _beat(
        ENTRY, ENTRY, title, detail,
        status=DONE if f["status"] == "ENTER_READY" else PENDING,
        level=f["entry"],
        comparator=AT_OR_BELOW if f["side"] == "long" else AT_OR_ABOVE,
        timeframe=f["setup_tf"],
    )


# --------------------------------------------------------------- chapters
def _invalidation(f: dict) -> dict | None:
    tf = f["setup_tf"] or "setup timeframe"
    stop_text = _px(f["pair"], f["stop"])
    if stop_text:
        anchor = f["stop_anchor"]
        anchor_price = _px(f["pair"], f["stop_anchor_price"])
        because = (
            f" — that is beyond the {anchor}"
            + (f" at {anchor_price}" if anchor_price else "")
            + " plus an ATR buffer, not a round number"
            if anchor else ""
        )
        side_word = "Below" if f["side"] == "long" else "Above"
        return {
            "level": f["stop"],
            "comparator": CLOSE_BELOW if f["side"] == "long" else CLOSE_ABOVE,
            "timeframe": tf,
            "detail": f"{side_word} {stop_text} the reason for the trade is gone{because}.",
        }

    far_edge = f["zone_min"] if f["side"] == "long" else f["zone_max"]
    edge_text = _px(f["pair"], far_edge)
    if edge_text:
        direction = "below" if f["side"] == "long" else "above"
        flip = "supply now, not demand" if f["side"] == "long" else "demand now, not supply"
        return {
            "level": far_edge,
            "comparator": CLOSE_BELOW if f["side"] == "long" else CLOSE_ABOVE,
            "timeframe": tf,
            "detail": f"A {tf} close {direction} {edge_text} — through the zone rather than off it — "
                      f"retires this idea. A zone that gets closed through is {flip}.",
        }
    return None


def _management(f: dict) -> list[dict]:
    out: list[dict] = []
    target_text = _px(f["pair"], f["target"])
    entry_text = _px(f["pair"], f["entry"])

    nxt = f["alternatives"][0] if f["alternatives"] else None
    if target_text and nxt:
        nxt_text = _px(f["pair"], nxt.get("price"))
        if nxt_text:
            out.append(_beat(
                "manage_runway", MANAGE,
                "Where a runner meets the next seller" if f["side"] == "long"
                else "Where a runner meets the next buyer",
                _sentence(
                    f"Nothing mapped sits between {entry_text} and the {target_text} target." if entry_text else None,
                    f"Past it, {nxt.get('basis', 'the next level')} at {nxt_text}"
                    + (f" ({nxt.get('rr'):.2f}R)" if _finite(nxt.get("rr")) is not None else "")
                    + " is the next real obstacle — that is where a runner stalls, not where it is safe to add.",
                ),
                level=nxt.get("price"),
                comparator=AT_OR_ABOVE if f["side"] == "long" else AT_OR_BELOW,
                timeframe=f["setup_tf"],
                optional=True,
            ))
    elif target_text:
        out.append(_beat(
            "manage_runway", MANAGE,
            "No mapped level past the target",
            f"Nothing is charted beyond {target_text}, so a runner past it is trading blind. "
            "Bank the planned target unless price gives you a new structure to trail behind.",
            timeframe=f["setup_tf"], optional=True,
        ))

    # Encodes monitor._check_exhaustion_at_target: a confirmed warning-role
    # pattern within 1R of the target raises EXHAUSTION_AT_TARGET.
    if target_text and f["entry"] is not None and f["stop"] is not None:
        band = _dist(f["pair"], f["entry"], f["stop"])
        out.append(_beat(
            "manage_exhaustion", MANAGE,
            "Hesitation in front of the target",
            _sentence(
                f"If a doji, spinning top or shooting star prints within {band} of {target_text},"
                if band else f"If a doji, spinning top or shooting star prints near {target_text},",
                "the monitor raises EXHAUSTION_AT_TARGET. That is the cue to bank partials or pull the "
                "stop up — buyers hesitating in front of the target is the classic failed-target tell.",
            ),
            timeframe=f["entry_tf"], optional=True,
        ))

    if f["stop_anchor"]:
        swing = "higher low" if f["side"] == "long" else "lower high"
        out.append(_beat(
            "manage_stop", MANAGE,
            "The stop only moves for structure",
            f"It sits beyond the {f['stop_anchor']} plus an ATR buffer. Move it when the {f['entry_tf']} "
            f"prints a new {swing} — never for comfort, and never wider once you are in.",
            timeframe=f["entry_tf"], optional=True,
        ))
    return out[:3]


def _alternate(f: dict) -> dict | None:
    summary = (f["setup_structure"].get("swing_summary") or {})
    swings = summary.get("lows" if f["side"] == "long" else "highs") or []
    flip = _finite(swings[-1]) if swings else None
    flip_text = _px(f["pair"], flip)
    if not flip_text:
        return None

    tf = f["setup_tf"] or "setup timeframe"
    direction = "below" if f["side"] == "long" else "above"
    broken = "higher-low" if f["side"] == "long" else "lower-high"
    trigger_detail = (
        f"If price closes {direction} {flip_text} on the {tf} instead — through the last "
        f"{broken.replace('-', ' ')} — the {broken} sequence is broken and this read is finished, "
        "not just paused."
    )

    opposite = "short" if f["side"] == "long" else "long"
    candidate = next(
        (e for e in f["setup_entries"] if e.get("side") == opposite),
        None,
    )
    if candidate:
        lo, hi = _px(f["pair"], candidate.get("zone_min")), _px(f["pair"], candidate.get("zone_max"))
        zone_phrase = f"the {'supply' if opposite == 'short' else 'demand'} at {lo}–{hi}" if lo and hi else None
    else:
        zone_phrase = None
    if not zone_phrase and f["target"] is not None:
        target_text = _px(f["pair"], f["target"])
        zone_phrase = f"the level at {target_text}" if target_text else None

    if zone_phrase:
        outcome = (
            f"That flips the book: {zone_phrase} stops being a target and becomes the {opposite} zone to "
            "watch, with the same rules in reverse — reach it, sweep it, change of character, confirmed candle."
        )
    else:
        outcome = (
            f"That flips the book: the next scan re-runs the same four gates for the {opposite} side, and "
            "nothing is armed until a zone there clears them."
        )

    return {
        "trigger_detail": trigger_detail,
        "level": flip,
        "comparator": CLOSE_BELOW if f["side"] == "long" else CLOSE_ABOVE,
        "outcome": outcome,
    }


# --------------------------------------------------------------- branches
def _title(f: dict) -> str:
    zone = _zone_text(f)
    side = f["side"] or "trade"
    pair = (f["pair_display"] or f["pair"] or "").upper()
    if zone:
        return f"The {pair} {side} from {zone}"
    return f"The {pair} {side}"


def _setup_story(f: dict) -> dict:
    if f["status"] == "DISCARDED":
        return _discarded_story(f)

    beats = [b for b in (
        _reach_zone_beat(f),
        _sweep_beat(f),
        _choch_beat(f),
        _confirmation_beat(f),
        _entry_beat(f),
    ) if b]
    if f["status"] == "ENTER_READY":
        # All four gates passed, so the whole roadmap is satisfied by definition.
        # Leaving an earlier beat "active" here would contradict the entry beat
        # sitting below it marked done.
        for beat in beats:
            beat["status"] = DONE
    else:
        _mark_active(beats)

    return _story(
        f["status"] or "WAITING",
        "in_trade" if f["status"] == "ENTER_READY" else "roadmap",
        side=f["side"],
        title=_title(f),
        situation=_situation(f),
        beats=beats,
        invalidation=_invalidation(f),
        management=_management(f),
        alternate=_alternate(f),
    )


def _requalifying_entry(f: dict) -> float | None:
    """The entry price that would restore min_rr against the same stop/target.

    |T - E| = min_rr * |E - S|  =>  E* = (T + min_rr * S) / (1 + min_rr)
    """
    target, stop, min_rr = f["target"], f["stop"], f["min_rr"]
    if target is None or stop is None or not min_rr:
        return None
    star = (target + min_rr * stop) / (1 + min_rr)
    if not math.isfinite(star):
        return None
    # Only honest if it is a *better* price than the one that was vetoed.
    if f["side"] == "long" and not (stop < star < (f["entry"] or star)):
        return None
    if f["side"] == "short" and not ((f["entry"] or star) < star < stop):
        return None
    return star


def _structure_flip_beat(f: dict) -> dict | None:
    """What would make a vetoed side legal again.

    Two different vetoes need two different answers, and conflating them is how a
    narrative starts lying: a *structure* veto has a swing level to watch, while
    an *MA bias* veto does not — the stack has to turn, and there is no single
    honest price for that (`_ma_bias` mixes three comparisons).
    """
    side = f["side"]
    tf = f["bias_tf"] or "higher timeframe"

    if f["veto_gate"] == "MA bias":
        want = "bullish" if side == "long" else "bearish"
        stack = ("price back above the EMA 50 with the 9 crossing over the 21"
                 if side == "long" else
                 "price back below the EMA 50 with the 9 crossing under the 21")
        return _beat(
            "requalify", CONFIRMATION,
            f"What would make a {side} legal again: a {want} moving-average stack",
            f"Structure is not the problem here — the moving averages are. The planner needs {stack} "
            f"before it will plan a {side} at all. Until the stack turns, this zone stays on the "
            f"watchlist and every bounce off it is someone else's trade.",
            timeframe=f["bias_tf"],
        )

    state = f["plan_structure"] or (f["structure"].get("state") or "").upper()
    summary = (f["structure"].get("swing_summary") or {})
    # A short vetoed by an uptrend becomes legal when the uptrend's last higher
    # LOW gives way — the opposite swing from the one `_alternate` watches.
    swings = summary.get("lows" if side == "short" else "highs") or []
    level = _finite(swings[-1]) if swings else None
    level_text = _px(f["pair"], level)
    if not level_text or state not in ("UPTREND", "DOWNTREND"):
        return None

    if side == "short":
        direction, swing = "below", "higher low"
    else:
        direction, swing = "above", "lower high"
    return _beat(
        "requalify", CHOCH,
        f"What would make a {side} legal again: a {tf} close {direction} {level_text}",
        f"The {state.lower()} owns this market until its last {swing} at {level_text} gives way. Trading "
        f"against it before then is the single most expensive habit there is — so this stays on the "
        f"watchlist, not in the book, until a {tf} candle closes {direction} that level.",
        level=level,
        comparator=CLOSE_BELOW if side == "short" else CLOSE_ABOVE,
        timeframe=f["bias_tf"],
    )


def _discarded_story(f: dict) -> dict:
    reason = f["reason"] or "the planner vetoed this setup"
    beats = [_beat(
        "vetoed", ENTRY,
        "Vetoed before it could be traded",
        reason[:1].upper() + reason[1:] + ("." if not reason.endswith(".") else ""),
        status=FAILED,
        timeframe=f["setup_tf"],
    )]

    star = _requalifying_entry(f)
    star_text = _px(f["pair"], star)
    stop_text = _px(f["pair"], f["stop"])
    target_text = _px(f["pair"], f["target"])
    if star_text and stop_text:
        beats.append(_beat(
            "requalify", REACH_ZONE,
            f"A better price re-qualifies it: {star_text}",
            f"It is not a bad idea, it is a bad price. A deeper pull-back to {star_text} — same stop at "
            f"{stop_text} — restores {f['min_rr']:.1f}R against that same obstacle. Below that price the "
            "setup re-qualifies on its own, so it is worth leaving on the watchlist.",
            level=star,
            comparator=AT_OR_BELOW if f["side"] == "long" else AT_OR_ABOVE,
            timeframe=f["setup_tf"],
        ))
    elif target_text:
        nxt = f["alternatives"][0] if f["alternatives"] else None
        nxt_text = _px(f["pair"], nxt.get("price")) if nxt else None
        beats.append(_beat(
            "requalify", REACH_ZONE,
            f"{target_text} has to break first",
            _sentence(
                f"If a {f['setup_tf']} candle closes through {target_text},",
                f"the next mapped level is {nxt.get('basis', 'the level beyond it')} at {nxt_text}"
                + (f" ({nxt.get('rr'):.2f}R)" if nxt and _finite(nxt.get("rr")) is not None else "")
                + " and the trade re-qualifies against that instead." if nxt_text
                else "the path opens up and the next scan re-prices the trade.",
            ),
            level=f["target"],
            comparator=CLOSE_ABOVE if f["side"] == "long" else CLOSE_BELOW,
            timeframe=f["setup_tf"],
        ))
    else:
        legal_again = _structure_flip_beat(f)
        if legal_again:
            beats.append(legal_again)

    _mark_active(beats)
    # On a structure/bias veto there is no entry, and the requalify beat above
    # already names the flip level — a second "if it goes the other way" would
    # just restate it. Only the R:R veto has a real alternate to tell.
    alternate = _alternate(f) if f["entry"] is not None else None
    return _story(
        "DISCARDED", "roadmap",
        side=f["side"],
        title=_title(f),
        situation=_situation(f),
        beats=beats,
        invalidation=_invalidation(f),
        management=[],
        alternate=alternate,
    )


def _no_setup_story(f: dict) -> dict:
    situation = []
    state = (f["structure"].get("state") or "UNDETERMINED").upper()
    reason = f["structure"].get("reason")
    tf = f["bias_tf"] or "higher timeframe"
    if reason and state != "UNDETERMINED":
        situation.append(f"On the {tf} the structure reads {state.lower()} — {_lower_first(reason)}")
    situation.append(
        f"No zone cleared the confluence threshold on the {f['setup_tf'] or 'setup timeframe'}, "
        "so nothing is armed and there is no plan to follow yet."
    )

    beats: list[dict] = []
    analysis, frame, price = f["analysis"], f["setup_frame"], f["price"]
    if analysis is not None and frame is not None and price is not None:
        import plan as plan_module

        above = plan_module.obstacles(analysis, frame, "long", price)[:1]
        below = plan_module.obstacles(analysis, frame, "short", price)[:1]
        for levels, word, comparator in (
            (below, "below", AT_OR_BELOW),
            (above, "above", AT_OR_ABOVE),
        ):
            if not levels:
                continue
            level = _finite(levels[0].get("price"))
            level_text = _px(f["pair"], level)
            if not level_text:
                continue
            gap = _dist(f["pair"], price, level)
            atr_mult = _atr_multiple(abs(price - level), f["atr"])
            beats.append(_beat(
                f"nearest_{word}", REACH_ZONE,
                f"Nearest level {word}: {levels[0].get('basis', 'level')} at {level_text}",
                _sentence(
                    f"{gap}" + (f" ({atr_mult})" if atr_mult else "") + f" {word}." if gap else None,
                    "It is not a setup yet — it needs a touch, a confirmed reversal candle, and a clear "
                    f"{f['min_rr']:.1f}R path to the next obstacle before anything arms.",
                ),
                level=level, comparator=comparator, timeframe=f["setup_tf"],
            ))

    if not beats:
        beats.append(_beat(
            "await_zone", REACH_ZONE,
            "Wait for a zone to form",
            f"Nothing on the {f['setup_tf'] or 'setup timeframe'} has enough confluence to plan around. "
            "The scan re-runs on the next bar close — there is nothing to monitor until it finds one.",
            timeframe=f["setup_tf"],
        ))

    _mark_active(beats)
    return _story("NO_SETUP", "no_setup", title="No setup yet", situation=situation, beats=beats)


def _feed_blocked_story(context: dict) -> dict:
    """No numbers at all: this is the branch where price cannot be trusted."""
    roles = context.get("roles") or topdown.timeframe_roles()
    error = context.get("feed_error") or "Feed integrity failed."
    entry_tf = roles.get("entry_tf") or "entry timeframe"
    return _story(
        "FEED_BLOCKED", "blocked",
        title="Nothing is armed — the feed is unresolved",
        situation=[error, "No new entries until the live price and the candle stream agree."],
        beats=[
            _beat("feed_check", CONFIRMATION, "Feed integrity check failed", error, status=FAILED),
            _beat(
                "await_feed", CONFIRMATION,
                f"Re-check on the next {entry_tf} close",
                "The live tick and the candle stream disagree, so every level would be measured off a "
                "price we do not trust. Nothing arms until they agree.",
                timeframe=entry_tf,
            ),
        ],
    )
