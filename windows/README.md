# TradeGuard — Windows Setup (double-click)

TradeGuard scans a market chart and gives one honest read — **WAIT / ENTER / STAND ASIDE** — with the
reasoning drawn on the chart (support/resistance, supply & demand, fair-value gaps, moving averages,
candlestick patterns, and multi-bar chart patterns like flags, triangles and head-and-shoulders). You
can paper-trade any setup on a simulated ledger — **no real money, no real orders, ever.**

This is the **Windows** package: two batch files do everything. No WSL or command-line knowledge needed.

---

## 1. Install the two prerequisites (once)

1. **Python 3.12** (recommended) — https://www.python.org/downloads/release/python-3127/
   - On the **first installer screen, tick "Add python.exe to PATH"**, then install. *(This step is
     essential — without it the setup can't find Python.)*
   - *Why 3.12:* the analysis libraries (numpy/scipy/pandas) ship ready-made installers for it, so
     setup is fast and needs no compiler. Very new Python versions can force a slow build that fails.
2. **Node.js 20+ (LTS)** — https://nodejs.org (the green "LTS" button). Install with the defaults.

*(You only do this once per computer. If they're already installed, skip to step 3.)*

---

## 2. Unzip

Right-click `TradeGuard-windows.zip` → **Extract All…** → pick a folder (e.g. your Desktop).
Open the extracted `TradeGuard` folder — you should see `setup.bat`, `start.bat`, `backend`, `webapp`.

---

## 3. Run — double-click `START-TRADEGUARD.bat`

That one file does everything:

- **First run:** it installs the environment and packages (a few minutes — details go to
  `setup-log.txt`), then launches the app.
- **Later runs:** it just starts the app.
- Two small windows open (the **backend** and the **frontend**) — leave them running.
- Your browser opens **http://localhost:5173** automatically after ~12 seconds.
  (If it doesn't, just visit that address yourself.)

**To stop:** close those two windows, or double-click **`STOP-TRADEGUARD.bat`**.

> **If Windows SmartScreen warns** ("Windows protected your PC"): click **More info → Run anyway**.
> The scripts are plain text — you can open any `.bat` in Notepad to read them first.
> **Do not** use PowerShell, and **do not** run `start.sh` — that's the Mac/Linux script.

---

## 5. Using the app (quick tour)

- **Top bar:** pick a market and timeframe. The price shows with a source badge (`live · yahoo` or
  `candle`). **Re-scan** re-runs the analysis.
- **Layers:** toggle S/R lines, supply & demand, fair-value gaps, and the moving averages.
- **Patterns tab** (right side): two checklists — **Chart patterns** (flags, triangles, wedges, double
  top/bottom, head & shoulders, channels, rounding, cup & handle) and **Candlestick patterns**.
  **Hover any pattern name** for a plain-language explanation. A pattern is only *confirmed* after price
  closes beyond its boundary; tick "show forming" to preview ones still building.
- **Verdict card:** the WAIT/ENTER call, the reasons, and a pre-computed plan. **Paper-trade this plan**
  opens a simulated position tracked to target/stop — no real order is ever placed.

---

## 6. Prices / data source

By default the app uses **`DATA_SOURCE=yahoo`** — **real live prices** for XAU/USD, EUR/USD, GBP/USD,
USD/JPY, US30 and BTC/USD, free and with no account. To change it, open the **`.env`** file (in the
TradeGuard folder) in Notepad and set `DATA_SOURCE` to:

| Value | What you get |
|---|---|
| `yahoo` (default) | Real live prices for gold / FX / US30 / BTC. Synthetic indices unavailable. |
| `fixture` | Offline synthetic candles (~100-level fake prices). Works with no internet. |
| `deriv` | Deriv live feed for the synthetic indices (Volatility/Crash). Usually needs a `DERIV_API_TOKEN`. |

After changing `.env`, run `stop.bat` then `start.bat` again.

---

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| "Python not found" | Reinstall Python 3.12 and **tick "Add python.exe to PATH"**, then double-click `START-TRADEGUARD.bat` again. |
| "Node.js / npm not found" | Install Node.js LTS from nodejs.org, then run `START-TRADEGUARD.bat` again. |
| Backend install failed | Open **`setup-log.txt`** and send it to us. Most often it's a too-new Python — installing **Python 3.12** fixes it. |
| SmartScreen blocks the `.bat` | **More info → Run anyway** (or open the file in Notepad to inspect it first). |
| Browser shows nothing at first | Give it ~15–20s; the servers are warming up. Refresh the page. |
| "Port already in use" | Run `STOP-TRADEGUARD.bat`, then `START-TRADEGUARD.bat` again. |
| A **synthetic index** (Volatility/Crash) shows "feed not ready" | Those need Deriv. Use `DATA_SOURCE=yahoo` for real markets, or add a `DERIV_API_TOKEN` in `.env`. |
| Prices look like ~100 and unreal | You're on `DATA_SOURCE=fixture`. Set `.env` to `DATA_SOURCE=yahoo`, then `stop.bat` + `start.bat`. |

---

## Notes for testers

- This is a **prototype for evaluation** — **not financial advice**. It never holds funds or places
  real trades. Everything is paper/simulated.
- All data stays local on your PC. Deleting the folder removes everything.
- Bug or feedback? Note the **pair, timeframe, and data source**, plus what you expected vs. saw.
