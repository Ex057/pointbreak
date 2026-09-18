# TradeGuard / Point Break — Current System Summary

Updated: Wednesday, July 29, 2026

This is the current handoff summary for another AI or engineer. It reflects the latest implemented state, not the older prototype state.

---

## 1. Product in one line

TradeGuard is a chart-driven trading decision and paper-ledger system for `XAU/USD` first, with support for other pairs, where the backend computes the verdict, the frontend explains it visually, and every paper outcome is meant to become part of a usable research dataset.

Current posture:
- focus is `gold-first`
- verdicts are deterministic, not purely LLM-driven
- live monitoring is still **single-pair at a time**
- the system is intentionally using **paper ledger only**, no real orders

---

## 2. What the app currently does

From top to bottom, the web app now includes:

1. Market and timeframe selection.
2. Watch tabs so multiple quotes can be pinned in the UI.
3. A live chart with:
   - candlesticks
   - live price
   - live timeframe OHLC in the header
   - S/R lines
   - supply/demand zones
   - FVG zones
   - moving averages
   - candlestick patterns (confirmed / pending)
   - chart / structure patterns (flags, triangles, wedges, double top/bottom, head & shoulders, channels, rounding, cup & handle) drawn with trendlines, necklines, pivots and a target
   - projected future setups
   - selected trade replay overlays
4. A planner-backed verdict card.
5. Macro Pulse / Patterns side rail tabs.
6. Positions & alerts / paper ledger / closed ledger / research journal.

Important UI truth:
- only one pair is actively monitored live by the backend at a time
- the selected watch tab can still show that pair’s own ledger/history even if it is not the current live monitor target

---

## 3. Current decision model

The app no longer treats the verdict as a loose score only. It uses a gate-based planner in `plan.py`.

Current pipeline:

1. **Conditions**
   - structure / trend
   - MA bias
   - zone quality
   - FVG confluence

2. **Trigger**
   - confirmed pattern
   - sweep / reclaim state
   - lower-timeframe confirmation path support

3. **Stop selection**
   - structural invalidation anchor
   - ATR buffer

4. **Target selection**
   - nearest real obstacle, not fixed 2R
   - trade is discarded if nearest obstacle caps reward below minimum R:R

Planner statuses:
- `ENTER_READY`
- `WAITING`
- `DISCARDED`

Top-level verdict mapping:
- `ENTER_READY -> ENTER`
- `WAITING -> WAIT`
- `DISCARDED -> WAIT`
- news-risk hard veto can still map to `STAND_ASIDE`

Additional instruction language was added so the UI can say:
- `BUY NOW`
- `SELL NOW`
- `WAIT FOR BUY CONFIRMATION`
- `WAIT FOR SELL CONFIRMATION`
- `DISCARD LONG SETUP`
- `DISCARD SHORT SETUP`
- `WAIT - FEED CHECK REQUIRED`

---

## 4. Timeframe philosophy currently in the app

The intended standard is now top-down:
- `1D` = bias
- `4H` = setup / location
- `15m` = entry confirmation

That is the current strategic direction and is partly surfaced in the UI and planner data. The system should treat higher timeframe confluence alone as a setup, not an executable entry, until lower-timeframe confirmation exists.

Related truth:
- older legacy rows in the ledger may predate this cleaner flow
- newer journaling is aimed at making this top-down lifecycle auditable

---

## 5. Data feeds and live price behavior

### Primary live source

The backend uses Deriv first where possible.

Important recent correction:
- `frxXAUUSD` was not actually the wrong symbol
- the earlier failure was in how the Deriv client subscribed
- the feed client was changed to use working request shapes based on polling/history instead of the earlier rejected subscription path

### Fallback behavior

For real markets such as `XAU/USD`, `EUR/USD`, `GBP/USD`, `USD/JPY`, and `US30`, the backend can fall back to Yahoo data when needed.

For gold specifically:
- Deriv may still be flaky depending on session/account/access
- Yahoo fallback is accepted as a valid chart/verdict source for real-market continuity

### Price shown in the UI

The header now includes:
- live price
- timeframe OHLC

Backend surfaces this through:
- `GET /price`
- `GET /ws/price`

Both now include latest OHLC for the selected timeframe, not just last traded price.

---

## 6. Supported markets

Core catalog includes:
- `xauusd`
- `eurusd`
- `gbpusd`
- `usdjpy`
- `us30`
- Deriv synthetics such as volatility indices / crash indices

Operational reality:
- real markets are the current practical focus
- `xauusd` is the main monitored research instrument right now

---

## 7. Chart behavior and overlays

The chart is built with `lightweight-charts` plus HTML overlays.

Current rendered elements:
- candlesticks
- moving averages
- thick horizontal S/R lines
- S/R focus mode
- supply/demand rectangles
- FVG rectangles
- candlestick pattern markers
- chart / structure pattern geometry (trendlines/necklines via line series, regions via overlay bands, pivots + name as markers, measured-move target as a price line), categorized bullish / bearish / range and gated by the Patterns checklist
- future projected zones / execution overlays
- selected trade replay overlay

Recent chart improvements:
- the page uses more of the full screen
- chart redraw was reduced to feel less like a full page refresh
- future view modes support:
  - projected
  - TP/SL path
  - both

### S/R display

Support and resistance were changed away from shaded bands and back to thick lines to better match candle peaks.

Current design:
- support and resistance use different colors
- they are ranked so only the more relevant nearby levels show by default
- `S/R focus` can emphasize them

---

## 8. Paper ledger, alerts, and monitoring

This is now one of the biggest parts of the product.

### Monitor model

The monitor remains **single-pair live**.

This means:
- one pair is actively advanced by the background monitor
- other watched pairs can still be viewed in the UI with their own ledger/history
- switching the live monitor target is explicit

### Alert lifecycle

The monitor and alert engine use states such as:
- `WATCHING`
- `TOUCHED`
- `SWEPT`
- `FILLED`
- `TARGET`
- `STOPPED`
- `EXPIRED`
- `INVALIDATED`

### Paper ledger

Each pair has its own paper account / ledger data model in the database.

Stored per pair:
- account row
- equity rows
- alerts
- alert events
- outcomes

Important recent operational changes:
- `xauusd` ledger was reset to a fresh baseline
- current clean start balance is `1000.00`
- old polluted/invalid gold rows were cleared or filtered from visible reporting

### Gold trade realism

Gold paper trading is now intended to behave more realistically:
- fixed `0.02` lot size assumption for gold
- spread-aware execution model
- real-market sanity validation blocks absurd trade levels

---

## 9. Closed ledger and trade replay

The closed ledger was substantially improved.

Current columns include:
- date / time
- pair
- side
- timeframe
- entry
- close
- replay mode
- result
- cash
- balance
- chart action

### Replay truth model

The system now distinguishes between:
- `exact`
- `estimated`
- `unavailable`

Meaning:
- `exact` = real fill event and enough stored timestamps/prices exist
- `estimated` = older legacy trade can be approximately replayed, but not truthfully as exact
- `unavailable` = insufficient data for reliable chart replay

This was added because some old rows were being drawn using arm time as if it were true fill time, which created misleading chart replays.

### Chart replay rendering

Selected closed trades can now render:
- entry dot
- exit dot
- dotted or dashed path from entry to close
- labels that indicate exact vs estimated replay

Important truth:
- legacy rows without `FILLED` events should not be treated as exact
- one historical malformed row still exists as a legacy incomplete case and should remain marked accordingly, not faked

---

## 10. Research journal and dataset fidelity

The system now has a real `setup_journal` in the backend.

Purpose:
- store not only closed trades
- also store attempted setups, waiting states, blocks, and arm decisions

Examples of journaled states/reasons:
- `scan_failed`
- `feed_blocked`
- `waiting`
- `blocked_max_active`
- `blocked_score`
- `blocked_overlap`
- `blocked_sanity`
- `armed`

This is important because the goal is no longer just “show trades”; it is to collect a useful research dataset that can explain:
- what was seen
- what was blocked
- what was armed
- what was filled
- what eventually won or lost

### Why this matters

Reducing losses should come from analyzing a trustworthy dataset, not from suppressing losing rows or relying on hindsight. The journal is the first step toward that.

---

## 11. Data-quality protections now in place

Several hardening changes were added after bad `xauusd` rows appeared with impossible prices like `97.xx`.

Current protections:
- sanity floor checks for gold
- reference-price drift validation
- invalid trade levels are blocked before manual or monitor-created paper trades are recorded
- invalid-price legacy rows are filtered out of the visible closed ledger

The backend also now tries to infer missing `side` safely for older rows instead of letting the UI crash.

Other recent data-quality improvements:
- outcome deduping by `alert_id`
- equity deduping by `alert_id`
- replay metadata carried with closed rows
- explicit `data_quality_flags`

Common flags:
- `legacy_incomplete`
- `missing_filled_event`
- `manual_trade`
- `invalid_price`

---

## 12. Macro Pulse and news state

The web app has a `Macro Pulse` side tab now.

What it currently does well:
- exposes monitoring/feed/verdict context in a cleaner side surface
- gives the UI a place for macro/news intelligence

What is still incomplete:
- live macro/news monitoring is not yet fully mature
- `backend/app/services/news.py` is no longer just a conceptual stub, but this area still needs more real provider wiring and stronger verdict integration

There has been work toward real feeds such as:
- Trading Economics calendar
- NewsAPI headlines

But this area should still be treated as partially implemented rather than finished.

---

## 13. Watch tabs and pair handling

The UI now has watch tabs so multiple quotes can be pinned and switched quickly.

Important distinction:
- watch tabs are a **UI convenience**
- they are **not** true multi-pair live monitoring

Current behavior:
- selected tab controls which chart and ledger you are viewing
- explicit action is used to make one pair the active live-monitored pair

This is a good staging point, but the backend is still not doing parallel monitoring across many instruments.

---

## 14. Connectors / Claude integration status

There was a custom connector panel in the UI for adding remote MCP-style connector details.

Current status:
- the **web UI section has been removed for now**
- backend connector endpoints/storage still exist
- this was removed because it is not part of the immediate trading/product focus

So if another AI sees connector-related backend code, that is expected. The frontend surface was intentionally removed.

---

## 15. Main backend pieces

Key backend files:

- `backend/app/main.py`
  - FastAPI app
  - routes for scan, verdict, price, stats, journal, alerts, account, monitor, trades

- `backend/app/services/scan.py`
  - engine-to-JSON adapter
  - index/time mapping
  - layer preparation

- `backend/app/services/verdict.py`
  - verdict assembly
  - planner-backed response shaping

- `plan.py`
  - four-gate planner
  - status/checklist generation
  - stop/target logic

- `backend/app/services/monitor.py`
  - live monitor loop
  - alert advancing
  - setup arming
  - setup journaling
  - paper trade lifecycle persistence

- `backend/app/services/markets.py`
  - candle loading
  - feed snapshot
  - live tick / OHLC retrieval
  - trade-level sanity validation

- `backend/app/engine/deriv_feed.py`
  - Deriv market-data client
  - recent request-shape fix

- `backend/app/db/database.py`
  - schema, including `setup_journal`

- `backend/app/db/repo.py`
  - ledger, journal, stats, and dedupe helpers

---

## 16. Main frontend pieces

Key frontend files:

- `webapp/src/App.tsx`
  - top-level state
  - pair/timeframe/watch-tab behavior
  - monitor switching
  - live price / OHLC stream usage

- `webapp/src/components/TgChart.tsx`
  - chart rendering
  - overlay drawing
  - future projections
  - replay drawing

- `webapp/src/components/VerdictCard.tsx`
  - planner checklist
  - verdict instruction surface

- `webapp/src/components/PositionsPanel.tsx`
  - positions & alerts
  - closed ledger
  - replay action
  - research journal

- `webapp/src/components/MacroPulse.tsx`
  - macro side tab

- `webapp/src/api/types.ts`
  - replay metadata
  - journal row types
  - OHLC price payload typing

---

## 17. Current known limitations

These are the honest current limits another AI should know immediately:

1. **Single-pair live monitor**
   - not yet true multi-pair background monitoring

2. **Replay fidelity depends on stored lifecycle completeness**
   - exact replay is only available for rows with proper fill/close provenance
   - estimated replay is a compromise for older legacy rows

3. **Macro/news is not yet fully production-grade**
   - side-tab exists
   - deeper verdict-grade macro integration still needs work

4. **Top-down flow is directionally in place but still maturing**
   - the intended `1D -> 4H -> 15m` discipline is clearer now than before
   - the research dataset is only now becoming good enough to evaluate it properly

5. **Gold-first focus**
   - other pairs are supported and can chart
   - but `xauusd` is still the cleanest current research target

6. **Legacy data still exists conceptually**
   - the visible ledger is cleaner now
   - but not every old row was born under the current stricter lifecycle rules

---

## 18. Best next ideas

If another AI is brainstorming from here, the highest-value directions are:

1. **Reduce losses through data review, not guesswork**
   - analyze journaled blocked/armed/filled outcomes
   - compare losing trades by trigger type, structure state, and nearest-obstacle quality

2. **Improve entry quality**
   - make `1D -> 4H -> 15m` enforcement stricter
   - demand clearer lower-timeframe confirmation before arming a candidate

3. **Add trade management realism**
   - break-even events
   - trailing stop updates
   - TP1 / TP2 partial management

4. **Strengthen macro veto**
   - finish real calendar/headline integration
   - score macro alignment against technical direction

5. **Make the journal analytics richer**
   - setup win rate by source
   - outcome quality by replay mode
   - time-of-day/session breakdown
   - stop-anchor / target-basis performance

6. **Eventually move to multi-pair monitoring**
   - but only after the single-pair dataset is trustworthy enough

---

## 19. Short version for another AI

TradeGuard is now a gold-first, planner-backed, paper-ledger trading system with live charting, single-pair background monitoring, setup journaling, and a more truthful closed-ledger replay model. The biggest recent improvements were: planner integration, target selection by nearest obstacle, live OHLC, watch tabs, data-quality guards for bad gold prices, exact-vs-estimated replay, setup journal persistence, and removal of the connector UI. The biggest unfinished areas are still multi-pair live monitoring, fully mature macro/news integration, and richer trade-management analytics.
