from app.db import repo
from app.db.database import init_schema
from app.services import whatif


def test_replay_long_hits_target():
    r = whatif.replay_setup(
        {"side": "long", "entry": 100.0, "stop": 98.0, "target": 104.0},
        [{"epoch": 1, "high": 101, "low": 99}, {"epoch": 2, "high": 105, "low": 100}],
    )
    assert r["outcome"] == "target"
    assert r["filled"] is True
    assert r["counterfactual_r"] == 2.0  # (104-100)/(100-98)


def test_replay_long_hits_stop():
    r = whatif.replay_setup(
        {"side": "long", "entry": 100.0, "stop": 98.0, "target": 104.0},
        [{"epoch": 1, "high": 101, "low": 99}, {"epoch": 2, "high": 100, "low": 97}],
    )
    assert r["outcome"] == "stopped"
    assert r["counterfactual_r"] == -1.0


def test_replay_stop_first_when_candle_spans_both():
    # a single candle touching both stop and target must resolve as a loss
    r = whatif.replay_setup(
        {"side": "long", "entry": 100.0, "stop": 98.0, "target": 104.0},
        [{"epoch": 1, "high": 100, "low": 99}, {"epoch": 2, "high": 105, "low": 97}],
    )
    assert r["outcome"] == "stopped"


def test_replay_unfilled_when_entry_never_touched():
    r = whatif.replay_setup(
        {"side": "long", "entry": 90.0, "stop": 88.0, "target": 94.0},
        [{"epoch": 1, "high": 101, "low": 99}],
    )
    assert r["outcome"] == "unfilled"
    assert r["filled"] is False
    assert r["counterfactual_r"] == 0.0


def test_replay_short_hits_target():
    r = whatif.replay_setup(
        {"side": "short", "entry": 100.0, "stop": 102.0, "target": 96.0},
        [{"epoch": 1, "high": 100, "low": 99}, {"epoch": 2, "high": 100, "low": 95}],
    )
    assert r["outcome"] == "target"
    assert r["counterfactual_r"] == 2.0


def test_replay_unavailable_on_bad_levels():
    assert whatif.replay_setup({"side": "long", "entry": 100, "stop": 100, "target": 104}, [{"epoch": 1, "high": 101, "low": 99}])["outcome"] == "unavailable"
    assert whatif.replay_setup({"side": "long", "entry": None, "stop": 98, "target": 104}, [])["outcome"] == "unavailable"


def test_review_day_attributes_missed_r_to_gate_and_flags_flaw():
    start, day_label, end = whatif.day_bounds("2026-07-30")
    # four R:R-vetoed setups, three of which would have won → a flagged flaw gate
    journal = []
    for i in range(4):
        journal.append({
            "status": "discarded",
            "reason": "nearest obstacle caps R:R at 1.60, below the 2.0 minimum",
            "side": "long", "source": "fvg", "trader_mode": "Scalper",
            "entry": 100.0, "stop": 98.0, "target": 103.0,  # rr = 1.5
            "entry_tf": "5m", "first_seen_at": start + 10, "created_at": start + 10,
        })

    def loader(tf, s, e):
        # first three win, fourth never fills
        return None  # overridden per-call below

    outcomes = [
        [{"epoch": start + 60, "high": 101, "low": 99}, {"epoch": start + 120, "high": 104, "low": 100}],  # target
        [{"epoch": start + 60, "high": 101, "low": 99}, {"epoch": start + 120, "high": 104, "low": 100}],  # target
        [{"epoch": start + 60, "high": 101, "low": 99}, {"epoch": start + 120, "high": 104, "low": 100}],  # target
        [{"epoch": start + 60, "high": 105, "low": 101}],  # never touches entry 100 -> unfilled
    ]
    calls = {"i": 0}

    def indexed_loader(tf, s, e):
        idx = calls["i"]
        calls["i"] += 1
        # the first four calls are per-rejection replays; later calls (e.g. the
        # missed-move detector's day-candle load) get an empty window
        return outcomes[idx] if idx < len(outcomes) else []

    review = whatif.review_day(
        "xauusd", "2026-07-30",
        journal_rows=journal, closed_rows=[], candle_loader=indexed_loader,
    )
    assert review["day"] == day_label
    assert review["rejections_total"] == 4
    assert review["rejections_scored"] == 4
    gate = next(g for g in review["gates"] if g["gate"] == "rr_below_min")
    assert gate["would_have_won"] == 3
    assert gate["missed_r"] > 0
    assert gate["flaw"] is True
    assert review["missed_r"] > 0
    assert len(review["missed_winners"]) == 3
    # replays feed the learner
    assert len(review["replays"]) == 4


def _v_reversal_candles(start):
    # a down leg to ~4030 then a rally to ~4080 (the screenshot move)
    prices = [4090, 4082, 4075, 4066, 4058, 4049, 4040, 4033, 4030, 4038, 4050, 4062, 4071, 4078, 4080, 4079]
    return [{"epoch": start + i * 300, "open": p, "high": p + 3, "low": p - 3, "close": p} for i, p in enumerate(prices)]


def test_missed_move_is_blind_spot_without_coverage():
    moves = whatif.detect_missed_moves(_v_reversal_candles(1785369600), coverage=[], min_rr=2.0)
    assert moves and moves[0]["direction"] == "long"
    assert moves[0]["coverage_kind"] == "none"  # nothing flagged it → detection blind spot
    assert moves[0]["rr"] >= 2.0


def test_missed_move_marked_vetoed_when_anchor_present():
    start = 1785369600
    cov = [{"price": 4030, "epoch": start + 8 * 300, "kind": "vetoed"}]
    moves = whatif.detect_missed_moves(_v_reversal_candles(start), coverage=cov, min_rr=2.0)
    long_move = next(m for m in moves if m["direction"] == "long")
    assert long_move["coverage_kind"] == "vetoed"  # seen-and-vetoed, not a blind spot


def test_review_day_no_flaw_when_rejections_would_lose():
    start, _, end = whatif.day_bounds("2026-07-30")
    journal = [{
        "status": "blocked_score", "reason": "score 0.30 below arm threshold 0.50",
        "side": "long", "source": "demand", "trader_mode": "Scalper",
        "entry": 100.0, "stop": 98.0, "target": 104.0,
        "entry_tf": "5m", "first_seen_at": start + 10, "created_at": start + 10,
    }]

    def loser(tf, s, e):
        return [{"epoch": start + 60, "high": 101, "low": 97}]  # fills then stops out

    review = whatif.review_day(
        "xauusd", "2026-07-30",
        journal_rows=journal, closed_rows=[], candle_loader=loser,
    )
    gate = next(g for g in review["gates"] if g["gate"] == "blocked_score")
    assert gate["would_have_lost"] == 1
    assert gate["flaw"] is False
    assert review["avoided_loss_r"] == 1.0


def test_scan_history_finds_missed_opportunities_across_recorded_days():
    init_schema()
    for pair_day in ("2026-07-27", "2026-07-28"):
        s, _, _ = whatif.day_bounds(pair_day)
        base = [4090, 4082, 4075, 4066, 4058, 4049, 4040, 4033, 4030, 4038, 4050, 4062, 4071, 4078, 4080, 4079]
        rows = [{"epoch": s + i * 300, "open": p, "high": p + 2, "low": (p - 6 if i == 8 else p - 3), "close": (p + 1 if i == 8 else p)} for i, p in enumerate(base)]
        repo.upsert_candles("histpair", "5m", rows)
    res = whatif.scan_history("histpair", days=60, now_ts=whatif.day_bounds("2026-07-30")[0])
    assert res["scan_tf"] == "5m"
    assert res["days_scanned"] == 2
    assert res["totals"]["blind"] == 2
    assert res["opportunities"][0]["coverage_kind"] == "none"
    assert res["opportunities"][0]["direction"] == "long"
    assert res["opportunities"][0]["formation"]["summary"]


def _fresh_sweep_reclaim_candles(start):
    closes = [4090, 4082, 4074, 4066, 4058, 4050, 4042, 4030, 4035, 4038]
    out = []
    for i, p in enumerate(closes):
        lo = 4022 if i == 7 else p - 3  # swept swing low at bar 7
        hi = 4092 if i == 0 else p + 3  # pre-sweep leg high = target
        out.append({"epoch": start + i * 300, "open": p, "high": hi, "low": lo, "close": p})
    return out


def test_live_sweep_reclaim_fires_on_fresh_reclaim():
    cand = whatif.detect_live_sweep_reclaim(_fresh_sweep_reclaim_candles(1785369600), min_rr=2.0)
    assert cand is not None
    assert cand["side"] == "long"
    # target now projects at least to the swept leg's high (a continuation reach),
    # not capped short of it
    assert cand["target"] >= 4092.0
    assert cand["rr"] >= 2.0
    assert "swept" in cand["formation"]["summary"]


def test_live_sweep_reclaim_skips_when_target_too_close():
    # a reclaim with no room to the target (min_rr not met) must not fire
    c = _fresh_sweep_reclaim_candles(1785369600)
    c[-1]["close"] = 4090  # already at the target — nothing left
    assert whatif.detect_live_sweep_reclaim(c, min_rr=2.0) is None


def test_missed_moves_drops_degenerate_formation_phantom():
    # a FLAT origin candle (high == low) is the "formation unavailable / inflated R"
    # phantom — it must be excluded, not reported as a huge-R opportunity.
    start = 1785369600
    closes = [4090, 4082, 4074, 4066, 4058, 4050, 4042, 4030, 4040, 4052, 4064, 4076]
    c = []
    for i, p in enumerate(closes):
        if i == 7:  # degenerate flat origin at the swing low
            c.append({"epoch": start + i * 300, "open": 4030, "high": 4030, "low": 4030, "close": 4030})
        else:
            c.append({"epoch": start + i * 300, "open": p, "high": p + 3, "low": p - 3, "close": p})
    moves = whatif.detect_missed_moves(c, coverage=[], min_rr=2.0)
    assert all(m.get("formation", {}).get("summary") != "formation unavailable" for m in moves)
    # and no absurd inflated R survives (risk floored at 0.5*ATR)
    assert all(m["rr"] < 60 for m in moves)
