# TradeGuard — Build Tracker

> **Read this file first.** It is the state of the build, not a design doc.
> Update the Status table and the Now section as work lands.

**Last updated:** 2026-07-20 · **Current milestone:** M1 · **Track:** Web app first

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
| M1 Scan API (no network) | 🔨 | Engine adapter (index→epoch), `/health`, `/scan/{pair}/{tf}`, snapshot test |
| M2 Live Deriv feed | ⬜ | Lifespan-owned client, thread→async bridge, `WS /stream` |
| M3 `alert_engine.py` (net-new) | ⬜ | State machine + turtle-soup gate. **Does not exist yet** |
| M4 Web app (Figure W1) | ⬜ | Vite + React + TS, lightweight-charts v4, 6 layer pills, verdict card |
| M5 Verdict + persistence + settings | ⬜ | Position sizing, SQLite, 6 tables, news stub |
| M6 Test runs (UAT §10) | ⬜ | Walk the proposal's acceptance table end to end |
| — Android (Phase 3) | ⬜ | Deferred. Backend stays client-agnostic to keep this cheap |

Legend: ✅ done · 🔨 in progress · ⬜ not started

---

## Now

M0 is complete: the repo is under git, restructured into `backend/` + `webapp/` + `docs/` +
`archive/`, and the nine proposal figures are extracted as PNGs for reference during the build.

M1 is next: write `backend/app/services/scan.py`, the adapter that turns `PatternEngine.analyze()`
output into JSON the chart can actually render. It is testable with zero network using the
synthetic random-walk generator already in the engine's `__main__` block.

## Next 3 actions

1. `backend/app/services/scan.py` — index→epoch mapping, precomputed layers into `project_future`,
   strip dead `broken` field, thread `min_rr` through to replace the hardcoded 2R.
2. `backend/app/main.py` + `api/scan.py` — `GET /health`, `GET /scan/{pair}/{tf}` on fixture data.
3. `backend/tests/test_scan.py` — snapshot the payload; assert emitted times are plausible epochs,
   not small ints. This is the regression net for every later refactor.

---

## Decisions log

- **2026-07-20 — Lean vertical slice before full Phase 1 infra.** FastAPI + engine + React +
  SQLite, single user, no auth, news stubbed. *Why:* a real test run in days rather than weeks.
  TimescaleDB, Redis, APScheduler, auth and FCM are staged after the first test run.
- **2026-07-20 — `alert_engine.py` is net-new work.** The proposal states it is "already built and
  tested" (§1, §7, Phase 0 roadmap row). It does not exist anywhere in the tree. The pattern
  engine and Deriv feed are real and solid; the turtle-soup state machine — the product's core
  economic argument — is unwritten. **This overclaim is a live risk in an investor-facing doc
  that offers to demo "engine, alerts and market feed" on request.**
- **2026-07-20 — Deriv demo `app_id` 1089, via `DERIV_APP_ID` env var from day one.** Real 24/7
  synthetic-index data, no account needed. The demo id is rate-limited — register one before any
  public beta.
- **2026-07-20 — Build on `pattern_engine_v2` only.** v1 archived to `archive/pattern_engine_v1.py`.
  v2 is nearly a superset; see Known traps for the two things v1 had that v2 lost.
- **2026-07-20 — Backend stays client-agnostic.** No web-specific response shaping, so the Android
  app in Phase 3 is a client, not a rewrite.

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
4. **`analyze()` double-computes zones.** `project_future()` recomputes all four zone layers from
   scratch that `analyze()` just built. Pass the precomputed layers in.
5. **Target is hardcoded 2R** (`pattern_engine_v2.py:495`). Parameterize it, or the Settings
   "minimum R:R" control means nothing.
6. **`deriv_feed` is threads, not asyncio.** `on_tick`/`on_candle` fire on the reader thread —
   bridge to async via `asyncio.run_coroutine_threadsafe`. Do not port it; the backoff,
   re-subscribe, keepalive and staleness watchdog all work.
7. **`broken` is always `False`** in `detect_supply_demand()` output — it filters broken zones
   before returning (`:213`). Drop the field from the API response.
8. **v2 detects 22 patterns, v1 detected 24.** Homing Pigeon and Tri-Star Doji were dropped. Don't
   promise a pattern count in marketing copy without checking.
9. **Patterns live in one monolithic loop**, no per-pattern functions. Per-pattern UI explanations
   need a name→metadata table maintained alongside it.
10. **Only `plot()` touches matplotlib**, lazily imported. The web app ignores it entirely.

---

## Open questions

- **News calendar provider** — ForexFactory scrape / Finnhub / TradingEconomics. Licensing and
  reliability differ sharply. `services/news.py` is a stub behind the real interface until this
  is decided, so the STAND_ASIDE path is wired and testable regardless.
- **Synthetic indices have no macro calendar.** The proposal (§4) says flag abnormal volatility
  spikes instead. That rule is unspecified — needs a concrete threshold.
- **Account balance for position sizing** — user-entered for now, or broker-linked later? Affects
  whether sizing is advisory or binding.
- **UAT steps 6 and 8** assume push to a locked phone. The web equivalent is browser push, which
  is deferred — log these as N/A-for-web rather than quietly passing them.

---

## Staged follow-ups (deliberately not in the current slice)

Proposal Phases 1/4/5, parked here so nothing is lost:
TimescaleDB hypertables · Redis pub/sub fan-out · APScheduler multi-pair background scanning ·
auth/JWT · FCM push · browser push · the outcomes-driven score re-weighting ("learning loop",
§7.3 item 4 — note the ~20-outcome guardrail before a bucket influences weights) · the public
read-only verdict feed · additional broker feeds as adapters.

---

## Repo layout

```
point_break/
├── PLAN.md                     ← you are here
├── backend/
│   ├── requirements.txt
│   └── app/
│       ├── main.py             ← FastAPI, lifespan owns the feed
│       ├── config.py           ← env: DERIV_APP_ID, DB_URL, ACCOUNT_BALANCE
│       ├── engine/             ← pattern_engine_v2.py, deriv_feed.py (moved verbatim)
│       ├── services/           ← scan · verdict · alert_engine · news · feed
│       ├── api/                ← routers
│       └── db/                 ← SQLAlchemy models + SQLite session
├── webapp/                     ← Vite + React + TS + zustand + lightweight-charts v4
├── docs/                       ← proposal, SETUP.md, playbook, figures/
└── archive/                    ← pattern_engine_v1.py, dependencies.gradle.kts (Android, later)
```
