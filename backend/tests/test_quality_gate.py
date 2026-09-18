from types import SimpleNamespace

import pandas as pd

from app.services import monitor
from app.services.monitor import Monitor


def _score(**over):
    base = {
        "status": "ready", "segment_matched": True, "model_confidence": "high",
        "quality_score": 20, "target_probability": 0.10,
    }
    base.update(over)
    return base


def test_gate_advisory_warns_but_does_not_block(monkeypatch):
    """Default mode. A low score is surfaced, not enforced.

    Blocking means never learning what the setup would have done, so the model's
    claim can never be checked. Arming with the warning attached is what makes it
    falsifiable.
    """
    monkeypatch.setattr("app.services.monitor.learning.score_plan", lambda pair, plan, state: _score())
    monkeypatch.setattr("app.services.monitor.repo.get_param_overrides", lambda pair: {})
    monkeypatch.setattr("app.services.monitor.QUALITY_GATE_MODE", "advisory")
    ok, reason, score = monitor._quality_gate("xauusd", "long", "sweep_reclaim", "Scalper", None)
    assert ok is True
    assert reason is None
    assert "learned quality" in score["advisory_warning"]


def test_gate_enforce_mode_blocks(monkeypatch):
    """The enforce path stays fully covered, so re-arming is a config flip."""
    monkeypatch.setattr("app.services.monitor.learning.score_plan", lambda pair, plan, state: _score())
    monkeypatch.setattr("app.services.monitor.repo.get_param_overrides", lambda pair: {})
    monkeypatch.setattr("app.services.monitor.QUALITY_GATE_MODE", "enforce")
    ok, reason, _ = monitor._quality_gate("xauusd", "long", "sweep_reclaim", "Scalper", None)
    assert ok is False
    assert "learned quality" in reason


def test_gate_per_pair_override_can_enforce(monkeypatch):
    monkeypatch.setattr("app.services.monitor.learning.score_plan", lambda pair, plan, state: _score())
    monkeypatch.setattr("app.services.monitor.QUALITY_GATE_MODE", "advisory")
    monkeypatch.setattr("app.services.monitor.repo.get_param_overrides",
                        lambda pair: {"quality_gate_enforce": 1})
    assert monitor._quality_gate("xauusd", "long", "sweep_reclaim", "Scalper", None)[0] is False


def test_gate_allows_high_quality(monkeypatch):
    monkeypatch.setattr("app.services.monitor.learning.score_plan", lambda pair, plan, state: _score(quality_score=70))
    monkeypatch.setattr("app.services.monitor.repo.get_param_overrides", lambda pair: {})
    ok, reason, _ = monitor._quality_gate("xauusd", "long", "sweep_reclaim", "Scalper", None)
    assert ok is True and reason is None


def test_gate_allows_unmatched_segment(monkeypatch):
    # falls back to global stats → never block a setup the model hasn't specifically learned
    monkeypatch.setattr("app.services.monitor.learning.score_plan", lambda pair, plan, state: _score(segment_matched=False))
    monkeypatch.setattr("app.services.monitor.repo.get_param_overrides", lambda pair: {})
    assert monitor._quality_gate("xauusd", "long", "sweep_reclaim", "Scalper", None)[0] is True


def test_gate_allows_low_confidence(monkeypatch):
    monkeypatch.setattr("app.services.monitor.learning.score_plan", lambda pair, plan, state: _score(model_confidence="low"))
    monkeypatch.setattr("app.services.monitor.repo.get_param_overrides", lambda pair: {})
    assert monitor._quality_gate("xauusd", "long", "sweep_reclaim", "Scalper", None)[0] is True


def test_gate_allows_insufficient_history(monkeypatch):
    monkeypatch.setattr(
        "app.services.monitor.learning.score_plan",
        lambda pair, plan, state: {"status": "insufficient_history", "segment_matched": False, "quality_score": None, "model_confidence": "low"},
    )
    monkeypatch.setattr("app.services.monitor.repo.get_param_overrides", lambda pair: {})
    assert monitor._quality_gate("xauusd", "long", "sweep_reclaim", "Scalper", None)[0] is True


def test_gate_respects_override_threshold(monkeypatch):
    # quality 40 with a raised floor of 50 → warns; default floor 35 → silent
    monkeypatch.setattr("app.services.monitor.learning.score_plan", lambda pair, plan, state: _score(quality_score=40))
    monkeypatch.setattr("app.services.monitor.QUALITY_GATE_MODE", "advisory")
    monkeypatch.setattr("app.services.monitor.repo.get_param_overrides", lambda pair: {"arm_quality_min": 50})
    assert monitor._quality_gate("xauusd", "long", "sweep_reclaim", "Scalper", None)[2].get("advisory_warning")
    monkeypatch.setattr("app.services.monitor.repo.get_param_overrides", lambda pair: {})
    assert not monitor._quality_gate("xauusd", "long", "sweep_reclaim", "Scalper", None)[2].get("advisory_warning")


def test_gate_never_breaks_on_scoring_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("model exploded")
    monkeypatch.setattr("app.services.monitor.learning.score_plan", boom)
    assert monitor._quality_gate("xauusd", "long", "sweep_reclaim", "Scalper", None)[0] is True


def _reversal_fixture(monkeypatch, journals, created):
    cand = {"side": "long", "entry": 4038.0, "stop": 4020.0, "target": 4092.0, "rr": 3.0,
            "origin_epoch": 1785369600, "formation": {"summary": "hammer/reclaim, swept prior low"},
            "atr": 6.0, "min_risk": 6.0}
    monkeypatch.setattr("app.services.whatif.detect_live_sweep_reclaim", lambda rows, min_rr=2.0: cand)
    monkeypatch.setattr("app.services.monitor.learning.score_plan", lambda pair, plan, state: _score())
    monkeypatch.setattr("app.services.monitor.markets.live_price_value", lambda market: 4038.0)
    monkeypatch.setattr("app.services.monitor.markets.validate_trade_levels", lambda *a, **k: (True, None))
    monkeypatch.setattr("app.services.monitor.repo.get_account", lambda pair, starting=1000.0: {"balance": 1000.0})
    monkeypatch.setattr("app.services.monitor.execution.execution_size", lambda pair, size: 0.02)
    monkeypatch.setattr("app.services.monitor.repo.create_setup_journal", lambda row: journals.append(row) or 1)
    monkeypatch.setattr("app.services.monitor.repo.create_alert", lambda a: created.append(a) or 1)
    monkeypatch.setattr("app.services.monitor.repo.add_event", lambda *a, **k: None)
    idx = pd.to_datetime([1785369600 + i * 300 for i in range(3)], unit="s")
    df = pd.DataFrame({"open": [4030, 4035, 4038], "high": [4033, 4040, 4041],
                       "low": [4022, 4032, 4035], "close": [4031, 4036, 4038]}, index=idx)
    return df, SimpleNamespace(id="xauusd", display="XAU/USD", code="frxXAUUSD")


def test_maybe_arm_reversal_arms_despite_low_quality_and_flags_it(monkeypatch):
    """Advisory: the setup arms, and the journal records that it was low-scored.

    Those flagged rows plus their outcomes are the evidence that could re-arm the
    gate. Blocking them produces no evidence at all, forever.
    """
    journals: list[dict] = []
    created: list[dict] = []
    monkeypatch.setattr("app.services.monitor.QUALITY_GATE_MODE", "advisory")
    monkeypatch.setattr("app.services.monitor.repo.get_param_overrides", lambda pair: {"sweep_reclaim": 1})
    df, market = _reversal_fixture(monkeypatch, journals, created)

    Monitor()._maybe_arm_reversal(market, df, "Scalper", learning_state=None)

    assert created, "advisory mode must still arm"
    armed = [r for r in journals if r["status"] == "armed"]
    assert armed and "low_learned_quality" in armed[0]["quality_flags"]
    assert "learned quality" in armed[0]["reason"]


def test_maybe_arm_reversal_blocks_low_quality_when_enforcing(monkeypatch):
    journals: list[dict] = []
    created: list[dict] = []
    monkeypatch.setattr("app.services.monitor.QUALITY_GATE_MODE", "enforce")
    monkeypatch.setattr("app.services.monitor.repo.get_param_overrides", lambda pair: {"sweep_reclaim": 1})
    df, market = _reversal_fixture(monkeypatch, journals, created)

    Monitor()._maybe_arm_reversal(market, df, "Scalper", learning_state=None)

    assert not created
    assert any(row["status"] == "blocked_quality" for row in journals)


def test_reversal_arms_when_enabled(monkeypatch):
    m = Monitor()
    cand = {"side": "short", "entry": 4090.0, "stop": 4102.0, "target": 4066.0, "rr": 2.0,
            "origin_epoch": 1785369600, "formation": {"summary": "rejection, swept prior high"}}
    monkeypatch.setattr("app.services.whatif.detect_live_sweep_reclaim", lambda rows, min_rr=2.0: cand)
    monkeypatch.setattr("app.services.monitor.repo.get_param_overrides", lambda pair: {"sweep_reclaim": 1})
    # quality gate allows (model not confident on this setup yet)
    monkeypatch.setattr("app.services.monitor.learning.score_plan",
                        lambda pair, plan, state: {"status": "insufficient_history", "segment_matched": False, "quality_score": None, "model_confidence": "low"})
    monkeypatch.setattr("app.services.monitor.markets.live_price_value", lambda market: 4090.0)
    monkeypatch.setattr("app.services.monitor.markets.validate_trade_levels", lambda *a, **k: (True, None))
    monkeypatch.setattr("app.services.monitor.repo.get_account", lambda pair, starting=1000.0: {"balance": 1000.0})
    monkeypatch.setattr("app.services.monitor.execution.execution_size", lambda pair, size: 0.02)

    created: list[dict] = []
    monkeypatch.setattr("app.services.monitor.repo.create_alert", lambda a: created.append(a) or 7)
    monkeypatch.setattr("app.services.monitor.repo.add_event", lambda *a, **k: None)
    monkeypatch.setattr("app.services.monitor.repo.create_setup_journal", lambda row: 1)

    idx = pd.to_datetime([1785369600 + i * 300 for i in range(3)], unit="s")
    df = pd.DataFrame({"open": [4095, 4092, 4090], "high": [4100, 4096, 4093],
                       "low": [4088, 4089, 4087], "close": [4093, 4091, 4090]}, index=idx)
    market = SimpleNamespace(id="xauusd", display="XAU/USD", code="frxXAUUSD")

    m._maybe_arm_reversal(market, df, "Scalper", learning_state=None)
    assert created  # armed when sweep_reclaim is enabled
