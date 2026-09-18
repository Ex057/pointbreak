"""End-of-day "what-if" counterfactual engine.

The deterministic planner vetoes most setups it scans (structure conflict, MA
bias, R:R below the minimum, low score, overlap, ...). Each veto is journaled in
`setup_journal`. This module replays those rejected setups against the prices
that *actually* printed after they were seen, works out what each one **would
have done** (filled? hit target or stop? how many R?), and attributes the profit
left on the table — or the loss correctly avoided — to the specific veto gate.

The output is a structured `EodReview` consumed by three things: the UI panel,
the learner (`learning.py` turns the per-setup replays into counterfactual
training samples), and the Claude strategy analyst (`strategy_review.py`).

Nothing here changes a trading decision. It only measures and advises.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..config import MIN_RISK_ATR_FRAC, SWEEP_TARGET_ATR_MULT
from ..db import repo
from . import markets

# A gate is flagged as a strategy *flaw* when its rejections were systematically
# profitable: enough of them filled, most of those won, and the missed R is
# material. Deliberately conservative so a single lucky reject isn't a "flaw".
FLAW_MIN_FILLED = 3
FLAW_MIN_WIN_RATE = 0.5
FLAW_MIN_MISSED_R = 2.0

# Human labels for each veto gate.
GATE_LABELS = {
    "structure_conflict": "Structure conflict veto",
    "ma_bias_conflict": "MA-bias conflict veto",
    "rr_below_min": "R:R-below-minimum veto",
    "degenerate_stop": "Degenerate-stop veto",
    "discarded_other": "Discarded (other)",
    "blocked_max_active": "Max-active-positions block",
    "blocked_score": "Low-score block",
    "blocked_overlap": "Overlap block",
    "blocked_sanity": "Sanity-check block",
    "blocked_quality": "Low-learned-quality block",
    "waiting_no_trigger": "Waiting — trigger never confirmed",
    "retired": "Retired before entry",
    "feed_blocked": "Feed blocked",
    "expired": "Expired",
    "scan_failed": "Scan failed",
}

# Statuses that carry no replayable trade levels — counted, never replayed.
_UNREPLAYABLE_GATES = {"feed_blocked", "scan_failed"}

# Journal statuses we treat as rejections (i.e. NOT "armed"/taken).
_REJECTION_STATUSES = {
    "discarded", "waiting", "blocked_max_active", "blocked_score",
    "blocked_overlap", "blocked_sanity", "blocked_quality", "retired",
    "feed_blocked", "expired", "scan_failed",
}


def _gate_of(row: dict) -> str:
    status = (row.get("status") or "").lower()
    reason = (row.get("reason") or "").lower()
    if status == "discarded":
        if "structure" in reason:
            return "structure_conflict"
        if "moving-average" in reason or "ma bias" in reason or "bias" in reason:
            return "ma_bias_conflict"
        if "r:r" in reason or "obstacle" in reason or "minimum" in reason:
            return "rr_below_min"
        if "stop" in reason:
            return "degenerate_stop"
        return "discarded_other"
    return status or "unknown"


def _num(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def replay_setup(setup: dict, candles: list[dict]) -> dict:
    """Pure, fully deterministic replay of a single rejected setup against a
    forward window of candles. No I/O — unit-testable in isolation.

    Fill model: a setup is a limit entry at its zone. A long fills when price
    trades down to `entry` (candle low <= entry); a short fills when price trades
    up to `entry` (candle high >= entry). After the fill, whichever of stop /
    target is touched first resolves it. **Conservative intrabar rule:** if a
    single candle spans both stop and target, we assume the stop was hit first —
    never flatter a rejected setup.

    Returns {filled, outcome, counterfactual_r, rr_target, fill_epoch,
    resolve_epoch}. `outcome` is one of target | stopped | open | unfilled |
    unavailable.
    """
    side = (setup.get("side") or "").lower()
    entry = _num(setup.get("entry"))
    stop = _num(setup.get("stop"))
    target = _num(setup.get("target"))
    base = {
        "filled": False, "outcome": "unavailable", "counterfactual_r": 0.0,
        "rr_target": None, "fill_epoch": None, "resolve_epoch": None,
    }
    if side not in ("long", "short") or entry is None or stop is None or target is None:
        return base
    risk = abs(entry - stop)
    if risk <= 0 or not candles:
        return base
    rr_target = round(abs(target - entry) / risk, 3)
    base["rr_target"] = rr_target
    long = side == "long"

    filled = False
    fill_epoch = None
    for c in candles:
        hi = _num(c.get("high"))
        lo = _num(c.get("low"))
        if hi is None or lo is None:
            continue
        if not filled:
            hit_entry = lo <= entry if long else hi >= entry
            if not hit_entry:
                continue
            filled = True
            fill_epoch = int(c.get("epoch")) if c.get("epoch") is not None else None
        # filled on this candle or an earlier one — check for resolution
        if long:
            hit_stop = lo <= stop
            hit_target = hi >= target
        else:
            hit_stop = hi >= stop
            hit_target = lo <= target
        if hit_stop:  # stop-first tiebreak covers the both-in-one-candle case
            return {**base, "filled": True, "outcome": "stopped",
                    "counterfactual_r": -1.0, "fill_epoch": fill_epoch,
                    "resolve_epoch": int(c.get("epoch")) if c.get("epoch") is not None else None}
        if hit_target:
            return {**base, "filled": True, "outcome": "target",
                    "counterfactual_r": rr_target, "fill_epoch": fill_epoch,
                    "resolve_epoch": int(c.get("epoch")) if c.get("epoch") is not None else None}
    if filled:
        return {**base, "filled": True, "outcome": "open",
                "counterfactual_r": 0.0, "fill_epoch": fill_epoch}
    return {**base, "filled": False, "outcome": "unfilled"}


def _formation(candles: list[dict], i: int, direction: str, atr: float) -> dict:
    """Characterize *how a setup formed* at candle `i` from OHLC alone, so a
    detection blind spot becomes a reusable, human-readable template ("5 down bars
    → hammer sweeping the prior low, close below SMA20, 03:00 EAT") rather than
    just a flagged miss. This is the memory the planner can later learn to detect."""
    def num(k, key):
        return _num(candles[k].get(key)) if 0 <= k < len(candles) else None

    o, c, h, lo = num(i, "open"), num(i, "close"), num(i, "high"), num(i, "low")
    if None in (o, c, h, lo) or h <= lo:
        return {"summary": "formation unavailable"}
    rng = h - lo
    body_frac = round(abs(c - o) / rng, 2)
    lower_wick = round((min(o, c) - lo) / rng, 2)
    upper_wick = round((h - max(o, c)) / rng, 2)

    # momentum run into the origin (down bars before a long, up bars before a short)
    run = 0
    for k in range(i - 1, -1, -1):
        ck, ok = num(k, "close"), num(k, "open")
        if ck is None or ok is None:
            break
        down = ck < ok
        if (direction == "long" and down) or (direction == "short" and not down):
            run += 1
        else:
            break

    # liquidity sweep: did the origin take out the prior W bars' extreme?
    W = 5
    if direction == "long":
        prior = [num(k, "low") for k in range(max(0, i - W), i) if num(k, "low") is not None]
        swept = bool(prior) and lo < min(prior)
    else:
        prior = [num(k, "high") for k in range(max(0, i - W), i) if num(k, "high") is not None]
        swept = bool(prior) and h > max(prior)

    closes = [num(k, "close") for k in range(max(0, i - 19), i + 1) if num(k, "close") is not None]
    sma = sum(closes) / len(closes) if closes else c
    sma_pos = "below" if c < sma else "above"
    atr_ratio = round(rng / atr, 2) if atr > 0 else None
    hour_eat = int(((int(candles[i].get("epoch") or 0)) + 3 * 3600) // 3600) % 24

    wick = lower_wick if direction == "long" else upper_wick
    if direction == "long" and lower_wick >= 0.4 and body_frac <= 0.4:
        kind = "hammer/reclaim"
    elif direction == "short" and upper_wick >= 0.4 and body_frac <= 0.4:
        kind = "rejection/shooting-star"
    else:
        kind = "reversal bar"

    lead = f"{run} {'down' if direction == 'long' else 'up'} bar(s) → " if run else ""
    summary = (
        f"{lead}{kind} ({int(wick * 100)}% {'lower' if direction == 'long' else 'upper'} wick)"
        f"{', swept prior ' + ('low' if direction == 'long' else 'high') if swept else ''}"
        f", close {sma_pos} SMA20, {hour_eat:02d}:00 EAT"
    )
    return {
        "run_before": run,
        "body_frac": body_frac,
        "lower_wick_frac": lower_wick,
        "upper_wick_frac": upper_wick,
        "swept": swept,
        "sma_pos": sma_pos,
        "atr_ratio": atr_ratio,
        "hour_eat": hour_eat,
        "kind": kind,
        "summary": summary,
    }


def _atr(candles: list[dict]) -> float:
    ranges = []
    for c in candles:
        hi = _num(c.get("high"))
        lo = _num(c.get("low"))
        if hi is not None and lo is not None and hi >= lo:
            ranges.append(hi - lo)
    if not ranges:
        return 0.0
    return sum(ranges) / len(ranges)


def detect_missed_moves(
    candles: list[dict],
    coverage: list[dict],
    *,
    min_rr: float = 2.0,
    pivot_k: int = 2,
    price_tol: float = 0.003,
    max_moves: int = 8,
) -> list[dict]:
    """Work *backwards from price*: find the day's real swing moves that would have
    made >= `min_rr` (entry at the reclaim close, stop just beyond the swing extreme —
    a realistic scalp stop, not a tiny ATR fraction), then check whether the system
    had ANY coverage near each move's origin. A move with no nearby journal entry,
    alert or taken trade is a **detection blind spot** — a move the planner never even
    proposed a candidate for (which the journal-replay in review_day cannot see,
    because nothing was ever journaled). Overlapping same-leg origins are collapsed
    into one opportunity so a single rally isn't counted many times.

    `coverage` anchors are dicts {price, epoch, kind} where kind ∈
    vetoed | armed | taken. Pure and unit-testable.
    """
    n = len(candles)
    if n < 2 * pivot_k + 2:
        return []
    atr = _atr(candles)
    if atr <= 0:
        return []
    highs = [_num(c.get("high")) for c in candles]
    lows = [_num(c.get("low")) for c in candles]
    eps = [int(c.get("epoch") or 0) for c in candles]

    def _classify(origin_price: float, origin_epoch: int, peak_epoch: int) -> str:
        best = "none"
        rank = {"none": 0, "vetoed": 1, "armed": 2, "taken": 3}
        for a in coverage:
            ap = _num(a.get("price"))
            ae = a.get("epoch")
            if ap is None or ap <= 0:
                continue
            if abs(ap - origin_price) / origin_price > price_tol:
                continue
            if ae is not None and (int(ae) < origin_epoch - 2 * 3600 or int(ae) > peak_epoch + 3600):
                continue
            kind = a.get("kind") or "vetoed"
            if rank.get(kind, 1) > rank[best]:
                best = kind
        return best

    closes = [_num(c.get("close")) for c in candles]
    # a realistic stop sits beyond the swing extreme, not a tiny fraction of it, so
    # R reflects a stop a scalper could actually place (not a 40R illusion).
    stop_buffer = max(0.2 * atr, 1e-9)

    moves: list[dict] = []
    for i in range(pivot_k, n - pivot_k):
        lo = lows[i]
        hi = highs[i]
        entry = closes[i] if closes[i] is not None else None
        if lo is None or hi is None or entry is None:
            continue
        # swing low -> up move (long). Entry is the reclaim close; stop below the low.
        if all(lows[j] is not None and lo <= lows[j] for j in range(i - pivot_k, i + pivot_k + 1)):
            peak, peak_idx = lo, i
            for k in range(i + 1, n):
                if lows[k] is not None and lows[k] < lo:
                    break  # move invalidated — price broke the origin low
                if highs[k] is not None and highs[k] > peak:
                    peak, peak_idx = highs[k], k
            stop = lo - stop_buffer
            # floor risk so a tiny/flat origin candle can't inflate R
            risk = max(entry - stop, MIN_RISK_ATR_FRAC * atr)
            rr = (peak - entry) / risk if risk > 0 else 0.0
            if peak_idx > i and rr >= min_rr:
                cov = _classify(lo, eps[i], eps[peak_idx])
                moves.append({
                    "direction": "long", "origin_idx": i,
                    "origin_epoch": eps[i], "origin_price": round(lo, 4),
                    "entry": round(entry, 4), "stop": round(stop, 4),
                    "peak_epoch": eps[peak_idx], "peak_price": round(peak, 4),
                    "rr": round(rr, 2), "coverage_kind": cov,
                    "covered": cov != "none",
                })
        # swing high -> down move (short). Entry is the reclaim close; stop above the high.
        if all(highs[j] is not None and hi >= highs[j] for j in range(i - pivot_k, i + pivot_k + 1)):
            trough, trough_idx = hi, i
            for k in range(i + 1, n):
                if highs[k] is not None and highs[k] > hi:
                    break
                if lows[k] is not None and lows[k] < trough:
                    trough, trough_idx = lows[k], k
            stop = hi + stop_buffer
            risk = max(stop - entry, MIN_RISK_ATR_FRAC * atr)
            rr = (entry - trough) / risk if risk > 0 else 0.0
            if trough_idx > i and rr >= min_rr:
                cov = _classify(hi, eps[i], eps[trough_idx])
                moves.append({
                    "direction": "short", "origin_idx": i,
                    "origin_epoch": eps[i], "origin_price": round(hi, 4),
                    "entry": round(entry, 4), "stop": round(stop, 4),
                    "peak_epoch": eps[trough_idx], "peak_price": round(trough, 4),
                    "rr": round(rr, 2), "coverage_kind": cov,
                    "covered": cov != "none",
                })

    # Drop moves whose origin candle is degenerate (flat/no formation) BEFORE the
    # dedup — otherwise a phantom (tiny stop → inflated R, e.g. the "122R" case) sorts
    # first and suppresses the real overlapping opportunity. Compute the formation once
    # here and carry it through.
    real: list[dict] = []
    for m in moves:
        idx = m.pop("origin_idx", None)
        formation = _formation(candles, idx, m["direction"], atr) if idx is not None else {}
        if formation.get("summary") == "formation unavailable":
            continue
        m["formation"] = formation
        real.append(m)

    # Collapse moves that are really the SAME leg seen from different swing origins:
    # for one direction, if two moves' [origin, peak] time windows overlap they belong
    # to one opportunity — keep the strongest. This stops one big rally being counted
    # a dozen times and inflating the missed-R total.
    real.sort(key=lambda m: m["rr"], reverse=True)
    kept: list[dict] = []
    for m in real:
        clash = any(
            m["direction"] == k["direction"]
            and m["origin_epoch"] <= k["peak_epoch"]
            and k["origin_epoch"] <= m["peak_epoch"]
            for k in kept
        )
        if clash:
            continue
        kept.append(m)
        if len(kept) >= max_moves:
            break
    return kept


def detect_live_sweep_reclaim(
    candles: list[dict],
    *,
    min_rr: float = 2.0,
    pivot_k: int = 2,
    lookback: int = 12,
    sweep_w: int = 5,
    stop_buffer_atr: float = 0.25,
) -> dict | None:
    """Detect a *live* liquidity-sweep-reclaim at the current bar — the formation
    behind almost every missed opportunity in the history scan. A long fires when a
    recent swing low swept the prior lows and price has closed back above it (the
    reclaim); a short mirrors it. Returns an armable candidate {side, entry, stop,
    target, rr, formation} only if it is *still valid*: the nearest obstacle leaves
    >= min_rr ahead, so we enter the part of the move that hasn't happened yet.
    Returns None when no valid setup is forming. Pure and unit-testable.
    """
    n = len(candles)
    if n < 2 * pivot_k + 3:
        return None
    atr = _atr(candles)
    if atr <= 0:
        return None
    highs = [_num(c.get("high")) for c in candles]
    lows = [_num(c.get("low")) for c in candles]
    closes = [_num(c.get("close")) for c in candles]
    eps = [int(c.get("epoch") or 0) for c in candles]
    last_close = closes[-1]
    if last_close is None:
        return None
    buffer = max(stop_buffer_atr * atr, 1e-9)
    measured = SWEEP_TARGET_ATR_MULT * atr  # measured-move reach into open space

    # structural pivot lows/highs across the window — the real obstacles a move
    # can target. Reused for both directions.
    piv_lows = [
        lows[k] for k in range(pivot_k, n - pivot_k)
        if lows[k] is not None
        and all(lows[j] is not None and lows[k] <= lows[j] for j in range(k - pivot_k, k + pivot_k + 1))
    ]
    piv_highs = [
        highs[k] for k in range(pivot_k, n - pivot_k)
        if highs[k] is not None
        and all(highs[j] is not None and highs[k] >= highs[j] for j in range(k - pivot_k, k + pivot_k + 1))
    ]

    best: dict | None = None
    for i in range(max(pivot_k, n - lookback), n - pivot_k):
        lo, hi = lows[i], highs[i]
        if lo is None or hi is None:
            continue
        # ---- long: swept swing low, reclaimed
        if all(lows[j] is not None and lo <= lows[j] for j in range(i - pivot_k, i + pivot_k + 1)):
            prior = [lows[k] for k in range(max(0, i - sweep_w), i) if lows[k] is not None]
            if prior and lo < min(prior) and last_close > lo:
                entry = last_close
                stop = lo - buffer
                # The volatility floor must move the STOP, not just the R:R
                # denominator. Flooring only the denominator advertises an R
                # computed against a risk the trade never actually takes — the
                # candidate then arms with a raw sub-ATR stop that noise removes.
                risk = max(entry - stop, MIN_RISK_ATR_FRAC * atr)
                stop = entry - risk
                # reach = the larger of an ATR measured move and the swept leg height
                # (so a big rejection projects a proportionally bigger target); target
                # = the FURTHEST of the structural highs above and that reach, so a
                # genuine continuation clears min R:R instead of being capped short.
                leg_hi = max((h for h in (highs[max(0, i - lookback):i]) if h is not None), default=entry)
                reach = max(measured, leg_hi - lo)
                highs_above = [h for h in piv_highs if h > entry]
                target = max(highs_above + [entry + reach])
                rr = (target - entry) / risk if risk > 0 else 0.0
                if risk > 0 and rr >= min_rr and (best is None or i > best["_i"]):
                    best = {
                        "side": "long", "entry": round(entry, 4), "stop": round(stop, 4),
                        "target": round(target, 4), "rr": round(rr, 2),
                        "origin_epoch": eps[i], "formation": _formation(candles, i, "long", atr),
                        "atr": round(atr, 6), "min_risk": round(MIN_RISK_ATR_FRAC * atr, 6),
                        "_i": i,
                    }
        # ---- short: swept swing high, reclaimed
        if all(highs[j] is not None and hi >= highs[j] for j in range(i - pivot_k, i + pivot_k + 1)):
            prior = [highs[k] for k in range(max(0, i - sweep_w), i) if highs[k] is not None]
            if prior and hi > max(prior) and last_close < hi:
                entry = last_close
                stop = hi + buffer
                risk = max(stop - entry, MIN_RISK_ATR_FRAC * atr)  # see the long branch
                stop = entry + risk
                leg_lo = min((x for x in (lows[max(0, i - lookback):i]) if x is not None), default=entry)
                reach = max(measured, hi - leg_lo)
                lows_below = [x for x in piv_lows if x < entry]
                target = min(lows_below + [entry - reach])
                rr = (entry - target) / risk if risk > 0 else 0.0
                if risk > 0 and rr >= min_rr and (best is None or i > best["_i"]):
                    best = {
                        "side": "short", "entry": round(entry, 4), "stop": round(stop, 4),
                        "target": round(target, 4), "rr": round(rr, 2),
                        "origin_epoch": eps[i], "formation": _formation(candles, i, "short", atr),
                        "atr": round(atr, 6), "min_risk": round(MIN_RISK_ATR_FRAC * atr, 6),
                        "_i": i,
                    }
    if best is not None:
        best.pop("_i", None)
    return best


def day_bounds(day: str | None = None, now_ts: int | None = None) -> tuple[int, str, int]:
    """Return (start_epoch, day_label, end_epoch) for a UTC calendar day. When
    `day` is None, uses the day containing `now_ts` (or the current time)."""
    if day:
        dt = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        ref = now_ts if now_ts is not None else int(datetime.now(timezone.utc).timestamp())
        dt = datetime.fromtimestamp(ref, tz=timezone.utc)
    start = int(datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc).timestamp())
    end = start + 86400 - 1
    return start, dt.strftime("%Y-%m-%d"), end


def review_day(
    pair: str,
    day: str | None = None,
    *,
    min_rr: float = 2.0,
    now_ts: int | None = None,
    journal_rows: list[dict] | None = None,
    closed_rows: list[dict] | None = None,
    candle_loader=None,
) -> dict:
    """Replay the day's rejections for `pair` and produce a structured EodReview.

    `journal_rows` / `closed_rows` / `candle_loader` are injectable for tests;
    in production they default to the repo + market history layer.
    """
    day_start, day_label, day_end = day_bounds(day, now_ts=now_ts)
    if journal_rows is None:
        journal_rows = repo.list_setup_journal(pair, since=day_start, until=day_end, limit=600)
    if closed_rows is None:
        closed_rows = repo.closed_ledger(pair, since=day_start, until=day_end)
    if candle_loader is None:
        candle_loader = lambda tf, start, end: markets.load_candle_window(pair, tf, start, end)  # noqa: E731

    realized_r = round(sum(float(r.get("result_r") or 0.0) for r in closed_rows), 3)

    rejections = [r for r in journal_rows if (r.get("status") or "").lower() in _REJECTION_STATUSES]

    gates: dict[str, dict] = {}
    replays: list[dict] = []
    missed_winners: list[dict] = []
    scored = 0

    for row in rejections:
        gate = _gate_of(row)
        g = gates.setdefault(gate, {
            "gate": gate, "label": GATE_LABELS.get(gate, gate),
            "count": 0, "filled": 0, "unfilled": 0, "would_have_won": 0,
            "would_have_lost": 0, "still_open": 0, "unscored": 0,
            "missed_r": 0.0, "avoided_loss_r": 0.0, "cf_r_sum": 0.0,
        })
        g["count"] += 1

        entry_tf = row.get("entry_tf") or row.get("timeframe") or "5m"
        first_seen = int(row.get("first_seen_at") or row.get("created_at") or day_start)

        if gate in _UNREPLAYABLE_GATES:
            g["unscored"] += 1
            continue

        candles = candle_loader(entry_tf, first_seen, day_end) or []
        result = replay_setup(row, candles)
        if result["outcome"] == "unavailable":
            g["unscored"] += 1
            continue

        scored += 1
        cf_r = float(result["counterfactual_r"])
        g["cf_r_sum"] += cf_r
        if result["filled"]:
            g["filled"] += 1
        else:
            g["unfilled"] += 1
        if result["outcome"] == "target":
            g["would_have_won"] += 1
            g["missed_r"] += cf_r
            missed_winners.append({
                "gate": gate, "side": row.get("side"), "source": row.get("source"),
                "entry": _num(row.get("entry")), "stop": _num(row.get("stop")),
                "target": _num(row.get("target")), "counterfactual_r": round(cf_r, 3),
                "first_seen_at": first_seen, "reason": row.get("reason"),
            })
        elif result["outcome"] == "stopped":
            g["would_have_lost"] += 1
            g["avoided_loss_r"] += -cf_r  # 1.0 per correctly-avoided loss
        elif result["outcome"] == "open":
            g["still_open"] += 1

        replays.append({
            "gate": gate, "side": row.get("side"), "source": row.get("source"),
            "trader_mode": row.get("trader_mode"), "entry_tf": entry_tf,
            "filled": result["filled"], "outcome": result["outcome"],
            "counterfactual_r": round(cf_r, 3),
        })

    gate_list = []
    flaws: list[str] = []
    for gate, g in gates.items():
        filled = g["filled"]
        won = g["would_have_won"]
        win_rate = round(won / filled, 3) if filled else None
        fill_rate = round(filled / max(1, filled + g["unfilled"]), 3)
        avg_cf_r = round(g["cf_r_sum"] / max(1, filled + g["unfilled"]), 3)
        missed_r = round(g["missed_r"], 3)
        flaw = (
            filled >= FLAW_MIN_FILLED
            and win_rate is not None and win_rate >= FLAW_MIN_WIN_RATE
            and missed_r >= FLAW_MIN_MISSED_R
        )
        note = None
        if flaw:
            note = (
                f"{g['label']} rejected {won} setups that would have hit target "
                f"(+{missed_r:.2f}R left on the table); this gate looks too strict."
            )
            flaws.append(note)
        gate_list.append({
            **g,
            "missed_r": missed_r,
            "avoided_loss_r": round(g["avoided_loss_r"], 3),
            "avg_cf_r": avg_cf_r,
            "fill_rate": fill_rate,
            "win_rate": win_rate,
            "flaw": flaw,
            "note": note,
        })
        g.pop("cf_r_sum", None)

    gate_list.sort(key=lambda x: x["missed_r"], reverse=True)
    missed_winners.sort(key=lambda x: x["counterfactual_r"], reverse=True)
    missed_r_total = round(sum(g["missed_r"] for g in gate_list), 3)
    avoided_loss_total = round(sum(g["avoided_loss_r"] for g in gate_list), 3)

    # -- detection blind spots: real moves the planner never proposed a candidate for
    entry_tf = _dominant_entry_tf(rejections)
    day_candles = candle_loader(entry_tf, day_start, day_end) or []
    coverage = _coverage_anchors(rejections, journal_rows, closed_rows)
    missed_moves = detect_missed_moves(day_candles, coverage, min_rr=min_rr)
    blind = [m for m in missed_moves if m["coverage_kind"] == "none"]
    biggest_blind_r = round(max((m["rr"] for m in blind), default=0.0), 2)
    if blind:
        flaws.append(
            f"{len(blind)} tradable move(s) got no candidate at all — the biggest was "
            f"{biggest_blind_r:.1f}R. The planner isn't detecting these setups (likely no "
            f"zone marked / trend filter suppressed the reversal), so no veto is even logged."
        )

    summary = _summary(pair, day_label, realized_r, len(closed_rows), len(rejections),
                       scored, missed_r_total, avoided_loss_total, flaws, len(blind), biggest_blind_r)

    return {
        "pair": pair,
        "day": day_label,
        "day_start": day_start,
        "day_end": day_end,
        "entry_tf": entry_tf,
        "realized_r": realized_r,
        "taken_trades": len(closed_rows),
        "rejections_total": len(rejections),
        "rejections_scored": scored,
        "missed_r": missed_r_total,
        "avoided_loss_r": avoided_loss_total,
        "gates": gate_list,
        "missed_winners": missed_winners[:8],
        "missed_moves": missed_moves,
        "missed_moves_total": len(missed_moves),
        "missed_moves_blind": len(blind),
        "biggest_blind_r": biggest_blind_r,
        "flaws": flaws,
        "summary": summary,
        "replays": replays,
    }


# Finest-first preference for which recorded timeframe to scan history on.
_SCAN_TF_PREFERENCE = ["1m", "5m", "15m", "30m", "1H", "4H", "1D"]


def scan_history(
    pair: str,
    *,
    days: int = 60,
    min_rr: float = 2.0,
    scan_tf: str | None = None,
    now_ts: int | None = None,
    timeframes_reader=None,
    candle_reader=None,
    journal_reader=None,
    closed_reader=None,
) -> dict:
    """Replay ALL recorded history (up to `days` back) day by day and surface every
    missed opportunity with its formation and *why* it was missed — a detection
    blind spot (no candidate) or a specific veto gate. This is the backfill the
    single-day EOD review can't give you: it works off the candles we've already
    stored, so it needs no live feed.

    Readers are injectable for tests; production wires them to repo/markets.
    """
    from ..db import repo  # lazy import
    if timeframes_reader is None:
        timeframes_reader = repo.candle_timeframes
    if candle_reader is None:
        candle_reader = lambda tf, s, e: repo.read_candles(pair, tf, s, e)  # noqa: E731
    if journal_reader is None:
        journal_reader = lambda p, s, e: repo.list_setup_journal(p, s, e, limit=600)  # noqa: E731
    if closed_reader is None:
        closed_reader = repo.closed_ledger

    if scan_tf is None:
        available = set(timeframes_reader(pair) or [])
        scan_tf = next((tf for tf in _SCAN_TF_PREFERENCE if tf in available), None)
    if scan_tf is None:
        return {
            "pair": pair, "scan_tf": None, "days_scanned": 0, "opportunities": [],
            "per_day": [], "totals": {"blind": 0, "vetoed": 0, "missed_r_blind": 0.0},
            "summary": f"No recorded candles for {pair.upper()} yet — let the feed run, then scan.",
        }

    lo, hi = repo.candle_range(pair, scan_tf)
    if lo is None:
        return {
            "pair": pair, "scan_tf": scan_tf, "days_scanned": 0, "opportunities": [],
            "per_day": [], "totals": {"blind": 0, "vetoed": 0, "missed_r_blind": 0.0},
            "summary": f"No recorded {scan_tf} candles for {pair.upper()} yet.",
        }

    now = now_ts if now_ts is not None else int(datetime.now(timezone.utc).timestamp())
    window_start = max(lo, now - days * 86400)
    first_day_start, _, _ = day_bounds(now_ts=window_start)
    last_day_start, _, _ = day_bounds(now_ts=hi)

    opportunities: list[dict] = []
    per_day: list[dict] = []
    combined_gates: dict[str, dict] = {}
    days_scanned = 0
    d = first_day_start
    while d <= last_day_start:
        day_start, day_label, day_end = day_bounds(now_ts=d)
        day_candles = candle_reader(scan_tf, day_start, day_end)
        if not day_candles:
            d += 86400
            continue
        review = review_day(
            pair, day_label, min_rr=min_rr, now_ts=d,
            journal_rows=journal_reader(pair, day_start, day_end),
            closed_rows=closed_reader(pair, day_start, day_end),
            candle_loader=lambda tf, s, e, _tf=scan_tf: candle_reader(_tf, s, e),
        )
        days_scanned += 1
        for m in review.get("missed_moves", []):
            opportunities.append({**m, "day": day_label})
        for g in review.get("gates", []):
            cg = combined_gates.setdefault(g["gate"], {
                "gate": g["gate"], "label": g.get("label", g["gate"]),
                "count": 0, "filled": 0, "unfilled": 0, "would_have_won": 0,
                "would_have_lost": 0, "still_open": 0, "unscored": 0,
                "missed_r": 0.0, "avoided_loss_r": 0.0,
            })
            for k in ("count", "filled", "unfilled", "would_have_won", "would_have_lost", "still_open", "unscored"):
                cg[k] += int(g.get(k) or 0)
            cg["missed_r"] += float(g.get("missed_r") or 0.0)
            cg["avoided_loss_r"] += float(g.get("avoided_loss_r") or 0.0)
        per_day.append({
            "day": day_label,
            "missed_moves_blind": review.get("missed_moves_blind", 0),
            "missed_r": review.get("missed_r", 0.0),
            "realized_r": review.get("realized_r", 0.0),
        })
        try:
            repo.delete_eod_reviews_for_day(pair, day_label)
            repo.create_eod_review({
                "pair": pair, "day": day_label,
                "day_start": day_start, "day_end": day_end,
                "realized_r": review["realized_r"], "missed_r": review["missed_r"],
                "review": review, "flags": review["flaws"], "provider": "history_scan",
            })
        except Exception:  # pragma: no cover - persistence is best-effort
            pass
        d += 86400

    blind = [o for o in opportunities if o["coverage_kind"] == "none"]
    vetoed = [o for o in opportunities if o["coverage_kind"] == "vetoed"]
    blind.sort(key=lambda o: o["rr"], reverse=True)
    missed_r_blind = round(sum(o["rr"] for o in blind), 2)

    # a single "combined" review across all scanned days, so the analyst can
    # recommend (and auto-apply) against the whole recorded history at once
    combined_gate_list: list[dict] = []
    combined_flaws: list[str] = []
    for cg in combined_gates.values():
        filled = cg["filled"]
        won = cg["would_have_won"]
        win_rate = round(won / filled, 3) if filled else None
        missed_r = round(cg["missed_r"], 3)
        flaw = (
            filled >= FLAW_MIN_FILLED
            and win_rate is not None and win_rate >= FLAW_MIN_WIN_RATE
            and missed_r >= FLAW_MIN_MISSED_R
        )
        note = None
        if flaw:
            note = f"{cg['label']} rejected {won} setups that would have hit target (+{missed_r:.2f}R across history)."
            combined_flaws.append(note)
        combined_gate_list.append({
            **cg, "missed_r": missed_r, "avoided_loss_r": round(cg["avoided_loss_r"], 3),
            "win_rate": win_rate, "flaw": flaw, "note": note,
        })
    combined_gate_list.sort(key=lambda x: x["missed_r"], reverse=True)
    combined_review = {
        "pair": pair, "day": "history",
        "realized_r": round(sum(p["realized_r"] for p in per_day), 3),
        "missed_r": round(sum(g["missed_r"] for g in combined_gate_list), 3),
        "avoided_loss_r": round(sum(g["avoided_loss_r"] for g in combined_gate_list), 3),
        "rejections_total": sum(g["count"] for g in combined_gate_list),
        "rejections_scored": sum(g["filled"] + g["unfilled"] for g in combined_gate_list),
        "gates": combined_gate_list, "missed_winners": [],
        "missed_moves": opportunities, "missed_moves_blind": len(blind),
        "biggest_blind_r": (blind[0]["rr"] if blind else 0.0),
        "flaws": combined_flaws,
    }

    summary = (
        f"{pair.upper()}: scanned {days_scanned} recorded day(s) on {scan_tf}. "
        f"Found {len(blind)} missed opportunit(y/ies) with no candidate "
        f"(worth ~{missed_r_blind:.1f}R total) and {len(vetoed)} that were seen but vetoed."
        if days_scanned else
        f"{pair.upper()}: no recorded {scan_tf} candles in the last {days} day(s) to scan."
    )
    return {
        "pair": pair,
        "scan_tf": scan_tf,
        "days_scanned": days_scanned,
        "opportunities": (blind + vetoed)[:40],
        "per_day": per_day,
        "totals": {"blind": len(blind), "vetoed": len(vetoed), "missed_r_blind": missed_r_blind},
        "summary": summary,
        "combined_review": combined_review,
    }


def _dominant_entry_tf(rejections: list[dict]) -> str:
    counts: dict[str, int] = {}
    for r in rejections:
        tf = r.get("entry_tf") or r.get("timeframe")
        if tf:
            counts[tf] = counts.get(tf, 0) + 1
    if counts:
        return max(counts.items(), key=lambda kv: kv[1])[0]
    return "5m"


def _coverage_anchors(rejections: list[dict], journal_rows: list[dict], closed_rows: list[dict]) -> list[dict]:
    """Price/time anchors of everything the system *did* flag, so the missed-move
    detector can tell a true detection blind spot (nothing near it) from a move the
    planner saw and vetoed (a rejection anchor) or actually took."""
    anchors: list[dict] = []
    rejection_ids = {id(r) for r in rejections}
    for row in journal_rows:
        entry = _num(row.get("entry"))
        if entry is None:
            continue
        kind = "vetoed" if id(row) in rejection_ids else "armed"
        anchors.append({"price": entry, "epoch": row.get("first_seen_at") or row.get("created_at"), "kind": kind})
    for row in closed_rows:
        entry = _num(row.get("entry"))
        if entry is None:
            continue
        anchors.append({
            "price": entry,
            "epoch": row.get("entry_time") or row.get("filled_at") or row.get("armed_at"),
            "kind": "taken",
        })
    return anchors


def _summary(pair, day, realized_r, taken, rejections, scored, missed_r, avoided_loss,
             flaws, blind_count=0, biggest_blind_r=0.0) -> str:
    head = (
        f"{pair.upper()} {day}: booked {realized_r:+.2f}R across {taken} taken "
        f"trade(s); replayed {scored} of {rejections} rejected setup(s)."
    )
    body = (
        f" Rejections would have added {missed_r:+.2f}R of winners and avoided "
        f"{avoided_loss:.2f}R of losers."
    )
    if blind_count:
        detect = (
            f" Detection blind spots: {blind_count} real move(s) never got a "
            f"candidate (biggest {biggest_blind_r:.1f}R)."
        )
    else:
        detect = " No uncovered moves — the planner proposed a candidate at every tradable swing."
    if flaws:
        tail = f" {len(flaws)} flaw(s) flagged."
    else:
        tail = " No systematic flaw today."
    return head + body + detect + tail
