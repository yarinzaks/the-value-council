#!/bin/bash
# The Value Council — dashboard launcher.
#
# Double-click this file in Finder (or run it from a terminal) to start
# the dashboard. Opens automatically in your browser at
# http://localhost:3000.
#
# What it does:
#   1. Frees port 3000 if a previous run is still bound to it.
#   2. Builds the Next.js production bundle if missing.
#   3. Starts the server in the background.
#   4. Opens the dashboard URL in your default browser.
#
# What it DOESN'T do:
#   - Auto-start on login (TCC-restricted; just rerun this script).
#   - Block the terminal — the server runs in the background. To stop
#     it, run:  pkill -f "next start -p 3000"

set -euo pipefail

# Resolve project paths from the location of this script — keeps the
# script portable if you move the repo.
SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$( dirname -- "$SCRIPT_DIR" )"
DASHBOARD_DIR="$PROJECT_ROOT/dashboard"

# Data lives outside ~/Documents so TCC doesn't block node from reading
# it. The Python daily runner writes here too via a symlink at
# <project>/data → ~/Library/Application Support/value-council.
DATA_DIR="$HOME/Library/Application Support/value-council"
export VALUE_COUNCIL_DATA_DIR="$DATA_DIR"

PORT=3000
LOG_DIR="$DATA_DIR/cron_logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/dashboard.out.log"

echo "→ Value Council dashboard"
echo "  project: $PROJECT_ROOT"
echo "  data:    $DATA_DIR"
echo "  port:    $PORT"

# Free port 3000 if anything is already there.
if lsof -ti :"$PORT" > /dev/null 2>&1; then
  echo "  port $PORT was in use — terminating previous server"
  lsof -ti :"$PORT" | xargs kill -9 2>/dev/null || true
  sleep 1
fi

cd "$DASHBOARD_DIR"

# Build the production bundle if it doesn't exist or if package.json
# has been edited more recently than the build.
if [ ! -d ".next" ] || [ "package.json" -nt ".next" ]; then
  echo "→ building production bundle (one-time)…"
  if [ ! -d "node_modules" ]; then
    npm install --no-audit --no-fund
  fi
  ./node_modules/.bin/next build
fi

# Start the server detached. nohup keeps it alive after this terminal
# closes; redirect stdout/stderr to a log file we can tail later.
echo "→ starting server (logs: $LOG_FILE)"
nohup ./node_modules/.bin/next start -p "$PORT" \
  > "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "  pid: $SERVER_PID"

# Wait up to 15 seconds for the server to start responding.
echo -n "→ waiting for server"
for _ in $(seq 1 30); do
  if curl -s -o /dev/null -w "%{http_code}" "http://localhost:$PORT/" | grep -q "200"; then
    echo " ✓"
    open "http://localhost:$PORT/"
    echo
    echo "Dashboard live at http://localhost:$PORT/"
    echo "To stop:  pkill -f \"next start -p $PORT\""
    exit 0
  fi
  echo -n "."
  sleep 0.5
done

echo
echo "⚠ server did not respond within 15s. Last log lines:"
tail -20 "$LOG_FILE"
exit 1
