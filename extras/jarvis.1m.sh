#!/usr/bin/env bash
# JARVIS menu-bar reactor · xbar/SwiftBar plugin (refreshes every 1m per filename)
# The arc reactor in your macOS menu bar: state glyph + needs count + pipeline.
# Install (your step, once):
#   brew install --cask swiftbar        (or xbar)
#   point its plugin folder at second-brain/extras/ (or symlink this file into it)
# Reads the brain token locally (.env BRAIN_TOKEN, then store/config.json brain_token).
# GET-only. Never sends anything.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOK="$(grep -m1 '^BRAIN_TOKEN=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")"
if [ -z "$TOK" ]; then
  TOK="$(/usr/bin/python3 -c "import json;print(json.load(open('$ROOT/store/config.json')).get('brain_token',''))" 2>/dev/null)"
fi

fetch(){ curl -sm 3 -H "x-brain-token: $TOK" "http://127.0.0.1:8765$1" 2>/dev/null; }

NEEDS="$(fetch /api/needs)"
MONEY="$(fetch /api/money)"

if [ -z "$NEEDS" ]; then
  echo "○ | color=#6b7280"
  echo "---"
  echo "JARVIS is offline (server not answering on 8765)"
  echo "Open dashboard | href=http://127.0.0.1:8765"
  exit 0
fi

/usr/bin/python3 - "$NEEDS" "$MONEY" <<'PY'
import json, sys
try:
    needs = json.loads(sys.argv[1] or "{}")
except Exception:
    needs = {}
try:
    money = json.loads(sys.argv[2] or "{}")
except Exception:
    money = {}
items = needs.get("items") or []
total = needs.get("total") or 0
hot = sum(1 for i in items if i.get("sev") == "hot")
pipe = money.get("pipeline_value") or 0
pk = ("$%dk" % round(pipe / 1000)) if pipe >= 1000 else ("$%d" % pipe)

if hot >= 3:
    glyph, color = "◉", "#ff5d73"
elif total > 0:
    glyph, color = "◉", "#ffb454"
else:
    glyph, color = "◉", "#3fe7ff"

top = ("%s %d" % (glyph, total)) if total else glyph
print("%s | color=%s font=Menlo" % (top, color))
print("---")
print("JARVIS · %s pipeline | font=Menlo color=#3fe7ff" % pk)
print("%d need you · %d hot | font=Menlo" % (total, hot))
print("---")
for i in items[:6]:
    sev = i.get("sev") or ""
    c = "#ff5d73" if sev == "hot" else ("#ffb454" if sev == "warn" else "#9ca3af")
    print("%s (%s) | font=Menlo color=%s href=http://127.0.0.1:8765" % (i.get("label", "?"), i.get("count", 0), c))
print("---")
print("Open the bridge | href=http://127.0.0.1:8765")
PY
