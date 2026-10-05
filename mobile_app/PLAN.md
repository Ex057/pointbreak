# TradeGuard Mobile (APK) — Build Tracker

> **Read this first.** State of the Android build, not a design doc.
> The web app and backend live one level up in `../`. This app is a **client** of
> that same backend — no backend feature work happens here.

**Last updated:** 2026-07-20 · **Current milestone:** A1→A2 · **Stack:** native Kotlin + Compose

---

## The product (mobile)

The Android companion to the TradeGuard web app: four bottom-nav tabs — **Chart · Alerts · Stats ·
Settings** — sharing the web app's amber **WAIT** / green **ENTER** / red **STAND ASIDE** verdict
language. The phone is the "alert companion"; the same shared Python backend does all analysis, so
the verdict on the phone and the web always agree. Blueprints: `docs/BLUEPRINTS.md` (from proposal
§5.2, figures M1–M4).

---

## Status

| Milestone | State | Notes |
|---|---|---|
| A0 Scaffold + blueprints | ✅ | Gradle project, wrapper (8.9), `docs/BLUEPRINTS.md`, figures copied |
| A1 4-tab shell | ✅ | NavHost + bottom nav, amber/green theme, all four screens present |
| A2 Chart tab live | 🔨 | Retrofit + DTOs + CandleCanvas + VerdictCard wired to `/health` + `/verdict`; **builds; on-device run pending a phone** |
| A3 Settings (local) | 🔨 | Screen matches mockup M4 with live controls; DataStore persistence not yet wired |
| A4 Stub polish + shareable APK | ⬜ | Alerts/Stats "coming soon" laid out; signed-debug APK + install doc |
| Later — Alerts live | ⬜ | Blocked on backend M3 (alert_engine / turtle-soup) |
| Later — Stats live | ⬜ | Blocked on backend M5 (outcomes persistence) |
| Later — WebSocket ticks | ⬜ | Blocked on backend `WS /stream` |
| Later — FCM push | ⬜ | Needs a Firebase project + `google-services.json` |

Legend: ✅ done · 🔨 in progress · ⬜ not started

---

## Now

**The APK builds** (`./gradlew assembleDebug` → `app/build/outputs/apk/debug/app-debug.apk`,
~18 MB) and the backend is confirmed reachable from the LAN. The four-tab shell, the live Chart
tab (candles on a Compose Canvas + verdict card + structure badge), the stub Alerts/Stats, and a
local Settings screen are all implemented. What's left before A2 is **done** is running it on a
real device and confirming the Chart renders live data.

## Next 3 actions

1. **Run on the phone.** `HOST=0.0.0.0 ./start.sh restart` (already supported), confirm the Mac's
   LAN IP (`ipconfig getifaddr en0` → currently `192.168.1.5`), build with that IP baked in
   (`./gradlew assembleDebug -PtgApiBase=http://<LAN-IP>:8000/`), `adb install -r` over USB, open
   the Chart tab, confirm candles + verdict. Phone + Mac on the same Wi-Fi.
2. **A3:** back the Settings controls with DataStore so they persist; let the watchlist drive the
   Chart pair list.
3. **A4:** polish the Alerts/Stats placeholders to match M2/M3 layout; produce the first shareable
   debug APK and write the install steps into `README.md`.

---

## Decisions log

- **2026-07-20 — Native Kotlin + Jetpack Compose.** Matches the proposal (§7.1) and
  `../archive/dependencies.gradle.kts`; the installed Android Studio + SDK support it. Chosen over
  React Native/Flutter despite the web app being React — this is the intended path and builds a
  real APK natively.
- **2026-07-20 — First scope = 4-tab shell, Chart live, rest stubbed.** Alerts/Stats have no
  backend yet (they need M3/M5); stubbing them keeps the app looking complete without faking data.
- **2026-07-20 — Physical phone is the test target.** Base URL is a BuildConfig field defaulting to
  the Mac LAN IP, overridable per build via `-PtgApiBase=`. Emulators would use `10.0.2.2`.
- **2026-07-20 — Trimmed the manifest for the first APK.** Room + Firebase (FCM) are deferred:
  they need KSP codegen and a `google-services.json`, neither required for the shell + Chart.
  DataStore covers Settings. Add them back at their milestones.
- **2026-07-20 — Reuse the backend contract unchanged.** Same `/health` + `/verdict` the web app
  uses. The only backend-side change was `start.sh` gaining `HOST=0.0.0.0` so a device can connect.

---

## Native traps (do not rediscover)

1. **`gradlew` itself needs a JVM before it reads `gradle.properties`.** This Mac has no standalone
   JDK. Export `JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"` in the
   shell that runs `./gradlew`, *and* `gradle.properties` sets `org.gradle.java.home` to the same
   for the build JVM. Miss the env export and you get "Unable to locate a Java Runtime."
2. **Physical phone reaches the Mac by LAN IP, not localhost.** `127.0.0.1` on the phone is the
   phone. Use `ipconfig getifaddr en0`. Emulator uses `10.0.2.2` instead.
3. **Backend must bind `0.0.0.0` for the device to connect.** Default `start.sh` is localhost-only;
   run `HOST=0.0.0.0 ./start.sh restart`. Expect a one-time macOS firewall prompt.
4. **Android blocks cleartext HTTP.** Allowed only for dev hosts via
   `res/xml/network_security_config.xml` — add your LAN IP there if it changes. Never ship this to
   production; the real API must be HTTPS.
5. **Base URL is `BuildConfig.API_BASE_URL`**, set from the `tgApiBase` Gradle property. Switching
   networks means a rebuild with a new `-PtgApiBase=`, not a code edit.
6. **Chart is hand-drawn on a Compose Canvas** (`ui/chart/CandleCanvas.kt`) — the manifest's
   explicit choice. Backend already sends epoch-stamped OHLC + real prices, so the renderer only
   min/max-scales; the positional-index trap never reaches the client.
7. **The local x86 emulator boots very slowly / went offline** during scaffolding. Prefer a
   physical device (the chosen target). If using an emulator, give it minutes on cold boot and
   point the APK at `10.0.2.2`.

---

## Open questions

- **Release signing / keystore** — the first APK is debug-signed. A release keystore + Play Store
  track is a later decision (proposal Phase 3 "Play Store beta").
- **Firebase project for FCM** — needed before push alerts; not yet created.
- **When to add the WebSocket tick stream** — depends on the backend adding `WS /stream`; until
  then the Chart shows candle-close price like the web app (see `../PLAN.md` known trap #13).

---

## Layout

```
mobile_app/
├── PLAN.md                    ← you are here
├── README.md                  ← quickstart + install steps
├── docs/BLUEPRINTS.md         ← per-screen breakdown, links figures M1–M4
├── settings.gradle.kts · build.gradle.kts · gradle.properties
├── gradle/wrapper/            ← wrapper (Gradle 8.9)
└── app/
    ├── build.gradle.kts       ← deps (Compose, Retrofit, kotlinx-serialization, DataStore)
    └── src/main/
        ├── AndroidManifest.xml · res/ (theme, network_security_config, launcher icon)
        └── java/com/tradeguard/app/
            ├── MainActivity.kt
            ├── ui/ theme · nav (bottom nav) · chart (live) · alerts · stats · settings · common
            └── data/ api (Retrofit) · model (DTOs) · repo (MarketRepository)
```
