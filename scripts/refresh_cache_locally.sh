#!/bin/bash
# Refresh the EDGAR cache from your Mac and publish the result as a
# GitHub release asset. Use this when:
#   - the weekly GHA cache-refresh workflow gets 403'd by SEC
#   - you want to force-update mid-week (e.g. fresh earnings drop)
#   - you're debugging the prefetch pipeline
#
# Prereqs:
#   - gh CLI authenticated (gh auth status)
#   - .venv with project deps installed
#   - SEC_USER_AGENT in your shell env (or .env)
#
# Usage:
#   bash scripts/refresh_cache_locally.sh
#   bash scripts/refresh_cache_locally.sh --force    # full re-fetch
#
# What it does:
#   1. Runs scripts.prefetch_full_us_market against the local cache at
#      ~/Library/Application Support/value-council/fundamentals_cache/
#   2. Refreshes the bundled company_tickers.json
#   3. Tarballs the cache and publishes as cache-latest release asset
#   4. Commits + pushes the company_tickers.json refresh

set -euo pipefail

PROJECT_ROOT="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/.." &> /dev/null && pwd )"
DATA_DIR="$HOME/Library/Application Support/value-council"
CACHE_DIR="$DATA_DIR/fundamentals_cache"
TMP_TARBALL="$(mktemp -t fundamentals_cache.XXXXXX).tar.gz"
USER_AGENT_DEFAULT='value-council research@yarinzaks.dev'
SEC_USER_AGENT="${SEC_USER_AGENT:-$USER_AGENT_DEFAULT}"

cd "$PROJECT_ROOT"

# 1. Refresh tickers bundle (small file; harmless if it fails).
echo "→ refreshing data_bundled/company_tickers.json …"
curl -sL \
  -H "User-Agent: $SEC_USER_AGENT" \
  -H "Accept: application/json" \
  "https://www.sec.gov/files/company_tickers.json" \
  -o data_bundled/company_tickers.json || \
  echo "  (skipped — www.sec.gov fetch failed)"

# 2. Run the prefetch (resumable; --force re-fetches everything).
echo "→ running prefetch (resumable; can take 30-60 min on cold cache) …"
.venv/bin/python -m scripts.prefetch_full_us_market "$@"

# 3. Sanity-check then package.
n_files=$(find "$CACHE_DIR" -name '*.parquet' 2>/dev/null | wc -l | tr -d ' ')
if [ "$n_files" -lt 500 ]; then
  echo "ABORT: cache contains only $n_files parquet files; refusing to publish a broken release."
  exit 1
fi
echo "→ packaging $n_files parquet files into $TMP_TARBALL …"
# COPYFILE_DISABLE=1 stops macOS tar from emitting sidecar ._* files
# (AppleDouble resource forks). They serialize as bytes that look like
# parquet to glob() but crash pyarrow on read. Belt + suspenders: also
# pass --exclude='._*' for tar implementations that ignore the env var.
( cd "$DATA_DIR" && COPYFILE_DISABLE=1 tar --exclude='._*' -czf "$TMP_TARBALL" fundamentals_cache/ )
size_mb=$(du -m "$TMP_TARBALL" | cut -f1)
echo "  → $size_mb MB"

# 4. Publish the release.
STAMP=$(date -u +%Y-%m-%dT%H:%MZ)
echo "→ publishing as cache-latest release …"
gh release delete cache-latest --yes --cleanup-tag 2>/dev/null || true
gh release create cache-latest "$TMP_TARBALL" \
  --title "EDGAR cache — $STAMP (manual refresh)" \
  --notes "Manual refresh from local Mac. $n_files parquet files (${size_mb} MB compressed)."

# 5. Commit the bundled tickers update.
if ! git diff --quiet data_bundled/company_tickers.json 2>/dev/null; then
  echo "→ committing data_bundled/company_tickers.json …"
  git add data_bundled/company_tickers.json
  STAMP_DATE=$(date -u +%Y-%m-%d)
  git commit -m "auto: refresh company_tickers.json bundle (${STAMP_DATE}, manual)"
  git push origin main
fi

rm -f "$TMP_TARBALL"
echo
echo "✓ cache-latest released ($n_files files, $size_mb MB)"
echo "  Daily Paper Trading workflow will pick it up on its next run."
