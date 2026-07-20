# TradeGuard — dependency manifests & Claude Code setup notes

Three stacks, three manifests. The backend is shared; each client is thin.

```
tradeguard/
├── backend/requirements.txt          # Python 3.11+  (pip install -r ...)
├── webapp/package.json               # Node 20+      (npm install)
└── android/dependencies.gradle.kts   # Kotlin/Gradle (paste into app/build.gradle.kts)
```

## What each stack is for

| Stack   | Runtime            | Role                                                        |
|---------|--------------------|-------------------------------------------------------------|
| backend | Python + FastAPI   | pattern_engine v2, alert_engine, deriv_feed, scheduler, news, DB, push |
| webapp  | React + Vite       | chart canvas (lightweight-charts), 6 layer toggles, verdict panel, stats |
| android | Kotlin + Compose   | 4 tabs (Chart / Alerts / Stats / Settings), Room cache, FCM alerts |

## Non-package prerequisites (install once)

- PostgreSQL 15+ with the TimescaleDB extension (the `candles` table is a hypertable)
- Redis 7+ (pub/sub fan-out of prices and alert events)
- A Firebase project (FCM server key for Android push; `google-services.json` in the app)
- A Deriv `app_id` (replace the demo 1089 in deriv_feed before production)
- VAPID key pair for web push (`pywebpush` docs show one-line generation)

## Suggested Claude Code prompts, in order

1. "Scaffold a FastAPI project in backend/ using requirements.txt. Mount
   pattern_engine_v2.py, alert_engine.py and deriv_feed.py as services.
   Endpoints: GET /scan/{pair}/{tf}, GET /verdict/{pair}/{tf},
   GET /alerts, GET /stats, GET/PUT /settings, WS /stream (prices + alert events)."
2. "Write the Alembic migration for tables: candles (hypertable), zones,
   alert_events, outcomes, user_settings, news_events."
3. "Scaffold the React app in webapp/ from package.json: routes /chart /alerts
   /stats /settings; render engine JSON on lightweight-charts; layer toggles
   flip series visibility; WS client consumes /stream."
4. "Scaffold the Android app from android/dependencies.gradle.kts: Compose,
   4-tab navigation, Retrofit + OkHttp WebSocket to the backend, Room cache
   of candles/verdicts, FCM service handling alert payloads."

## Version policy

Python pins are minimums (`>=`) — pip resolves latest stable.
npm uses caret ranges — the lockfile resolves on first install.
Gradle versions are known-good baselines — bump to latest stable when scaffolding.
