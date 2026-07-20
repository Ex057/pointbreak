"""
================================================================================
deriv_feed.py - self-contained Deriv WebSocket market-data client
================================================================================
Python port of the app's `DerivWebSocketClient` (JS) with the same protocol
and robustness guarantees:

  * subscribe to live ticks                     {"ticks": SYM, "subscribe": 1}
  * candle history + streaming updates          {"ticks_history": ..., "style":
                                                 "candles", "subscribe": 1}
  * request/response matching via req_id / echo_req
  * application-level ping every 15 s           {"ping": 1}
  * exponential-backoff auto reconnect (with full re-subscribe)
  * staleness watchdog: no message for N sec -> force reconnect
  * thread-safe in-memory cache of prices + candle stacks

It continuously populates DERIV_CONFIG["prices"] (and candle stacks), so the
existing `fetch_deriv_price` / `get_live_price()` path keeps working with ZERO
changes to the analysis pipeline - the client just makes those dicts live.
This removes the "click to connect" fragility and lets you drop the delayed
Yahoo fallback for Deriv-supported symbols.

Dependencies
------------
    pip install websocket-client        (the sync `websocket` package)

An asyncio/websockets variant would be a mechanical translation of the same
state machine; the threaded version below is chosen because it drops into a
synchronous Flask/script app without an event loop.

Quick start
-----------
    from deriv_feed import DERIV_CONFIG, start_deriv_feed, get_live_price

    start_deriv_feed(
        symbols=["R_100", "R_50", "frxEURUSD"],
        granularities=[60, 300],          # candle streams to maintain (sec)
        app_id=1089,                      # your registered Deriv app_id
    )

    price = get_live_price("R_100")               # float or None
    candles = DERIV_CONFIG["candles"]["R_100"][60]  # deque of dicts (OHLC)

    # feed candles straight into PatternEngine:
    import pandas as pd
    df = pd.DataFrame(list(candles))[["open", "high", "low", "close"]]
================================================================================
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque

try:
    import websocket  # websocket-client (only required to actually connect)
except ImportError:  # pragma: no cover
    websocket = None  # module stays importable; connect will raise clearly

log = logging.getLogger("deriv_feed")

# =============================================================================
#  Shared config the rest of the app already reads.
#  If your app defines its own DERIV_CONFIG, pass it to DerivWebSocketClient
#  (prices_ref= / candles_ref=) and this module will populate YOURS instead.
# =============================================================================
DERIV_CONFIG = {
    "prices": {},      # {symbol: {"price": float, "epoch": int, "ts": float}}
    "candles": {},     # {symbol: {granularity: deque([{...ohlc...}, ...])}}
    "status": {"connected": False, "last_msg": 0.0, "reconnects": 0},
}

_DEFAULT_URL = "wss://ws.derivws.com/websockets/v3?app_id={app_id}"


class DerivWebSocketClient:
    """
    Threaded Deriv WS client.

    Parameters
    ----------
    symbols        : list[str]   tick subscriptions (e.g. "R_100", "frxEURUSD")
    granularities  : list[int]   candle streams per symbol, in seconds
                                 (60, 120, 180, 300, 600, 900, 1800, 3600, ...)
    app_id         : int|str     Deriv application id
    candles_count  : int         history bars requested on (re)connect
    candles_maxlen : int         in-memory stack depth per symbol/granularity
    ping_interval  : float       app-level ping period (Deriv wants <= ~30 s)
    stale_after    : float       watchdog: max silence before forced reconnect
    max_backoff    : float       reconnect backoff ceiling (seconds)
    prices_ref / candles_ref / status_ref
                   : dict        inject the app's own DERIV_CONFIG sub-dicts
    on_tick / on_candle
                   : callable    optional hooks (symbol, payload) for push UIs
    """

    def __init__(
        self,
        symbols,
        granularities=(60,),
        app_id=1089,
        url=None,
        candles_count=300,
        candles_maxlen=1000,
        ping_interval=15.0,
        stale_after=30.0,
        max_backoff=60.0,
        prices_ref=None,
        candles_ref=None,
        status_ref=None,
        on_tick=None,
        on_candle=None,
    ):
        self.url = url or _DEFAULT_URL.format(app_id=app_id)
        self.symbols = list(dict.fromkeys(symbols))
        self.granularities = list(dict.fromkeys(int(g) for g in granularities))
        self.candles_count = candles_count
        self.candles_maxlen = candles_maxlen
        self.ping_interval = ping_interval
        self.stale_after = stale_after
        self.max_backoff = max_backoff
        self.on_tick = on_tick
        self.on_candle = on_candle

        self.prices = prices_ref if prices_ref is not None else DERIV_CONFIG["prices"]
        self.candles = candles_ref if candles_ref is not None else DERIV_CONFIG["candles"]
        self.status = status_ref if status_ref is not None else DERIV_CONFIG["status"]

        self._lock = threading.RLock()
        self._ws: websocket.WebSocket | None = None
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._threads: list[threading.Thread] = []

        self._req_id = 0
        self._pending: dict[int, dict] = {}      # req_id -> {"event", "resp"}
        self._sub_ids: dict[tuple, str] = {}     # (kind, symbol, gran) -> id
        self._backoff = 1.0
        self._last_msg = 0.0

        for s in self.symbols:
            self.candles.setdefault(s, {})
            for g in self.granularities:
                self.candles[s].setdefault(
                    g, deque(maxlen=self.candles_maxlen))

    # ------------------------------------------------------------ lifecycle
    def start(self):
        """Non-blocking: spins up connect/reader, ping and watchdog threads."""
        self._stop.clear()
        for name, fn in (("deriv-reader", self._run),
                         ("deriv-ping", self._ping_loop),
                         ("deriv-watchdog", self._watchdog_loop)):
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        return self

    def stop(self):
        self._stop.set()
        self._close_socket()
        for t in self._threads:
            t.join(timeout=2)
        self._threads.clear()

    def wait_ready(self, timeout=10.0):
        """Block until first successful connection (or timeout)."""
        return self._connected.wait(timeout)

    # ------------------------------------------------------------ public API
    def price(self, symbol):
        rec = self.prices.get(symbol)
        return None if rec is None else rec["price"]

    def candle_frame(self, symbol, granularity):
        """Return the candle stack as a list of dicts (oldest -> newest)."""
        with self._lock:
            return list(self.candles.get(symbol, {}).get(int(granularity), []))

    def request(self, payload: dict, timeout=10.0):
        """
        Synchronous request/response using req_id + echo_req matching.
        e.g. client.request({"active_symbols": "brief"})
        """
        with self._lock:
            self._req_id += 1
            rid = self._req_id
            payload = dict(payload, req_id=rid)
            slot = {"event": threading.Event(), "resp": None}
            self._pending[rid] = slot
        self._send(payload)
        if not slot["event"].wait(timeout):
            with self._lock:
                self._pending.pop(rid, None)
            raise TimeoutError(f"Deriv request timed out: {payload.get('msg_type', payload)}")
        return slot["resp"]

    # ------------------------------------------------------------ internals
    def _send(self, obj):
        ws = self._ws
        if ws is None:
            raise ConnectionError("not connected")
        ws.send(json.dumps(obj))

    def _close_socket(self):
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        self._connected.clear()
        self.status["connected"] = False

    # ---- main connect / read loop with backoff reconnect -------------------
    def _run(self):
        if websocket is None:  # pragma: no cover
            raise ImportError("deriv_feed needs `pip install websocket-client` to connect")
        while not self._stop.is_set():
            try:
                log.info("connecting %s", self.url)
                self._ws = websocket.create_connection(
                    self.url, timeout=self.stale_after)
                self._last_msg = time.time()
                self._backoff = 1.0
                self._connected.set()
                self.status["connected"] = True
                self._subscribe_all()

                while not self._stop.is_set():
                    raw = self._ws.recv()
                    if not raw:
                        continue
                    self._last_msg = time.time()
                    self.status["last_msg"] = self._last_msg
                    self._handle(json.loads(raw))

            except Exception as e:
                if self._stop.is_set():
                    break
                log.warning("connection dropped: %s", e)
                self._close_socket()
                self.status["reconnects"] = self.status.get("reconnects", 0) + 1
                # exponential backoff with ceiling
                sleep = self._backoff
                self._backoff = min(self._backoff * 2, self.max_backoff)
                log.info("reconnecting in %.1fs", sleep)
                self._stop.wait(sleep)
        self._close_socket()

    # ---- (re)subscribe everything after each connect ------------------------
    def _subscribe_all(self):
        self._sub_ids.clear()
        for s in self.symbols:
            self._send({"ticks": s, "subscribe": 1})
            for g in self.granularities:
                self._send({
                    "ticks_history": s,
                    "style": "candles",
                    "granularity": g,
                    "count": self.candles_count,
                    "end": "latest",
                    "adjust_start_time": 1,
                    "subscribe": 1,
                })

    # ---- ping & watchdog -----------------------------------------------------
    def _ping_loop(self):
        while not self._stop.wait(self.ping_interval):
            if self._ws is not None:
                try:
                    self._send({"ping": 1})
                except Exception:
                    pass  # reader loop will notice and reconnect

    def _watchdog_loop(self):
        while not self._stop.wait(5.0):
            if self._ws is not None and self._last_msg:
                if time.time() - self._last_msg > self.stale_after:
                    log.warning("stale feed (%.0fs silent) - forcing reconnect",
                                time.time() - self._last_msg)
                    self._close_socket()   # reader recv() raises -> backoff path

    # ---- message routing -------------------------------------------------------
    def _handle(self, msg: dict):
        echo = msg.get("echo_req", {}) or {}

        # resolve any synchronous request() waiting on this req_id
        rid = echo.get("req_id") or msg.get("req_id")
        if rid is not None:
            with self._lock:
                slot = self._pending.pop(rid, None)
            if slot is not None:
                slot["resp"] = msg
                slot["event"].set()
                # subscriptions keep flowing below even if requested via request()

        if msg.get("error"):
            log.error("deriv error: %s (echo=%s)", msg["error"], echo)
            return

        mtype = msg.get("msg_type")

        if mtype == "tick":
            t = msg["tick"]
            sym = t["symbol"]
            self.prices[sym] = {
                "price": float(t["quote"]),
                "epoch": int(t["epoch"]),
                "ts": time.time(),
                "source": "deriv",
            }
            self._sub_ids[("tick", sym, None)] = t.get("id")
            if self.on_tick:
                self.on_tick(sym, self.prices[sym])

        elif mtype == "candles":
            # initial history batch
            sym = echo.get("ticks_history")
            gran = int(echo.get("granularity", 60))
            stack = self.candles.setdefault(sym, {}).setdefault(
                gran, deque(maxlen=self.candles_maxlen))
            with self._lock:
                stack.clear()
                for c in msg.get("candles", []):
                    stack.append(self._norm_candle(c))
            if msg.get("subscription"):
                self._sub_ids[("candles", sym, gran)] = msg["subscription"]["id"]
            log.info("history loaded %s g=%s (%d bars)", sym, gran, len(stack))

        elif mtype == "ohlc":
            # streaming candle update: replace last bar or append new one
            o = msg["ohlc"]
            sym = o["symbol"]
            gran = int(o.get("granularity", echo.get("granularity", 60)))
            bar = self._norm_candle(o, open_time=o.get("open_time"))
            stack = self.candles.setdefault(sym, {}).setdefault(
                gran, deque(maxlen=self.candles_maxlen))
            with self._lock:
                if stack and stack[-1]["epoch"] == bar["epoch"]:
                    stack[-1] = bar                  # same bucket -> update
                else:
                    stack.append(bar)                # new bucket -> append
            if self.on_candle:
                self.on_candle(sym, gran, bar)

        elif mtype in ("pong", "ping"):
            pass  # keepalive handled; _last_msg already refreshed

        elif mtype == "forget_all":
            pass

    @staticmethod
    def _norm_candle(c, open_time=None):
        return {
            "epoch": int(open_time if open_time is not None else c.get("epoch")),
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
        }


# =============================================================================
#  Module-level convenience layer (matches the app's existing call sites)
# =============================================================================
_client: DerivWebSocketClient | None = None


def start_deriv_feed(symbols, granularities=(60,), app_id=1089,
                     wait=True, **kwargs) -> DerivWebSocketClient:
    """Start (once) the background feed populating DERIV_CONFIG."""
    global _client
    if _client is None:
        _client = DerivWebSocketClient(
            symbols, granularities=granularities, app_id=app_id, **kwargs
        ).start()
        if wait:
            _client.wait_ready(timeout=10)
    return _client


def stop_deriv_feed():
    global _client
    if _client is not None:
        _client.stop()
        _client = None


def fetch_deriv_price(symbol, max_age=10.0):
    """
    Existing pipeline entry point - now served from the live cache.
    Returns float or None (None if unknown symbol or the quote is stale).
    """
    rec = DERIV_CONFIG["prices"].get(symbol)
    if rec is None:
        return None
    if max_age is not None and time.time() - rec["ts"] > max_age:
        return None
    return rec["price"]


def get_live_price(symbol, fallback=None, max_age=10.0):
    """
    Deriv-first quote. For supported symbols the delayed Yahoo/futures
    fallback is no longer consulted; `fallback(symbol)` only runs when the
    symbol isn't on the feed at all.
    """
    px = fetch_deriv_price(symbol, max_age=max_age)
    if px is not None:
        return px
    supported = _client is not None and symbol in _client.symbols
    if not supported and fallback is not None:
        return fallback(symbol)
    return None


def get_candles_df(symbol, granularity=60):
    """Candle stack -> pandas DataFrame ready for PatternEngine."""
    import pandas as pd
    if _client is None:
        return pd.DataFrame(columns=["open", "high", "low", "close"])
    rows = _client.candle_frame(symbol, granularity)
    df = pd.DataFrame(rows)
    if not df.empty:
        df["time"] = pd.to_datetime(df["epoch"], unit="s")
        df = df.set_index("time")[["open", "high", "low", "close"]]
    return df


# =============================================================================
#  Demo (requires internet + `pip install websocket-client`)
# =============================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    start_deriv_feed(["R_100", "R_50"], granularities=[60], app_id=1089)
    try:
        for _ in range(12):
            time.sleep(5)
            print("R_100:", get_live_price("R_100"),
                  "| R_50:", get_live_price("R_50"),
                  "| bars:", len(DERIV_CONFIG["candles"].get("R_100", {}).get(60, [])),
                  "| status:", DERIV_CONFIG["status"])
    finally:
        stop_deriv_feed()
