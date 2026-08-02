#!/bin/bash
# Replay (#48) — run a single named agent in the foreground by hand, for when
# something misbehaved overnight and the fastest way to see why is to just run it
# again and watch it. Output goes to the terminal AND is appended to
# agents/replay.log, so a replay triggered from a phone/Raycast context still
# leaves a paper trail to check later.
#
# Usage: tools/replay.sh <agent>
#   agent is a short alias, not a filename — see the map below for what's covered.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

LOG="agents/replay.log"
PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

usage() {
  echo "usage: $(basename "$0") <agent>" >&2
  echo "  known agents: jobs, brief, cold_import, cold_feeder, readback, metrics, insight" >&2
  exit 1
}

NAME="${1:-}"
[ -n "$NAME" ] || usage

case "$NAME" in
  jobs)         SCRIPT="agents/jobs.py" ;;
  brief)        SCRIPT="agents/daily_brief.py" ;;
  cold_import)  SCRIPT="agents/cold_import.py" ;;
  cold_feeder)  SCRIPT="agents/cold_feeder.py" ;;
  readback)     SCRIPT="agents/content_readback.py" ;;
  metrics)      SCRIPT="agents/metrics_rollup.py" ;;
  insight)      SCRIPT="agents/daily_insight.py" ;;
  *)
    echo "replay: unknown agent '$NAME'" >&2
    usage
    ;;
esac

if [ ! -f "$SCRIPT" ]; then
  echo "replay: mapped script $SCRIPT does not exist on disk" >&2
  exit 1
fi

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') replay $NAME -> $SCRIPT ==="
} >> "$LOG"

# tee both to the terminal (so the person running this by hand sees it live) and
# to the log (so it's captured even if this was kicked off headlessly).
"$PY" "$SCRIPT" 2>&1 | tee -a "$LOG"
exit "${PIPESTATUS[0]}"
