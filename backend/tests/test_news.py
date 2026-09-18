from app.services import news


def test_context_for_marketaux_scores_xauusd_from_headlines(monkeypatch):
    monkeypatch.setenv("HEADLINE_PROVIDER", "marketaux")
    monkeypatch.setenv("MARKETAUX_API_KEY", "test-key")
    monkeypatch.delenv("NEWSAPI_KEY", raising=False)
    monkeypatch.setattr(news, "_calendar_events", lambda pair: [])
    monkeypatch.setattr(
        news,
        "_fetch_marketaux_articles",
        lambda pair: (
            [
                {
                    "title": "Gold rises as dollar weakens after dovish Fed remarks",
                    "description": "Safe-haven demand and lower yields support bullion.",
                    "source": "Example Wire",
                    "published_at": "2026-07-29T08:00:00Z",
                    "provider": "marketaux",
                    "entities": [],
                    "provider_sentiment": 0.18,
                }
            ],
            None,
        ),
    )
    news._headline_cache.clear()

    ctx = news.context_for("xauusd")

    assert ctx["provider_status"]["selected_headline_provider"] == "marketaux"
    assert ctx["provider_status"]["headlines"] == "live"
    assert ctx["articles"][0]["title"].startswith("Gold rises")
    assert ctx["sentiment"]["label"] == "bullish"
    assert ctx["sentiment"]["score"] > 0
    assert "bullish" in ctx["sentiment"]["movement_explainer"].lower()
    assert ctx["sentiment"]["drivers"]


def test_context_for_uses_inverse_event_logic_for_xauusd(monkeypatch):
    monkeypatch.setenv("HEADLINE_PROVIDER", "none")
    monkeypatch.delenv("MARKETAUX_API_KEY", raising=False)
    monkeypatch.delenv("NEWSAPI_KEY", raising=False)
    monkeypatch.setattr(
        news,
        "_calendar_events",
        lambda pair: [
            {
                "title": "Unemployment Rate",
                "currency": "USD",
                "impact": "high",
                "minutes_away": -15,
                "release_ts": 1,
                "provider": "tradingeconomics",
                "actual": "4.0%",
                "forecast": "4.2%",
                "previous": "4.1%",
            }
        ],
    )
    news._headline_cache.clear()

    ctx = news.context_for("xauusd")

    assert ctx["provider_status"]["headlines"] == "disabled"
    assert ctx["sentiment"]["label"] == "bearish"
    assert ctx["sentiment"]["score"] < 0
    assert any("USD stronger" in driver or "USD" in driver for driver in ctx["sentiment"]["drivers"])
