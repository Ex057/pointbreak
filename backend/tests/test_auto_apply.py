from app.db import repo
from app.db.database import init_schema
from app.services import strategy_review


def _rec():
    return {
        "param": "min_rr", "current_value": 2.0, "proposed_value": 1.75,
        "shadow_delta_r": 4.5, "shadow_added_trades": 3, "confidence": "medium",
        "rationale": "R:R gate too strict",
    }


def test_persist_recommendations_returns_ids_and_stays_proposed():
    init_schema()
    repo.clear_all_overrides("xauusd")
    out = strategy_review.persist_recommendations("xauusd", [_rec()], None)
    assert len(out) == 1
    assert isinstance(out[0]["id"], int)          # the buttons need this
    assert out[0]["status"] == "proposed"
    assert repo.get_param_overrides("xauusd") == {}  # nothing applied


def test_persist_recommendations_never_writes_an_override():
    """The load-bearing invariant. Auto-apply once turned on a broken detection
    engine off a hindsight estimate and cost -139R; there is no code path from a
    recommendation to a live parameter except a human clicking Approve."""
    init_schema()
    repo.clear_all_overrides("xauusd")
    strategy_review.persist_recommendations(
        "xauusd",
        [_rec(), {**_rec(), "param": "sweep_reclaim", "current_value": 0.0,
                  "proposed_value": 1.0, "shadow_delta_r": 112.8}],
        None,
    )
    assert repo.get_param_overrides("xauusd") == {}
    # and the signature must not grow an apply flag back
    import inspect
    params = inspect.signature(strategy_review.persist_recommendations).parameters
    assert "auto_apply" not in params


def test_reset_clears_overrides():
    init_schema()
    repo.set_param_override("xauusd", "min_rr", 1.5)
    repo.set_param_override("xauusd", "sweep_reclaim", 1.0)
    assert repo.get_param_overrides("xauusd")
    cleared = repo.clear_all_overrides("xauusd")
    assert cleared >= 2
    assert repo.get_param_overrides("xauusd") == {}


def test_auto_apply_setting_roundtrips():
    init_schema()
    assert repo.get_user_settings().get("auto_apply") == 0
    repo.update_user_settings(auto_apply=1)
    assert repo.get_user_settings().get("auto_apply") == 1


def test_run_eod_review_returns_id_bearing_recommendations(monkeypatch):
    init_schema()
    repo.update_user_settings(auto_apply=0)
    monkeypatch.setattr(
        "app.services.whatif.review_day",
        lambda pair, day=None, min_rr=2.0: {
            "day": "2026-07-30", "day_start": 0, "day_end": 1, "realized_r": 0.0,
            "missed_r": 4.5, "flaws": [], "gates": [], "missed_moves": [], "replays": [],
        },
    )
    monkeypatch.setattr(
        "app.services.strategy_review.run",
        lambda pair, review: {"provider": "builtin", "recommendations": [_rec()]},
    )
    monkeypatch.setattr("app.services.learning.ensure_current_model", lambda pair, force_refresh=False: {})
    from app.services.monitor import monitor
    result = monitor.run_eod_review("xauusd", day="2026-07-30")
    assert result["recommendations"]
    assert isinstance(result["recommendations"][0]["id"], int)   # the fix
    assert result["recommendations"][0]["status"] == "proposed"
