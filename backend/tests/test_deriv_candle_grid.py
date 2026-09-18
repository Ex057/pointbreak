"""Candle epochs must land on the granularity grid.

A streaming `ohlc` message carries both `open_time` (bucket start) and `epoch`
(the tick that produced the update). Falling back to `epoch` stamped bars with
tick time, so every tick appended a new "bucket" instead of updating the current
one. The stored 5m series ended up 90% off-grid with 23-37 second gaps, and every
ATR / swing / pattern computed over it was quietly wrong.
"""
from app.engine.deriv_feed import DerivWebSocketClient as C, _resolve_url
import json


def test_default_url_is_the_no_auth_public_endpoint():
    assert _resolve_url() == "wss://api.derivws.com/trading/v1/options/ws/public"


def test_custom_legacy_url_can_still_interpolate_app_id():
    assert _resolve_url("wss://example.test/ws?app_id={app_id}", 42).endswith("app_id=42")


def test_open_time_is_used_and_floored():
    bar = C._norm_candle(
        {"open": 1, "high": 2, "low": 0.5, "close": 1.5, "epoch": 1_800_000_123},
        open_time=1_800_000_100, granularity=3600,
    )
    assert bar["epoch"] % 3600 == 0


def test_missing_open_time_still_lands_on_the_grid():
    """The actual failure mode: no open_time, so it fell through to tick epoch."""
    bar = C._norm_candle(
        {"open": 1, "high": 2, "low": 0.5, "close": 1.5, "epoch": 1_800_000_123},
        granularity=300,
    )
    assert bar["epoch"] % 300 == 0
    assert bar["epoch"] == 1_800_000_123 - (1_800_000_123 % 300)


def test_ticks_within_one_bucket_all_normalise_to_the_same_epoch():
    """This is what makes 'update the last bar' work instead of appending."""
    base = 1_800_000_000
    epochs = {
        C._norm_candle({"open": 1, "high": 2, "low": 0.5, "close": 1.5,
                        "epoch": base + offset}, granularity=3600)["epoch"]
        for offset in (0, 23, 59, 1800, 3599)
    }
    assert len(epochs) == 1, f"one bucket produced {len(epochs)} distinct epochs"


def test_ohlc_values_are_preserved():
    bar = C._norm_candle({"open": 1.25, "high": 2.5, "low": 0.75, "close": 1.75,
                          "epoch": 1_800_000_123}, granularity=900)
    assert (bar["open"], bar["high"], bar["low"], bar["close"]) == (1.25, 2.5, 0.75, 1.75)


def test_desired_streams_are_subscriptions_not_poll_requests():
    class FakeSocket:
        def __init__(self):
            self.messages = []

        def send(self, value):
            self.messages.append(json.loads(value))

    socket = FakeSocket()
    client = C(["frxEURUSD"], granularities=[60, 300])
    client._ws = socket
    client._connected.set()
    client._subscribe_desired()

    assert socket.messages[0] == {"ticks": "frxEURUSD", "subscribe": 1}
    candles = socket.messages[1:]
    assert {m["granularity"] for m in candles} == {60, 300}
    assert all(m["subscribe"] == 1 and m["style"] == "candles" for m in candles)
    assert not any("style" not in m and "ticks_history" in m for m in socket.messages)


def test_configure_same_pair_adds_timeframe_without_reconnect():
    class FakeSocket:
        def __init__(self):
            self.messages = []

        def send(self, value):
            self.messages.append(json.loads(value))

    socket = FakeSocket()
    client = C(["frxEURUSD"], granularities=[60])
    client._ws = socket
    client._connected.set()
    client._subscribe_desired()
    before = len(socket.messages)
    client.configure(["frxEURUSD"], [60, 300])

    assert len(socket.messages) == before + 1
    assert socket.messages[-1]["granularity"] == 300
    assert not any(m.get("forget_all") for m in socket.messages)
