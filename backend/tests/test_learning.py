from app.services import learning


def test_build_samples_derives_closed_and_retired_history(monkeypatch):
    monkeypatch.setattr(
        "app.services.learning.repo.closed_ledger",
        lambda pair: [
            {
                "pair": pair,
                "closed_at": 200,
                "result_r": 2.0,
                "side": "long",
                "zone_type": "fvg",
                "trader_mode": "Scalper",
                "stop_distance_points": 10.0,
                "target_distance_points": 20.0,
                "rr": 2.0,
                "data_quality_flags": [],
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.learning.repo.list_setup_journal",
        lambda pair, limit=600: [
            {
                "pair": pair,
                "status": "retired",
                "side": "long",
                "source": "fvg",
                "trader_mode": "Scalper",
                "last_seen_at": 150,
                "created_at": 100,
                "quality_flags": [],
            },
            {
                "pair": pair,
                "status": "waiting",
                "side": "long",
                "source": "fvg",
                "trader_mode": "Scalper",
                "created_at": 50,
                "quality_flags": [],
            },
        ],
    )
    samples = learning.build_samples("xauusd")
    assert len(samples) == 2
    assert samples[0].outcome == "target"
    assert samples[1].outcome == "retired"


def test_build_samples_folds_in_counterfactual_replays(monkeypatch):
    monkeypatch.setattr("app.services.learning.repo.closed_ledger", lambda pair: [])
    monkeypatch.setattr("app.services.learning.repo.list_setup_journal", lambda pair, limit=600: [])
    monkeypatch.setattr(
        "app.services.learning.repo.eod_review_history",
        lambda pair, limit=30: [
            {
                "created_at": 500,
                "review": {
                    "day_end": 499,
                    "replays": [
                        {"gate": "rr_below_min", "side": "long", "source": "fvg",
                         "trader_mode": "Scalper", "entry_tf": "5m", "filled": True,
                         "outcome": "target", "counterfactual_r": 1.6},
                        {"gate": "blocked_score", "side": "short", "source": "demand",
                         "trader_mode": "Scalper", "entry_tf": "5m", "filled": True,
                         "outcome": "stopped", "counterfactual_r": -1.0},
                        {"gate": "rr_below_min", "side": "long", "source": "fvg",
                         "trader_mode": "Scalper", "entry_tf": "5m", "filled": False,
                         "outcome": "unavailable", "counterfactual_r": 0.0},
                    ],
                },
            }
        ],
    )
    samples = learning.build_samples("xauusd")
    # two usable replays (the "unavailable" one is skipped)
    assert len(samples) == 2
    # counterfactuals land in their own whatif:<gate> segments, never real ones
    keys = {s.feature_key() for s in samples}
    assert "Scalper|whatif:rr_below_min|long" in keys
    assert "Scalper|whatif:blocked_score|short" in keys
    won = next(s for s in samples if s.source == "whatif:rr_below_min")
    assert won.target_label == 1 and won.fill_label == 1


def test_score_plan_uses_promoted_model_segment(monkeypatch):
    monkeypatch.setattr(
        "app.services.learning.repo.latest_learning_model",
        lambda pair, status=None: {
            "version": "setup_quality_v1-1",
            "artifact": {
                "global": {"count": 20, "fill_probability": 0.5, "target_probability": 0.3, "retire_risk": 0.2},
                "segments": {
                    "Scalper|fvg|long": {
                        "count": 12,
                        "fill_probability": 0.7,
                        "target_probability": 0.5,
                        "retire_risk": 0.1,
                    }
                },
            },
        },
    )
    score = learning.score_plan(
        "xauusd",
        {"trader_mode": "Scalper", "source": "fvg", "side": "long"},
        {"sample_count": 30, "training_window": {"start": 1, "end": 2}},
    )
    assert score is not None
    assert score["quality_score"] == 64
    assert score["fill_probability"] == 0.7
    assert score["retire_risk"] == 0.1
