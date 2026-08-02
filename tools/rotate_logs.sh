#!/bin/bash
# J192: rotate agents/*.log files once they cross 5MB. gzip's the current log to
# <name>.log.1.gz, shifting existing .1.gz -> .2.gz -> .3.gz (keeps 3 generations, drops
# anything older), then truncates the live log to empty so the agent keeps appending to
# the same open path without needing to reopen a file handle.
#
# Safe to run any time: only touches files matching agents/*.log, never *.jsonl stores.
# Files under the 5MB threshold are left completely alone (not even truncated).
#
# Wire into morning.sh as: bash tools/rotate_logs.sh
set -uo pipefail

SB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAX_BYTES=$((5 * 1024 * 1024))
GENERATIONS=3

rotated_any=0

# 2026-07-07: cover the root-level server.out.log/server.err.log/run.log and ingest/*.log
# too — they used to fall through both this glob (agents/ only) and janitor's lossy truncate,
# so the top-level server logs grew forever with no gzip-and-keep-generations rotation.
for log in "$SB_ROOT"/agents/*.log "$SB_ROOT"/*.log "$SB_ROOT"/ingest/*.log; do
  [ -e "$log" ] || continue  # glob didn't match anything
  size=$(stat -f%z "$log" 2>/dev/null || stat -c%s "$log" 2>/dev/null || echo 0)
  if [ "$size" -lt "$MAX_BYTES" ]; then
    continue
  fi
  echo "rotate_logs: $log is $((size / 1024 / 1024))MB, rotating"
  rotated_any=1

  # Shift existing generations up: .2.gz -> .3.gz (drop old .3.gz first), .1.gz -> .2.gz
  oldest="$log.$GENERATIONS.gz"
  [ -e "$oldest" ] && rm -f "$oldest"
  gen=$GENERATIONS
  while [ "$gen" -gt 1 ]; do
    prev=$((gen - 1))
    if [ -e "$log.$prev.gz" ]; then
      mv -f "$log.$prev.gz" "$log.$gen.gz"
    fi
    gen=$prev
  done

  # gzip the current log into generation 1, then truncate the live file (don't delete +
  # recreate: some agents may hold an open append handle across a run; truncating in place
  # via `: >` keeps the inode, which is the safe move for a live log).
  gzip -c "$log" > "$log.1.gz"
  : > "$log"
  echo "  -> $log.1.gz, live log truncated"
done

if [ "$rotated_any" -eq 0 ]; then
  echo "rotate_logs: no agents/*.log file is over $((MAX_BYTES / 1024 / 1024))MB, nothing to rotate"
fi

exit 0
