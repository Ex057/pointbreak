"""SQLite access — stdlib only, WAL mode so the monitor thread and API requests
can read/write concurrently without an ORM.

Connections are short-lived and opened per call (`connect()`); SQLite in WAL
handles many readers + one writer cleanly, which is exactly our shape (the
monitor writes, the API reads).
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager

from ..config import DB_URL

DB_PATH = DB_URL.replace("sqlite:///", "")


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=10.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA foreign_keys=ON")
    return con


@contextmanager
def cursor():
    con = connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()


def now() -> int:
    return int(time.time())


# The full schema — single source of truth. IF NOT EXISTS so it augments the
# tables start.sh already created and adds the monitor's new ones.
SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
  pair TEXT, timeframe TEXT, epoch INTEGER,
  open REAL, high REAL, low REAL, close REAL,
  source TEXT DEFAULT 'legacy', ingested_at INTEGER, quality_status TEXT DEFAULT 'unchecked',
  PRIMARY KEY (pair, timeframe, epoch));

-- Historical research inputs retain provider identity. The live candles table
-- has one value per timestamp; research must be able to compare provider lanes
-- without overwriting Deriv candles with Yahoo's GC=F futures proxy.
CREATE TABLE IF NOT EXISTS research_candles (
  pair TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  source TEXT NOT NULL,
  epoch INTEGER NOT NULL,
  open REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  close REAL NOT NULL,
  ingested_at INTEGER NOT NULL,
  PRIMARY KEY (pair, timeframe, source, epoch));

CREATE TABLE IF NOT EXISTS research_data_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pair TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  status TEXT NOT NULL,
  source TEXT,
  years REAL NOT NULL,
  written INTEGER DEFAULT 0,
  message TEXT,
  result_json TEXT,
  error TEXT,
  created_at INTEGER NOT NULL,
  started_at INTEGER,
  completed_at INTEGER);

CREATE TABLE IF NOT EXISTS research_forecast_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pair TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  source TEXT NOT NULL,
  status TEXT NOT NULL,
  metrics_json TEXT,
  points_json TEXT,
  data_json TEXT,
  error TEXT,
  created_at INTEGER NOT NULL,
  completed_at INTEGER);

CREATE TABLE IF NOT EXISTS zones (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pair TEXT, timeframe TEXT, type TEXT,
  level_min REAL, level_max REAL, touches INTEGER,
  fresh INTEGER, score REAL, created_at INTEGER);

-- one row per armed alert; current state lives here, the timeline in alert_events
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pair TEXT, timeframe TEXT, side TEXT, source TEXT,
  zone_min REAL, zone_max REAL,
  entry REAL, stop REAL, target REAL,
  score REAL, confluence INTEGER,
  state TEXT, swept_price REAL,
  size REAL, rr REAL,
  trader_mode TEXT,
  bias_tf TEXT, setup_tf TEXT, entry_tf TEXT,
  feed_source TEXT, resolved_symbol TEXT,
  price_valid INTEGER, confirmation_state TEXT,
  last_price REAL, unrealized_r REAL,
  armed_at INTEGER, updated_at INTEGER);

CREATE TABLE IF NOT EXISTS alert_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_id INTEGER, state TEXT, ts INTEGER, price REAL, reason TEXT);

CREATE TABLE IF NOT EXISTS setup_journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pair TEXT,
  timeframe TEXT,
  status TEXT,
  side TEXT,
  source TEXT,
  reason TEXT,
  entry REAL,
  stop REAL,
  target REAL,
  rr REAL,
  trader_mode TEXT,
  bias_tf TEXT,
  setup_tf TEXT,
  entry_tf TEXT,
  feed_source TEXT,
  resolved_symbol TEXT,
  price_valid INTEGER,
  reference_price REAL,
  candle_price REAL,
  active_fingerprint TEXT,
  first_seen_at INTEGER,
  last_seen_at INTEGER,
  repeat_count INTEGER DEFAULT 1,
  quality_flags TEXT,
  plan_json TEXT,
  created_at INTEGER
);

CREATE TABLE IF NOT EXISTS outcomes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_id INTEGER, pair TEXT, timeframe TEXT, zone_type TEXT,
  confluence INTEGER, swept INTEGER, result_r REAL, closed_at INTEGER);

-- the paper-trading ledger, one row per monitored pair
CREATE TABLE IF NOT EXISTS accounts (
  pair TEXT PRIMARY KEY,
  starting_balance REAL, balance REAL,
  wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
  updated_at INTEGER);

-- equity curve points (one per closed outcome)
CREATE TABLE IF NOT EXISTS equity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pair TEXT, ts INTEGER, balance REAL, r REAL, alert_id INTEGER);

CREATE TABLE IF NOT EXISTS user_settings (
  user_id TEXT PRIMARY KEY, risk_pct REAL, min_rr REAL,
  max_positions INTEGER, news_window_min INTEGER, turtle_soup INTEGER,
  trader_mode TEXT, ai_provider TEXT);

CREATE TABLE IF NOT EXISTS news_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  currency TEXT, impact TEXT, release_ts INTEGER, title TEXT);

CREATE TABLE IF NOT EXISTS learning_models (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pair TEXT NOT NULL,
  model_kind TEXT NOT NULL,
  version TEXT NOT NULL,
  status TEXT NOT NULL,
  trained_at INTEGER NOT NULL,
  window_start INTEGER,
  window_end INTEGER,
  sample_count INTEGER DEFAULT 0,
  holdout_size INTEGER DEFAULT 0,
  artifact_json TEXT,
  metrics_json TEXT,
  summary_json TEXT,
  promotion_reason TEXT
);

CREATE TABLE IF NOT EXISTS connectors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  oauth_client_id TEXT,
  oauth_client_secret TEXT,
  enabled INTEGER DEFAULT 1,
  created_at INTEGER,
  updated_at INTEGER);

-- one row per end-of-day "what-if" counterfactual review of a pair's rejections
CREATE TABLE IF NOT EXISTS eod_reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pair TEXT NOT NULL,
  day TEXT NOT NULL,              -- YYYY-MM-DD (session date, UTC)
  day_start INTEGER,             -- epoch seconds of the reviewed window
  day_end INTEGER,
  realized_r REAL,               -- R actually booked on taken trades that day
  missed_r REAL,                 -- R left on the table by rejected-but-would-have-won setups
  review_json TEXT,              -- full EodReview payload (per-gate stats, missed winners, flaws)
  flags_json TEXT,               -- flagged gates (systematically-profitable rejections)
  provider TEXT,                 -- who produced the analyst commentary (claude/builtin/none)
  created_at INTEGER);

-- advisory strategy-parameter recommendations produced by the analyst, shadow-tested
CREATE TABLE IF NOT EXISTS strategy_recommendations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pair TEXT NOT NULL,
  eod_review_id INTEGER,
  param TEXT NOT NULL,           -- e.g. min_rr, arm_score_min, impulse_body_mult
  current_value REAL,
  proposed_value REAL,
  rationale TEXT,
  expected_added_trades INTEGER,
  expected_r_impact REAL,        -- analyst's own estimate
  shadow_delta_r REAL,           -- our shadow-test net-R delta over the counterfactual set
  shadow_added_trades INTEGER,
  confidence TEXT,
  status TEXT DEFAULT 'proposed', -- proposed | applied | dismissed
  created_at INTEGER,
  updated_at INTEGER);

-- approved parameter overrides the deterministic planner reads (only set on approval)
CREATE TABLE IF NOT EXISTS param_overrides (
  scope TEXT NOT NULL,           -- pair id, or 'global'
  param TEXT NOT NULL,
  value REAL,
  source_recommendation_id INTEGER,
  updated_at INTEGER,
  PRIMARY KEY (scope, param));

-- Offline-only model experiments. No runtime verdict or monitor code reads
-- these tables; they are an audit trail for the Research Lab.
CREATE TABLE IF NOT EXISTS research_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pair TEXT NOT NULL,
  modes_json TEXT NOT NULL,
  status TEXT NOT NULL,
  config_json TEXT,
  data_fingerprint TEXT,
  summary_json TEXT,
  error TEXT,
  created_at INTEGER NOT NULL,
  started_at INTEGER,
  completed_at INTEGER
);

CREATE TABLE IF NOT EXISTS research_mode_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  trader_mode TEXT NOT NULL,
  status TEXT NOT NULL,
  sample_count INTEGER DEFAULT 0,
  independent_days INTEGER DEFAULT 0,
  positive_count INTEGER DEFAULT 0,
  negative_count INTEGER DEFAULT 0,
  censored_count INTEGER DEFAULT 0,
  metrics_json TEXT,
  folds_json TEXT,
  feature_importance_json TEXT,
  blockers_json TEXT,
  artifact_path TEXT,
  FOREIGN KEY(run_id) REFERENCES research_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS research_predictions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  trader_mode TEXT NOT NULL,
  epoch INTEGER NOT NULL,
  day TEXT NOT NULL,
  fold INTEGER NOT NULL,
  label INTEGER NOT NULL,
  outcome TEXT NOT NULL,
  rr REAL,
  risk_distance REAL,
  predictions_json TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES research_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_alert_events_alert ON alert_events(alert_id);
CREATE INDEX IF NOT EXISTS idx_alerts_pair_state ON alerts(pair, state);
CREATE INDEX IF NOT EXISTS idx_outcomes_pair ON outcomes(pair);
-- One outcome per alert, enforced by the DB: the guard in repo.record_outcome is
-- a SELECT-then-INSERT and two monitor threads once raced through it.
CREATE UNIQUE INDEX IF NOT EXISTS idx_outcomes_alert ON outcomes(alert_id);
CREATE INDEX IF NOT EXISTS idx_setup_journal_pair_time ON setup_journal(pair, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_learning_models_pair_time ON learning_models(pair, trained_at DESC);
CREATE INDEX IF NOT EXISTS idx_connectors_updated_at ON connectors(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_eod_reviews_pair_time ON eod_reviews(pair, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_recommendations_pair_status ON strategy_recommendations(pair, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_candles_pair_tf_epoch ON candles(pair, timeframe, epoch);
CREATE INDEX IF NOT EXISTS idx_research_candles_lookup ON research_candles(pair, timeframe, source, epoch);
CREATE INDEX IF NOT EXISTS idx_research_data_jobs_pair_time ON research_data_jobs(pair, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_forecast_runs_pair_time ON research_forecast_runs(pair, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_runs_pair_time ON research_runs(pair, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_results_run ON research_mode_results(run_id, trader_mode);
CREATE INDEX IF NOT EXISTS idx_research_predictions_run ON research_predictions(run_id, trader_mode, epoch);
"""


# Columns added after the first release — ALTER them onto an existing alerts
# table (CREATE IF NOT EXISTS won't add columns to a table that already exists).
_MIGRATIONS = {
    "alerts": {
        "last_price": "REAL",
        "unrealized_r": "REAL",
        "size": "REAL",
        "rr": "REAL",
        "trader_mode": "TEXT",
        "bias_tf": "TEXT",
        "setup_tf": "TEXT",
        "entry_tf": "TEXT",
        "feed_source": "TEXT",
        "resolved_symbol": "TEXT",
        "price_valid": "INTEGER",
        "confirmation_state": "TEXT",
        # risk context captured at arm time — see alert_engine._wick_eps
        "atr": "REAL",
        "min_risk": "REAL",
        "planned_rr": "REAL",
        "fill_price": "REAL",
        "fill_epoch": "INTEGER",
    },
    "setup_journal": {
        "trader_mode": "TEXT",
        "active_fingerprint": "TEXT",
        "first_seen_at": "INTEGER",
        "last_seen_at": "INTEGER",
        "repeat_count": "INTEGER DEFAULT 1",
    },
    "user_settings": {
        "trader_mode": "TEXT",
        "ai_provider": "TEXT",
        "auto_apply": "INTEGER DEFAULT 0",
    },
    "equity": {
        # per-trade cash booked on this equity point, stored at apply time so the
        # ledger reads it directly instead of (mis-)diffing consecutive balances.
        "cash": "REAL",
    },
    "candles": {
        "source": "TEXT DEFAULT 'legacy'",
        "ingested_at": "INTEGER",
        "quality_status": "TEXT DEFAULT 'unchecked'",
    },
    "research_mode_results": {
        "artifact_path": "TEXT",
    },
}


def _apply_migrations(con) -> None:
    for table, cols in _MIGRATIONS.items():
        existing = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols.items():
            if name not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init_schema() -> list[str]:
    with cursor() as con:
        con.executescript(SCHEMA)
        _apply_migrations(con)
        # Do not copy from the legacy single-provider table: older versions
        # mislabeled some Yahoo fallback windows as Deriv. New research history
        # is fetched with explicit provider identity instead.
        # A process that died mid-benchmark cannot still be running after a
        # restart. Keep the audit row and make the interruption explicit.
        con.execute(
            """UPDATE research_runs
               SET status='failed', error=COALESCE(error, 'Research worker interrupted by application restart'),
                   completed_at=COALESCE(completed_at, ?)
               WHERE status IN ('queued', 'running')""",
            (now(),),
        )
        con.execute(
            """UPDATE research_data_jobs SET status='failed', error=COALESCE(error,'Data worker interrupted by application restart'),
                   message='Historical data fetch interrupted by application restart', completed_at=COALESCE(completed_at,?)
               WHERE status IN ('queued','running')""",
            (now(),),
        )
        con.execute(
            """UPDATE research_forecast_runs SET status='failed', error=COALESCE(error,'Forecast worker interrupted by application restart'),
                   completed_at=COALESCE(completed_at,?)
               WHERE status IN ('queued','running')""",
            (now(),),
        )
        con.execute(
            """INSERT OR IGNORE INTO user_settings
               (user_id, risk_pct, min_rr, max_positions, news_window_min, turtle_soup, trader_mode, ai_provider)
               VALUES ('local', 1.0, 2.0, 3, 30, 1, 'Swing Trader', 'builtin')"""
        )
        return [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
