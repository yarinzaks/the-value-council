#!/usr/bin/env bash
#
# Build the dashboard as a static site in both languages.
#
# The app renders its Hebrew on the server, from a cookie, which is the
# one thing a static host cannot do. So it is built twice — once per
# language, each rooted at its own basePath — and the two results are
# published side by side:
#
#   site/he/...   NEXT_PUBLIC_SITE_LOCALE=he  basePath=/he
#   site/en/...   NEXT_PUBLIC_SITE_LOCALE=en  basePath=/en
#   site/_redirects   sends / to /he
#
# The language toggle then navigates between them (see swapLocalePath in
# components/Providers.tsx) instead of setting a cookie no server would
# read.
#
# Every fs read in lib/data.ts happens here, at build time, against
# VALUE_COUNCIL_DATA_DIR. What ships is HTML.
#
# Usage:
#   VALUE_COUNCIL_DATA_DIR=/path/to/data ./scripts/build-static-site.sh
#
# Run from the dashboard/ directory.

set -euo pipefail

DASHBOARD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DASHBOARD_DIR"

# Default to the repo's own data tree, which is what CI has. A developer
# with the Mac's canonical tree can point at it explicitly.
: "${VALUE_COUNCIL_DATA_DIR:=$(cd "$DASHBOARD_DIR/.." && pwd)/data}"
export VALUE_COUNCIL_DATA_DIR

if [ ! -d "$VALUE_COUNCIL_DATA_DIR" ]; then
  echo "error: VALUE_COUNCIL_DATA_DIR does not exist: $VALUE_COUNCIL_DATA_DIR" >&2
  exit 1
fi

# Fail loudly rather than publish a site with no numbers in it. Every
# loader in lib/data.ts returns [] or null on a read error by design, so
# a missing tree would otherwise build clean and ship blank.
for required in backtest_results portfolios snapshots; do
  if [ ! -d "$VALUE_COUNCIL_DATA_DIR/$required" ]; then
    echo "error: $VALUE_COUNCIL_DATA_DIR/$required is missing — the site would" >&2
    echo "       build successfully and contain no data. Refusing." >&2
    exit 1
  fi
done

SITE_DIR="$DASHBOARD_DIR/site"
rm -rf "$SITE_DIR"
mkdir -p "$SITE_DIR"

for locale in he en; do
  echo "=== building $locale ==="
  # Each language gets its own dist dir, which keeps a dev server's
  # .next intact (see next.config.mjs) and keeps the two builds from
  # reading each other's cache. With `output: "export"` and a custom
  # distDir, Next writes the exported site *into* that directory rather
  # than into out/ — so the dist dir is the artifact to move.
  DIST=".next-$locale"
  rm -rf "${DASHBOARD_DIR:?}/$DIST"
  NEXT_PUBLIC_SITE_LOCALE="$locale" \
  NEXT_DIST_DIR="$DIST" \
    npx next build

  if [ ! -f "$DASHBOARD_DIR/$DIST/index.html" ]; then
    echo "error: $locale build produced no index.html in $DIST" >&2
    exit 1
  fi
  mv "$DASHBOARD_DIR/$DIST" "$SITE_DIR/$locale"
  echo "    -> site/$locale ($(find "$SITE_DIR/$locale" -type f | wc -l | tr -d ' ') files)"
done

# The assistant's view of this site, as data rather than HTML.
#
# The pages are a static export, so every number in them is only
# readable by a browser. /api/chat runs on the edge and needs the same
# facts, so they are published once here, from the same tree the pages
# were just built from. A failure is fatal: an assistant that answers
# questions about a dashboard it cannot read would make things up.
echo "=== chat context ==="
node "$DASHBOARD_DIR/scripts/export-chat-context.mjs" "$SITE_DIR/chat-context.json"

# Hebrew is the default: this dashboard has one reader and that is the
# language they use. Cloudflare Pages reads this file; other static
# hosts ignore it harmlessly, and the index.html below covers them.
cat > "$SITE_DIR/_redirects" <<'REDIRECTS'
/  /he/  302
REDIRECTS

cat > "$SITE_DIR/index.html" <<'INDEX'
<!doctype html>
<meta charset="utf-8">
<title>The Value Council</title>
<meta http-equiv="refresh" content="0; url=/he/">
<link rel="canonical" href="/he/">
<a href="/he/">The Value Council</a>
INDEX

# git restores this faster than reasoning about which dist dir won.
if git -C "$DASHBOARD_DIR" rev-parse --git-dir >/dev/null 2>&1; then
  git -C "$DASHBOARD_DIR" checkout -- tsconfig.json 2>/dev/null || true
fi

echo
echo "site/ is ready to publish:"
du -sh "$SITE_DIR" | sed 's/^/  /'
