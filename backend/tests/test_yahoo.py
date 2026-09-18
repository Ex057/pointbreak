import time

from app.services import yahoo


def test_price_record_preserves_provider_and_receipt_age(monkeypatch):
    symbol = "TEST=F"
    yahoo._price_cache.pop(symbol, None)
    now = int(time.time())
    monkeypatch.setattr(
        yahoo,
        "_get",
        lambda _url: {
            "chart": {
                "result": [{
                    "timestamp": [now - 7],
                    "indicators": {"quote": [{"close": [10.0]}]},
                    "meta": {"regularMarketPrice": 10.25, "regularMarketTime": now - 7},
                }]
            }
        },
    )

    record = yahoo.fetch_price_record(symbol)

    assert record is not None
    assert record["price"] == 10.25
    assert record["epoch"] == now - 7
    assert record["age"] >= 7
    assert record["received_age"] < 2


def test_candle_cache_avoids_repeated_provider_calls(monkeypatch):
    symbol = "CACHE=F"
    key = (symbol, "1D")
    yahoo._candle_cache.pop(key, None)
    calls = []
    now = int(time.time())

    def fake_get(_url):
        calls.append(1)
        return {
            "chart": {
                "result": [{
                    "timestamp": [now - 86400],
                    "indicators": {"quote": [{
                        "open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5]
                    }]},
                    "meta": {"regularMarketPrice": 1.5, "regularMarketTime": now - 86400},
                }]
            }
        }

    monkeypatch.setattr(yahoo, "_get", fake_get)
    first = yahoo.fetch_candles(symbol, "1D")
    second = yahoo.fetch_candles(symbol, "1D")

    assert not first.empty and not second.empty
    assert len(calls) == 1
    assert second.attrs["source"] == "yahoo"
