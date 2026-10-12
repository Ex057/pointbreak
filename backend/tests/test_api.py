from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.services.fixtures import make_candles


def api():
    return TestClient(app)


def test_health_exposes_pairs_and_timeframes():
    with api() as client:
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["default_pair"] == "xauusd"
    assert "1W" in body["timeframes"]
    assert body["default_ai_provider"] == "builtin"
    assert body["ai_providers"]["builtin"]["status"] == "ready"
    assert any(item["id"] == "eurusd" for item in body["pairs"])
    # The specialized catalog includes every active Deriv volatility index.
    ids = {item["id"] for item in body["pairs"]}
    assert {
        "r_10", "r_25", "r_50", "r_75", "r_100",
        "r_10_1s", "r_15_1s", "r_25_1s", "r_30_1s", "r_50_1s",
        "r_75_1s", "r_90_1s", "r_100_1s",
    } <= ids


def test_research_history_endpoint_reports_provider_coverage():
    with api() as client:
        response = client.get("/research/history", params={"pair": "xauusd"})
    assert response.status_code == 200
    body = response.json()
    assert body["history"]["timeframe"] == "1H"
    assert set(body["history"]["sources"]) == {"deriv", "yahoo"}
    assert "jobs" in body


def test_health_uses_fallback_catalog_when_bootstrap_fails(monkeypatch):
    monkeypatch.setattr("app.main.build_catalog", lambda: (_ for _ in ()).throw(RuntimeError("catalog down")))
    with api() as client:
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert any(item["id"] == "xauusd" for item in body["pairs"])


def test_research_data_status_is_explicitly_research_only(monkeypatch):
    monkeypatch.setattr(
        "app.main.research_lab.data_status",
        lambda pair: {"pair": pair, "research_only": True, "modes": []},
    )
    with api() as client:
        response = client.get("/research/data-status", params={"pair": "xauusd"})
    assert response.status_code == 200
    assert response.json() == {"pair": "xauusd", "research_only": True, "modes": []}


def test_research_run_request_defaults_to_all_modes(monkeypatch):
    captured = {}

    def start(pair, modes):
        captured.update(pair=pair, modes=modes)
        return {"id": 7, "pair": pair, "status": "queued", "modes": modes}

    monkeypatch.setattr("app.main.research_lab.start_run", start)
    with api() as client:
        response = client.post("/research/runs", json={"pair": "xauusd"})
    assert response.status_code == 202
    assert captured["pair"] == "xauusd"
    assert captured["modes"] == ["Scalper", "Day Trader", "Swing Trader", "Position Trader"]


def test_monitor_endpoint_degrades_instead_of_500(monkeypatch):
    monkeypatch.setattr("app.main.monitor.status", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr("app.main.repo.get_user_settings", lambda: {"trader_mode": "Swing Trader"})
    with api() as client:
        response = client.get("/monitor")
    assert response.status_code == 200
    body = response.json()
    assert body["running"] is False
    assert body["error"] == "monitor status degraded: boom"
    assert body["entry_tf"] == "1H"


def test_verdict_query_supports_safe_pair_ids(monkeypatch):
    monkeypatch.setattr(
        "app.main._load_candles",
        lambda pair, timeframe: (
            SimpleNamespace(id="eurusd", display="EUR/USD", category="forex", code="frxEURUSD"),
            make_candles("EURUSD", timeframe),
        ),
    )
    monkeypatch.setattr(
        "app.main.topdown.load_context",
        lambda market, min_rr, risk_pct, trader_mode=None, counter_trend_ok=None: {
            "feed_blocked": False,
            "plan": None,
        },
    )
    monkeypatch.setattr(
        "app.main.feed_snapshot",
        lambda market, timeframe, df: {
            "pair": market.id,
            "timeframe": timeframe,
            "feed_source": "fixture",
            "resolved_symbol": market.code,
            "price_valid": True,
            "reference_price": float(df["close"].iloc[-1]),
            "candle_price": float(df["close"].iloc[-1]),
            "price_drift": 0.0,
            "feed_error": None,
        },
    )
    with api() as client:
        response = client.get("/verdict", params={"pair": "eurusd", "tf": "1H", "min_rr": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["pair"] == "eurusd"
    assert body["pair_display"] == "EUR/USD"
    assert body["verdict"]["ai_analysis"]["provider"] == "builtin"
    assert "reasoning" in body["verdict"]["ai_analysis"]
    # The story must survive the degenerate context above (no frames, no scans,
    # no analysis) — that is the shape most likely to KeyError in production.
    story = body["verdict"]["story"]
    assert story["version"] == 1
    assert isinstance(story["beats"], list)
    assert story["state"] in {"roadmap", "in_trade", "no_setup", "blocked", "failed"}
    for beat in story["beats"]:
        assert (beat["comparator"] is None) == (beat["level"] is None)


def test_ai_analysis_endpoint_returns_history_aware_payload(monkeypatch):
    monkeypatch.setattr(
        "app.main._load_candles",
        lambda pair, timeframe: (
            SimpleNamespace(id="eurusd", display="EUR/USD", category="forex", code="frxEURUSD"),
            make_candles("EURUSD", timeframe),
        ),
    )
    monkeypatch.setattr(
        "app.main.topdown.load_context",
        lambda market, min_rr, risk_pct, trader_mode=None, counter_trend_ok=None: {
            "feed_blocked": False,
            "plan": {
                "status": "WAITING",
                "side": "long",
                "entry": 1.085,
                "stop": 1.08,
                "target": 1.095,
                "rr": 2.0,
                "size": 1.0,
                "trader_mode": "Swing Trader",
                "bias_tf": "1D",
                "setup_tf": "4H",
                "entry_tf": "1H",
                "target_basis": "support x2",
                "notes": [],
                "alternatives": [],
                "checklist": [{"stage": "trigger", "name": "Confirmation", "passed": False, "detail": "waiting for bullish trigger candle"}],
                "feed_source": "fixture",
                "resolved_symbol": "frxEURUSD",
                "price_valid": True,
                "feed_error": None,
                "confirmation_state": "WAITING",
                "reason": "conditions aligned — waiting for a trigger at the zone",
            },
            "setup_scan": {"future": {"entries": [{"side": "long", "source": "fvg", "zone_min": 1.0845, "zone_max": 1.0855, "entry_price": 1.085}]}},
        },
    )
    monkeypatch.setattr(
        "app.main.feed_snapshot",
        lambda market, timeframe, df: {
            "pair": market.id,
            "timeframe": timeframe,
            "feed_source": "fixture",
            "resolved_symbol": market.code,
            "price_valid": True,
            "reference_price": float(df["close"].iloc[-1]),
            "candle_price": float(df["close"].iloc[-1]),
            "price_drift": 0.0,
            "feed_error": None,
        },
    )
    monkeypatch.setattr(
        "app.main.repo.list_setup_journal",
        lambda pair, since=None, until=None, limit=100: [
            {"status": "retired", "side": "long", "source": "fvg", "trader_mode": "Swing Trader"},
            {"status": "waiting", "side": "long", "source": "fvg", "trader_mode": "Swing Trader"},
        ],
    )
    monkeypatch.setattr(
        "app.main.repo.closed_ledger",
        lambda pair, since=None, until=None: [
            {"result_r": 2.0, "side": "long", "zone_type": "fvg", "trader_mode": "Swing Trader"},
            {"result_r": -1.0, "side": "long", "zone_type": "fvg", "trader_mode": "Swing Trader"},
        ],
    )
    monkeypatch.setattr(
        "app.main.learning.ensure_current_model",
        lambda pair, force_refresh=False: {
            "pair": pair,
            "enabled": True,
            "model_kind": "setup_quality_v1",
            "active_model_version": "setup_quality_v1-123",
            "active_model_status": "promoted",
            "last_retrained": 123,
            "training_window": {"start": 1, "end": 2},
            "sample_count": 24,
            "holdout_size": 5,
            "promotion_result": "promoted",
            "promotion_reason": "Candidate beat baseline.",
            "metrics": {"candidate_mae": 0.2, "baseline_mae": 0.3},
            "summary": {"targets": 4, "stopped": 2, "retired": 1, "discarded": 3},
            "history": [],
        },
    )
    monkeypatch.setattr(
        "app.main.learning.score_plan",
        lambda pair, plan, status=None: {
            "status": "ready",
            "quality_score": 68,
            "fill_probability": 0.62,
            "target_probability": 0.44,
            "retire_risk": 0.18,
            "model_confidence": "medium",
            "model_version": "setup_quality_v1-123",
            "training_sample_count": 24,
        },
    )
    with api() as client:
        response = client.get("/ai-analysis", params={"pair": "eurusd", "tf": "1H"})
    assert response.status_code == 200
    body = response.json()
    assert body["analysis"]["agreement"]["state"] == "cautious"
    assert body["analysis"]["history_check"]["sample_size"] == 2
    assert body["analysis"]["what_is_missing"] == ["waiting for bullish trigger candle"]
    assert body["analysis"]["learned_score"]["quality_score"] == 68
    assert body["analysis"]["learning_status"]["active_model_version"] == "setup_quality_v1-123"


def test_learning_status_endpoint(monkeypatch):
    monkeypatch.setattr(
        "app.main.learning.ensure_current_model",
        lambda pair, force_refresh=False: {
            "pair": pair,
            "enabled": True,
            "model_kind": "setup_quality_v1",
            "active_model_version": "setup_quality_v1-999",
            "active_model_status": "promoted",
            "last_retrained": 999,
            "training_window": {"start": 100, "end": 200},
            "sample_count": 32,
            "holdout_size": 6,
            "promotion_result": "promoted",
            "promotion_reason": "Candidate beat baseline.",
            "metrics": {"candidate_mae": 0.18, "baseline_mae": 0.25},
            "summary": {"targets": 5},
            "history": [],
        },
    )
    with api() as client:
        response = client.get("/learning/status", params={"pair": "xauusd"})
    assert response.status_code == 200
    body = response.json()
    assert body["active_model_version"] == "setup_quality_v1-999"
    assert body["sample_count"] == 32


def test_settings_support_ai_provider_updates():
    with api() as client:
        updated = client.put("/settings", json={"ai_provider": "none"})
        restored = client.put("/settings", json={"ai_provider": "builtin"})
    assert updated.status_code == 200
    assert updated.json()["settings"]["ai_provider"] == "none"
    assert restored.status_code == 200
    assert restored.json()["settings"]["ai_provider"] == "builtin"


def test_unknown_timeframe_returns_structured_error():
    with api() as client:
        response = client.get("/verdict", params={"pair": "xauusd", "tf": "2H", "min_rr": 2})
    assert response.status_code == 404
    body = response.json()
    assert body["detail"]["code"] == "unknown_timeframe"


def test_alerts_endpoint_returns_pagination(monkeypatch):
    monkeypatch.setattr(
        "app.main.repo.list_alerts_page",
        lambda pair, page, per_page: {
            "items": [
                {
                    "id": 7,
                    "pair": "eurusd",
                    "timeframe": "1H",
                    "side": "long",
                    "source": "plan",
                    "zone_min": 1.08,
                    "zone_max": 1.09,
                    "entry": 1.085,
                    "stop": 1.08,
                    "target": 1.095,
                    "score": 1.0,
                    "confluence": 3,
                    "state": "WATCHING",
                    "swept_price": None,
                    "size": 2.0,
                    "rr": 2.0,
                    "last_price": None,
                    "unrealized_r": None,
                    "armed_at": 1,
                    "updated_at": 2,
                }
            ],
            "page": page,
            "per_page": per_page,
            "total": 11,
            "total_pages": 2,
        },
    )
    monkeypatch.setattr(
        "app.main.repo.alert_events",
        lambda alert_id: [{"id": 1, "alert_id": alert_id, "state": "WATCHING", "ts": 2, "price": 1.085, "reason": "armed"}],
    )
    with api() as client:
        response = client.get("/alerts", params={"pair": "eurusd", "page": 2, "per_page": 8})
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"] == {"page": 2, "per_page": 8, "total": 11, "total_pages": 2}
    assert body["alerts"][0]["id"] == 7
    assert body["alerts"][0]["events"][0]["reason"] == "armed"


def test_connectors_crud_endpoints(monkeypatch):
    store = []

    def fake_list():
        return list(store)

    def fake_create(item):
        row = {
            "id": 1,
            "name": item["name"],
            "url": item["url"],
            "oauth_client_id": item.get("oauth_client_id"),
            "enabled": True,
            "has_secret": bool(item.get("oauth_client_secret")),
            "created_at": 1,
            "updated_at": 1,
        }
        store[:] = [row]
        return 1

    def fake_delete(connector_id):
        if store and store[0]["id"] == connector_id:
            store.clear()
            return True
        return False

    monkeypatch.setattr("app.main.repo.list_connectors", fake_list)
    monkeypatch.setattr("app.main.repo.create_connector", fake_create)
    monkeypatch.setattr("app.main.repo.delete_connector", fake_delete)

    with api() as client:
        created = client.post(
            "/connectors",
            json={
                "name": "Claude data bridge",
                "url": "https://example.com/mcp",
                "oauth_client_id": "abc",
                "oauth_client_secret": "secret",
            },
        )
        listed = client.get("/connectors")
        deleted = client.delete("/connectors/1")

    assert created.status_code == 201
    assert created.json()["name"] == "Claude data bridge"
    assert created.json()["has_secret"] is True
    assert listed.status_code == 200
    assert listed.json()["connectors"][0]["url"] == "https://example.com/mcp"
    assert deleted.status_code == 204
