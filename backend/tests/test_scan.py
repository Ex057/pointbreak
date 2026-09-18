"""M1 regression net. The epoch assertions are the important ones."""
import time

import pytest

from app.config import TIMEFRAMES
from app.services.fixtures import make_candles
from app.services.scan import BarClock, scan
from app.services.verdict import build, position_size

# Anything before 2001-09-09 is almost certainly a positional bar index that
# escaped translation, not a real timestamp.
PLAUSIBLE_EPOCH = 1_000_000_000


@pytest.fixture(scope="module")
def result():
    df = make_candles("R_100", "1H")
    return scan(df, pair="R_100", timeframe="1H")


def test_fixtures_are_deterministic():
    a = make_candles("R_100", "1H")
    b = make_candles("R_100", "1H")
    assert a["close"].tolist() == b["close"].tolist()
    assert make_candles("R_50", "1H")["close"].tolist() != a["close"].tolist()


def test_bar_clock_projects_into_the_future():
    df = make_candles("R_100", "1H", n=50)
    clock = BarClock(df.index, TIMEFRAMES["1H"])
    last = clock.at(49)
    assert clock.at(50) == last + 3600
    assert clock.at(55) == last + 6 * 3600
    assert clock.at(0) < last


def test_candles_carry_real_epochs(result):
    assert result["candles"], "no candles produced"
    for c in result["candles"]:
        assert c["time"] > PLAUSIBLE_EPOCH
    times = [c["time"] for c in result["candles"]]
    assert times == sorted(times), "candles must be chronological"


def test_every_layer_time_is_an_epoch_not_an_index(result):
    """The trap: engine emits positional ints. Nothing may leak through."""
    layers = result["layers"]

    for side in ("highs", "lows"):
        for s in layers["swings"][side]:
            assert s["time"] > PLAUSIBLE_EPOCH

    for z in layers["supply_demand"]:
        assert z["time"] > PLAUSIBLE_EPOCH
        assert z["end_time"] > PLAUSIBLE_EPOCH
        assert "broken" not in z, "dead field should be stripped"

    for z in layers["fvg"]:
        assert z["time"] > PLAUSIBLE_EPOCH
        # exact 3-candle span for hugging the gap
        assert z["start_time"] > PLAUSIBLE_EPOCH and z["end_time"] > PLAUSIBLE_EPOCH
        assert z["start_time"] <= z["time"] <= z["end_time"]

    for ser in layers["moving_averages"]:
        for pt in ser["points"]:
            assert pt["time"] > PLAUSIBLE_EPOCH

    for group in ("patterns_high", "patterns_all"):
        for p in layers[group]:
            assert p["time"] > PLAUSIBLE_EPOCH
            assert p["start_time"] > PLAUSIBLE_EPOCH and p["end_time"] > PLAUSIBLE_EPOCH
            assert p["status"] in {"confirmed", "pending"}
            assert isinstance(p["confirmed"], bool)
            assert isinstance(p["available"], bool)
            assert p["role"] in {"trigger", "warning", "context"}
            assert p["group"]
            assert p["education"]["meaning"]


def test_future_projections_are_epochs_beyond_the_last_candle(result):
    last = result["candles"][-1]["time"]
    for e in result["future"]["entries"]:
        assert e["t_future"] > last, "projected entry must be in the future"


def test_min_rr_overrides_the_hardcoded_2r():
    df = make_candles("R_100", "1H")
    three = scan(df, pair="R_100", timeframe="1H", min_rr=3.0)
    for e in three["future"]["entries"]:
        risk = abs(e["entry_price"] - e["stop"])
        reward = abs(e["target"] - e["entry_price"])
        assert reward == pytest.approx(3 * risk, rel=1e-3)


def test_layer_keys_match_the_toggle_contract(result):
    assert set(result["layers"]) == {
        "swings", "sr", "supply_demand", "fvg",
        "patterns_high", "patterns_all", "moving_averages", "chart_patterns",
    }


def test_pattern_catalog_is_pair_specific():
    df = make_candles("R_100", "1H")
    syn = scan(df, pair="R_100", timeframe="1H", pair_category="synthetic")
    fx = scan(df, pair="EURUSD", timeframe="1H", pair_category="forex")
    assert syn["pattern_catalog"] != fx["pattern_catalog"]
    # a classic-only pattern should be offered on forex, not on the synthetic set
    assert "Bullish Harami" in fx["pattern_catalog_flat"]
    assert "Bullish Harami" not in syn["pattern_catalog_flat"]


def test_pattern_catalog_groups_include_education_and_counts():
    df = make_candles("EURUSD", "1H")
    res = scan(df, pair="EURUSD", timeframe="1H", pair_category="forex")
    assert [group["key"] for group in res["pattern_catalog"]] == [
        "reversal_bullish",
        "reversal_bearish",
        "exhaustion_indecision",
        "continuation_momentum",
    ]
    for group in res["pattern_catalog"]:
        assert group["label"]
        assert group["blurb"]
        assert group["color"]
        for item in group["patterns"]:
            assert item["education"]["meaning"]
            assert item["role"] in {"trigger", "warning", "context"}


def test_last_bar_patterns_are_never_confirmed():
    """The forming bar can't confirm a pattern; confirmation needs a later close."""
    df = make_candles("R_100", "1H")
    res = scan(df, pair="R_100", timeframe="1H")
    last_candle_time = res["candles"][-1]["time"]
    for p in res["layers"]["patterns_all"]:
        if p["time"] == last_candle_time:
            assert p["status"] == "pending"
            assert p["confirmed"] is False


def test_zone_layers_include_priority_metadata(result):
    for layer in ("sr", "supply_demand", "fvg"):
        for zone in result["layers"][layer]:
            assert zone["priority"] in {"high", "medium", "low"}
            assert isinstance(zone["priority_score"], float)
            assert zone["priority_reason"]


def test_position_size_is_fixed_fractional():
    # Risk 1% of 1000 = 10. Entry 100, stop 95 -> 5 per unit -> 2 units.
    assert position_size(100, 95, balance=1000, risk_pct=1.0) == 2.0
    assert position_size(100, 100, balance=1000, risk_pct=1.0) == 0.0


def test_verdict_state_is_structured(result):
    """Verdict state stays within the public contract as planner gates evolve."""
    v = build(result)
    assert v["state"] in {"WAIT", "ENTER", "STAND_ASIDE"}
    if v["plan"] is not None:
        assert v["plan"]["status"] in {"ENTER_READY", "WAITING", "DISCARDED"}


def test_wait_verdict_states_its_plan(result):
    v = build(result)
    if v["plan"] is not None:
        assert len(v["reasons"]) >= 2, "WAIT must explain itself"
        assert v["plan"]["entry"]
        assert "checklist" in v["plan"]


def test_scan_is_fast_enough_for_uat_step_4(result):
    """UAT §10 step 4: scan completes < 3s."""
    df = make_candles("R_100", "1H")
    start = time.perf_counter()
    scan(df, pair="R_100", timeframe="1H")
    assert time.perf_counter() - start < 3.0


def test_expanded_timeframes_scan_without_shape_changes():
    for tf in ("15m", "30m", "4H", "1W"):
        df = make_candles("R_100", tf)
        result = scan(df, pair="R_100", timeframe=tf)
        assert result["timeframe"] == tf
        assert result["candles"]
