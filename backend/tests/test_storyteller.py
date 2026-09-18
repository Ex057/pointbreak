import math
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))
from plan import build_trade_plan, micro_break_of_structure  # noqa: E402

from app.engine import pattern_meta  # noqa: E402
from app.services import storyteller, topdown  # noqa: E402


def _candles():
    idx = pd.date_range("2026-07-20", periods=8, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "open": [100, 101, 102, 103, 104, 105, 106, 107],
            "high": [101, 102, 103, 104, 105, 106, 107, 108],
            "low": [99, 100, 101, 102, 103, 104, 105, 106],
            "close": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5, 106.5, 107.0],
        },
        index=idx,
    )


def _ltf():
    """Lower-TF frame with a clear minor lower high, so micro-BOS has a level.

    The last bar closes below that peak, so the break has NOT fired — which is
    exactly the case the change-of-character beat exists for.
    """
    highs = [100, 101, 104, 102, 101, 102, 103, 102, 101, 102, 103, 102]
    idx = pd.date_range("2026-07-20", periods=len(highs), freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "open": [h - 1 for h in highs],
            "high": highs,
            "low": [h - 2 for h in highs],
            "close": [h - 0.5 for h in highs],
        },
        index=idx,
    )


def _analysis(**over):
    base = {
        "structure": "UPTREND",
        "bias": "bullish",
        "entry": {
            "side": "long",
            "zone_min": 104.0,
            "zone_max": 105.0,
            "entry_price": 105.0,
            "confluence": 3,
            "fresh": True,
            "source": "supply_demand",
            "eta_bars": 4,
        },
        "sr_zones": [{"level_min": 108.0, "touches": 2}, {"level_min": 110.0, "touches": 4}],
        "supply_demand": [],
        "fvgs": [],
        "patterns": [],
    }
    base.update(over)
    return base


def _scan_result(**over):
    base = {
        "pair": "xauusd",
        "pair_display": "XAU/USD",
        "price": 107.0,
        "structure": {
            "state": "UPTREND",
            "reason": "Higher highs (104.00→108.00) and higher lows (100.00→106.00).",
            "swing_summary": {"highs": [104.0, 108.0], "lows": [100.0, 106.0]},
        },
        "future": {"entries": [], "bias": "bullish"},
        "layers": {},
    }
    base.update(over)
    return base


def _context(plan, analysis=None, **over):
    roles = topdown.timeframe_roles("Swing Trader")
    scan = _scan_result()
    base = {
        "feed_blocked": False,
        "roles": roles,
        "plan": plan,
        "analysis": analysis if analysis is not None else _analysis(),
        "frames": {roles["setup_tf"]: _candles()},
        "bias_scan": scan,
        "setup_scan": scan,
    }
    base.update(over)
    return base


def _build(analysis, *, min_rr=2.0, ltf=None, scan_over=None, ctx_over=None, **plan_kw):
    plan = build_trade_plan(
        _candles(), analysis, account_balance=1000, risk_pct=1.0,
        min_rr=min_rr, ltf_candles=ltf, **plan_kw,
    )
    plan.update({"bias_tf": "1D", "setup_tf": "4H", "entry_tf": "1H", "trader_mode": "Swing Trader"})
    scan = _scan_result(**(scan_over or {}))
    context = _context(plan, analysis=analysis)
    context.update(ctx_over or {})
    story = storyteller.build(scan, plan, analysis.get("entry"), min_rr=min_rr, topdown_context=context)
    return plan, story


def _all_text(story):
    """Every user-visible string in a story, flattened."""
    beats = story["beats"] + story["management"]
    texts = [story["title"], *story["situation"]]
    texts += [b["title"] for b in beats] + [b["detail"] for b in beats]
    if story["invalidation"]:
        texts.append(story["invalidation"]["detail"])
    if story["alternate"]:
        texts += [story["alternate"]["trigger_detail"], story["alternate"]["outcome"]]
    return [t for t in texts if t]


# ------------------------------------------------------------------ roadmap
def test_waiting_plan_beats_are_ordered_and_exactly_one_is_active():
    analysis = _analysis(patterns=[
        {"name": "Doji", "direction": "neutral", "confirmed": True, "index": 6, "role": "warning"},
    ])
    plan, story = _build(analysis, ltf=_ltf())

    assert plan["status"] == "WAITING"
    assert story["state"] == "roadmap"
    assert [b["kind"] for b in story["beats"]] == [
        storyteller.REACH_ZONE, storyteller.SWEEP, storyteller.CHOCH,
        storyteller.CONFIRMATION, storyteller.ENTRY,
    ]

    statuses = [b["status"] for b in story["beats"]]
    assert statuses.count(storyteller.ACTIVE) == 1
    active = statuses.index(storyteller.ACTIVE)
    assert all(
        b["status"] == storyteller.DONE
        for b in story["beats"][:active] if not b["optional"]
    )
    assert all(b["status"] == storyteller.PENDING for b in story["beats"][active + 1:])
    assert story["invalidation"]["level"] is not None


def test_choch_beat_quotes_the_micro_bos_level():
    ltf = _ltf()
    fired, level = micro_break_of_structure(ltf, "long")
    assert not fired and level is not None, "fixture must give a pending, unbroken level"

    analysis = _analysis(patterns=[
        {"name": "Doji", "direction": "neutral", "confirmed": True, "index": 6, "role": "warning"},
    ])
    _, story = _build(analysis, ltf=ltf)

    choch = next(b for b in story["beats"] if b["kind"] == storyteller.CHOCH)
    assert choch["level"] == pytest.approx(level)
    assert choch["comparator"] == storyteller.CLOSE_ABOVE
    assert choch["timeframe"] == "1H"
    assert choch["status"] == storyteller.PENDING
    assert "change of character" in choch["title"].lower()
    assert f"{level:,.2f}" in choch["detail"]


def test_confirmation_beat_quotes_pattern_education():
    analysis = _analysis(patterns=[
        {"name": "Bullish Engulfing", "direction": "bullish", "confirmed": True,
         "index": 6, "role": "trigger"},
    ])
    plan, story = _build(analysis)

    assert plan["status"] == "ENTER_READY"
    assert plan["trigger_kind"] == "pattern"
    edu = pattern_meta.meta_for("Bullish Engulfing")["education"]
    conf = next(b for b in story["beats"] if b["kind"] == storyteller.CONFIRMATION)
    assert conf["status"] == storyteller.DONE
    assert edu["story"] in conf["detail"]
    assert "Bullish Engulfing" in conf["title"]
    # Every gate passed, so nothing in the roadmap may still read as pending.
    assert {b["status"] for b in story["beats"]} == {storyteller.DONE}
    assert story["state"] == "in_trade"


# ----------------------------------------------------------------- vetoes
def test_discarded_story_states_the_price_that_would_requalify_it():
    analysis = _analysis(
        entry={
            "side": "long", "zone_min": 102.0, "zone_max": 105.0, "entry_price": 105.0,
            "confluence": 3, "fresh": True, "source": "supply_demand",
        },
        sr_zones=[{"level_min": 106.0, "touches": 3}],
        supply_demand=[{"kind": "supply", "level_min": 113.0, "fresh": True}],
    )
    # Same shape as test_plan.py's R:R veto: the sweep supplies the trigger, and
    # the nearest obstacle at 106 caps reward below the 2.0R minimum.
    plan, story = _build(analysis, alert_state="SWEPT", sweep_low=102.0)

    assert plan["status"] == "DISCARDED"
    assert story["status"] == "DISCARDED"
    assert any(b["status"] == storyteller.FAILED for b in story["beats"])

    requalify = (plan["target"] + 2.0 * plan["stop"]) / 3.0
    text = " ".join(_all_text(story))
    assert f"{requalify:,.2f}" in text
    assert "2.0R" in text


def test_structure_veto_names_the_level_that_makes_the_side_legal_again():
    analysis = _analysis(structure="DOWNTREND", bias="bearish")
    plan, story = _build(analysis)

    assert plan["status"] == "DISCARDED"
    requalify = next(b for b in story["beats"] if b["id"] == "requalify")
    # The downtrend's last lower high is what has to give way for a long.
    assert requalify["level"] == pytest.approx(108.0)
    assert requalify["comparator"] == storyteller.CLOSE_ABOVE
    assert "downtrend" in requalify["detail"].lower()


def test_bias_veto_does_not_claim_a_trend_that_is_not_there():
    """An MA-bias veto has no swing level — and must not invent a trend word."""
    analysis = _analysis(structure="RANGE", bias="bearish")
    plan, story = _build(analysis)

    assert plan["status"] == "DISCARDED"
    requalify = next(b for b in story["beats"] if b["id"] == "requalify")
    assert requalify["level"] is None
    assert "moving-average" in requalify["title"].lower()
    text = " ".join(_all_text(story)).lower()
    assert "downtrend owns" not in text and "uptrend owns" not in text


# --------------------------------------------------------- degraded branches
def test_no_setup_story_names_the_nearest_level_and_what_would_arm_it():
    scan = _scan_result()
    context = _context(None)
    story = storyteller.build(scan, None, None, min_rr=2.0, topdown_context=context)

    assert story["state"] == "no_setup"
    assert story["status"] == "NO_SETUP"
    assert story["beats"]
    assert any(b["status"] == storyteller.ACTIVE for b in story["beats"])
    assert re.search(r"\d[\d,]*\.\d\d", " ".join(_all_text(story)))


def test_feed_blocked_story_carries_no_levels():
    context = {
        "feed_blocked": True,
        "feed_error": "Deriv tick disagrees with the 15m close.",
        "roles": topdown.timeframe_roles("Swing Trader"),
        "feed": {},
    }
    story = storyteller.build(_scan_result(), None, None, min_rr=2.0, topdown_context=context)

    assert story["state"] == "blocked"
    assert story["beats"]
    assert all(b["level"] is None for b in story["beats"])
    assert all(b["comparator"] is None for b in story["beats"])
    assert story["invalidation"] is None
    assert story["alternate"] is None
    assert "Deriv tick disagrees" in " ".join(story["situation"])


def test_build_never_raises_on_a_degenerate_context():
    """test_api.py monkeypatches load_context down to two keys — survive that."""
    story = storyteller.build({}, None, None, min_rr=2.0, topdown_context={"feed_blocked": False})
    assert story["version"] == storyteller.VERSION
    assert isinstance(story["beats"], list)


# ------------------------------------------------------------ the hard rule
_CASES = {
    "waiting": dict(patterns=[{"name": "Doji", "direction": "neutral", "confirmed": True,
                               "index": 6, "role": "warning"}]),
    "enter_ready": dict(patterns=[{"name": "Bullish Engulfing", "direction": "bullish",
                                   "confirmed": True, "index": 6, "role": "trigger"}]),
    "discarded_structure": dict(structure="DOWNTREND", bias="bearish"),
    "discarded_bias": dict(structure="RANGE", bias="bearish"),
    "no_zone_geometry": dict(entry={"side": "long", "zone_min": 104.0, "zone_max": 105.0,
                                    "entry_price": 105.0, "confluence": 3, "source": "sr"}),
    "short_side": dict(structure="DOWNTREND", bias="bearish",
                       entry={"side": "short", "zone_min": 109.0, "zone_max": 110.0,
                              "entry_price": 109.0, "confluence": 3, "source": "supply_demand"},
                       sr_zones=[{"level_max": 100.0, "touches": 3}]),
}


@pytest.mark.parametrize("case", sorted(_CASES))
@pytest.mark.parametrize("degrade", ["none", "no_swings", "no_structure", "empty_scan"])
def test_no_beat_ever_emits_a_null_or_nan_level_in_its_copy(case, degrade):
    scan_over = {}
    if degrade == "no_swings":
        scan_over["structure"] = {"state": "UPTREND", "reason": "Higher highs.", "swing_summary": {}}
    elif degrade == "no_structure":
        scan_over["structure"] = {}
    elif degrade == "empty_scan":
        scan_over = {"price": None, "structure": {}}

    ctx_over = {}
    if degrade == "empty_scan":
        ctx_over = {"bias_scan": {}, "setup_scan": {}, "frames": {}, "analysis": None}

    _, story = _build(_analysis(**_CASES[case]), ltf=_ltf(), scan_over=scan_over, ctx_over=ctx_over)

    for text in _all_text(story):
        assert not re.search(r"\bNone\b|\bnan\b|\binf\b|\bundefined\b|\bnull\b", text), text
        assert "  " not in text, f"double space from a dropped clause: {text!r}"
    for beat in story["beats"] + story["management"]:
        assert beat["level"] is None or math.isfinite(beat["level"])
        # A comparator without a level would make the frontend evaluate nothing.
        assert (beat["comparator"] is None) == (beat["level"] is None)
