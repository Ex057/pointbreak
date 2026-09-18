# TradeGuard — Build Tracker

> **Read this file first.** It is the state of the build, not a design doc.
> Update the Status table and the Now section as work lands.

**Last updated:** 2026-07-20 · **Current milestone:** M2 · **Track:** Web app first
· **Next action:** first push to GitHub (see *Pushing to GitHub* below)

---

## The product in one paragraph

TradeGuard is a **verdict machine**, not a charting app. It scans a chart and returns one honest
recommendation: **WAIT** (amber, wordy — because it must persuade patience), **ENTER** (green,
terse — because only execution details remain), or **STAND ASIDE** (news imminent). ENTER is only
reachable through the **turtle-soup gate**: the zone must be TOUCHED → SWEPT → RECLAIMED. The
anti-scam positioning collapses if the product over-claims, so every alert shows its reasoning
and every outcome — win or loss — stays on the record.

Source of truth: `docs/TradeGuard_Proposal 2.docx`. Mockups extracted to `docs/figures/`.
**`docs/figures/W1_web_dashboard_wait.png` is the layout target for the web app.**

---

## Status

| Milestone | State | Notes |
|---|---|---|
| M0 Tracker + repo skeleton | ✅ | git init, backend/ webapp/ docs/ archive/, files moved, figures extracted |
| M1 Scan API (no network) | ✅ | Engine adapter (index→epoch), `/health`, `/scan` + `/verdict`, regression tests |
| M2 Live Deriv feed | 🔨 | Deriv WS client wired, env-driven startup, selected-pair feed activation replacing full preload |
| M3 `alert_engine.py` (net-new) | ✅ | State machine + turtle-soup gate in `services/alert_engine.py`; `services/monitor.py` runs it always-on; smoke-tested |
| M4 Web app (Figure W1) | 🔨 | Vite + React + TS, lightweight-charts v4, live config-driven pairs/timeframes, layer priority filters, market-structure badge, **Positions & alerts panel + paper-trade entry** |
| M5 Verdict + persistence + settings | 🔨 | Position sizing, SQLite schema **built** (`db/database.py` + `db/repo.py`, WAL, migrations), paper ledger + equity, news stub, backend-scored level priorities |
| + Market-structure classifier | ✅ | `services/structure.py`: swing-first UPTREND/DOWNTREND/RANGE/CONSOLIDATION, badge + verdict reason, 11 tests |
| M6 Test runs (UAT §10) | ⬜ | Walk the proposal's acceptance table end to end |
| — Android (Phase 3) | 🔨 | Deferred UI-wise, but backend pair/timeframe contract is being kept mobile-safe |

Legend: ✅ done · 🔨 in progress · ⬜ not started

---

## Now — web app is wired to the live alert system (2026-07-20)

The database and alert engine are no longer backend-only. The web app now shows the whole
loop: a **Positions & alerts** panel polls `/monitor`, `/alerts`, `/stats` every 4s and renders
the always-on monitor's pair, the paper account (balance, P&L, win rate, avg R), and every armed
alert walking `WATCHING → TOUCHED → SWEPT → FILLED → TARGET/STOPPED` with its event trail.
**Paper-trade entry** is live: an ENTER/WAIT plan can be taken with one click (`POST /trades`),
which opens a FILLED position **at market on the demo ledger only — no real Deriv order is ever
placed** — and hands it to the monitor to track to target/stop. Positions can be closed at market
(`POST /trades/{id}/close`), which books the actual R. Open positions show a live running-R while
they run. Smoke-tested end-to-end in fixture mode (entry → open → close → account move).

**Backend surface used by the web app:** `/monitor` (GET/PUT), `/alerts`, `/stats`, `/account`,
`/trades`, `/trades/{id}/close`. Schema carries `alerts.last_price`/`unrealized_r` (added via a
lightweight ALTER-based migration in `db/database.py`).

## Prior status note (kept for context)

The vertical slice is real and running on **live Deriv data**: the web app renders, the backend
exposes health + scan + verdict, engine output is epoch-mapped for lightweight-charts, the UI
reads its pair/timeframe catalog from the backend, and the market-structure classifier labels each
scan (UPTREND / DOWNTREND / RANGE / CONSOLIDATION) with reasoning.

**Immediate goal: first push to GitHub.** The stack is in a demonstrable state and should be
version-controlled remotely before more is built. See *Pushing to GitHub* below for the checklist.

## Next 3 actions

1. **Push to GitHub** — see the checklist. `.gitignore` is now hardened; verify no secrets/junk,
   write a README, commit the tree, create the remote, push.
2. Close the **live-price gap**: the displayed price is the last *candle close*, not the live
   *tick*. Decide whether to expose the tick cache (`GET /price` or a poll) or accept candle-close
   for now. See *Live price flow* below.
3. Finish M2 polish: surface feed readiness cleanly in the UI, no hammering on known
   `503 feed_not_ready`, and keep the mobile-safe contract stable.

---

## Live price flow (how the real price is actually obtained) — reviewed 2026-07-20

Traced end to end. The path from Deriv to the number on screen:

1. **Connect** — `markets._ensure_live_feed()` calls `start_deriv_feed(symbols=[code],
   granularities=[…])`, which opens a **synchronous** websocket (`websocket-client`,
   `create_connection`) to `wss://…/websockets/v3?app_id=1089` on a background **reader thread**
   (not asyncio). One active pair at a time; switching pair calls `stop_deriv_feed()` first — this
   is what side-steps the `start_deriv_feed()` "ignores new symbols" trap (#3).
2. **Subscribe** — on connect, `_subscribe_all()` sends **two** kinds of subscription per symbol:
   `{"ticks": code, "subscribe":1}` (live ticks) **and** a `ticks_history … style:candles`
   subscription per granularity in the bundle.
3. **Ingest** (reader thread, `_handle`): `tick` → writes `DERIV_CONFIG["prices"][sym]` (the live
   price); `candles` (history batch) → fills the deque; `ohlc` (streaming) → updates/appends the
   in-progress bar.
4. **Consume** — `markets.load_candles()` polls `get_candles_df(code, granularity)` up to 6s until
   candles appear, returns an OHLC DataFrame. (This blocks the request thread; fine because FastAPI
   runs sync endpoints in a threadpool, but the first request after a pair switch is slow.)
5. **Display** — `scan()` sets `price = df["close"].iloc[-1]` — **the last candle's close.** Served
   in `/scan` + `/verdict` JSON; the frontend shows it and refreshes only on Re-scan / pair switch
   (there is **no polling and no push**).

**The finding that matters:** the live **tick** price is captured in `DERIV_CONFIG["prices"]` but
**never exposed or displayed.** The on-screen "price" is the latest candle close. On 1m this is
near-real-time (the `ohlc` stream keeps the current bar fresh — measured ~7s old), but on higher
timeframes it lags by up to the bar size, and nothing auto-updates it between scans. "Real-time
price" is therefore aspirational today; it's "price as of the last scan's last candle close."
Options, in order of effort: (a) accept candle-close and relabel honestly; (b) add
`GET /price/{pair}` reading the tick cache + a small frontend poll; (c) the eventual `WS /stream`
push for both web and Android. Logged as Next action #2, not a blocker for the push.

---

## Decisions log

- **2026-07-20 — Trade entry on the demo is PAPER, never a real Deriv order.** "Enter trades"
  moves the SQLite paper ledger (`accounts`/`equity`/`outcomes`) and hands the position to the
  monitor's state machine. TradeGuard still never places a broker order — this preserves the
  "never trades on your behalf" footer. A real Deriv demo `buy` (authorize + proposal + buy) was
  explicitly declined for this slice. *Why:* the anti-scam positioning depends on us not touching
  order flow; the paper ledger already existed and makes the mechanics demonstrable safely.
- **2026-07-20 — `alert_engine.py` now exists; the earlier "does not exist" note below is
  superseded.** The turtle-soup state machine (`services/alert_engine.py`), the always-on
  `services/monitor.py`, and the SQLite persistence layer (`db/`) are built and smoke-tested. The
  automated ENTER *verdict* is still gated in `verdict.py` (`enter_blocked_by`); paper entry is a
  deliberate manual override, not the automated gate opening.

- **2026-07-20 — Lean vertical slice before full Phase 1 infra.** FastAPI + engine + React +
  SQLite, single user, no auth, news stubbed. *Why:* a real test run in days rather than weeks.
  TimescaleDB, Redis, APScheduler, auth and FCM are staged after the first test run.
- **2026-07-20 — Safe pair ids + query endpoints over path labels.** Pairs like `EUR/USD` and
  `XAU/USD` are user-facing labels, not path-safe identifiers. The web app now uses backend-driven
  ids (`eurusd`, `xauusd`, `r_100`, etc.) and query-based `/scan` / `/verdict` requests. *Why:*
  avoid slash/path bugs now and keep the contract reusable by Android later.
- **2026-07-20 — `alert_engine.py` is net-new work.** The proposal states it is "already built and
  tested" (§1, §7, Phase 0 roadmap row). It does not exist anywhere in the tree. The pattern
  engine and Deriv feed are real and solid; the turtle-soup state machine — the product's core
  economic argument — is unwritten. **This overclaim is a live risk in an investor-facing doc
  that offers to demo "engine, alerts and market feed" on request.**
- **2026-07-20 — Deriv demo `app_id` 1089, via `DERIV_APP_ID` env var from day one.** Real 24/7
  synthetic-index data, no account needed. The demo id is rate-limited — register one before any
  public beta.
- **2026-07-20 — Live mode must never fake candles.** In `DATA_SOURCE=deriv`, a feed failure now
  returns `503 feed_not_ready` rather than falling back to synthetic fixture data. *Why:* fake gold
  or crypto prices are worse than an explicit error and destroy operator trust.
- **2026-07-20 — Build on `pattern_engine_v2` only.** v1 archived to `archive/pattern_engine_v1.py`.
  v2 is nearly a superset; see Known traps for the two things v1 had that v2 lost.
- **2026-07-20 — Backend stays client-agnostic.** No web-specific response shaping, so the Android
  app in Phase 3 is a client, not a rewrite.
- **2026-07-20 — Selected-pair Deriv activation over full preload.** The first attempt subscribed
  the whole catalog and hit Deriv `RateLimit`, especially on `frxXAUUSD` 1m. The feed is being
  reshaped around the active pair and a compact timeframe bundle. *Why:* make the demo app id
  usable while preserving the same backend for mobile later.

---

## Known traps (do not rediscover)

1. **Engine indices are positional ints (0..n-1), never timestamps.** Every `index`, `x0`, `x1`,
   `x_future` must be mapped to an epoch by the scan adapter. lightweight-charts needs epochs.
   Get this wrong and the chart renders looking entirely plausible while being completely wrong.
   Future epochs = `last_epoch + n × granularity_seconds`.
2. **lightweight-charts stays on v4** (`^4.1`, already pinned). v5 changed to
   `chart.addSeries(CandlestickSeries, ...)` and breaks the mapping documented in
   `webapp/package.json`'s `notes` block. Do not let a scaffold drift.
3. **`start_deriv_feed()` silently ignores new symbols once `_client` exists**
   (`deriv_feed.py:377`). Use `DerivWebSocketClient` directly with injected
   `prices_ref`/`candles_ref`/`status_ref`. Avoid the module singleton.
4. **The demo Deriv app id will rate-limit aggressive preload.** Subscribing many pairs × many
   `ticks_history` granularities at startup looks fine in code and fails in production-looking
   ways. The correct demo-safe strategy is selected-pair activation plus a minimal timeframe bundle.
5. **`analyze()` double-computes zones.** `project_future()` recomputes all four zone layers from
   scratch that `analyze()` just built. Pass the precomputed layers in.
6. **Target is hardcoded 2R** (`pattern_engine_v2.py:495`). Parameterize it, or the Settings
   "minimum R:R" control means nothing.
7. **`deriv_feed` is threads, not asyncio.** `on_tick`/`on_candle` fire on the reader thread —
   bridge to async via `asyncio.run_coroutine_threadsafe`. Do not port it; the backoff,
   re-subscribe, keepalive and staleness watchdog all work.
8. **`broken` is always `False`** in `detect_supply_demand()` output — it filters broken zones
   before returning (`:213`). Drop the field from the API response.
9. **v2 detects 22 patterns, v1 detected 24.** Homing Pigeon and Tri-Star Doji were dropped. Don't
   promise a pattern count in marketing copy without checking.
10. **Patterns live in one monolithic loop**, no per-pattern functions. Per-pattern UI explanations
   need a name→metadata table maintained alongside it.
11. **Only `plot()` touches matplotlib**, lazily imported. The web app ignores it entirely.
12. **`start.sh` uses the backend venv directly; shell activation is not the issue.** If live mode
   fails, inspect `.run/backend.log` for websocket/rate-limit/subscription errors, not your shell.
13. **The displayed "price" is the last candle close, not the live tick.** `scan()` sets
   `price = df["close"].iloc[-1]`. The tick cache (`DERIV_CONFIG["prices"]`) is populated but
   unused. Don't describe the current UI as showing a real-time tick — it doesn't. See *Live price
   flow*.

---

## Open questions

- **News calendar provider** — ForexFactory scrape / Finnhub / TradingEconomics. Licensing and
  reliability differ sharply. `services/news.py` is a stub behind the real interface until this
  is decided, so the STAND_ASIDE path is wired and testable regardless.
- **Synthetic indices have no macro calendar.** The proposal (§4) says flag abnormal volatility
  spikes instead. That rule is unspecified — needs a concrete threshold.
- **Account balance for position sizing** — user-entered for now, or broker-linked later? Affects
  whether sizing is advisory or binding.
- **Dedicated Deriv `app_id` timing** — the demo id is enough for local dev with selected-pair
  activation, but mobile + web side-by-side use will need a real app id sooner rather than later.
- **UAT steps 6 and 8** assume push to a locked phone. The web equivalent is browser push, which
  is deferred — log these as N/A-for-web rather than quietly passing them.

---

## Pushing to GitHub (first remote push)

The stack is demonstrable and should be on a remote before more is built. This is a plan, not yet
executed — run it when ready.

**Pre-flight (must be clean before the first commit):**
- [x] `.gitignore` hardened — now excludes `.run/`, `webapp/vite.config.js|.d.ts`, `*.tsbuildinfo`,
  `build/`, on top of `.env`, `.venv/`, `node_modules/`, `*.sqlite`, `.qodo/`.
- [ ] **Secret check** — confirm no real secrets are tracked. Today there are none: the only
  credential is the Deriv demo `app_id=1089`, which is public. `.env` is git-ignored; `.env.example`
  holds only `DATA_SOURCE` + the demo id. Re-verify with `git status` + a grep for keys/tokens
  before pushing.
- [ ] **Remove tracked junk** — `webapp/vite.config.js`, `*.tsbuildinfo`, `backend/tradeguard.sqlite`
  and `.run/` must not be in the first commit (now covered by `.gitignore`; verify with
  `git status --short`).
- [ ] **Write `README.md`** — what TradeGuard is (one paragraph from the top of this file), the
  anti-scam positioning, a quickstart (`./start.sh setup && ./start.sh start`), the `start.sh`
  command list, the architecture sketch (thin clients + shared Python backend + Deriv feed), and an
  explicit **"not financial advice / prototype"** disclaimer. Point to `docs/` for the proposal.
- [ ] **License** — decide (private repo vs a license file). Investor-facing IP; default to a
  **private** repo unless the user wants it public.

**Push sequence:**
1. `git add -A` and review `git status` — the working tree currently has M0/M1 committed plus a
   large body of **uncommitted** M2 + M4 + structure work. Group into a few honest commits (feed
   activation / web app / structure classifier) rather than one dump, so history is legible.
2. Create the remote with `gh repo create` (recommend **private**), then `git push -u origin main`.
3. Confirm the pushed tree on GitHub excludes all ignored artifacts and contains the README.

**Note on authorship:** several files were hand-edited by the user after the agent wrote them
(config, main, markets, feed). Commit messages should reflect that this is joint work — do not
claim sole authorship of the M2 feed layer.

---

## Staged follow-ups (deliberately not in the current slice)

Proposal Phases 1/4/5, parked here so nothing is lost:
TimescaleDB hypertables · Redis pub/sub fan-out · APScheduler multi-pair background scanning ·
auth/JWT · FCM push · browser push · the outcomes-driven score re-weighting ("learning loop",
§7.3 item 4 — note the ~20-outcome guardrail before a bucket influences weights) · the public
read-only verdict feed · additional broker feeds as adapters · a proper request/response or stream
surface for the later Android app (`WS /stream` or equivalent) once the selected-pair live feed is
stable on web.

---

## Repo layout

```
point_break/
├── PLAN.md                     ← you are here
├── backend/
│   ├── requirements.txt
│   └── app/
│       ├── main.py             ← FastAPI, backend-driven health/config + live/feed status
│       ├── config.py           ← env: DATA_SOURCE, DERIV_APP_ID, DB_URL, ACCOUNT_BALANCE
│       ├── engine/             ← pattern_engine_v2.py, deriv_feed.py (Deriv WS client)
│       ├── services/           ← scan · verdict · markets · structure · news · fixtures
│       │                          · alert_engine (turtle-soup state machine) · monitor (always-on)
│       ├── api/                ← (empty — routers still inline in main.py)
│       └── db/                 ← database.py (schema + WAL + migrations) · repo.py (helpers)
├── webapp/                     ← Vite + React + TS + lightweight-charts v4
│   └── src/components/         ← TgChart · VerdictCard · StructureBadge · PositionsPanel
├── docs/                       ← proposal, SETUP.md, playbook, figures/
└── archive/                    ← pattern_engine_v1.py, dependencies.gradle.kts (Android, later)
```
