#!/usr/bin/env bash
# Linux/macOS twin of LAUNCH.bat: start the FastAPI backend (:8000) and the
# Vite frontend (:5173), then open the app in a chromeless browser window.
set -euo pipefail
cd "$(dirname "$0")"

# The console script lives in the venv, which is NOT on a plain shell's PATH.
# Resolve it explicitly: a bare `chronicler` here fails silently, because it is
# backgrounded (so `set -e` never sees it) and Vite comes up anyway — the app
# then answers every /api call with a proxy "Bad Gateway".
CHRONICLER="./.venv/bin/chronicler"
if [ ! -x "$CHRONICLER" ]; then
  CHRONICLER="$(command -v chronicler || true)"
fi
if [ -z "$CHRONICLER" ]; then
  echo "launch.sh: no chronicler executable — expected ./.venv/bin/chronicler." >&2
  echo "Create the venv and install editable (uv venv && uv pip install -e .)." >&2
  exit 1
fi

# Backend in the background. Boot can take ~10s on cold start (baseline parse).
#
# `set -m` puts the backend in its OWN process group, so the trap can kill the
# whole group rather than just the direct child (#60). Killing only $BACKEND_PID
# left the parse-pool workers alive, reparented to init and still holding :8000,
# which made the next launch fail with a proxy Bad Gateway. The negative PID is
# the group. `set +m` restores the default afterwards so npm keeps this shell's
# job-control behaviour.
set -m
"$CHRONICLER" dev &
BACKEND_PID=$!
set +m
trap 'kill -- -"$BACKEND_PID" 2>/dev/null || kill "$BACKEND_PID" 2>/dev/null || true' EXIT

# Find a Chromium-family browser for --app (chromeless) mode.
BROWSER=""
for b in google-chrome-stable google-chrome chromium chromium-browser microsoft-edge brave-browser; do
  if command -v "$b" >/dev/null 2>&1; then BROWSER="$b"; break; fi
done

# Open the app once BOTH Vite (:5173) and the backend (:8000) are accepting
# connections. Vite binds in well under a second; the backend needs ~10s for the
# baseline save parse, and opening before it listens shows a "Bad Gateway" shelf.
wait_for_port() {
  for _ in $(seq 1 "$2"); do
    if (exec 3<>"/dev/tcp/localhost/$1") 2>/dev/null; then exec 3>&- 3<&-; return 0; fi
    sleep 0.2
  done
  return 1
}
(
  wait_for_port 5173 75 || echo "launch.sh: Vite never came up on :5173." >&2
  # Watch the backend PROCESS, not just the port: a stray worker from an earlier
  # run can hold :8000 (#60), and a port check alone reads that as success and
  # opens the browser onto an app whose /api calls all 502.
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "launch.sh: the backend exited during startup — is something already on :8000?" >&2
    echo "           ss -ltnp | grep :8000   # then kill -9 whatever is listed" >&2
  fi
  wait_for_port 8000 300 || \
    echo "launch.sh: backend never came up on :8000 — /api will 502." >&2
  if [ -n "$BROWSER" ]; then
    "$BROWSER" --app=http://localhost:5173 >/dev/null 2>&1 &
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open http://localhost:5173 >/dev/null 2>&1 &
  else
    echo "No browser found — open http://localhost:5173 manually."
  fi
) &

# Frontend stays in the foreground so npm logs / Ctrl+C land here.
cd frontend
exec npm run dev
