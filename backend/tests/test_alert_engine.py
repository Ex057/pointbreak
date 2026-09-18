"""AlertEngine regression net. The sweep-without-reclaim case is the important one:
no ENTER may ever fire without a recorded sweep AND reclaim.

The second important case is `test_fill_never_resolves_on_the_fill_bar`. Every
test in this file used a 2.0-wide zone, so `_wick_eps` was always 0.04 and the
zero-width path — the one production actually armed on — was never exercised.
That gap cost 145 trades. Hence the `zone_width` parametrisation below.
"""
import pytest

from app.services.alert_engine import (
    EXPIRED, FILLED, STOPPED, SWEPT, TARGET, TOUCHED, WATCHING,
    Alert, _wick_eps, on_bar, result_r,
)


def long_alert(**kw) -> Alert:
    base = dict(pair="xauusd", timeframe="1H", side="long", source="sr",
                zone_min=100.0, zone_max=102.0, entry=102.0, stop=98.0, target=110.0,
                atr=1.0, min_risk=1.0, planned_rr=2.0)
    base.update(kw)
    return Alert(**base)


def tick(a: Alert, price: float):
    return on_bar(a, price, price, price, price)


def test_touch_then_sweep_then_reclaim_fills():
    a = long_alert()
    # approach from above, tap the zone
    on_bar(a, 103, 103, 101.5, 101.5)      # enters zone -> TOUCHED
    assert a.state == TOUCHED
    # wick below the zone (sweep), close still low
    on_bar(a, 101, 101.2, 99.0, 99.2)      # low 99 < 100 -> SWEPT
    assert a.state == SWEPT
    assert a.swept and a.swept_price == 99.0
    # close reclaims back inside the zone -> FILLED
    on_bar(a, 99.5, 101.0, 99.4, 100.5)    # close 100.5 >= zone_min -> FILLED
    assert a.state == FILLED
    # stop moved to just below the sweep wick, tighter than the original 98
    assert a.stop > 98.0 and a.stop < 99.0


def test_sweep_without_reclaim_never_fills():
    """The guard: a sweep that never closes back inside must NOT enter."""
    a = long_alert()
    on_bar(a, 103, 103, 101.5, 101.5)      # TOUCHED
    on_bar(a, 101, 101.2, 99.0, 98.5)      # SWEPT, closes below
    assert a.state == SWEPT
    # keeps closing below the zone — reclaim never happens
    for p in (98.0, 97.5, 97.0):
        on_bar(a, p, p + 0.2, p - 0.2, p)
    assert a.state != FILLED
    assert a.state != TARGET
    # eventually invalidated, never entered
    assert a.state in {SWEPT, "INVALIDATED"}


def test_filled_reaches_target():
    a = long_alert()
    on_bar(a, 103, 103, 101.5, 101.5)      # TOUCHED
    on_bar(a, 101, 101.2, 99.0, 99.2)      # SWEPT
    on_bar(a, 99.5, 101, 99.4, 100.5)      # FILLED
    assert a.state == FILLED
    on_bar(a, 105, 111, 104, 110)          # high >= target -> TARGET
    assert a.state == TARGET
    assert result_r(a) > 0


def test_filled_gets_stopped():
    a = long_alert()
    on_bar(a, 103, 103, 101.5, 101.5)
    on_bar(a, 101, 101.2, 99.0, 99.2)      # SWEPT at 99.0
    on_bar(a, 99.5, 101, 99.4, 100.5)      # FILLED, stop just under 99
    assert a.state == FILLED
    on_bar(a, 99, 99, 98.0, 98.2)          # low breaks the recomputed stop
    assert a.state == STOPPED
    # Slightly worse than -1R on purpose: you enter at the ask and exit at the
    # bid, so a stop-out costs the nominal risk PLUS the spread.
    assert -1.3 < result_r(a) <= -1.0


def test_untouched_expires():
    a = long_alert(max_bars_to_touch=5)
    for _ in range(5):
        on_bar(a, 120, 121, 119, 120)      # nowhere near the zone
    assert a.state == EXPIRED


def test_short_side_symmetry():
    a = long_alert(side="short", zone_min=100, zone_max=102, entry=100, stop=104, target=92)
    on_bar(a, 99, 100.5, 99, 100.5)        # into zone -> TOUCHED
    assert a.state == TOUCHED
    on_bar(a, 101, 103.0, 101, 102.5)      # high 103 > 102 -> SWEPT
    assert a.state == SWEPT
    on_bar(a, 102, 102, 100.5, 101.0)      # close 101 <= zone_max -> FILLED
    assert a.state == FILLED


def test_terminal_is_sticky():
    a = long_alert(max_bars_to_touch=2)
    on_bar(a, 120, 121, 119, 120)
    on_bar(a, 120, 121, 119, 120)
    assert a.state == EXPIRED
    # further bars do nothing
    assert on_bar(a, 101, 101, 100.5, 101) == []
    assert a.state == EXPIRED


# ---------------------------------------------------------------------------
# The same-bar stop-out regression. 145 trades / 1 win / 139 zero-second losses.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("zone_width", [0.0, 0.001, 2.0])
def test_wick_eps_is_never_zero(zone_width):
    """Reversal setups arm at a single price, so zone width is legitimately 0."""
    a = long_alert(zone_min=100.0, zone_max=100.0 + zone_width)
    assert _wick_eps(a) > 0.0


def test_zero_width_zone_still_gets_a_usable_eps_without_atr():
    """Even with no ATR the spread/tick floors must carry it."""
    a = long_alert(zone_min=4057.37, zone_max=4057.37, entry=4057.37, atr=None)
    assert _wick_eps(a) > 0.0


@pytest.mark.parametrize("zone_width", [0.0, 0.001, 2.0])
def test_fill_never_resolves_on_the_fill_bar(zone_width):
    """A fill may not win OR lose on the bar that produced it.

    We fill on the reclaim close, so that bar's high and low are already in the
    past — judging the position against them is time travel. Previously the
    FILLED branch fell through in the same call and `l <= stop` was `l <= l`.
    """
    a = long_alert(zone_min=100.0, zone_max=100.0 + zone_width,
                   entry=100.0 + zone_width, stop=98.0, target=104.0)
    on_bar(a, 103, 103, 100.0 + zone_width, 100.0 + zone_width)   # TOUCHED
    trans = on_bar(a, 100.0, 100.2, 99.0, 100.0 + zone_width)     # sweep + reclaim
    states = [t.state for t in trans]
    assert FILLED in states, states
    assert STOPPED not in states and TARGET not in states, states
    assert a.state == FILLED


def test_a_shallow_dip_within_the_spread_is_not_a_sweep():
    """0.1 below a zone on gold is the bid/ask, not a liquidity sweep."""
    a = long_alert(zone_min=100.0, zone_max=100.0, entry=100.0, stop=98.0,
                   target=108.0, atr=2.0, min_risk=2.0, planned_rr=4.0)
    on_bar(a, 100.5, 100.5, 100.0, 100.0)     # TOUCHED
    on_bar(a, 100.0, 100.2, 99.9, 100.1)      # only 0.1 deep — inside the 0.30 spread
    assert a.state == TOUCHED


def test_sweep_stop_respects_the_volatility_floor():
    """A shallow sweep must not produce a stop inside the noise band."""
    a = long_alert(zone_min=100.0, zone_max=100.0, entry=100.0, stop=98.0,
                   target=108.0, atr=2.0, min_risk=2.0, planned_rr=4.0)
    on_bar(a, 100.5, 100.5, 100.0, 100.0)     # TOUCHED
    on_bar(a, 100.0, 100.2, 99.5, 100.1)      # real sweep, but only 0.5 deep
    assert a.state == FILLED
    # raw wick stop would be 99.5 - 0.30 = 99.20 (risk 0.8, well inside 1 ATR)
    assert 100.0 - a.stop >= 2.0, f"stop tightened to {a.stop} — inside the noise band"


def test_planned_rr_survives_the_stop_move():
    """When the stop tightens the target must come in, holding R:R constant."""
    a = long_alert(zone_min=100.0, zone_max=100.0, entry=100.0, stop=96.0,
                   target=108.0, atr=1.0, min_risk=1.0, planned_rr=2.0)
    on_bar(a, 100.5, 100.5, 100.0, 100.0)
    on_bar(a, 100.0, 100.2, 98.5, 100.1)      # sweep to 98.5, reclaim
    assert a.state == FILLED
    risk = a.entry - a.stop
    assert risk < 4.0, "stop should have tightened"
    assert abs((a.target - a.entry) / risk - 2.0) < 1e-6, "R:R must be preserved"


def test_a_corrupt_sweep_price_cannot_write_an_absurd_stop():
    """A bad tick once wrote stop=116.20 on a 4057 instrument — immortal position."""
    a = long_alert(zone_min=4057.0, zone_max=4057.0, entry=4057.0, stop=4055.0,
                   target=4065.0, atr=2.5, min_risk=2.5, planned_rr=4.0)
    on_bar(a, 4057.5, 4057.5, 4057.0, 4057.0)          # TOUCHED
    on_bar(a, 4057.0, 4057.2, 116.20, 4057.1)          # garbage low, then reclaim
    assert a.state == FILLED
    assert a.stop == 4055.0, f"absurd stop {a.stop} was accepted"


def test_result_r_is_net_of_costs_and_measured_off_the_fill():
    a = long_alert(zone_min=100.0, zone_max=100.0, entry=100.0, stop=98.0,
                   target=104.0, atr=1.0, min_risk=2.0, planned_rr=2.0)
    on_bar(a, 100.5, 100.5, 100.0, 100.0)
    on_bar(a, 100.0, 100.2, 99.5, 100.1)      # FILLED at 100.1, not the planned 100.0
    assert a.fill_price == 100.1
    on_bar(a, 100.2, 105.0, 100.2, 104.5)     # TARGET
    assert a.state == TARGET
    # spread is charged, so the realised R must fall short of the nominal 2.0
    assert 0 < result_r(a) < 2.0
