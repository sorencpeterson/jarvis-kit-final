#!/bin/bash
# Launch the living second-brain app. Open http://localhost:8765
# launchd-safe: uses the project venv directly (launchd's PATH won't have `uv`).
# Phone access anywhere: Tailscale + serve.sh 0.0.0.0 (see app/README.md).
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
HOST="${1:-127.0.0.1}"
if [ -x ".venv/bin/uvicorn" ]; then
  exec .venv/bin/uvicorn app.server:app --host "$HOST" --port 8765 --no-access-log
else
  exec uv run uvicorn app.server:app --host "$HOST" --port 8765 --no-access-log
fi
