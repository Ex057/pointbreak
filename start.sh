#!/usr/bin/env bash
# TradeGuard control script — one entrypoint for the whole stack.
#   ./start.sh setup            install backend venv + frontend deps
#   ./start.sh start            run backend + frontend (detached)
#   ./start.sh stop             stop both
#   ./start.sh restart          stop then start
#   ./start.sh status           what's running, on which ports
#   ./start.sh logs [be|fe]     tail logs (both by default)
#   ./start.sh test             run backend pytest suite
#   ./start.sh build-frontend   production build into webapp/dist
#   ./start.sh clear-cache      drop venv/node_modules/build/pycache artifacts
#   ./start.sh setup-db         create the SQLite schema
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
WEBAPP="$ROOT/webapp"
RUN="$ROOT/.run"
VENV="$BACKEND/.venv"
PY="$VENV/bin/python"
PIP="$VENV/bin/pip"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
# Honour .env's DATA_SOURCE (what testers set) unless an inline env var overrides
# it, e.g. `DATA_SOURCE=fixture ./start.sh start`. Falls back to Deriv's free
# public WebSocket when neither is set; no account or API token is required.
if [ -z "${DATA_SOURCE:-}" ] && [ -f "$ROOT/.env" ]; then
  DATA_SOURCE="$(grep -E '^[[:space:]]*DATA_SOURCE=' "$ROOT/.env" | tail -1 | cut -d= -f2- | tr -d '[:space:]\r')"
fi
DATA_SOURCE="${DATA_SOURCE:-deriv}"
DERIV_APP_ID="${DERIV_APP_ID:-1089}"
BACKEND_RELOAD="${BACKEND_RELOAD:-0}"
# Bind host. Default localhost-only. Set HOST=0.0.0.0 to let a phone / emulator
# on the LAN reach the backend (expect a one-time macOS firewall prompt).
#   HOST=0.0.0.0 ./start.sh restart
BACKEND_HOST="${HOST:-127.0.0.1}"

mkdir -p "$RUN"

c()  { printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
ok()   { c '0;32' "✓ $1"; }
warn() { c '0;33' "! $1"; }
err()  { c '0;31' "✗ $1"; }
info() { c '0;36' "→ $1"; }

port_pid() { lsof -ti :"$1" 2>/dev/null | head -1; }

is_up() { [ -n "$(port_pid "$1")" ]; }

wait_for() { # wait_for <url> <label> <tries>
  local url="$1" label="$2" tries="${3:-40}"
  for _ in $(seq "$tries"); do
    curl -fsS "$url" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  return 1
}

# ---------------------------------------------------------------- setup
cmd_setup() {
  command -v python3 >/dev/null || { err "python3 not found"; exit 1; }
  command -v npm     >/dev/null || { err "npm not found (install Node 20+)"; exit 1; }

  info "backend venv"
  [ -d "$VENV" ] || python3 -m venv "$VENV"
  "$PIP" install -q --upgrade pip
  "$PIP" install -q -r "$BACKEND/requirements.txt" || {
    err "backend install failed"; exit 1;
  }
  ok "backend deps installed"

  info "frontend deps (npm install)"
  (cd "$WEBAPP" && npm ci --no-fund --no-audit) || { err "npm install failed"; exit 1; }
  ok "frontend deps installed"

  cmd_setup_db
  ok "setup complete — run ./start.sh start"
}

# ---------------------------------------------------------------- database
cmd_setup_db() {
  [ -x "$PY" ] || { err "no venv — run ./start.sh setup first"; exit 1; }
  info "creating SQLite schema"
  (cd "$BACKEND" && "$PY" - <<'PYEOF'
import sqlite3
from app.config import DB_URL
path = DB_URL.replace("sqlite:///", "")
con = sqlite3.connect(path)
# The six tables from the proposal (§7.3). SQLite now; the move to
# Postgres/TimescaleDB later is a connection string + a hypertable migration.
con.executescript("""
CREATE TABLE IF NOT EXISTS candles (
  pair TEXT, timeframe TEXT, epoch INTEGER,
  open REAL, high REAL, low REAL, close REAL,
  PRIMARY KEY (pair, timeframe, epoch));
CREATE TABLE IF NOT EXISTS zones (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pair TEXT, timeframe TEXT, type TEXT,
  level_min REAL, level_max REAL, touches INTEGER,
  fresh INTEGER, score REAL, created_at INTEGER);
CREATE TABLE IF NOT EXISTS alert_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_id TEXT, state TEXT, ts INTEGER, price REAL, reason TEXT);
CREATE TABLE IF NOT EXISTS outcomes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_id TEXT, pair TEXT, timeframe TEXT, zone_type TEXT,
  confluence INTEGER, swept INTEGER, result_r REAL, closed_at INTEGER);
CREATE TABLE IF NOT EXISTS user_settings (
  user_id TEXT PRIMARY KEY, risk_pct REAL, min_rr REAL,
  max_positions INTEGER, news_window_min INTEGER, turtle_soup INTEGER);
CREATE TABLE IF NOT EXISTS news_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  currency TEXT, impact TEXT, release_ts INTEGER, title TEXT);
INSERT OR IGNORE INTO user_settings VALUES ('local', 1.0, 2.0, 3, 30, 1);
""")
con.commit()
n = [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print("tables:", ", ".join(n))
print("db:", path)
PYEOF
  ) && ok "database ready" || { err "db setup failed"; exit 1; }
}

# ---------------------------------------------------------------- start/stop
start_backend() {
  if is_up "$BACKEND_PORT"; then warn "backend already on :$BACKEND_PORT"; return 0; fi
  [ -x "$PY" ] || { err "no venv — run ./start.sh setup first"; exit 1; }
  info "starting backend on $BACKEND_HOST:$BACKEND_PORT (source=$DATA_SOURCE app_id=$DERIV_APP_ID)"
  local reload_flag=""
  if [ "$BACKEND_RELOAD" = "1" ]; then
    reload_flag="--reload"
  fi
  # Detach every fd: children must not inherit our stdout, or a piped
  # `./start.sh start | tail` never sees EOF and appears to hang.
  (cd "$BACKEND" && nohup env DATA_SOURCE="$DATA_SOURCE" DERIV_APP_ID="$DERIV_APP_ID" "$VENV/bin/uvicorn" app.main:app \
      --host "$BACKEND_HOST" --port "$BACKEND_PORT" ${reload_flag} \
      >"$RUN/backend.log" 2>&1 </dev/null & echo $! >"$RUN/backend.pid"; disown) </dev/null
  if wait_for "http://127.0.0.1:$BACKEND_PORT/health" backend 40; then
    ok "backend up   → http://127.0.0.1:$BACKEND_PORT/docs"
  else
    err "backend failed to start — last lines:"; tail -20 "$RUN/backend.log"; return 1
  fi
}

start_frontend() {
  if is_up "$FRONTEND_PORT"; then warn "frontend already on :$FRONTEND_PORT"; return 0; fi
  [ -d "$WEBAPP/node_modules" ] || { err "no node_modules — run ./start.sh setup first"; exit 1; }
  info "starting frontend on :$FRONTEND_PORT"
  (cd "$WEBAPP" && nohup npm run dev -- --port "$FRONTEND_PORT" --strictPort \
      >"$RUN/frontend.log" 2>&1 </dev/null & echo $! >"$RUN/frontend.pid"; disown) </dev/null
  if wait_for "http://127.0.0.1:$FRONTEND_PORT" frontend 60; then
    ok "frontend up  → http://localhost:$FRONTEND_PORT"
  else
    err "frontend failed to start — last lines:"; tail -20 "$RUN/frontend.log"; return 1
  fi
}

cmd_start() {
  start_backend || exit 1
  start_frontend || exit 1
  echo
  c '1;32' "TradeGuard is running"
  echo "  app      http://localhost:$FRONTEND_PORT"
  echo "  api      http://127.0.0.1:$BACKEND_PORT/docs"
  echo "  logs     ./start.sh logs"
  echo "  stop     ./start.sh stop"
}

kill_port() { # kill_port <port> <label>
  local pid; pid="$(port_pid "$1")"
  if [ -n "$pid" ]; then
    kill "$pid" 2>/dev/null
    sleep 1
    pid="$(port_pid "$1")"
    [ -n "$pid" ] && kill -9 "$pid" 2>/dev/null
    ok "stopped $2 (:$1)"
  else
    info "$2 not running"
  fi
}

cmd_stop() {
  # vite and uvicorn --reload both spawn children; killing by port is what
  # actually frees the port, so do that rather than trusting the pid file.
  kill_port "$FRONTEND_PORT" frontend
  kill_port "$BACKEND_PORT" backend
  pkill -f "uvicorn app.main:app" 2>/dev/null
  rm -f "$RUN"/*.pid
}

cmd_restart() { cmd_stop; sleep 1; cmd_start; }

cmd_status() {
  echo "TradeGuard status"
  for spec in "backend:$BACKEND_PORT:http://127.0.0.1:$BACKEND_PORT/health" \
              "frontend:$FRONTEND_PORT:http://127.0.0.1:$FRONTEND_PORT"; do
    IFS=: read -r name port _ <<<"$spec"
    url="${spec#*:*:}"
    if is_up "$port"; then
      curl -fsS "$url" >/dev/null 2>&1 \
        && ok "$name  :$port  responding" \
        || warn "$name  :$port  port held but not responding"
    else
      err "$name  :$port  down"
    fi
  done
  [ -d "$VENV" ] && ok "venv present" || warn "venv missing (./start.sh setup)"
  [ -d "$WEBAPP/node_modules" ] && ok "node_modules present" || warn "node_modules missing"
}

cmd_logs() {
  case "${1:-both}" in
    be|backend)  tail -f "$RUN/backend.log" ;;
    fe|frontend) tail -f "$RUN/frontend.log" ;;
    *)           tail -f "$RUN/backend.log" "$RUN/frontend.log" ;;
  esac
}

cmd_test() {
  [ -x "$PY" ] || { err "no venv — run ./start.sh setup first"; exit 1; }
  (cd "$BACKEND" && "$PY" -m pytest tests/ -q)
}

cmd_build_frontend() {
  [ -d "$WEBAPP/node_modules" ] || { err "no node_modules — run ./start.sh setup first"; exit 1; }
  info "building production bundle"
  (cd "$WEBAPP" && npm run build) && ok "built → webapp/dist"
}

cmd_clear_cache() {
  info "clearing caches and build artifacts"
  rm -rf "$WEBAPP/node_modules/.vite" "$WEBAPP/dist"
  find "$BACKEND" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null
  rm -rf "$BACKEND/.pytest_cache"
  rm -f "$RUN"/*.log
  ok "caches cleared (venv and node_modules kept)"
  if [ "${1:-}" = "--all" ]; then
    warn "removing venv and node_modules too"
    rm -rf "$VENV" "$WEBAPP/node_modules"
    ok "full clean — run ./start.sh setup to rebuild"
  else
    info "use ./start.sh clear-cache --all to also drop venv + node_modules"
  fi
}

usage() {
  sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
}

case "${1:-}" in
  setup)          cmd_setup ;;
  start)          cmd_start ;;
  stop)           cmd_stop ;;
  restart)        cmd_restart ;;
  status)         cmd_status ;;
  logs)           cmd_logs "${2:-both}" ;;
  test)           cmd_test ;;
  build-frontend) cmd_build_frontend ;;
  clear-cache)    cmd_clear_cache "${2:-}" ;;
  setup-db)       cmd_setup_db ;;
  ""|-h|--help|help) usage ;;
  *)              err "unknown command: $1"; echo; usage; exit 1 ;;
esac
