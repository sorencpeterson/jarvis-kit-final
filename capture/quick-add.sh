#!/bin/bash
# Capture a thought into the brain from anywhere on the Mac.
#   ./quick-add.sh "call the roofer about the quote"
#
# Raycast: Settings -> Extensions -> Script Commands -> Add Directory -> this folder,
# then "Brain Capture" gets a global hotkey. The headers below make it a Raycast command.
#
# @raycast.schemaVersion 1
# @raycast.title Brain Capture
# @raycast.mode silent
# @raycast.icon 🧠
# @raycast.packageName Second Brain
# @raycast.argument1 { "type": "text", "placeholder": "what's on your mind" }

DIR="$(cd "$(dirname "$0")/.." && pwd)"
TOKEN="$(grep '^BRAIN_TOKEN=' "$DIR/.env" | cut -d= -f2)"
TEXT="$*"
[ -z "$TEXT" ] && exit 0
BODY="$(printf '%s' "$TEXT" | /usr/bin/python3 -c 'import json,sys;print(json.dumps({"text": sys.stdin.read().strip()}))')"
curl -sf -m 6 -X POST "http://127.0.0.1:8765/api/todo" \
  -H "X-Brain-Token: $TOKEN" -H "Content-Type: application/json" \
  -d "$BODY" >/dev/null && echo "🧠 captured" || echo "brain unreachable"
