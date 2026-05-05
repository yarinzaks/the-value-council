#!/bin/bash
# Pull GitHub Actions–generated data into the local Mac data directory.
#
# Why this script exists
# ----------------------
# When the Mac is asleep or off, GitHub Actions still runs the
# paper-trading scanner Mon-Fri at market open + close. Each run
# commits updated portfolio JSON / decision logs / snapshots back
# to `main` as council-bot. This script pulls those commits down
# to the live data directory the dashboard reads from.
#
# Architecture
# ------------
# We don't pull into the developer's working repo at
# ~/Documents/The-Value-Council — that would mix automation
# commits with feature branches. Instead we maintain a *separate*
# bare-ish clone at SYNC_DIR, fetch + reset it to origin/main, and
# rsync only the data/{portfolios,decisions,snapshots,cron_logs}
# subdirs into the live data root.
#
# When the Mac is off, the next sync run picks up every commit
# since the last successful pull — no special catch-up logic.
#
# Logs
# ----
# Each run appends to $LIVE_DATA/cron_logs/sync.<mode>.log so the
# dashboard's debug pane (and the user) can see what happened.

set -euo pipefail

REPO_URL="https://github.com/yarinzaks/the-value-council.git"
SYNC_DIR="$HOME/Library/Application Support/value-council-sync"
LIVE_DATA="$HOME/Library/Application Support/value-council"
MODE="${1:-manual}"   # "open", "close", or "manual"

LOG_DIR="$LIVE_DATA/cron_logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/sync.${MODE}.log"

log() {
  printf '%s [sync %s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$MODE" "$*" \
    | tee -a "$LOG_FILE"
}

log "starting sync"

# Step 1: ensure the sync clone exists.
FIRST_CLONE=0
if [ ! -d "$SYNC_DIR/.git" ]; then
  log "first run — cloning $REPO_URL into $SYNC_DIR"
  rm -rf "$SYNC_DIR"
  if ! git clone --depth 50 "$REPO_URL" "$SYNC_DIR" 2>&1 | tee -a "$LOG_FILE"; then
    log "ERROR: clone failed"
    exit 1
  fi
  FIRST_CLONE=1
fi

# Step 2: capture OLD sha BEFORE fetch. On a freshly-cloned dir,
# OLD_SHA == origin's tip already, so we mark FIRST_CLONE separately.
cd "$SYNC_DIR"
OLD_SHA="$(git rev-parse HEAD 2>/dev/null || echo none)"

# Step 3: fetch + hard-reset to origin/main. We use reset rather than
# pull because GHA's commits are linear automation we never want to
# diverge from locally — there are no human edits in the sync clone.
if ! git fetch --depth 50 origin main 2>&1 | tee -a "$LOG_FILE"; then
  log "ERROR: fetch failed (Mac may be offline — try again later)"
  exit 1
fi
git reset --hard origin/main 2>&1 | tee -a "$LOG_FILE"
NEW_SHA="$(git rev-parse HEAD)"

# On a non-first run, no-op early if there's nothing new.
if [ "$FIRST_CLONE" -eq 0 ] && [ "$OLD_SHA" = "$NEW_SHA" ]; then
  log "no new commits since last sync ($NEW_SHA)"
  exit 0
fi

# Step 4: SAFETY CHECK — never overwrite live data with a clearly
# broken upstream. If GHA ran without a populated fundamentals cache
# (it doesn't have one yet by default), every portfolio comes back at
# the $10,000 seed amount with zero positions. Syncing that down
# would wipe out real local trading state. Detect and refuse.
ALL_SEED=1
for f in "$SYNC_DIR"/data/portfolios/*.json; do
  [ -f "$f" ] || continue
  # Use Python so we don't depend on jq being installed.
  if ! /usr/bin/env python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
seed = d.get('cash', 0) == d.get('initial_cash', -1) and len(d.get('positions', [])) == 0
sys.exit(0 if seed else 1)
" "$f" 2>/dev/null; then
    ALL_SEED=0
    break
  fi
done
if [ "$ALL_SEED" -eq 1 ]; then
  log "ABORT: upstream portfolios are all at \$10K seed state (GHA likely missing fundamentals cache). Skipping rsync to protect local data."
  exit 2
fi

# Step 3: rsync only the state subtrees into the live data root.
# rsync is the right tool because:
#   - it's idempotent (safe to re-run)
#   - --delete on portfolios/ and snapshots/ removes stale entries
#     that no longer exist upstream (e.g. an agent renamed)
#   - we DO NOT touch data/cache or data/fundamentals_cache (those
#     are local-only, regenerable, and 2GB+)
for subdir in portfolios decisions snapshots cron_logs; do
  src="$SYNC_DIR/data/$subdir"
  dst="$LIVE_DATA/$subdir"
  if [ ! -d "$src" ]; then
    log "  skip $subdir (not present upstream)"
    continue
  fi
  mkdir -p "$dst"
  rsync -a --delete "$src/" "$dst/" 2>&1 | tee -a "$LOG_FILE"
  log "  synced $subdir"
done

log "done — $OLD_SHA → $NEW_SHA"
exit 0
