#!/bin/bash
# Self-healing watchdog: runs every 5 min. Restarts a dead server, and pushes [OWNER]'s phone
# on NEWLY-appeared problems (never spams the same alert). Pure shell, zero allowance cost.
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
SB=[APP_ROOT]
STATE=$SB/agents/.watchdog-state
TOPIC=$(grep -o '"ntfy_topic": *"[^"]*"' "$SB/store/config.json" 2>/dev/null | cut -d'"' -f4)
TOK=$(grep '^BRAIN_TOKEN=' "$SB/.env" 2>/dev/null | cut -d= -f2)
push(){ [ -n "$TOPIC" ] && curl -s -m 8 -d "$1" -H "Title: Brain watchdog" -H "Tags: warning" "https://ntfy.sh/$TOPIC" >/dev/null; }

# 1. Server reachable? If not, restart it and stop (a restart is the fix).
if ! curl -sf -m 8 -o /dev/null http://127.0.0.1:8765/; then
  launchctl kickstart -k "gui/$(id -u)/com.jarvis.brain-server" 2>/dev/null
  push "Server was down. Restarted it."
  echo "server_down" > "$STATE"
  exit 0
fi

# 2. Health snapshot -> collect currently-active problem flags.
H=$(curl -s -m 10 -H "X-Brain-Token: $TOK" http://127.0.0.1:8765/api/health)
prev=$(cat "$STATE" 2>/dev/null)
cur=""
echo "$H" | grep -q '"brief_error": *true'   && cur="$cur brief_error"
echo "$H" | grep -q '"morning_stale": *true' && cur="$cur morning_stale"
echo "$H" | grep -q '"jobs_stale": *true'    && cur="$cur jobs_stale"
echo "$H" | grep -qE '"disk_free_gb": *[0-9]\.' && cur="$cur disk_low"   # single-digit GB free
# cadence checker (built + tested but never wired in — 2026-07-06 audit M1); flag goes
# through the same dedupe as the rest so a standing MISS pushes once, not every 5 min
[ -x "$SB/.venv/bin/python" ] && "$SB/.venv/bin/python" "$SB/agents/agent_cadence_checker.py" 2>/dev/null | head -1 | grep -qE '[1-9][0-9]* MISS' && cur="$cur cadence_miss"

# 3. Push only problems that are NEW since last run.
for c in $cur; do
  echo "$prev" | grep -qw "$c" && continue
  case $c in
    brief_error)   push "Morning brief failed (CLI offline or API error). Check the Mac's network." ;;
    morning_stale) push "Morning run hasn't completed in over a day. Check the Mac." ;;
    jobs_stale)    push "Job scanner hasn't written anything in 30h. It may be silently broken." ;;
    cadence_miss)  push "An agent missed its cadence. Details: second-brain/store/cadence_report.json" ;;
    disk_low)      push "Disk is low (under 10GB free)." ;;
  esac
done
echo "$cur" > "$STATE"

# runaway-agent tripwire (#100) + stale heartbeats (#43) — quiet unless something's wrong
cd [APP_ROOT] 2>/dev/null && {
  [ -x .venv/bin/python ] && .venv/bin/python agents/tripwire.py >/dev/null 2>&1
  [ -x .venv/bin/python ] && .venv/bin/python agents/hbcheck.py 2>/dev/null | grep -q WARN &&     .venv/bin/python -c "import sys;sys.path.insert(0,'app');import planner;planner.notify('Agent heartbeat stale','An agent has not run on schedule. Check SYSTEM.')" >/dev/null 2>&1

  # call_escalator afternoon fire (red-team: it only ran at 6:30 when it's a no-op, and no
  # afternoon scheduler was loaded, so the 3pm/8pm warm-call accountability NEVER fired).
  # Firing it here every 5 min during the 15:00 + 20:00 hours means it works without [OWNER]
  # loading the escalator plist; the agent self-gates once-per-stage-per-day so extra calls
  # are instant no-ops. (The plist stays as the belt-and-suspenders path once he loads it.)
  H=$(date +%H)
  if [ "$H" = "15" ] || [ "$H" = "20" ]; then
    [ -x .venv/bin/python ] && .venv/bin/python agents/call_escalator.py >/dev/null 2>&1
  fi
}
