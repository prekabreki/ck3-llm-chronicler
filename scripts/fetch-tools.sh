#!/usr/bin/env bash
# Fetch the Linux helper binaries the chronicler shells out to:
#   - rakaly   (runtime: melts/json-ifies CK3 binary saves)   github.com/rakaly/cli
#   - ck3-tiger (dev: validates the CK3 mod)                   github.com/amtep/ck3-tiger
#
# Both are gitignored (`rakaly-*/`, `ck3-tiger-*/`) — "fetched per machine" — and
# land in the repo-local layout that scripts/run_rakaly.py / scripts/run_tiger.py
# probe (after PATH). Re-run to re-fetch. Windows/macOS users grab the matching
# release archives from the same projects instead.
set -euo pipefail
cd "$(dirname "$0")/.."

RAKALY_VER="0.8.15"
TIGER_VER="1.18.0"
RAKALY_TRIPLE="x86_64-unknown-linux-musl"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "==> rakaly ${RAKALY_VER} (${RAKALY_TRIPLE})"
curl -fSL -o "$tmp/rakaly.tgz" \
  "https://github.com/rakaly/cli/releases/download/v${RAKALY_VER}/rakaly-${RAKALY_VER}-${RAKALY_TRIPLE}.tar.gz"
# Finder expects rakaly-<ver>/<platform>/rakaly — the tarball's top dir IS the platform dir.
rm -rf "rakaly-${RAKALY_VER}"; mkdir -p "rakaly-${RAKALY_VER}"
tar -xzf "$tmp/rakaly.tgz" -C "rakaly-${RAKALY_VER}/"
chmod +x "rakaly-${RAKALY_VER}"/*/rakaly

echo "==> ck3-tiger ${TIGER_VER} (linux)"
curl -fSL -o "$tmp/tiger.tgz" \
  "https://github.com/amtep/ck3-tiger/releases/download/v${TIGER_VER}/ck3-tiger-linux-v${TIGER_VER}.tar.gz"
# Finder expects ck3-tiger-*/ck3-tiger (flat) — strip the tarball's top dir.
rm -rf "ck3-tiger-linux-v${TIGER_VER}"; mkdir -p "ck3-tiger-linux-v${TIGER_VER}"
tar -xzf "$tmp/tiger.tgz" -C "ck3-tiger-linux-v${TIGER_VER}/" --strip-components=1
chmod +x "ck3-tiger-linux-v${TIGER_VER}"/ck3-tiger*

echo "==> done:"
python scripts/run_rakaly.py --version >/dev/null 2>&1 && echo "    rakaly OK" || echo "    rakaly NOT found by run_rakaly.py"
"ck3-tiger-linux-v${TIGER_VER}/ck3-tiger" --version 2>/dev/null | sed 's/^/    /'
