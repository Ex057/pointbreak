# TradeGuard Android — Screen Blueprints

Source: `docs/TradeGuard_Proposal 2.docx` §5.2 ("Mobile application — the alert companion") and
figures M1–M4. Reference images are in `figures/` beside this file. Four bottom-nav tabs, shared
amber/green verdict language with the web app.

Design rule (from the proposal): **the verdict is the hero.** WAIT is amber and wordy because it
must persuade patience; ENTER is green and terse because only execution details remain.

---

## Bottom navigation

Four tabs, always visible: **Chart · Alerts · Stats · Settings**
(`ui/nav/AppNav.kt`, Material3 `NavigationBar`). Icons: candlestick / bell / bar-chart / gear.

---

## M1 · Chart tab — the hero  (`figures/M1_mobile_chart_enter.png`)

The only tab wired to live data. Top-to-bottom:

- **Header** — pair display name + live price + signed % change (green up / red down).
- **Timeframe pills** — 1m / 5m / 1H / 1D (the mockup's set; backend offers more). Selected pill is
  dark. Horizontally scrollable.
- **Pair chips** — the watchlist as selectable chips (backend `/health` `pairs`), selected = indigo.
- **Structure badge** — the market-structure read (UPTREND / DOWNTREND / RANGE / CONSOLIDATION) with
  confidence, colour-coded, reusing the backend `structure` block.
- **Chart** — candlesticks hand-drawn on a Compose Canvas with translucent S/R, supply/demand and
  FVG zone bands behind the candles. (Sweep/reclaim marks + projected-entry markers + trendline
  extension are the next layer to add.)
- **Verdict card** — amber WAIT (states nearest zone, distance, confluence, ETA) or green ENTER
  ("sweep+reclaim ✓" badge, terse). Below it four tiles: Planned entry · Stop · Target (2R) · Size.
- **"Journal this trade"** action on ENTER — planned, wired when the outcomes journal (backend M5)
  exists.

Implemented: `ui/chart/ChartScreen.kt`, `CandleCanvas.kt`, `VerdictCard.kt`, `ChartViewModel.kt`.

---

## M2 · Alerts tab  (`figures/M2_mobile_alerts.png`) — **stubbed**

Intended: a header count ("3 active") and one card per armed zone. Each card is one zone; the top
card expands into a full **state timeline**:

```
Alert armed from scan      07:02 · projected long · score 0.72
Zone touched 698–702       09:14 · support x3 + FVG · confluence x4
Sweep + reclaim confirmed  09:31 · wick 697.9, closed 701.8
Entered long 701.20        09:32 · stop 697.40 · target 708.80
```

Plus honest secondary states: a **news-paused** card ("paused for news"), a **touched/awaiting
sweep** card, and an **expired** card ("no touch in 60 bars · retired on re-scan"). Status badges:
entered=green, watching=amber, touched=blue, expired=grey.

**Blocked on backend M3** (alert_engine + turtle-soup state machine). Currently a "coming soon"
placeholder — `ui/alerts/AlertsScreen.kt`.

---

## M3 · Stats tab  (`figures/M3_mobile_stats.png`) — **stubbed**

Intended: a period selector ("90 days"), three headline tiles — **Trades · Win rate · Avg result
(R)** — then **win-rate bars per zone type** with sample counts (Demand/supply 68% ·19, FVG
58% ·12, Support/resist. 44% ·16), then plain-language insights ("Best: fresh demand R_100 1H ·
sweep confirmed — 74% over 15"; "Weakest: counter-trend S/R shorts 5m · 31% over 13"; "Turtle-soup
avoided 11 first-touch stop-outs"). Footer: Export / By pair / By timeframe.

**Blocked on backend M5** (outcomes journal + aggregation). Currently a "coming soon" placeholder —
`ui/stats/StatsScreen.kt`.

---

## M4 · Settings tab  (`figures/M4_mobile_settings.png`) — **local for now**

Sections, matching the mockup:
- **Risk rules** — Risk per trade slider (0.5–2%), Minimum R:R (1:2), Turtle-soup gate toggle,
  Max concurrent positions (3).
- **News stand-aside** — Pause before high-impact news toggle, Window (30 min before).
- **Notifications** — Zone touched, Entry confirmed (sweep+reclaim), Every scan summary.
- **Watchlist** — pair chips + Add.

The proposal is emphatic that these rules are **enforced by the backend, not merely suggested**.
For now the controls are live but local (`remember`); DataStore persistence + backend
`user_settings` sync is milestone A3. Implemented shell: `ui/settings/SettingsScreen.kt`.

---

## Architecture (`figures/fig3_android_architecture.png`)

Thin client, shared backend. The app streams/fetches from the FastAPI gateway; analysis,
scheduling and alert state live server-side; FCM (later) delivers alerts to a locked phone.
Networking: Retrofit + OkHttp (`data/api/`), DTOs mirror the backend JSON one-to-one
(`data/model/Dtos.kt`), `MarketRepository` is the single seam a WebSocket tick stream slots into
later without any UI change.
