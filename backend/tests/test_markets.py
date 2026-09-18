from app.services import markets


def test_build_catalog_skips_probe_when_active_symbols_empty(monkeypatch):
    monkeypatch.setattr(markets, "DATA_SOURCE", "deriv")
    monkeypatch.setattr(markets, "fetch_active_symbols", lambda app_id=None, url=None: [])
    monkeypatch.setattr(
        markets,
        "stop_deriv_feed",
        lambda: None,
    )
    monkeypatch.setattr(markets, "_reset_feed_cache", lambda: None)

    catalog, status = markets.build_catalog()

    assert len(catalog) == len(markets.PREFERRED_MARKETS)
    assert all(item.live is False for item in catalog)
    assert "active_symbols" in (status["error"] or "")
