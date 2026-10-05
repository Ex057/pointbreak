# TradeGuard — Android app

Native Kotlin + Jetpack Compose client for the TradeGuard backend. Four tabs
(Chart · Alerts · Stats · Settings). The Chart tab is wired to the live backend; the rest are
stubs or local until their backend milestones land. See `PLAN.md` for status and `docs/BLUEPRINTS.md`
for the screen designs.

## Prerequisites

- **Android Studio** (bundles the JDK the build needs) + Android SDK (platform 35, build-tools 35+).
- The **backend running** on your Mac, reachable from the phone — see below.

## Build

Gradle needs a JVM before it can read its own config, so export the JDK first:

```bash
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
cd mobile_app
./gradlew assembleDebug
# → app/build/outputs/apk/debug/app-debug.apk
```

Or just open `mobile_app/` in Android Studio and press Run.

## Point the app at your backend

The base URL is baked in at build time. Default is `http://192.168.1.5:8000/`. Override it with
your Mac's current LAN IP:

```bash
# find your Mac's LAN IP
ipconfig getifaddr en0                 # e.g. 192.168.1.9

# build with it baked in
./gradlew assembleDebug -PtgApiBase=http://192.168.1.9:8000/
```

- **Physical phone:** use the Mac's LAN IP (above). Phone + Mac on the same Wi-Fi.
- **Emulator:** use `-PtgApiBase=http://10.0.2.2:8000/` (the emulator's alias for the host).

If your LAN IP isn't already listed in `app/src/main/res/xml/network_security_config.xml`, add it —
Android blocks cleartext HTTP except for hosts named there (dev only).

## Run the backend so the phone can reach it

From the repo root, bind beyond localhost:

```bash
HOST=0.0.0.0 ./start.sh restart
```

Expect a one-time macOS firewall prompt (allow it). Verify from another device on the LAN:
`curl http://<Mac-LAN-IP>:8000/health`.

## Install on a phone

Enable USB debugging on the phone, connect it, then:

```bash
~/Library/Android/sdk/platform-tools/adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Open the app → **Chart** tab. You should see live candles, the market-structure badge, and the
WAIT/ENTER verdict — matching the web app for the same pair/timeframe.

## Not financial advice

TradeGuard is an analysis and education tool. It never holds funds or trades on your behalf.
