"""The always-on monitor — the product's watchful loop.

One favourite pair at a time (matching the single-pair Deriv feed). A background
thread polls the live candle, advances every armed alert through the turtle-soup
state machine, arms new alerts on each closed bar from a fresh scan, and persists
everything: alert_events (audit trail), outcomes (journal), the paper account.

Owns its in-memory alert list on its own thread; the API only ever reads the DB
(WAL), so there's no shared-memory contention.
"""
from __future__ import annotations

import json
import logging
import threading
import time

from ..config import (
    ARM_QUALITY_MIN,
    EOD_REVIEW_HOUR,
    EOD_REVIEW_PAIR,
    EOD_TZ_OFFSET_HOURS,
    MIN_RISK_ATR_FRAC,
    MIN_RR,
    QUALITY_GATE_MODE,
    RISK_PCT,
)
from ..db import repo
from . import alert_engine as ae
from . import execution, learning, markets, topdown
from .scan import scan as run_scan

log = logging.getLogger("tradeguard.monitor")

POLL_SECONDS = 3.0
ARM_SCORE_MIN = 0.5
MAX_ACTIVE = 6
EXHAUSTION_AT_TARGET = "EXHAUSTION_AT_TARGET"


def _effective_param(pair: str, name: str, default: float) -> float:
    """Config default, overridden by any approved param override for this pair or
    'global'. This is how an approved what-if recommendation reaches the live
    planner/monitor — and only after a human approves it."""
    try:
        return float(repo.get_param_overrides(pair).get(name, default))
    except Exception:  # pragma: no cover - defensive
        return default


def _quality_gate(pair: str, side: str | None, source: str | None, trader_mode: str,
                  learning_state: dict | None) -> tuple[bool, str | None, dict | None]:
    """Evaluate the learned-quality score for a setup.

    In `advisory` mode (the default, see config.QUALITY_GATE_MODE) this NEVER
    blocks: it returns ok=True and attaches `advisory_warning` to the score so
    the reason is displayed and journalled. In `enforce` mode it blocks exactly
    as it used to.

    Advisory is not a softening — it is what makes the gate falsifiable. Blocking
    a setup means we never learn what it would have done, so a model claiming a
    setup type loses can never be checked. Arming it with the warning attached
    collects precisely the outcomes needed to confirm or refute the claim.
    """
    try:
        score = learning.score_plan(pair, {"side": side, "source": source, "trader_mode": trader_mode}, learning_state)
    except Exception:  # pragma: no cover - scoring must never break arming
        return True, None, None
    if not score or score.get("status") != "ready":
        return True, None, score
    if not score.get("segment_matched") or score.get("model_confidence") not in ("medium", "high"):
        return True, None, score
    q = score.get("quality_score")
    threshold = _effective_param(pair, "arm_quality_min", ARM_QUALITY_MIN)
    if q is not None and q < threshold:
        tp = int((score.get("target_probability") or 0.0) * 100)
        reason = (
            f"learned quality {q}/100 (target {tp}%) below the {threshold:.0f} arm-quality "
            f"floor for {trader_mode}|{source}|{side}"
        )
        if _quality_gate_enforced(pair):
            return False, reason, score
        return True, None, {**score, "advisory_warning": reason}
    return True, None, score


def _quality_gate_enforced(pair: str) -> bool:
    """Per-pair override wins, then the process-wide mode."""
    override = repo.get_param_overrides(pair).get("quality_gate_enforce")
    if override is not None:
        return bool(float(override))
    return (QUALITY_GATE_MODE or "advisory").strip().lower() == "enforce"


class Monitor:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._market: markets.Market | None = None
        self._mode: str = topdown.DEFAULT_TRADER_MODE
        self._tf: str = "1H"
        self._alerts: list[ae.Alert] = []
        self._last_epoch: int | None = None
        self._last_error: str | None = None

    # -- control -----------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="tg-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def set_favourite(self, market: markets.Market, timeframe: str, trader_mode: str | None = None) -> None:
        roles = topdown.timeframe_roles(trader_mode)
        with self._lock:
            self._market = market
            self._mode = roles["trader_mode"]
            self._tf = roles["entry_tf"]
            self._alerts = []            # fresh set for the new pair
            self._last_epoch = None
            self._last_error = None
            # resume any still-live alerts for this pair from the DB
            for row in repo.active_alerts(market.id):
                self._alerts.append(_alert_from_row(row))
        log.info("monitor favourite set pair=%s tf=%s mode=%s", market.id, timeframe, self._mode)

    def status(self) -> dict:
        with self._lock:
            m = self._market
            roles = topdown.timeframe_roles(self._mode)
            try:
                active_alerts = sum(
                    1 for a in self._alerts
                    if getattr(a, "state", None) not in ae.TERMINAL
                )
            except Exception as exc:  # pragma: no cover - defensive API hardening
                log.exception("monitor status alert count failed")
                active_alerts = 0
                if self._last_error is None:
                    self._last_error = f"monitor state degraded: {exc}"
            return {
                "pair": getattr(m, "id", None) if m else None,
                "pair_display": getattr(m, "display", None) if m else None,
                "trader_mode": roles["trader_mode"],
                "timeframe": self._tf,
                "bias_tf": roles["bias_tf"],
                "setup_tf": roles["setup_tf"],
                "entry_tf": roles["entry_tf"],
                "running": bool(self._thread and self._thread.is_alive()),
                "active_alerts": active_alerts,
                "error": self._last_error,
            }

    # -- manual paper trades ----------------------------------------------
    def take_trade(self, market: "markets.Market", tf: str, side: str,
                   entry: float, stop: float, target: float,
                   size: float, rr: float, source: str = "manual") -> int:
        """Open a paper position at market. Unlike auto-armed alerts (which start
        WATCHING and must clear the turtle-soup gate), a user-taken trade is an
        explicit override: it enters FILLED at the current price, and the monitor
        then tracks it to TARGET/STOPPED exactly like any other alert. No real
        order is ever placed — this only moves the paper ledger."""
        ok, reason = markets.validate_trade_levels(
            market,
            entry=entry,
            stop=stop,
            target=target,
            reference_price=markets.live_price_value(market) or entry,
        )
        if not ok:
            raise ValueError(reason or "trade levels invalid")
        alert_id = repo.create_alert(dict(
            pair=market.id, timeframe=tf, side=side, source=source,
            zone_min=entry, zone_max=entry, entry=entry, stop=stop, target=target,
            score=0.0, confluence=0, state=ae.FILLED, size=execution.execution_size(market.id, size), rr=rr,
        ))
        repo.add_event(alert_id, ae.FILLED, entry,
                       f"paper trade entered at market {entry:.4f} (size {size:g})")

        with self._lock:
            same = bool(self._market and self._market.id == market.id and self._tf == tf)
        if same:
            alert = ae.Alert(
                pair=market.id, timeframe=tf, side=side, source=source,
                zone_min=entry, zone_max=entry, entry=entry, stop=stop,
                target=target, size=size, state=ae.FILLED,
                planned_rr=rr, fill_price=entry)
            alert.id = alert_id
            with self._lock:
                self._alerts.append(alert)
        else:
            # point the monitor at this pair so the position is tracked live;
            # set_favourite resumes the just-persisted FILLED alert from the DB.
            self.set_favourite(market, tf, self._mode)
            self.start()
        return alert_id

    def close_trade(self, alert_id: int, price: float) -> dict:
        """Close a live paper position at the given market price and book the
        actual R (not the idealised target/stop R). Returns the booked result."""
        with self._lock:
            alert = next((a for a in self._alerts if a.id == alert_id), None)
            if alert is None:
                raise KeyError(alert_id)
            if alert.state in ae.TERMINAL:
                raise ValueError("already closed")
            risk = abs(alert.entry - alert.stop)
            r = 0.0 if risk == 0 else (
                (price - alert.entry) / risk if alert.side == "long"
                else (alert.entry - price) / risk)
            r = round(r, 2)
            alert.state = ae.TARGET if r >= 0 else ae.STOPPED
            repo.update_alert(alert.id, state=alert.state)
            repo.add_event(alert.id, alert.state, price, f"closed manually at {price:.4f} · {r:+.2f}R")
            repo.record_outcome(dict(
                alert_id=alert.id, pair=alert.pair, timeframe=alert.timeframe,
                zone_type=alert.source, confluence=alert.confluence,
                swept=alert.swept, result_r=r))
            acct = repo.apply_outcome_to_account(
                alert.pair,
                r=r,
                risk_pct=RISK_PCT,
                alert_id=alert.id,
                cash_delta=execution.cash_delta(alert.pair, alert.side, alert.entry, price, alert.size),
            )
            self._alerts = [a for a in self._alerts if a.id != alert_id]
        log.info("paper trade %s closed manually r=%s", alert_id, r)
        return {"alert_id": alert_id, "result_r": r, "state": alert.state, "account": acct}

    # -- loop --------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:  # keep the loop alive
                self._last_error = str(exc)
                log.exception("monitor tick failed")
            try:
                self._maybe_run_eod()
            except Exception:  # never let the EOD review kill the loop
                log.exception("eod review check failed")
            self._stop.wait(POLL_SECONDS)

    # -- end-of-day what-if review ----------------------------------------
    def _maybe_run_eod(self) -> None:
        """Once per day, after the local session close, replay the day's rejected
        setups and ask the analyst what the strategy left on the table. Guarded so
        it fires exactly once per UTC day."""
        pair = EOD_REVIEW_PAIR
        utc = time.gmtime()
        local_hour = (utc.tm_hour + EOD_TZ_OFFSET_HOURS) % 24
        if local_hour < EOD_REVIEW_HOUR:
            return
        from . import whatif  # lazy import
        _, day_label, _ = whatif.day_bounds(None)
        existing = repo.latest_eod_review(pair)
        if existing and existing.get("day") == day_label:
            return  # already reviewed today
        log.info("firing end-of-day what-if review pair=%s day=%s", pair, day_label)
        self.run_eod_review(pair)

    def run_eod_review(self, pair: str, day: str | None = None) -> dict:
        """Run the what-if counterfactual review + strategy analyst for `pair`,
        persist the review and its (advisory) recommendations, and fold the
        counterfactuals into the learner. Callable on demand from the API."""
        from . import learning, strategy_review, whatif  # lazy imports
        min_rr = _effective_param(pair, "min_rr", MIN_RR)
        review = whatif.review_day(pair, day=day, min_rr=min_rr)
        analyst = strategy_review.run(pair, review)
        review_id = repo.create_eod_review({
            "pair": pair,
            "day": review["day"],
            "day_start": review["day_start"],
            "day_end": review["day_end"],
            "realized_r": review["realized_r"],
            "missed_r": review["missed_r"],
            "review": review,
            "flags": review["flaws"],
            "provider": analyst["provider"],
        })
        recommendations = strategy_review.persist_recommendations(
            pair, analyst["recommendations"], review_id
        )
        try:
            learning.ensure_current_model(pair, force_refresh=True)
        except Exception:  # pragma: no cover - learning must not block the review
            log.exception("eod learning refresh failed pair=%s", pair)
        log.info(
            "eod review complete pair=%s day=%s recs=%d provider=%s missed_r=%.2f",
            pair, review["day"], len(recommendations),
            analyst["provider"], review["missed_r"],
        )
        return {
            "review_id": review_id,
            "review": review,
            "recommendations": recommendations,
            "provider": analyst["provider"],
            # Kept for API shape stability; recommendations are never auto-applied.
            "auto_apply": False,
        }

    def _tick(self) -> None:
        with self._lock:
            market, tf, mode = self._market, self._tf, self._mode
        if market is None:
            return

        df = markets.load_candles(market, tf)
        if df.empty or len(df) < 20:
            return
        snap = markets.feed_snapshot(market, tf, df)
        if not snap["price_valid"]:
            self._last_error = snap["feed_error"]
            return

        last = df.iloc[-1]
        epoch = int(df.index[-1].timestamp())
        new_bar = epoch != self._last_epoch

        live_price = markets.live_price_value(market)
        price = float(live_price if live_price is not None else last.close)

        # advance live alerts against the current (forming) bar
        with self._lock:
            for a in self._alerts:
                if a.state in ae.TERMINAL:
                    continue
                trans = ae.on_bar(a, float(last.open), float(last.high),
                                  float(last.low), price, closed=new_bar)
                for t in trans:
                    self._persist_transition(a, t)
                if not new_bar and live_price is not None and a.state not in ae.TERMINAL:
                    tick_trans = ae.on_bar(a, price, price, price, price, closed=False)
                    for t in tick_trans:
                        self._persist_transition(a, t)

            # keep every OPEN (entered) position updated at the poll interval, so
            # the user sees the running result of what they'd have if they entered
            for a in self._alerts:
                if a.state == ae.FILLED:
                    risk = abs(a.entry - a.stop)
                    ur = 0.0 if risk == 0 else (
                        (price - a.entry) / risk if a.side == "long"
                        else (a.entry - price) / risk)
                    repo.update_alert(a.id, last_price=round(price, 5),
                                      unrealized_r=round(ur, 3))

            if new_bar:
                scan_result = run_scan(
                    df,
                    pair=market.id,
                    pair_display=market.display,
                    timeframe=tf,
                    pair_category=market.category,
                )
                self._check_exhaustion_at_target(market, scan_result)
                self._last_epoch = epoch
                try:
                    learning_state = learning.ensure_current_model(market.id)
                except Exception:  # pragma: no cover - scoring must never break the loop
                    learning_state = None
                self._arm_from_scan(market, mode, learning_state)
                self._maybe_arm_reversal(market, df, mode, learning_state)
                # prune terminal alerts from memory (they stay in the DB)
                self._alerts = [a for a in self._alerts if a.state not in ae.TERMINAL]

    def _maybe_arm_reversal(self, market: markets.Market, df, trader_mode: str, learning_state: dict | None = None) -> None:
        """The catch-the-missed-move loop. On each new bar, if the `sweep_reclaim`
        override is enabled, look for a *live* liquidity-sweep-reclaim forming right
        now (the formation behind almost every missed opportunity). If a still-valid
        one is found — target ahead, R:R clears — arm it (paper) so the monitor can
        run it to target/stop. Opt-in and paper-only; never places a real order."""
        # Opt-in (default OFF). Default-on flooded the account with tiny 5m scalp
        # reversals; re-enable via the sweep_reclaim override once a minimum-stop
        # guard is in place. Runs every bar when on, gated by R:R + quality + sanity.
        if not bool(_effective_param(market.id, "sweep_reclaim", 0)):
            return
        roles = topdown.timeframe_roles(trader_mode)
        entry_tf = roles["entry_tf"]
        min_rr = _effective_param(market.id, "min_rr", MIN_RR)
        try:
            rows = [
                {"epoch": int(idx.timestamp()), "open": float(r.open),
                 "high": float(r.high), "low": float(r.low), "close": float(r.close)}
                for idx, r in df.iterrows()
            ]
        except Exception:
            return
        from . import whatif  # lazy import
        cand = whatif.detect_live_sweep_reclaim(rows, min_rr=min_rr)
        if cand is None:
            return

        active = [a for a in self._alerts if a.state not in ae.TERMINAL]
        e = {"side": cand["side"], "zone_min": cand["entry"], "zone_max": cand["entry"]}
        if _overlaps_existing(e, active):
            return
        if len(active) >= int(_effective_param(market.id, "max_positions", MAX_ACTIVE)):
            return
        gate_ok, gate_reason, _score = _quality_gate(market.id, cand["side"], "sweep_reclaim", trader_mode, learning_state)
        if not gate_ok:
            self._journal_attempt(
                market, status="blocked_quality", reason=gate_reason,
                plan={"status": "ENTER_READY", "side": cand["side"], "source": "sweep_reclaim",
                      "entry": cand["entry"], "stop": cand["stop"], "target": cand["target"], "rr": cand["rr"]},
                feed=None, trader_mode=trader_mode, roles=roles,
            )
            return
        quality_warning = (_score or {}).get("advisory_warning")

        reference_price = markets.live_price_value(market) or cand["entry"]
        ok, reason = markets.validate_trade_levels(
            market, entry=cand["entry"], stop=cand["stop"], target=cand["target"],
            reference_price=reference_price,
        )
        plan_stub = {
            "status": "ENTER_READY", "side": cand["side"], "source": "sweep_reclaim",
            "entry": cand["entry"], "stop": cand["stop"], "target": cand["target"],
            "rr": cand["rr"], "notes": [cand.get("formation", {}).get("summary", "")],
        }
        feed = {"feed_source": market.code if hasattr(market, "code") else None,
                "resolved_symbol": getattr(market, "code", None), "price_valid": True,
                "reference_price": reference_price, "candle_price": cand["entry"]}
        if not ok:
            self._journal_attempt(market, status="blocked_sanity", reason=reason,
                                  plan=plan_stub, feed=feed, trader_mode=trader_mode, roles=roles)
            return

        balance = repo.get_account(market.id).get("balance", 1000.0)
        risk_dist = abs(cand["entry"] - cand["stop"])
        raw_size = (balance * RISK_PCT / 100.0) / risk_dist if risk_dist > 0 else 0.0
        alert = ae.Alert(
            pair=market.id, timeframe=entry_tf, side=cand["side"], source="sweep_reclaim",
            zone_min=cand["entry"], zone_max=cand["entry"],
            entry=cand["entry"], stop=cand["stop"], target=cand["target"],
            score=1.0, confluence=0, size=execution.execution_size(market.id, raw_size),
            # Risk context: without these the zero-width zone above gives eps=0
            # and the stop lands on the sweep wick. See alert_engine._wick_eps.
            atr=cand.get("atr"), min_risk=cand.get("min_risk"), planned_rr=cand.get("rr"),
        )
        alert.id = repo.create_alert(dict(
            pair=alert.pair, timeframe=alert.timeframe, side=alert.side, source=alert.source,
            zone_min=alert.zone_min, zone_max=alert.zone_max, entry=alert.entry,
            stop=alert.stop, target=alert.target, score=alert.score, confluence=alert.confluence,
            state=ae.WATCHING, size=alert.size, rr=alert.rr(), trader_mode=trader_mode,
            bias_tf=roles["bias_tf"], setup_tf=roles["setup_tf"], entry_tf=entry_tf,
            resolved_symbol=getattr(market, "code", None), price_valid=True,
        ))
        repo.add_event(alert.id, ae.WATCHING, cand["entry"],
                       f"sweep-reclaim reversal armed · {cand.get('formation', {}).get('summary', '')}")
        self._journal_attempt(market, status="armed",
                              reason=f"live sweep-reclaim {cand['side']} @ {cand['entry']:.4g} → {cand['target']:.4g} ({cand['rr']}R)",
                              plan=plan_stub, feed=feed, trader_mode=trader_mode, roles=roles,
                              advisory_warning=quality_warning)
        self._alerts.append(alert)
        log.info("armed sweep-reclaim reversal pair=%s side=%s entry=%s rr=%s",
                 market.id, cand["side"], cand["entry"], cand["rr"])

    def _arm_from_scan(self, market: markets.Market, trader_mode: str, learning_state: dict | None = None) -> None:
        roles = topdown.timeframe_roles(trader_mode)
        entry_tf = roles["entry_tf"]
        min_rr = _effective_param(market.id, "min_rr", MIN_RR)
        arm_score_min = _effective_param(market.id, "arm_score_min", ARM_SCORE_MIN)
        max_active = int(_effective_param(market.id, "max_positions", MAX_ACTIVE))
        counter_trend_ok = bool(_effective_param(market.id, "counter_trend", 0))
        try:
            context = topdown.load_context(market, risk_pct=RISK_PCT, trader_mode=trader_mode, min_rr=min_rr, counter_trend_ok=counter_trend_ok)
        except Exception as exc:
            self._last_error = f"scan failed: {exc}"
            self._journal_attempt(
                market,
                status="scan_failed",
                reason=self._last_error,
                plan=None,
                feed=None,
                trader_mode=trader_mode,
                roles=roles,
            )
            return
        if context.get("feed_blocked"):
            self._last_error = context.get("feed_error")
            self._journal_attempt(
                market,
                status="feed_blocked",
                reason=self._last_error,
                plan=context.get("plan"),
                feed=context.get("feed", {}).get(entry_tf),
                trader_mode=trader_mode,
                roles=roles,
            )
            return
        result = context["setup_scan"]
        plan = context["plan"]
        feed = context.get("feed", {}).get(entry_tf)
        if plan["status"] == "DISCARDED":
            self._retire_vetoed_alerts(market, plan=plan, entries=result.get("future", {}).get("entries", []), feed=feed, roles=roles)
        if plan["status"] != "ENTER_READY" or not plan.get("entry") or not plan.get("stop") or not plan.get("target"):
            self._journal_attempt(
                market,
                status=plan["status"].lower(),
                reason=plan.get("reason") or "plan not armable",
                plan=plan,
                feed=feed,
                trader_mode=trader_mode,
                roles=roles,
            )
            return
        entries = result.get("future", {}).get("entries", [])
        entry_match = next(
            (
                e for e in entries
                if e.get("side") == plan["side"] and abs(float(e.get("entry_price", 0.0)) - float(plan["entry"])) < 1e-6
            ),
            None,
        )
        if entry_match is None:
            entry_match = {
                "side": plan["side"],
                "source": "plan",
                "zone_min": plan["entry"],
                "zone_max": plan["entry"],
                "entry_price": plan["entry"],
                "stop": plan["stop"],
                "target": plan["target"],
                "score": 0.0,
                "confluence": 0,
            }
        active = [a for a in self._alerts if a.state not in ae.TERMINAL]
        e = entry_match
        if len([a for a in self._alerts if a.state not in ae.TERMINAL]) >= max_active:
            self._journal_attempt(
                market,
                status="blocked_max_active",
                reason=f"max active alerts reached ({max_active})",
                plan=plan,
                feed=feed,
                trader_mode=trader_mode,
                roles=roles,
            )
            return
        if e.get("score", 0) < arm_score_min and e.get("source") != "plan":
            self._journal_attempt(
                market,
                status="blocked_score",
                reason=f"score {e.get('score', 0):.2f} below arm threshold {arm_score_min:.2f}",
                plan=plan,
                feed=feed,
                trader_mode=trader_mode,
                roles=roles,
            )
            return
        if _overlaps_existing(e, active):
            self._journal_attempt(
                market,
                status="blocked_overlap",
                reason="overlaps an existing live alert on the same side",
                plan=plan,
                feed=feed,
                trader_mode=trader_mode,
                roles=roles,
            )
            return
        gate_ok, gate_reason, _score = _quality_gate(market.id, plan.get("side"), e.get("source") or plan.get("source"), trader_mode, learning_state)
        if not gate_ok:
            self._journal_attempt(
                market,
                status="blocked_quality",
                reason=gate_reason,
                plan=plan,
                feed=feed,
                trader_mode=trader_mode,
                roles=roles,
            )
            return
        quality_warning = (_score or {}).get("advisory_warning")
        reference_price = markets.live_price_value(market)
        if reference_price is None:
            reference_price = float(context["frames"][entry_tf]["close"].iloc[-1])
        ok, reason = markets.validate_trade_levels(
            market,
            entry=float(plan["entry"]),
            stop=float(plan["stop"]),
            target=float(plan["target"]),
            reference_price=reference_price,
        )
        if not ok:
            self._last_error = reason
            log.warning("blocked invalid alert arm pair=%s tf=%s reason=%s", market.id, entry_tf, reason)
            self._journal_attempt(
                market,
                status="blocked_sanity",
                reason=reason,
                plan=plan,
                feed=feed,
                trader_mode=trader_mode,
                roles=roles,
            )
            return
        _atr = plan.get("atr")
        alert = ae.Alert(
            pair=market.id, timeframe=entry_tf, side=e["side"], source=e["source"],
            zone_min=float(e["zone_min"]), zone_max=float(e["zone_max"]),
            entry=float(plan["entry"]), stop=float(plan["stop"]),
            target=float(plan["target"]), score=float(e.get("score", 0)),
            confluence=int(e.get("confluence", 0)),
            size=execution.execution_size(market.id, float(plan.get("size") or 0.0)),
            atr=_atr,
            min_risk=(MIN_RISK_ATR_FRAC * _atr) if _atr else None,
            planned_rr=plan.get("rr"),
        )
        alert.id = repo.create_alert(dict(
            pair=alert.pair, timeframe=alert.timeframe, side=alert.side,
            source=alert.source, zone_min=alert.zone_min, zone_max=alert.zone_max,
            entry=alert.entry, stop=alert.stop, target=alert.target,
            score=alert.score, confluence=alert.confluence, state=ae.WATCHING,
            size=alert.size, rr=alert.rr(), trader_mode=trader_mode,
            bias_tf=roles["bias_tf"], setup_tf=roles["setup_tf"],
            entry_tf=roles["entry_tf"], feed_source=plan.get("feed_source"),
            resolved_symbol=plan.get("resolved_symbol"), price_valid=plan.get("price_valid", True),
            confirmation_state=plan.get("confirmation_state"),
        ))
        repo.add_event(
            alert.id,
            ae.WATCHING,
            float(context["frames"][entry_tf]["close"].iloc[-1]),
            f"armed from top-down plan · {alert.source} · {entry_tf} confirmed",
        )
        self._journal_attempt(
            market,
            status="armed",
            reason=f"armed from top-down plan · {alert.source}",
            plan=plan,
            feed=feed,
            trader_mode=trader_mode,
            roles=roles,
            advisory_warning=quality_warning,
        )
        self._alerts.append(alert)

    def _retire_vetoed_alerts(
        self,
        market: markets.Market,
        *,
        plan: dict,
        entries: list[dict],
        feed: dict | None,
        roles: dict,
    ) -> None:
        candidate = _entry_candidate_for_plan(plan, entries)
        if candidate is None:
            return
        reason = plan.get("reason") or "fresh re-scan vetoed the setup"
        price = float((feed or {}).get("reference_price") or plan.get("entry") or candidate.get("entry_price") or 0.0)
        retired = 0
        with self._lock:
            for alert in self._alerts:
                if alert.pair != market.id or alert.state not in {ae.WATCHING, ae.TOUCHED, ae.SWEPT}:
                    continue
                if not _materially_matches(candidate, alert):
                    continue
                transition = ae.Transition(ae.INVALIDATED, price, f"retired by fresh veto: {reason}")
                alert.state = ae.INVALIDATED
                self._persist_transition(alert, transition)
                repo.create_setup_journal({
                    "pair": market.id,
                    "timeframe": roles["entry_tf"],
                    "status": "retired",
                    "side": alert.side,
                    "source": alert.source,
                    "reason": transition.reason,
                    "entry": alert.entry,
                    "stop": alert.stop,
                    "target": alert.target,
                    "rr": alert.rr(),
                    "trader_mode": self._mode,
                    "bias_tf": roles["bias_tf"],
                    "setup_tf": roles["setup_tf"],
                    "entry_tf": roles["entry_tf"],
                    "feed_source": (feed or {}).get("feed_source"),
                    "resolved_symbol": (feed or {}).get("resolved_symbol"),
                    "price_valid": (feed or {}).get("price_valid", True),
                    "reference_price": (feed or {}).get("reference_price"),
                    "candle_price": (feed or {}).get("candle_price"),
                    "quality_flags": ["retired_by_veto"],
                    "plan": {
                        "status": "RETIRED",
                        "instruction": "RETIRED BY FRESH VETO",
                        "notes": [reason],
                    },
                })
                retired += 1
        if retired:
            log.info("retired %s pre-fill alerts pair=%s reason=%s", retired, market.id, reason)

    def _journal_attempt(
        self,
        market: markets.Market,
        *,
        status: str,
        reason: str,
        plan: dict | None,
        feed: dict | None,
        trader_mode: str,
        roles: dict,
        advisory_warning: str | None = None,
    ) -> None:
        quality_flags: list[str] = []
        if plan and not plan.get("price_valid", True):
            quality_flags.append("invalid_feed")
        if plan and plan.get("status") != "ENTER_READY":
            quality_flags.append("not_traded")
        if advisory_warning:
            # Armed DESPITE a low learned score. These rows and their outcomes are
            # the only evidence that can ever confirm or refute the gate's claim.
            quality_flags.append("low_learned_quality")
        entry = None if not plan else plan.get("entry")
        stop = None if not plan else plan.get("stop")
        target = None if not plan else plan.get("target")
        replay_stub = {
            "status": plan.get("status") if plan else None,
            "instruction": plan.get("instruction") if plan else None,
            "checklist": plan.get("checklist", []) if plan else [],
            "notes": plan.get("notes", []) if plan else [],
        }
        repo.create_setup_journal({
            "pair": market.id,
            "timeframe": roles["entry_tf"],
            "status": status,
            "side": None if not plan else plan.get("side"),
            "source": None if not plan else (plan.get("source") or "plan"),
            "reason": f"{reason} · {advisory_warning}" if advisory_warning else reason,
            "entry": entry,
            "stop": stop,
            "target": target,
            "rr": None if not plan else plan.get("rr"),
            "trader_mode": trader_mode,
            "bias_tf": roles["bias_tf"],
            "setup_tf": roles["setup_tf"],
            "entry_tf": roles["entry_tf"],
            "feed_source": (feed or {}).get("feed_source"),
            "resolved_symbol": (feed or {}).get("resolved_symbol"),
            "price_valid": (feed or {}).get("price_valid", True),
            "reference_price": (feed or {}).get("reference_price"),
            "candle_price": (feed or {}).get("candle_price"),
            "quality_flags": quality_flags,
            "plan": replay_stub,
        })

    def _persist_transition(self, a: ae.Alert, t: ae.Transition) -> None:
        repo.update_alert(a.id, state=a.state, stop=a.stop, target=a.target,
                          swept_price=a.swept_price, fill_price=a.fill_price,
                          fill_epoch=a.fill_epoch)
        repo.add_event(a.id, t.state, t.price, t.reason)
        if a.state in (ae.TARGET, ae.STOPPED):
            # Book R off the actual exit price and the actual fill, so R and the
            # cash figure below can never tell different stories about one trade.
            r = ae.result_r(a, t.price)
            entry_px = a.fill_price if a.fill_price is not None else a.entry
            repo.record_outcome(dict(
                alert_id=a.id, pair=a.pair, timeframe=a.timeframe, zone_type=a.source,
                confluence=a.confluence, swept=a.swept, result_r=r))
            repo.apply_outcome_to_account(
                a.pair,
                r=r,
                risk_pct=RISK_PCT,
                alert_id=a.id,
                cash_delta=execution.cash_delta(a.pair, a.side, entry_px, t.price, a.size),
            )
            log.info("alert %s closed %s r=%s", a.id, a.state, r)

    def _check_exhaustion_at_target(self, market: markets.Market, scan_result: dict) -> None:
        roles = topdown.timeframe_roles(self._mode)
        patterns = [
            p for p in scan_result.get("layers", {}).get("patterns_all", [])
            if p.get("confirmed") and p.get("role") == "warning"
        ]
        if not patterns:
            return
        latest = max(patterns, key=lambda p: (int(p.get("time", 0)), int(p.get("index", -1))))
        with self._lock:
            live_positions = [a for a in self._alerts if a.state == ae.FILLED and a.id is not None]
        for alert in live_positions:
            if not _warning_against_position(latest, alert.side):
                continue
            risk = abs(alert.entry - alert.stop)
            if risk <= 0 or abs(float(latest.get("price", 0.0)) - alert.target) > risk:
                continue
            if _has_event(alert.id, EXHAUSTION_AT_TARGET, latest.get("name", "")):
                continue
            reason = (
                f'{latest.get("name", "Warning pattern")} at target zone — '
                "consider partial / tighten stop"
            )
            repo.add_event(alert.id, EXHAUSTION_AT_TARGET, float(latest.get("price", alert.target)), reason)
            repo.create_setup_journal({
                "pair": market.id,
                "timeframe": roles["entry_tf"],
                "status": "exhaustion_at_target",
                "side": alert.side,
                "source": alert.source,
                "reason": reason,
                "entry": alert.entry,
                "stop": alert.stop,
                "target": alert.target,
                "rr": alert.rr(),
                "trader_mode": self._mode,
                "bias_tf": roles["bias_tf"],
                "setup_tf": roles["setup_tf"],
                "entry_tf": roles["entry_tf"],
                "feed_source": scan_result.get("feed", {}).get("feed_source"),
                "resolved_symbol": scan_result.get("feed", {}).get("resolved_symbol"),
                "price_valid": True,
                "reference_price": scan_result.get("price"),
                "candle_price": scan_result.get("price"),
                "quality_flags": ["management_note"],
                "plan": {
                    "status": EXHAUSTION_AT_TARGET,
                    "instruction": "MANAGEMENT NOTE",
                    "notes": [reason],
                },
            })


def _overlaps_existing(entry: dict, active: list[ae.Alert]) -> bool:
    lo, hi = float(entry["zone_min"]), float(entry["zone_max"])
    for a in active:
        if a.side == entry["side"] and lo <= a.zone_max and a.zone_min <= hi:
            return True
    return False


def _entry_candidate_for_plan(plan: dict, entries: list[dict]) -> dict | None:
    side = plan.get("side")
    entry = plan.get("entry")
    if not side or entry is None:
        return None
    match = next(
        (
            e for e in entries
            if e.get("side") == side and abs(float(e.get("entry_price", 0.0)) - float(entry)) < 1e-6
        ),
        None,
    )
    if match is not None:
        return match
    return {
        "side": side,
        "source": plan.get("source") or "plan",
        "zone_min": plan.get("entry"),
        "zone_max": plan.get("entry"),
        "entry_price": plan.get("entry"),
    }


def _materially_matches(entry: dict, alert: ae.Alert) -> bool:
    if alert.side != entry.get("side"):
        return False
    if entry.get("source") and entry.get("source") != "plan" and alert.source != entry.get("source"):
        return False
    lo, hi = float(entry["zone_min"]), float(entry["zone_max"])
    return lo <= alert.zone_max and alert.zone_min <= hi


def _warning_against_position(pattern: dict, side: str) -> bool:
    direction = (pattern.get("direction") or "").lower()
    if side == "long":
        return direction != "bullish"
    return direction != "bearish"


def _has_event(alert_id: int | None, state: str, pattern_name: str) -> bool:
    if alert_id is None:
        return False
    needle = pattern_name.lower()
    for event in repo.alert_events(alert_id):
        if event.get("state") != state:
            continue
        if needle and needle in (event.get("reason") or "").lower():
            return True
    return False


def _alert_from_row(row: dict) -> ae.Alert:
    a = ae.Alert(
        pair=row["pair"], timeframe=row["timeframe"], side=row["side"],
        source=row["source"], zone_min=row["zone_min"], zone_max=row["zone_max"],
        entry=row["entry"], stop=row["stop"], target=row["target"],
        score=row.get("score", 0.0), confluence=row.get("confluence", 0),
        size=row.get("size") or 0.0,
        state=row["state"], swept_price=row.get("swept_price"),
        # Restoring these matters: an alert resumed from the DB without its ATR
        # falls back to the spread/tick eps floors and loses its stop guard.
        atr=row.get("atr"), min_risk=row.get("min_risk"),
        planned_rr=row.get("planned_rr") or row.get("rr"),
        fill_price=row.get("fill_price"), fill_epoch=row.get("fill_epoch"),
    )
    a.id = row["id"]
    return a


# module singleton
monitor = Monitor()
