#!/bin/bash
# One-shot installer for the always-on Second Brain server (launchd).
# Run:  bash ~/Claude/second-brain/install-autostart.sh
U=$(id -u)
LABEL=com.jarvis.brain-server
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "1/5  Freeing port 8765 (stops any temporary instance)…"
pkill -f "uvicorn app.server" 2>/dev/null || true
sleep 2

echo "2/5  Installing service file…"
mkdir -p "$HOME/Library/LaunchAgents"
cp "$HOME/Claude/second-brain/$LABEL.plist" "$PLIST"

echo "3/5  Clearing any stale registration…"
launchctl bootout "gui/$U/$LABEL" 2>/dev/null || true
launchctl enable "gui/$U/$LABEL" 2>/dev/null || true

echo "4/5  Starting always-on service…"
launchctl bootstrap "gui/$U" "$PLIST" 2>&1 || echo "   (bootstrap returned an error — see result below)"
launchctl kickstart -k "gui/$U/$LABEL" 2>/dev/null || true
sleep 4

echo "5/5  Result:"
curl -s -o /dev/null -w "     server: HTTP %{http_code}\n" http://localhost:8765/api/state || echo "     server: no response"
STATE=$(launchctl print "gui/$U/$LABEL" 2>/dev/null | grep -E "state =" | head -1 | xargs)
echo "     ${STATE:-state = (service not found)}"
echo
echo "Want: 'server: HTTP 200' and 'state = running'."
