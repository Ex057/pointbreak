"""Runtime configuration, env-driven."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BASE_DIR.parent

load_dotenv(ROOT_DIR / ".env")
load_dotenv(BASE_DIR / ".env")

# Deriv public market data needs no account, token, or app id. DERIV_APP_ID is
# retained for custom/legacy endpoint compatibility.
DERIV_APP_ID = int(os.getenv("DERIV_APP_ID", "1089"))
DERIV_URL = os.getenv("DERIV_URL", "")
# Optional compatibility setting for a custom legacy DERIV_URL. The default
# public Options endpoint is intentionally unauthenticated and ignores this.
DERIV_API_TOKEN = os.getenv("DERIV_API_TOKEN", "")

# Candle history tuning. 600 gives the chart and indicator engine more context
# while remaining a modest WebSocket request. The feed retains extra bars so
# users can zoom/scroll without reconnecting. Yahoo keeps its own provider
# limits because intraday retention varies by timeframe.
CANDLE_HISTORY_BARS = max(100, int(os.getenv("CANDLE_HISTORY_BARS", "600")))
CANDLE_CACHE_MAXLEN = max(CANDLE_HISTORY_BARS, int(os.getenv("CANDLE_CACHE_MAXLEN", "2000")))

# Lean slice: SQLite. The six-table schema matches the proposal, so the swap to
# Postgres/TimescaleDB later is a connection string plus a hypertable migration.
DB_URL = os.getenv("DB_URL", f"sqlite:///{BASE_DIR / 'tradeguard.sqlite'}")

# Position sizing. User-entered for now — see PLAN.md open questions.
ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "1000"))
RISK_PCT = float(os.getenv("RISK_PCT", "1.0"))
MIN_RR = float(os.getenv("MIN_RR", "2.0"))
XAUUSD_PAPER_LOT = float(os.getenv("XAUUSD_PAPER_LOT", "0.02"))
XAUUSD_SPREAD = float(os.getenv("XAUUSD_SPREAD", "0.30"))

# Optional explanation-only AI providers. The deterministic planner remains the
# source of truth; these providers only turn the current plan into readable
# trade commentary. `builtin` is always available and free.
AI_PROVIDER = os.getenv("AI_PROVIDER", "builtin").strip() or "builtin"
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
# Anthropic Claude — used both as an explanation provider and as the end-of-day
# strategy analyst that reads the what-if backtest and proposes bounded fixes.
# Runs locally against your own key; the deterministic planner stays authoritative
# and no recommendation is ever auto-applied to a live gate.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")

# Score threshold below which the monitor blocks arming a scanned setup
# (`blocked_score`). The what-if analyst may recommend lowering it; the planner
# reads any approved override before this default.
ARM_SCORE_MIN = float(os.getenv("ARM_SCORE_MIN", "0.5"))

# Minimum learned quality-score (0-100) used by the learned-quality gate.
# Deliberately NOT auto-tunable.
ARM_QUALITY_MIN = float(os.getenv("ARM_QUALITY_MIN", "35"))

# "advisory" (default) or "enforce".
#
# The learned-quality gate is DISARMED until the model behind it has been shown
# to work out of sample. The model that currently feeds it labels every vetoed
# setup as a loser, so it concluded nothing ever wins: its two dominant segments
# score 15/100 against the floor above, with confidence "high". Enforcing that
# blocks essentially every planned setup on the strength of a ratio table that
# has never made a verified prediction.
#
# Advisory mode keeps the whole computation and surfaces it as a warning, which
# is also the only way to gather the evidence that could re-arm it: setups armed
# *despite* a low score, and what they went on to do.
#
# Re-arming requires two independent backtest runs with verdict='skill', the
# second on data collected after the model's registration date. See
# backend/app/research/HYPOTHESES.md.
QUALITY_GATE_MODE = os.getenv("QUALITY_GATE_MODE", "advisory")

# What-if / reversal detection tuning.
# MIN_RISK_ATR_FRAC floors a setup's risk at this fraction of ATR so a tiny/flat
# origin candle can't inflate the reported R (the "122R phantom"). SWEEP_TARGET_
# ATR_MULT is the measured-move target (in ATR) used when a sweep-reclaim breaks
# into open space with no nearby structural obstacle — so genuine trend
# continuations clear min R:R while counter-trend bounces (near obstacle) do not.
# Raised 0.5 -> 1.0: a stop inside 1 ATR is hit by ordinary noise most of the
# time, so 0.5 was a coin-flip generator. This floor now moves the STOP itself,
# not just the R:R denominator (see whatif.detect_live_sweep_reclaim).
MIN_RISK_ATR_FRAC = float(os.getenv("MIN_RISK_ATR_FRAC", "1.0"))
SWEEP_TARGET_ATR_MULT = float(os.getenv("SWEEP_TARGET_ATR_MULT", "2.5"))

# Which pair the end-of-day what-if review + Claude strategy analyst run for.
EOD_REVIEW_PAIR = os.getenv("EOD_REVIEW_PAIR", "xauusd")
# Local hour (0-23) at/after which the always-on monitor fires the once-daily
# EOD review — "the chart is done for the day". Uses the UTC offset below.
EOD_REVIEW_HOUR = int(os.getenv("EOD_REVIEW_HOUR", "21"))
EOD_TZ_OFFSET_HOURS = int(os.getenv("EOD_TZ_OFFSET_HOURS", "3"))  # EAT default

# Timeframe label -> granularity in seconds. Deriv candles support up to 1D
# directly; 1W is derived locally from the 1D stream.
TIMEFRAMES = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1H": 3600,
    "4H": 14400,
    "1D": 86400,
    "1W": 604800,
}

DERIV_GRANULARITIES = sorted({g for tf, g in TIMEFRAMES.items() if tf != "1W"})

# The specialized trading catalog. `code` is the
# raw Deriv WebSocket symbol; `category` is free-form and drives pattern
# availability (see engine PATTERN_META) and news gating (services/news.py).
#
# The public endpoint is queried with `active_symbols` at startup, so unavailable
# codes are marked as such instead of failing silently. Volatility codes below
# were verified against the public endpoint on 2026-09-16.
#   - Crash 300 uses the newer "N" suffix; Crash 600's suffix is the least certain
#   - US30 = Deriv "Wall Street 30" index, symbol OTC_DJI (category "index")
# `fallback` is a free Yahoo Finance symbol used when Deriv is unreachable (e.g.
# geo-restricted). REAL markets have a Yahoo equivalent; SYNTHETIC indices are
# Deriv-proprietary and have NO fallback anywhere (fallback=None) — they only
# work through Deriv.
PREFERRED_MARKETS = [
    {"id": "xauusd", "display": "XAU/USD", "code": "frxXAUUSD", "category": "metals", "fallback": "GC=F", "sanity_min": 500.0},
    {"id": "r_10", "display": "Volatility 10", "code": "R_10", "category": "synthetic", "fallback": None},
    {"id": "r_25", "display": "Volatility 25", "code": "R_25", "category": "synthetic", "fallback": None},
    {"id": "r_50", "display": "Volatility 50", "code": "R_50", "category": "synthetic", "fallback": None},
    {"id": "r_75", "display": "Volatility 75", "code": "R_75", "category": "synthetic", "fallback": None},
    {"id": "r_100", "display": "Volatility 100", "code": "R_100", "category": "synthetic", "fallback": None},
    {"id": "r_10_1s", "display": "Volatility 10 (1s)", "code": "1HZ10V", "category": "synthetic", "fallback": None},
    {"id": "r_15_1s", "display": "Volatility 15 (1s)", "code": "1HZ15V", "category": "synthetic", "fallback": None},
    {"id": "r_25_1s", "display": "Volatility 25 (1s)", "code": "1HZ25V", "category": "synthetic", "fallback": None},
    {"id": "r_30_1s", "display": "Volatility 30 (1s)", "code": "1HZ30V", "category": "synthetic", "fallback": None},
    {"id": "r_50_1s", "display": "Volatility 50 (1s)", "code": "1HZ50V", "category": "synthetic", "fallback": None},
    {"id": "r_75_1s", "display": "Volatility 75 (1s)", "code": "1HZ75V", "category": "synthetic", "fallback": None},
    {"id": "r_90_1s", "display": "Volatility 90 (1s)", "code": "1HZ90V", "category": "synthetic", "fallback": None},
    {"id": "r_100_1s", "display": "Volatility 100 (1s)", "code": "1HZ100V", "category": "synthetic", "fallback": None},
    {"id": "crash_300", "display": "Crash 300", "code": "CRASH300N", "category": "synthetic", "fallback": None},
    {"id": "crash_600", "display": "Crash 600", "code": "CRASH600", "category": "synthetic", "fallback": None},
    {"id": "gbpusd", "display": "GBP/USD", "code": "frxGBPUSD", "category": "forex", "fallback": "GBPUSD=X"},
    {"id": "usdjpy", "display": "USD/JPY", "code": "frxUSDJPY", "category": "forex", "fallback": "USDJPY=X"},
    {"id": "eurusd", "display": "EUR/USD", "code": "frxEURUSD", "category": "forex", "fallback": "EURUSD=X"},
    {"id": "us30", "display": "US30", "code": "OTC_DJI", "category": "index", "fallback": "^DJI", "sanity_min": 1000.0},
    {"id": "btcusd", "display": "BTC/USD", "code": "cryBTCUSD", "category": "crypto", "fallback": "BTC-USD", "sanity_min": 1000.0},
]

DEFAULT_PAIR_ID = "xauusd"

# Data source for the current slice: "fixture" (deterministic synthetic candles,
# no network) or "deriv" (live WebSocket). M2 flips the default to deriv.
DATA_SOURCE = os.getenv("DATA_SOURCE", "fixture")
