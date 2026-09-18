"""The alert state machine — the turtle-soup gate, the product's core argument.

The proposal claims this is "already built and tested"; it wasn't. This is it.

Per armed zone, an alert walks a state machine driven by live price:

    WATCHING → TOUCHED → SWEPT → FILLED → {TARGET | STOPPED}
             ↘ EXPIRED (untouched too long)
             ↘ INVALIDATED (zone broken the wrong way before entry)

The whole economic point: an ENTER (FILLED) can ONLY be reached after a recorded
SWEEP beyond the zone edge AND a RECLAIM (close back inside). There is no path
from "price looks nice" to "enter now". The stop is then placed just beyond the
sweep wick — a tighter stop than the zone edge, so the same risk buys a better
position. This is enforced here, in the engine, never in the UI.

Pure logic over price/candle data — no DB, no network, unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import execution

WATCHING = "WATCHING"
TOUCHED = "TOUCHED"
SWEPT = "SWEPT"
FILLED = "FILLED"
TARGET = "TARGET"
STOPPED = "STOPPED"
EXPIRED = "EXPIRED"
INVALIDATED = "INVALIDATED"

TERMINAL = {TARGET, STOPPED, EXPIRED, INVALIDATED}


@dataclass
class Alert:
    """One armed zone. Prices are absolute; side is 'long' or 'short'."""
    pair: str
    timeframe: str
    side: str
    source: str                 # sr | supply_demand | fvg
    zone_min: float
    zone_max: float
    entry: float
    stop: float
    target: float
    score: float = 0.0
    confluence: int = 0
    size: float = 0.0           # paper position size (lots), set when entered
    state: str = WATCHING
    swept_price: float | None = None   # wick extreme when SWEPT (for stop recompute)
    swept: bool = False                # did a sweep ever occur (for the journal)
    bars_since_arm: int = 0
    max_bars_to_touch: int = 60        # EXPIRE if untouched this long
    id: int | None = None              # set by the persistence layer
    # --- risk context captured at arm time -----------------------------------
    # These exist so the engine can never tighten a stop below what the
    # instrument's volatility justifies, and so R:R survives a stop move.
    atr: float | None = None           # ATR on this alert's timeframe, at arm time
    min_risk: float | None = None      # volatility floor on |entry - stop|
    planned_rr: float | None = None    # R:R at arm time, preserved across stop moves
    fill_price: float | None = None    # the price actually filled at (not the plan's)
    fill_epoch: int | None = None

    def rr(self) -> float:
        risk = abs(self.entry - self.stop)
        return abs(self.target - self.entry) / risk if risk else 0.0


@dataclass
class Transition:
    state: str
    price: float
    reason: str


def _wick_eps(alert: Alert) -> float:
    """A sweep must exceed the zone edge by a hair, not just touch it.

    The hair must never be ZERO. A zero-width zone is a legitimate concept — a
    single price level, which is how reversal setups arm — but with eps == 0 the
    post-sweep stop lands exactly on the sweep wick and the very next bar test
    (`l <= stop`) is trivially true. That produced 145 trades, 1 win, and 139
    same-second stop-outs before it was caught, so the floors below are load
    bearing, not defensive padding.

    Floors, widest wins:
      - 5% of ATR   — scales the hair to the instrument's own volatility
      - the spread  — a "sweep" that only swept the bid/ask is not a sweep
      - two ticks   — last resort when ATR is unavailable or zero
    """
    width = max(alert.zone_max - alert.zone_min, 0.0)
    floor_atr = 0.05 * alert.atr if alert.atr else 0.0
    floor_cost = execution.spread_for(alert.pair)
    floor_tick = 2.0 * (execution.PIP_SIZE_BY_PAIR.get(alert.pair) or 0.0)
    return max(0.02 * width, floor_atr, floor_cost, floor_tick)


def _clamped_sweep_stop(alert: Alert, eps: float, long: bool) -> float:
    """The post-sweep stop, never tighter than the volatility floor.

    Tightening the stop onto the sweep wick is the whole point of the turtle-soup
    gate — the same risk buys a better position. But the wick can be one tick
    away, and a stop inside the noise band is a guaranteed loss dressed up as
    good risk management. `min_risk` is the floor; the sanity check catches a bad
    tick writing an absurd stop (one such row survived with stop=116.20 on a
    4057 instrument, which made the position immortal).
    """
    cand = (alert.swept_price - eps) if long else (alert.swept_price + eps)
    floor = alert.min_risk or 0.0
    stopped = min(cand, alert.entry - floor) if long else max(cand, alert.entry + floor)
    # Refuse an obviously corrupt level rather than persisting it.
    if alert.atr and abs(alert.entry - stopped) > 10.0 * alert.atr:
        return alert.stop
    if (long and stopped >= alert.entry) or (not long and stopped <= alert.entry):
        return alert.stop
    return stopped


def on_bar(alert: Alert, o: float, h: float, l: float, c: float,
           closed: bool = True) -> list[Transition]:
    """Advance the alert given one bar (or a synthetic bar from a tick where
    o=h=l=c=price). `closed` is True only for a fully-closed bar — the monitor
    polls the live forming bar many times, so bar-counting (expiry) must happen
    once per real close, not per poll. Returns the transitions; mutates alert."""
    out: list[Transition] = []
    if alert.state in TERMINAL:
        return out

    long = alert.side == "long"
    eps = _wick_eps(alert)

    def fire(state: str, reason: str, price: float):
        alert.state = state
        out.append(Transition(state, price, reason))

    # ---- WATCHING → TOUCHED : price enters the zone --------------------------
    if alert.state == WATCHING:
        if closed:
            alert.bars_since_arm += 1
        touched = (l <= alert.zone_max and h >= alert.zone_min)
        if touched:
            fire(TOUCHED, f"price tapped {alert.source} zone "
                          f"{alert.zone_min:.2f}–{alert.zone_max:.2f}", c)
        elif closed and alert.bars_since_arm >= alert.max_bars_to_touch:
            fire(EXPIRED, f"no touch in {alert.max_bars_to_touch} bars", c)
            return out
        else:
            return out

    # ---- TOUCHED → SWEPT : wick runs beyond the zone edge --------------------
    if alert.state == TOUCHED:
        if long and l < alert.zone_min - eps:
            alert.swept = True
            alert.swept_price = l
            fire(SWEPT, f"swept below zone to {l:.2f}", c)
        elif not long and h > alert.zone_max + eps:
            alert.swept = True
            alert.swept_price = h
            fire(SWEPT, f"swept above zone to {h:.2f}", c)
        # a decisive close beyond the zone without ever reclaiming can invalidate
        elif long and c < alert.zone_min - 3 * eps:
            fire(INVALIDATED, "closed well below zone without reclaim", c)
            return out
        elif not long and c > alert.zone_max + 3 * eps:
            fire(INVALIDATED, "closed well above zone without reclaim", c)
            return out

    # ---- SWEPT → FILLED : a close reclaims back inside the zone --------------
    if alert.state == SWEPT:
        reclaimed = (long and c >= alert.zone_min) or (not long and c <= alert.zone_max)
        if reclaimed:
            # Capture R:R BEFORE moving the stop. Reading alert.rr() afterwards is
            # an identity no-op — it returns |target-entry|/risk_new, so
            # entry + that * risk_new == target_old and the target never moves.
            rr0 = alert.planned_rr or alert.rr()
            if alert.swept_price is not None:
                alert.stop = _clamped_sweep_stop(alert, eps, long)
            risk = abs(alert.entry - alert.stop)
            alert.target = (alert.entry + rr0 * risk) if long else (alert.entry - rr0 * risk)
            alert.fill_price = c
            fire(FILLED, f"sweep + reclaim confirmed, closed back inside at {c:.2f}", c)
            # A fill may NOT resolve on its own bar. We fill on the reclaim close,
            # so this bar's high and low are already in the past at the moment of
            # entry — testing them against the stop is not pessimism, it is time
            # travel. Let the next bar (or the next live tick) judge the position.
            return out

    # ---- FILLED → TARGET / STOPPED ------------------------------------------
    if alert.state == FILLED:
        if long:
            if l <= alert.stop:
                fire(STOPPED, f"stopped at {alert.stop:.2f}", alert.stop)
            elif h >= alert.target:
                fire(TARGET, f"target hit at {alert.target:.2f}", alert.target)
        else:
            if h >= alert.stop:
                fire(STOPPED, f"stopped at {alert.stop:.2f}", alert.stop)
            elif l <= alert.target:
                fire(TARGET, f"target hit at {alert.target:.2f}", alert.target)

    return out


def result_r(alert: Alert, exit_price: float | None = None) -> float:
    """R-multiple for a closed alert, measured off the FILL and net of costs.

    The old version returned a flat +rr / -1.0 computed from the *planned* entry.
    Three things were wrong with that: the fill is not the plan, the exit rarely
    lands exactly on the level, and it ignored the spread that `cash_delta`
    already charges — so R and cash could disagree about the same trade.
    """
    if alert.state not in (TARGET, STOPPED):
        return 0.0
    entry = alert.fill_price if alert.fill_price is not None else alert.entry
    risk = abs(entry - alert.stop)
    if risk <= 0:
        return 0.0
    if exit_price is None:
        exit_price = alert.target if alert.state == TARGET else alert.stop
    return round(execution.r_multiple(alert.pair, alert.side, entry, exit_price, risk), 3)
