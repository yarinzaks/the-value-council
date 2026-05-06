#!/bin/bash
# Start the dashboard + open an ngrok tunnel so the URL works from
# any device, anywhere. Use this when you want to view the dashboard
# from your phone, share with a friend, or check the portfolio on
# the go.
#
# Usage:
#   bash scripts/start_with_tunnel.sh
#
# What it does:
#   1. Verifies ngrok is installed and an authtoken is configured.
#      If not, prints exact next steps and exits 1.
#   2. Starts the dashboard locally on http://localhost:3000 if it
#      isn't already running.
#   3. Opens an ngrok HTTPS tunnel to that port.
#   4. Prints the public URL (https://*.ngrok-free.app or similar).
#   5. Tails ngrok's log; Ctrl-C kills both the tunnel and (if
#      this script started it) the dashboard.
#
# Free-tier limits to know about:
#   - Public URL changes every restart (random subdomain)
#   - 1 tunnel at a time
#   - Bandwidth limit (≈ 1GB/month) — fine for personal dashboard use
#   - For a stable subdomain, upgrade to ngrok paid OR use Cloudflare
#     Tunnel as an alternative

set -euo pipefail

PROJECT_ROOT="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/.." &> /dev/null && pwd )"
DASHBOARD_PORT=3000
NGROK_API="http://127.0.0.1:4040/api/tunnels"
LOG_DIR="$HOME/Library/Application Support/value-council/cron_logs"
mkdir -p "$LOG_DIR"
TUNNEL_LOG="$LOG_DIR/ngrok.log"

# ----- Preflight: ngrok installed? -------------------------------------
if ! command -v ngrok &> /dev/null; then
  cat <<EOF
✗ ngrok not found.
  Install: brew install ngrok
EOF
  exit 1
fi

# ----- Preflight: authtoken configured? --------------------------------
NGROK_CONFIG="$HOME/Library/Application Support/ngrok/ngrok.yml"
if [ ! -f "$NGROK_CONFIG" ] || ! ngrok config check &> /dev/null; then
  cat <<EOF
✗ ngrok is not authed yet (free tier requires a one-time signup).

  1. Sign up (free) at https://dashboard.ngrok.com/signup
  2. Copy your authtoken from https://dashboard.ngrok.com/get-started/your-authtoken
  3. Run:  ngrok config add-authtoken <your-token>
  4. Re-run this script.
EOF
  exit 1
fi

# ----- Step 1: dashboard already running? -----------------------------
DASHBOARD_PID=""
DASHBOARD_OURS=0
if lsof -ti :"$DASHBOARD_PORT" > /dev/null 2>&1; then
  DASHBOARD_PID="$(lsof -ti :"$DASHBOARD_PORT" | head -1)"
  echo "→ dashboard already running on :$DASHBOARD_PORT (pid $DASHBOARD_PID), reusing"
else
  echo "→ dashboard not running — starting it via scripts/start_dashboard.sh"
  bash "$PROJECT_ROOT/scripts/start_dashboard.sh"
  # start_dashboard.sh nohup'd it; grab the new pid
  DASHBOARD_PID="$(lsof -ti :"$DASHBOARD_PORT" | head -1 || true)"
  DASHBOARD_OURS=1
fi

# ----- Step 2: start ngrok tunnel -------------------------------------
echo "→ opening ngrok tunnel to localhost:$DASHBOARD_PORT (logs: $TUNNEL_LOG)"
# Run ngrok in the background with file logging. The log-format=json is
# easier to grep but we don't strictly need it; default text is fine.
ngrok http "$DASHBOARD_PORT" --log=stdout --log-level=info > "$TUNNEL_LOG" 2>&1 &
NGROK_PID=$!

# ----- Step 3: wait for the tunnel URL -------------------------------
PUBLIC_URL=""
echo -n "→ waiting for tunnel URL"
for _ in $(seq 1 30); do
  sleep 0.5
  # Query ngrok's local API for the public URL.
  if PUBLIC_URL="$(curl -fsS "$NGROK_API" 2>/dev/null \
      | python3 -c '
import json, sys
data = json.load(sys.stdin)
tunnels = data.get("tunnels", [])
for t in tunnels:
    if t.get("public_url", "").startswith("https://"):
        print(t["public_url"])
        sys.exit(0)
sys.exit(1)
')"; then
    if [ -n "$PUBLIC_URL" ]; then
      echo " ✓"
      break
    fi
  fi
  echo -n "."
done

if [ -z "$PUBLIC_URL" ]; then
  echo
  echo "✗ tunnel did not come up within 15 seconds. Last log lines:"
  tail -20 "$TUNNEL_LOG"
  kill "$NGROK_PID" 2>/dev/null || true
  exit 1
fi

# ----- Step 4: print + watch -----------------------------------------
cat <<EOF

============================================================
  Value Council dashboard is now public on the internet.
  Public URL:  $PUBLIC_URL
  Local URL:   http://localhost:$DASHBOARD_PORT
============================================================

  Open the public URL on your phone, tablet, or any
  browser anywhere — even outside home WiFi.

  Press Ctrl-C to stop the tunnel.
EOF

# ----- Cleanup on exit -----------------------------------------------
cleanup() {
  echo
  echo "→ stopping ngrok tunnel (pid $NGROK_PID)"
  kill "$NGROK_PID" 2>/dev/null || true
  if [ "$DASHBOARD_OURS" = "1" ] && [ -n "$DASHBOARD_PID" ]; then
    echo "→ stopping dashboard (pid $DASHBOARD_PID)"
    kill "$DASHBOARD_PID" 2>/dev/null || true
  fi
  echo "  bye 👋"
}
trap cleanup INT TERM EXIT

# Keep the script alive until ngrok dies or the user Ctrl-Cs.
wait "$NGROK_PID"
