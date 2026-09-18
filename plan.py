"""
================================================================================
plan.py — The trader's decision pipeline (Conditions → Trigger → Stop → Target)
================================================================================
Implements the four-stage framework as an ORDERED VETO PIPELINE. Each gate can
kill the trade, and every check emits a checklist row so the VerdictCard can
show the reasoning like a human mentor would:

    [✓] Structure: UPTREND (HH/HL) agrees with long
    [✓] Bias: MA stack bullish
    [✓] Zone: demand x fresh, confluence x4
    [✗] Trigger: waiting — no confirmed pattern / micro-BOS yet
    ...

Verdict mapping (drop-in for services/verdict.py):
    ENTER_READY  -> ENTER          (all four gates passed)
    WAITING      -> WAIT           (conditions ok, trigger/level not yet)
    DISCARDED    -> WAIT + reason  (R:R veto or structure conflict — show why)

Interfaces (kept deliberately small so scan.py output adapts in one place):
    build_trade_plan(
        candles,            # pd.DataFrame with open/high/low/close (trading TF)
        analysis,           # dict — see ADAPTER NOTES below
        account_balance,    # float, for position sizing
        risk_pct=1.0,       # 1–2% rule
        min_rr=2.0,         # research: discard below 1:2
        ltf_candles=None,   # optional lower-TF DataFrame for micro-BOS
        alert_state=None,   # optional: "TOUCHED"/"SWEPT" from monitor/alert_engine
        sweep_low=None, sweep_high=None,   # turtle-soup wick extremes if SWEPT
    ) -> TradePlan dict

ADAPTER NOTES — mapping from your scan.py result:
    analysis = {
      "structure":   scan["structure"]["state"],        # "UPTREND"/"DOWNTREND"/"RANGE"/...
      "bias":        scan["future"]["bias"],            # "bullish"/"bearish"/"neutral"
      "entry":       scan["future"]["entries"][0],      # the candidate being planned
      "sr_zones":    scan["layers"]["sr"],              # level_min/max/mid, touches, kind
      "supply_demand": scan["layers"]["supply_demand"], # kind, level_min/max, fresh
      "fvgs":        scan["layers"]["fvg"],             # kind, top, bottom, filled/fill_index
      "patterns":    scan["layers"]["patterns"],        # name, direction, index, confirmed, at_zone
    }
Everything index-based stays positional here (engine world); epochs are the
chart's problem via BarClock, exactly as in scan.py today.
================================================================================
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.signal import find_peaks

BULL, BEAR = "bullish", "bearish"

ENTER_READY = "ENTER_READY"
WAITING = "WAITING"
DISCARDED = "DISCARDED"


# ---------------------------------------------------------------- utilities
def _atr(df: pd.DataFrame, period: int = 14) -> float:
    h, l, c = df["high"].to_numpy(float), df["low"].to_numpy(float), df["close"].to_numpy(float)
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    return float(pd.Series(tr).rolling(period, min_periods=1).mean().iloc[-1])


def _swings(df: pd.DataFrame, distance: int = 5):
    """Swing highs/lows via find_peaks (same recipe as the engine)."""
    h, l = df["high"].to_numpy(float), df["low"].to_numpy(float)
    prom = 0.8 * _atr(df)
    hi, _ = find_peaks(h, distance=distance, prominence=prom)
    lo, _ = find_peaks(-l, distance=distance, prominence=prom)
    return ([{"index": int(i), "price": float(h[i])} for i in hi],
            [{"index": int(i), "price": float(l[i])} for i in lo])


def classify_hh_hl(df: pd.DataFrame) -> str:
    """
    HH/HL vs LH/LL classification from the last two swing pairs.
    Fallback used only if the caller didn't supply structure.py's verdict.
    """
    highs, lows = _swings(df)
    if len(highs) < 2 or len(lows) < 2:
        return "UNKNOWN"
    hh = highs[-1]["price"] > highs[-2]["price"]
    hl = lows[-1]["price"] > lows[-2]["price"]
    lh = highs[-1]["price"] < highs[-2]["price"]
    ll = lows[-1]["price"] < lows[-2]["price"]
    if hh and hl:
        return "UPTREND"
    if lh and ll:
        return "DOWNTREND"
    return "RANGE"


def micro_break_of_structure(ltf: pd.DataFrame, side: str, lookback: int = 40):
    """
    Lower-timeframe confirmation: after tapping the zone, price breaks the most
    recent MINOR opposing swing (a local lower-high for longs; local higher-low
    for shorts). Returns (fired: bool, level: float|None).
    """
    if ltf is None or len(ltf) < 10:
        return False, None
    seg = ltf.tail(lookback)
    h, l, c = seg["high"].to_numpy(float), seg["low"].to_numpy(float), seg["close"].to_numpy(float)
    if side == "long":
        pk, _ = find_peaks(h, distance=2)
        if len(pk) == 0:
            return False, None
        level = float(h[pk[-1]]) if pk[-1] < len(seg) - 1 else (float(h[pk[-2]]) if len(pk) > 1 else None)
        if level is None:
            return False, None
        return bool(c[-1] > level), level
    else:
        tr, _ = find_peaks(-l, distance=2)
        if len(tr) == 0:
            return False, None
        level = float(l[tr[-1]]) if tr[-1] < len(seg) - 1 else (float(l[tr[-2]]) if len(tr) > 1 else None)
        if level is None:
            return False, None
        return bool(c[-1] < level), level


# ================================================================ the pipeline
def build_trade_plan(candles, analysis, account_balance, risk_pct=1.0,
                     min_rr=2.0, ltf_candles=None, alert_state=None,
                     sweep_low=None, sweep_high=None,
                     stop_buffer_atr=0.25, pip_value=1.0,
                     counter_trend_ok=False):
    checklist, notes = [], []

    def check(stage, name, passed, detail):
        checklist.append({"stage": stage, "name": name,
                          "passed": bool(passed), "detail": detail})
        return passed

    price = float(candles["close"].iloc[-1])
    atr = _atr(candles)
    entry_cand = analysis.get("entry") or {}
    side = entry_cand.get("side")
    if side not in ("long", "short"):
        return _plan(DISCARDED, None, checklist,
                     reason="no candidate entry from the scan")

    zmin, zmax = float(entry_cand["zone_min"]), float(entry_cand["zone_max"])

    # =====================================================================
    # GATE 1 — CONDITIONS  (trend/structure + bias + zone quality + FVG)
    # =====================================================================
    structure = (analysis.get("structure") or classify_hh_hl(candles)).upper()
    want = "UPTREND" if side == "long" else "DOWNTREND"
    opposing = "DOWNTREND" if side == "long" else "UPTREND"

    s_ok = structure == want or (structure in ("RANGE", "CONSOLIDATION", "UNKNOWN"))
    check("conditions", "Market structure",
          s_ok, f"{structure} " + ("(HH/HL)" if structure == "UPTREND"
                                   else "(LH/LL)" if structure == "DOWNTREND" else ""))
    counter_trend = False
    if structure == opposing:
        # A counter-trend setup is only allowed when counter_trend_ok is enabled
        # (e.g. an approved what-if recommendation). It still has to clear GATE 2's
        # trigger below — so this permits a *reversal with a real trigger*, never a
        # naked counter-trend entry.
        if not counter_trend_ok:
            return _plan(DISCARDED, side, checklist,
                         reason=f"{side} against {structure} structure — trade with the trend",
                         structure=structure)
        counter_trend = True
        notes.append(f"counter-trend {side} against {structure} — allowed, but needs a reversal trigger")
    if structure in ("RANGE", "CONSOLIDATION"):
        notes.append("range: zone-to-zone trade, temper expectations")

    bias = (analysis.get("bias") or "neutral").lower()
    b_ok = (bias == BULL and side == "long") or (bias == BEAR and side == "short") \
        or bias == "neutral"
    check("conditions", "MA bias", b_ok, f"bias {bias}")
    if not b_ok:
        if not counter_trend_ok:
            return _plan(DISCARDED, side, checklist,
                         reason=f"{side} against {bias} moving-average bias",
                         structure=structure)
        counter_trend = True
        notes.append(f"counter-trend {side} against {bias} MA bias — allowed, needs a reversal trigger")

    conf = int(entry_cand.get("confluence", 1))
    fresh = bool(entry_cand.get("fresh", entry_cand.get("with_bias", False)))
    z_ok = conf >= 2
    check("conditions", "Zone quality", z_ok,
          f'{entry_cand.get("source", "zone")} · confluence x{conf}'
          + (" · fresh" if fresh else ""))
    if not z_ok:
        notes.append("weak zone (confluence x1) — treat as low priority")

    fvg_conf = _fvg_confluence(analysis.get("fvgs", []), zmin, zmax, side)
    check("conditions", "FVG confluence", True,
          "unfilled FVG overlaps zone" if fvg_conf else "no FVG at zone (informational)")

    # =====================================================================
    # GATE 2 — ENTRY TRIGGER  (confirmed pattern OR micro-BOS OR sweep+reclaim)
    # =====================================================================
    trigger_candles = analysis.get("pattern_candles")
    if trigger_candles is None or len(trigger_candles) == 0:
        trigger_candles = ltf_candles if ltf_candles is not None else candles
    trig_pat = _confirmed_pattern_at_zone(analysis.get("patterns", []),
                                          trigger_candles, side, zmin, zmax, atr)
    bos_fired, bos_level = micro_break_of_structure(ltf_candles, side)
    soup = alert_state == "SWEPT"
    rejected = _non_trigger_pattern_at_zone(analysis.get("patterns", []),
                                            trigger_candles, zmin, zmax, atr)

    trig_ok = bool(trig_pat) or bos_fired or soup
    detail = (trig_pat["name"] + " confirmed at zone" if trig_pat
              else f"micro-BOS on lower TF @ {bos_level:.5g}" if bos_fired
              else "turtle-soup sweep + reclaim" if soup
              else rejected or "waiting — zone not confirmed yet")

    # Trigger metadata for the storyteller. bos_level is carried even when the
    # break has NOT fired: that pending level is the change-of-character line the
    # narrative tells the user to watch ("until a close above X, sellers still own
    # the short-term structure"). Without this it stays trapped in `detail` above.
    trigger_kind = ("pattern" if trig_pat
                    else "micro_bos" if bos_fired
                    else "sweep_reclaim" if soup
                    else "none")
    trigger_pattern = trig_pat["name"] if trig_pat else None
    if trig_pat is not None:
        trigger_level = float(trigger_candles["high"].iloc[trig_pat["index"]]) if side == "long" \
            else float(trigger_candles["low"].iloc[trig_pat["index"]])
    else:
        trigger_level = float(bos_level) if bos_level is not None else None
    trigger_meta = {
        "trigger_level": trigger_level,
        "trigger_kind": trigger_kind,
        "trigger_pattern": trigger_pattern,
        "atr": atr,
        "zone_min": zmin,
        "zone_max": zmax,
        "structure": structure,
    }
    if counter_trend and not (trig_pat or soup):
        # counter-trend needs an actual reversal signal, not just a micro-BOS
        notes.append("counter-trend: only a confirmed reversal pattern or sweep+reclaim qualifies")
    check("trigger", "Confirmation", trig_ok, detail)
    if not trig_ok:
        return _plan(WAITING, side, checklist,
                     reason="conditions aligned — waiting for a trigger at the zone",
                     entry=float(entry_cand.get("entry_price",
                                                zmax if side == "long" else zmin)),
                     counter_trend=counter_trend, **trigger_meta)

    # =====================================================================
    # GATE 3 — STOP LOSS  (structural invalidation + buffer, then size)
    # =====================================================================
    entry = float(entry_cand.get("entry_price", zmax if side == "long" else zmin))
    buffer = max(stop_buffer_atr * atr, 1e-9)

    if side == "long":
        cands = {"zone low": zmin}
        if soup and sweep_low is not None:
            cands["sweep wick"] = float(sweep_low)
        if trig_pat is not None:
            cands["trigger-candle low"] = float(trigger_candles["low"].iloc[trig_pat["index"]])
        hl = _last_swing(candles, "low")
        if hl is not None and hl < entry:
            cands["last higher low"] = hl
        # nearest level that still invalidates the thesis = the HIGHEST candidate
        anchor_name, anchor = max(cands.items(), key=lambda kv: kv[1])
        stop = anchor - buffer
    else:
        cands = {"zone high": zmax}
        if soup and sweep_high is not None:
            cands["sweep wick"] = float(sweep_high)
        if trig_pat is not None:
            cands["trigger-candle high"] = float(trigger_candles["high"].iloc[trig_pat["index"]])
        lh = _last_swing(candles, "high")
        if lh is not None and lh > entry:
            cands["last lower high"] = lh
        anchor_name, anchor = min(cands.items(), key=lambda kv: kv[1])
        stop = anchor + buffer

    risk_dist = abs(entry - stop)
    if risk_dist <= 0:
        return _plan(DISCARDED, side, checklist, reason="degenerate stop distance")
    size = round((account_balance * risk_pct / 100.0) / (risk_dist * pip_value), 4)
    check("stop", "Structural stop", True,
          f"beyond {anchor_name} @ {anchor:.5g} + {stop_buffer_atr} ATR buffer → {stop:.5g}")
    check("stop", "Position size", True,
          f"{risk_pct}% of {account_balance:,.0f} / {risk_dist:.5g} = {size} units")

    # =====================================================================
    # GATE 4 — TAKE PROFIT  (next real obstacle, then the R:R veto)
    # =====================================================================
    obstacles = _obstacles(analysis, candles, side, entry)
    if not obstacles:
        tp = entry + 2 * risk_dist if side == "long" else entry - 2 * risk_dist
        obstacles = [{"price": tp, "basis": "no visible obstacle — default 2R"}]

    # Research rule (strict): the NEAREST obstacle is the honest target.
    # If it caps reward below min_rr the trade is discarded — a farther level
    # cannot rescue the trade, because price must get through this one first.
    nearest = obstacles[0]
    rr0 = abs(nearest["price"] - entry) / risk_dist
    if rr0 < min_rr:
        alternatives = _alternative_targets(obstacles[1:], entry, risk_dist)
        check("target", "R:R filter", False,
              f'nearest obstacle ({nearest["basis"]} @ {nearest["price"]:.5g}) '
              f"caps reward at {rr0:.2f}R < {min_rr}R minimum")
        return _plan(DISCARDED, side, checklist,
                     reason=f"trade discarded — nearest obstacle caps R:R at "
                            f"{rr0:.2f}, below the {min_rr} minimum",
                     entry=entry, stop=stop, target=nearest["price"], rr=round(rr0, 2),
                     size=size, target_basis=nearest["basis"], notes=notes,
                     alternatives=alternatives,
                     stop_anchor=anchor_name, stop_anchor_price=anchor, **trigger_meta)

    chosen = {**nearest, "rr": round(rr0, 2)}
    if len(obstacles) > 1:
        nxt = obstacles[1]
        notes.append(f'runner target (TP2 candidate): {nxt["basis"]} '
                     f'@ {nxt["price"]:.5g} '
                     f'({abs(nxt["price"] - entry) / risk_dist:.1f}R)')
    alternatives = _alternative_targets(obstacles[1:], entry, risk_dist)
    check("target", "Structural target", True,
          f'{chosen["basis"]} @ {chosen["price"]:.5g}')
    check("target", "R:R filter", True, f'{chosen["rr"]}R ≥ {min_rr}R')

    return _plan(ENTER_READY, side, checklist, entry=entry, stop=stop,
                 target=chosen["price"], rr=chosen["rr"], size=size,
                 target_basis=chosen["basis"], notes=notes,
                 alternatives=alternatives, counter_trend=counter_trend,
                 reason="all four gates passed",
                 stop_anchor=anchor_name, stop_anchor_price=anchor, **trigger_meta)


# ---------------------------------------------------------------- helpers
def _plan(status, side, checklist, reason="", entry=None, stop=None,
          target=None, rr=None, size=None, target_basis=None, notes=None,
          alternatives=None, counter_trend=False, *,
          trigger_level=None, trigger_kind="none", trigger_pattern=None,
          stop_anchor=None, stop_anchor_price=None,
          atr=None, zone_min=None, zone_max=None, structure=None):
    """Assemble the plan dict.

    The trailing keyword-only fields are narrative metadata: they never affect a
    decision, they let services/storyteller.py quote the exact level the gate
    used instead of recomputing it by a different recipe. They are deliberately
    NOT forwarded into verdict._plan_payload — the story carries them.
    """
    return {"status": status, "side": side, "reason": reason,
            "entry": entry, "stop": stop, "target": target, "rr": rr,
            "size": size, "target_basis": target_basis,
            "notes": notes or [], "alternatives": alternatives or [],
            "counter_trend": bool(counter_trend),
            "checklist": checklist,
            "trigger_level": trigger_level, "trigger_kind": trigger_kind,
            "trigger_pattern": trigger_pattern,
            "stop_anchor": stop_anchor, "stop_anchor_price": stop_anchor_price,
            "atr": atr, "zone_min": zone_min, "zone_max": zone_max,
            "structure": structure}


def _confirmed_pattern_at_zone(patterns, candles, side, zmin, zmax, atr,
                               recent_bars=6):
    """Latest CONFIRMED pattern, right direction, printed at/near the zone."""
    n = len(candles)
    want = BULL if side == "long" else BEAR
    best = None
    for p in patterns:
        if p.get("role") != "trigger":
            continue
        if p.get("direction") != want:
            continue
        if not p.get("confirmed", p.get("at_zone", False)):
            continue                     # anti-hindsight: confirmed only
        i = int(p.get("index", -1))
        if i < n - recent_bars:
            continue
        lo = float(candles["low"].iloc[i]); hi = float(candles["high"].iloc[i])
        pad = 0.25 * atr
        if lo <= zmax + pad and hi >= zmin - pad:
            if best is None or i > best["index"]:
                best = {"name": p.get("name", "pattern"), "index": i}
    return best


def _non_trigger_pattern_at_zone(patterns, candles, zmin, zmax, atr, recent_bars=6):
    n = len(candles)
    for p in sorted(patterns, key=lambda item: int(item.get("index", -1)), reverse=True):
        if p.get("role") == "trigger":
            continue
        if not p.get("confirmed", p.get("at_zone", False)):
            continue
        i = int(p.get("index", -1))
        if i < n - recent_bars:
            continue
        lo = float(candles["low"].iloc[i]); hi = float(candles["high"].iloc[i])
        pad = 0.25 * atr
        if lo <= zmax + pad and hi >= zmin - pad:
            role = p.get("role") or "context"
            if role == "warning":
                return f'{p.get("name", "Pattern")} at zone is indecision — not a valid entry trigger'
            return f'{p.get("name", "Pattern")} at zone is context only — not a valid entry trigger'
    return None


def _last_swing(df, kind, distance=5):
    highs, lows = _swings(df, distance)
    pts = lows if kind == "low" else highs
    return pts[-1]["price"] if pts else None


def _fvg_confluence(fvgs, zmin, zmax, side):
    want = BULL if side == "long" else BEAR
    for g in fvgs:
        if g.get("kind") != want:
            continue
        if g.get("filled") or g.get("fill_index") is not None:
            continue
        if g["bottom"] <= zmax and g["top"] >= zmin:
            return True
    return False


def _obstacles(analysis, candles, side, entry):
    """
    Everything that could stop price before the target, sorted nearest-first:
    opposing S/R clusters, fresh opposing supply/demand (proximal edge),
    unfilled opposing FVGs, and the prior swing extreme.
    """
    out = []
    if side == "long":
        for z in analysis.get("sr_zones", []):
            lvl = float(z.get("level_min", z.get("level_mid", 0)))
            if lvl > entry:
                out.append({"price": lvl, "basis": f'resistance x{z.get("touches", "?")}'})
        for z in analysis.get("supply_demand", []):
            if z.get("kind") == "supply" and float(z["level_min"]) > entry:
                out.append({"price": float(z["level_min"]),
                            "basis": "supply zone" + (" (fresh)" if z.get("fresh") else "")})
        for g in analysis.get("fvgs", []):
            if g.get("kind") == BEAR and not g.get("filled") \
                    and g.get("fill_index") is None and float(g["bottom"]) > entry:
                out.append({"price": float(g["bottom"]), "basis": "unfilled bearish FVG"})
        sw = _last_swing(candles, "high")
        if sw is not None and sw > entry:
            out.append({"price": sw, "basis": "prior swing high"})
        out.sort(key=lambda o: o["price"])
    else:
        for z in analysis.get("sr_zones", []):
            lvl = float(z.get("level_max", z.get("level_mid", 0)))
            if lvl < entry:
                out.append({"price": lvl, "basis": f'support x{z.get("touches", "?")}'})
        for z in analysis.get("supply_demand", []):
            if z.get("kind") == "demand" and float(z["level_max"]) < entry:
                out.append({"price": float(z["level_max"]),
                            "basis": "demand zone" + (" (fresh)" if z.get("fresh") else "")})
        for g in analysis.get("fvgs", []):
            if g.get("kind") == BULL and not g.get("filled") \
                    and g.get("fill_index") is None and float(g["top"]) < entry:
                out.append({"price": float(g["top"]), "basis": "unfilled bullish FVG"})
        sw = _last_swing(candles, "low")
        if sw is not None and sw < entry:
            out.append({"price": sw, "basis": "prior swing low"})
        out.sort(key=lambda o: -o["price"])
    # de-duplicate near-identical levels (keep nearest label)
    dedup, seen = [], []
    for o in out:
        if all(abs(o["price"] - s) > 1e-9 and abs(o["price"] - s) > 0.001 * o["price"]
               for s in seen):
            dedup.append(o); seen.append(o["price"])
    return dedup


def obstacles(analysis, candles, side, entry):
    """Public alias for the nearest-first obstacle list.

    The storyteller calls this with `entry = current price` to find the nearest
    mapped level in either direction when there is no plan to talk about.
    """
    return _obstacles(analysis, candles, side, entry)


def _alternative_targets(obstacles, entry, risk_dist):
    out = []
    for o in obstacles:
        rr = abs(o["price"] - entry) / risk_dist
        out.append({
            "price": o["price"],
            "basis": o["basis"],
            "rr": round(rr, 2),
        })
    return out
