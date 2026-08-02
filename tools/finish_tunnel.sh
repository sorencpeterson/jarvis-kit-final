#!/bin/bash
# Self-completing Cloudflare tunnel setup (2026-07-11). run.sh calls this every 10-min
# tick; it exits instantly until ~/.cloudflared/cert.pem exists ([OWNER]'s one Authorize
# click), then idempotently: create tunnel -> DNS route -> config.yml -> LaunchAgent
# (user domain, no sudo) -> verify running -> stamp store/.tunnel-ready (never runs again).
# Any failed step exits quietly and the next tick retries. After this, agents/domain_watch.py
# flips public_base_url the moment DNS propagation makes the domain answer. Chain:
#   his Authorize click -> (this) tunnel up -> NS propagation -> (domain_watch) links flip.
set -u
BRAIN="[APP_ROOT]"
CF="$HOME/.local/bin/cloudflared"
STAMP="$BRAIN/store/.tunnel-ready"
NAME="brain-proposals"
DOMAIN="proposals.[OWNER_SITE]"
LOG="$BRAIN/cloudflared-setup.log"

[ -f "$STAMP" ] && exit 0
[ -f "$HOME/.cloudflared/cert.pem" ] || exit 0   # waiting on the Authorize click
[ -x "$CF" ] || exit 0

exec >> "$LOG" 2>&1
echo "=== $(date '+%F %T') finish_tunnel attempt ==="

# 1. tunnel (create once)
if ! "$CF" tunnel list 2>/dev/null | grep -q " $NAME "; then
  "$CF" tunnel create "$NAME" || { echo "create failed"; exit 0; }
fi
TID=$("$CF" tunnel list 2>/dev/null | awk -v n="$NAME" '$2==n{print $1}' | head -1)
[ -n "$TID" ] || { echo "no tunnel id"; exit 0; }
echo "tunnel id: $TID"

# 2. DNS route (idempotent; 'already exists' is fine)
"$CF" tunnel route dns "$NAME" "$DOMAIN" 2>&1 | grep -vi "already exists" || true

# 3. config
mkdir -p "$HOME/.cloudflared"
cat > "$HOME/.cloudflared/config.yml" <<EOF
tunnel: $NAME
credentials-file: $HOME/.cloudflared/$TID.json
ingress:
  - hostname: $DOMAIN
    service: http://localhost:8765
  - service: http_status:404
EOF
[ -f "$HOME/.cloudflared/$TID.json" ] || { echo "credentials json missing"; exit 0; }

# 4. LaunchAgent (user domain: no sudo). KeepAlive so it survives crashes + reboots.
PLIST="$HOME/Library/LaunchAgents/com.jarvis.cloudflared.plist"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.jarvis.cloudflared</string>
  <key>ProgramArguments</key><array>
    <string>$HOME/.local/bin/cloudflared</string>
    <string>tunnel</string><string>run</string><string>$NAME</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$BRAIN/cloudflared.log</string>
  <key>StandardErrorPath</key><string>$BRAIN/cloudflared.log</string>
</dict></plist>
EOF
launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null \
  || launchctl kickstart -k "gui/$(id -u)/com.jarvis.cloudflared" 2>/dev/null || true

sleep 4
if pgrep -f "cloudflared tunnel run $NAME" >/dev/null; then
  touch "$STAMP"
  echo "tunnel RUNNING; stamped"
  "$BRAIN/.venv/bin/python" - <<'PY' 2>/dev/null || true
import sys; sys.path[:0]=['[APP_ROOT]','[APP_ROOT]/app']
import planner
planner.feed_add("proposals", "Cloudflare tunnel is up (brain-proposals). Waiting on DNS to flip the links.")
planner.notify("Tunnel running", "Cloudflare tunnel is live. Links flip automatically when DNS propagates.")
PY
else
  echo "cloudflared not running yet; will retry next tick"
fi
exit 0
