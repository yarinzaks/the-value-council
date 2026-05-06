#!/usr/bin/env bash
# Start the dashboard + open an ngrok tunnel so the URL works from
# any device, anywhere — phone, tablet, friend's browser, even
# outside home WiFi.
#
# Usage:
#   bash scripts/start_with_tunnel.sh
#
# What it does:
#   1. Verifies ngrok is installed and an authtoken is configured.
#      Prints exact next steps and exits 1 if either is missing.
#   2. Starts the dashboard locally on http://localhost:3000 if it
#      isn't already running (via scripts/start_dashboard.sh).
#   3. Opens an ngrok HTTPS tunnel to that port.
#   4. Prints the public URL (e.g. https://abc123.ngrok-free.app).
#   5. Tails ngrok's log; Ctrl-C kills the tunnel, plus the dashboard
#      if and only if this script started it.
#
# Free-tier limits to know about:
#   * Public URL changes every restart (random subdomain).
#   * 1 tunnel at a time on free tier.
#   * Bandwidth ~1GB/month — fine for a personal dashboard.
#   * For a stable URL, use ngrok paid OR Cloudflare Tunnel (free,
#     supports stable subdomains).

set -eu

# ----- Config ---------------------------------------------------------
PROJECT_ROOT="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/.." > /dev/null 2>&1 && pwd )"
DASHBOARD_PORT=3000
NGROK_API="http://127.0.0.1:4040/api/tunnels"
LOG_DIR="$HOME/Library/Application Support/value-council/cron_logs"
mkdir -p "$LOG_DIR"
TUNNEL_LOG="$LOG_DIR/ngrok.log"

# ----- Preflight 1: ngrok installed? ----------------------------------
if ! command -v ngrok > /dev/null 2>&1; then
  printf '%s\n' "ngrok is not installed."
  printf '%s\n' "  Install: brew install ngrok"
  exit 1
fi

# ----- Preflight 2: authtoken configured? -----------------------------
NGROK_CONFIG="$HOME/Library/Application Support/ngrok/ngrok.yml"
if [ ! -f "$NGROK_CONFIG" ] || ! ngrok config check > /dev/null 2>&1; then
  printf '%s\n' "ngrok is not authed yet (free tier requires a one-time signup)."
  printf '%s\n' ""
  printf '%s\n' "  1. Sign up (free) at https://dashboard.ngrok.com/signup"
  printf '%s\n' "  2. Copy your authtoken at https://dashboard.ngrok.com/get-started/your-authtoken"
  printf '%s\n' "  3. Run:  ngrok config add-authtoken <your-token>"
  printf '%s\n' "  4. Re-run this script."
  exit 1
fi

# ----- Step 1: dashboard already running? -----------------------------
DASHBOARD_PID=""
DASHBOARD_OURS=0
EXISTING_PID="$(lsof -ti :"$DASHBOARD_PORT" 2>/dev/null | head -1 || true)"
if [ -n "$EXISTING_PID" ]; then
  DASHBOARD_PID="$EXISTING_PID"
  printf 'dashboard already running on :%s (pid %s), reusing\n' "$DASHBOARD_PORT" "$DASHBOARD_PID"
else
  printf 'dashboard not running — starting via scripts/start_dashboard.sh\n'
  bash "$PROJECT_ROOT/scripts/start_dashboard.sh"
  DASHBOARD_PID="$(lsof -ti :"$DASHBOARD_PORT" 2>/dev/null | head -1 || true)"
  DASHBOARD_OURS=1
fi

# ----- Step 2: start ngrok tunnel -------------------------------------
printf 'opening ngrok tunnel to localhost:%s (logs: %s)\n' "$DASHBOARD_PORT" "$TUNNEL_LOG"
ngrok http "$DASHBOARD_PORT" --log=stdout --log-level=info > "$TUNNEL_LOG" 2>&1 &
NGROK_PID=$!

# ----- Step 3: poll for the tunnel URL --------------------------------
# Use plain grep on the JSON instead of a Python parser. The ngrok
# local-API response always quotes public_url like:
#     "public_url":"https://abc123.ngrok-free.app"
# so a simple regex extraction works robustly.
PUBLIC_URL=""
printf 'waiting for tunnel URL'
i=0
while [ "$i" -lt 30 ]; do
  sleep 0.5
  RAW="$(curl -fsS "$NGROK_API" 2>/dev/null || true)"
  if [ -n "$RAW" ]; then
    CANDIDATE="$(printf '%s' "$RAW" | grep -oE 'https://[A-Za-z0-9.-]+\.ngrok[A-Za-z.-]*\.app' | head -1 || true)"
    if [ -n "$CANDIDATE" ]; then
      PUBLIC_URL="$CANDIDATE"
      printf ' done\n'
      break
    fi
  fi
  printf '.'
  i=$((i + 1))
done

if [ -z "$PUBLIC_URL" ]; then
  printf '\ntunnel did not come up within 15 seconds. Last log lines:\n'
  tail -20 "$TUNNEL_LOG"
  kill "$NGROK_PID" 2>/dev/null || true
  exit 1
fi

# ----- Step 4: print + watch -----------------------------------------
printf '\n'
printf '============================================================\n'
printf '  Value Council dashboard is now public on the internet.\n'
printf '  Public URL:  %s\n' "$PUBLIC_URL"
printf '  Local URL:   http://localhost:%s\n' "$DASHBOARD_PORT"
printf '============================================================\n'
printf '\n'
printf '  Open the public URL on your phone, tablet, or any\n'
printf '  browser anywhere — even outside home WiFi.\n'
printf '\n'
printf '  Press Ctrl-C to stop the tunnel.\n'
printf '\n'

# ----- Cleanup on exit -----------------------------------------------
cleanup() {
  printf '\nstopping ngrok tunnel (pid %s)\n' "$NGROK_PID"
  kill "$NGROK_PID" 2>/dev/null || true
  if [ "$DASHBOARD_OURS" = "1" ] && [ -n "$DASHBOARD_PID" ]; then
    printf 'stopping dashboard (pid %s)\n' "$DASHBOARD_PID"
    kill "$DASHBOARD_PID" 2>/dev/null || true
  fi
  printf 'bye\n'
}
trap cleanup INT TERM EXIT

# Keep the script alive until ngrok dies or the user Ctrl-Cs.
wait "$NGROK_PID"
