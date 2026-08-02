#!/bin/bash
# One refresh cycle: pull new Siri captures from Reminders, rebuild the dashboard.
# Used by hand and by the launchd timer (see com.jarvis.secondbrain.plist).
set -euo pipefail
cd "$(dirname "$0")"

LOG="run.log"
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  if command -v uv >/dev/null 2>&1; then RUN="uv run python"; else RUN="python3"; fi
  # `|| true` on EVERY step (2026-07-12 hunt): under `set -e`, one uncaught exception in any
  # early step aborted the whole tick before money_session/evening_chain/self-heal ran. A
  # single crashy agent must never silence the money-critical steps below it. Failures still
  # land in run.log via the block's 2>&1 capture.
  $RUN capture/pull_reminders.py || true
  $RUN dashboard/build_dashboard.py || true
  $RUN agents/absence_digest.py || true
  $RUN agents/futures_check.py || true
  # Daily Money Session (2026-07-12, STATE-OF-JARVIS fix #1): 18:30-18:59 window, once a
  # day, pushes his 5 highest-value clicks (staged proposals, replies, content, top todo)
  # to his phone right before the evening chain. Self-gating; knob money_session.
  $RUN agents/money_session.py || true
  # Evening job lane (2026-07-11): self-gates to knob job_evening_chain + 19:00-22:00 local
  # + a once-a-day stamp, so calling it on every 10-min tick is free. Geo-held runs retry
  # each tick until the VPN comes on. `|| true`: a crash must not kill the block (set -e).
  $RUN agents/evening_chain.py || true
  # Cloudflare chain finishers (2026-07-11), both instant no-ops until their gate opens:
  # finish_tunnel waits for [OWNER]'s cloudflared Authorize click (cert.pem) then builds
  # tunnel+DNS route+LaunchAgent once; domain_watch waits for the domain to ANSWER then
  # flips public_base_url + re-links staged proposals once. Zero babysitting.
  bash tools/finish_tunnel.sh || true
  $RUN agents/domain_watch.py || true
} >> "$LOG" 2>&1

# Self-heal: if the 6:30 morning run never completed its net-dependent steps (Mac asleep
# or offline — happened 2026-07-02: 0 jobs scanned, brief failed), re-run it once the
# network is back. morning.sh writes the stamp only on a genuine brief.
STAMP="store/.morning-done-$(date +%Y-%m-%d)"
LOCK="store/.morning.lock"
# Clear a stale lock (crashed run) after 45 min so self-heal can't wedge forever.
if [ -d "$LOCK" ] && [ -n "$(find "$LOCK" -maxdepth 0 -mmin +45 2>/dev/null)" ]; then
  rmdir "$LOCK" 2>/dev/null || true
fi
if [ "$(date +%H)" -ge 7 ] && [ ! -f "$STAMP" ] \
   && curl -m 5 -sf https://api.anthropic.com >/dev/null 2>&1 \
   && mkdir "$LOCK" 2>/dev/null; then
  trap 'rmdir "$LOCK" 2>/dev/null' EXIT
  echo "$(date '+%H:%M') morning stamp missing -> self-heal re-run" >> "$LOG"
  bash agents/morning.sh >> "$LOG" 2>&1 || true
fi
