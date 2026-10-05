#!/usr/bin/env bash
# Build TradeGuard-windows.zip from this repo.
#
# Windows launcher files live here in windows/ for tidiness, but they must sit at
# the ZIP ROOT (next to backend/ and webapp/) so the tester's double-click works
# — the .bat files do `cd backend` / `cd webapp` relative to their own folder.
# This script assembles that layout and produces <repo>/TradeGuard-windows.zip.
#
# Usage:  bash windows/package-windows.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../windows
ROOT="$(cd "$HERE/.." && pwd)"                          # repo root
WORK="$(mktemp -d)"
STAGE="$WORK/TradeGuard"
mkdir -p "$STAGE"

# --- app source (lean; no venv/node_modules/db/build artifacts) ---
rsync -a \
  --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' --exclude '*.pyc' \
  --exclude 'dist' --exclude '*.sqlite*' --exclude 'db_backups' --exclude '.run' \
  --exclude '*.tsbuildinfo' --exclude 'build' --exclude '.DS_Store' --exclude 'setup-log.txt' \
  --exclude 'vite.config.js' --exclude 'vite.config.d.ts' \
  "$ROOT/backend" "$ROOT/webapp" "$ROOT/plan.py" "$ROOT/.env.example" "$STAGE"/

# --- Windows launcher files, placed at the ZIP ROOT ---
cp "$HERE/START-TRADEGUARD.bat" "$HERE/STOP-TRADEGUARD.bat" "$HERE/READ-ME-FIRST.txt" "$STAGE"/
cp "$HERE/README.md" "$STAGE/README.md"

# --- CRLF line endings for the files Windows opens/runs ---
for f in START-TRADEGUARD.bat STOP-TRADEGUARD.bat READ-ME-FIRST.txt; do
  perl -pe 's/\r?\n/\r\n/' "$STAGE/$f" > "$STAGE/$f.t" && mv "$STAGE/$f.t" "$STAGE/$f"
done

OUT="$ROOT/TradeGuard-windows.zip"
rm -f "$OUT"
( cd "$WORK" && zip -r -q "$OUT" TradeGuard )
rm -rf "$WORK"
echo "built $OUT ($(du -h "$OUT" | cut -f1))"
