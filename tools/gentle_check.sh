#!/bin/bash
# Gentle-morning check (#83 support) — prints GENTLE if today's short-sleep flag
# exists (store/.gentle-morning, written by agents/sleep_aware.py), else prints
# NORMAL. For a future brief/morning step to branch on; this script itself does
# not soften anything.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

FLAG="store/.gentle-morning"
TODAY="$(date +%Y-%m-%d)"

if [ -f "$FLAG" ] && grep -q "^${TODAY}" "$FLAG" 2>/dev/null; then
  echo "GENTLE"
  exit 0
fi

echo "NORMAL"
exit 0
