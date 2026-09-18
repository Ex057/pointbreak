import pandas as pd
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[2]))
from plan import DISCARDED, ENTER_READY, build_trade_plan


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


def test_nearest_obstacle_vetoes_even_if_farther_target_passes():
    candles = _candles()
    analysis = {
        "structure": "UPTREND",
        "bias": "bullish",
        "entry": {
            "side": "long",
            "zone_min": 102.0,
            "zone_max": 105.0,
            "entry_price": 105.0,
            "confluence": 3,
            "fresh": True,
            "source": "demand",
        },
        "sr_zones": [{"level_min": 106.0, "touches": 3}],
        "supply_demand": [{"kind": "supply", "level_min": 113.0, "fresh": True}],
        "fvgs": [],
        "patterns": [],
    }

    plan = build_trade_plan(
        candles,
        analysis,
        account_balance=1000,
        risk_pct=1.0,
        min_rr=2.0,
        alert_state="SWEPT",
        sweep_low=102.0,
    )

    assert plan["status"] == DISCARDED
    assert plan["target"] == 106.0
    assert plan["target_basis"] == "resistance x3"
    assert plan["alternatives"]
    assert plan["alternatives"][0]["price"] == 113.0
    assert plan["alternatives"][0]["rr"] > 2.0


def test_enter_ready_surfaces_alternative_runner_targets():
    candles = _candles()
    analysis = {
        "structure": "UPTREND",
        "bias": "bullish",
        "entry": {
            "side": "long",
            "zone_min": 104.0,
            "zone_max": 105.0,
            "entry_price": 105.0,
            "confluence": 3,
            "fresh": True,
            "source": "demand",
        },
        "sr_zones": [{"level_min": 108.0, "touches": 2}, {"level_min": 110.0, "touches": 4}],
        "supply_demand": [],
        "fvgs": [],
        "patterns": [{"name": "Bullish Engulfing", "direction": "bullish", "confirmed": True, "index": 6, "role": "trigger"}],
    }

    plan = build_trade_plan(candles, analysis, account_balance=1000, risk_pct=1.0, min_rr=2.0)

    assert plan["status"] == ENTER_READY
    assert plan["target"] == 108.0
    assert plan["alternatives"][0]["price"] == 110.0
    assert plan["checklist"][-1]["passed"] is True


def test_warning_pattern_at_zone_does_not_trigger_gate_two():
    candles = _candles()
    analysis = {
        "structure": "UPTREND",
        "bias": "bullish",
        "entry": {
            "side": "long",
            "zone_min": 104.0,
            "zone_max": 105.0,
            "entry_price": 105.0,
            "confluence": 3,
            "fresh": True,
            "source": "demand",
        },
        "sr_zones": [{"level_min": 108.0, "touches": 2}],
        "supply_demand": [],
        "fvgs": [],
        "patterns": [{"name": "Doji", "direction": "neutral", "confirmed": True, "index": 6, "role": "warning"}],
    }

    plan = build_trade_plan(candles, analysis, account_balance=1000, risk_pct=1.0, min_rr=2.0)

    assert plan["status"] != ENTER_READY
    assert plan["status"] == "WAITING"
    assert "not a valid entry trigger" in plan["checklist"][4]["detail"]


def _ltf_with_pending_lower_high():
    """Lower-TF frame whose last close sits under a clear minor lower high."""
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


def test_plan_surfaces_trigger_and_stop_anchor_without_changing_the_checklist():
    candles = _candles()
    analysis = {
        "structure": "UPTREND",
        "bias": "bullish",
        "entry": {
            "side": "long",
            "zone_min": 104.0,
            "zone_max": 105.0,
            "entry_price": 105.0,
            "confluence": 3,
            "fresh": True,
            "source": "demand",
        },
        "sr_zones": [{"level_min": 108.0, "touches": 2}, {"level_min": 110.0, "touches": 4}],
        "supply_demand": [],
        "fvgs": [],
        "patterns": [{"name": "Bullish Engulfing", "direction": "bullish", "confirmed": True, "index": 6, "role": "trigger"}],
    }

    plan = build_trade_plan(candles, analysis, account_balance=1000, risk_pct=1.0, min_rr=2.0)

    assert plan["status"] == ENTER_READY
    assert plan["trigger_kind"] == "pattern"
    assert plan["trigger_pattern"] == "Bullish Engulfing"
    assert plan["trigger_level"] is not None
    assert isinstance(plan["stop_anchor"], str) and plan["stop_anchor"]
    assert plan["stop_anchor_price"] is not None
    assert plan["structure"] == "UPTREND"
    assert plan["zone_min"] == 104.0 and plan["zone_max"] == 105.0
    # The narrative fields must not disturb the checklist the UI indexes into.
    assert len(plan["checklist"]) == 9


def test_waiting_plan_carries_the_pending_micro_bos_level():
    """The change-of-character level exists BEFORE the break fires.

    That pending level is the whole point: it is what the storyteller tells the
    user to watch for. Losing it would silently gut the feature.
    """
    candles = _candles()
    analysis = {
        "structure": "UPTREND",
        "bias": "bullish",
        "entry": {
            "side": "long",
            "zone_min": 104.0,
            "zone_max": 105.0,
            "entry_price": 105.0,
            "confluence": 3,
            "fresh": True,
            "source": "demand",
        },
        "sr_zones": [{"level_min": 108.0, "touches": 2}],
        "supply_demand": [],
        "fvgs": [],
        "patterns": [],
    }

    plan = build_trade_plan(
        candles, analysis, account_balance=1000, risk_pct=1.0, min_rr=2.0,
        ltf_candles=_ltf_with_pending_lower_high(),
    )

    assert plan["status"] == "WAITING"
    assert plan["trigger_kind"] == "none"      # nothing has fired…
    assert plan["trigger_level"] is not None   # …but the level to watch is there
