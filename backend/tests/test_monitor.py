from types import SimpleNamespace

from app.services import alert_engine as ae
from app.services.monitor import EXHAUSTION_AT_TARGET, Monitor


def test_exhaustion_at_target_creates_management_event(monkeypatch):
    monitor = Monitor()
    alert = ae.Alert(
        pair="xauusd",
        timeframe="15m",
        side="long",
        source="fvg",
        zone_min=4030.0,
        zone_max=4034.0,
        entry=4034.0,
        stop=4029.0,
        target=4042.0,
        state=ae.FILLED,
    )
    alert.id = 11
    monitor._alerts = [alert]

    events: list[tuple[int, str, float, str]] = []
    journals: list[dict] = []

    monkeypatch.setattr("app.services.monitor.repo.add_event", lambda alert_id, state, price, reason: events.append((alert_id, state, price, reason)))
    monkeypatch.setattr("app.services.monitor.repo.create_setup_journal", lambda row: journals.append(row) or 1)
    monkeypatch.setattr("app.services.monitor.repo.alert_events", lambda alert_id: [])

    market = SimpleNamespace(id="xauusd", display="XAU/USD")
    scan_result = {
        "price": 4041.2,
        "layers": {
            "patterns_all": [
                {
                    "name": "High Wave",
                    "direction": "neutral",
                    "confirmed": True,
                    "role": "warning",
                    "price": 4041.5,
                    "time": 1,
                    "index": 10,
                }
            ]
        },
    }

    monitor._check_exhaustion_at_target(market, scan_result)

    assert events
    assert events[0][1] == EXHAUSTION_AT_TARGET
    assert "High Wave at target zone" in events[0][3]
    assert journals and journals[0]["status"] == "exhaustion_at_target"
    assert monitor._alerts[0].state == ae.FILLED


def test_fresh_discarded_plan_retires_matching_prefill_alert(monkeypatch):
    monitor = Monitor()
    alert = ae.Alert(
        pair="xauusd",
        timeframe="5m",
        side="short",
        source="fvg",
        zone_min=4013.30,
        zone_max=4026.45,
        entry=4013.30,
        stop=4021.65,
        target=3996.60,
        state=ae.TOUCHED,
    )
    alert.id = 42
    monitor._alerts = [alert]

    events: list[tuple[int, str, float, str]] = []
    updates: list[dict] = []
    journals: list[dict] = []

    monkeypatch.setattr(
        "app.services.monitor.topdown.load_context",
        lambda market, risk_pct, trader_mode=None, min_rr=None, counter_trend_ok=None: {
            "feed_blocked": False,
            "setup_scan": {
                "future": {
                    "entries": [
                        {
                            "side": "short",
                            "source": "fvg",
                            "zone_min": 4013.30,
                            "zone_max": 4026.45,
                            "entry_price": 4013.30,
                        }
                    ]
                }
            },
            "plan": {
                "status": "DISCARDED",
                "side": "short",
                "entry": 4013.30,
                "stop": 4021.65,
                "target": 3996.60,
                "rr": 2.0,
                "reason": "nearest obstacle caps R:R at 0.55, below the 2.0 minimum",
                "feed_source": "deriv",
                "resolved_symbol": "frxXAUUSD",
                "price_valid": True,
            },
            "feed": {
                "5m": {
                    "feed_source": "deriv",
                    "resolved_symbol": "frxXAUUSD",
                    "price_valid": True,
                    "reference_price": 4022.0,
                    "candle_price": 4022.0,
                }
            },
        },
    )
    monkeypatch.setattr("app.services.monitor.repo.add_event", lambda alert_id, state, price, reason: events.append((alert_id, state, price, reason)))
    monkeypatch.setattr("app.services.monitor.repo.update_alert", lambda alert_id, **fields: updates.append({"alert_id": alert_id, **fields}))
    monkeypatch.setattr("app.services.monitor.repo.create_setup_journal", lambda row: journals.append(row) or 1)

    market = SimpleNamespace(id="xauusd", display="XAU/USD")
    monitor._arm_from_scan(market, "Scalper")

    assert monitor._alerts[0].state == ae.INVALIDATED
    assert any(event[1] == ae.INVALIDATED for event in events)
    assert any("retired by fresh veto" in event[3] for event in events)
    assert any(row["status"] == "retired" for row in journals)
    assert any(row["status"] == "discarded" for row in journals)
