#!/bin/bash
# Black box (#106) — bundle the last ~24h of evidence into one tarball for
# post-incident debugging, so "what was happening right before it broke" doesn't
# require re-deriving it from six different log files by hand.
#
# Includes: tail of every *.log at repo root and agents/*.log (last 200KB each,
# plenty for a day of activity without dragging in ancient history), store/config.json
# (secrets already live in .env, not config.json, and config.json has no raw tokens —
# see store_lib.secret()), last 20 git log entries, and the jarvis launchd jobs.
#
# Output: /tmp/brain-blackbox-<ts>.tar.gz — printed at the end. Read-only against
# the live system; only ever appends bytes to /tmp.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

TS="$(date +%Y%m%d-%H%M%S)"
WORKDIR="$(mktemp -d /tmp/brain-blackbox-work.XXXXXX)"
OUT="/tmp/brain-blackbox-${TS}.tar.gz"
LOGDIR="$WORKDIR/logs"
mkdir -p "$LOGDIR"

# 1. Last 200KB of every top-level and agents/ log.
for f in *.log agents/*.log; do
  [ -f "$f" ] || continue
  safe_name="$(echo "$f" | tr '/' '_')"
  tail -c 200000 "$f" > "$LOGDIR/$safe_name" 2>/dev/null
done

# 2. Config snapshot (no raw secrets live here — see .env for those).
[ -f "store/config.json" ] && cp "store/config.json" "$WORKDIR/config.json"

# 3. Recent git history.
git log -20 > "$WORKDIR/git-log.txt" 2>/dev/null || echo "(no git history)" > "$WORKDIR/git-log.txt"

# 4. This system's launchd jobs.
launchctl list 2>/dev/null | grep jarvis > "$WORKDIR/launchctl.txt" || echo "(no jarvis jobs found)" > "$WORKDIR/launchctl.txt"

tar -czf "$OUT" -C "$WORKDIR" .
rm -rf "$WORKDIR"

echo "$OUT"
