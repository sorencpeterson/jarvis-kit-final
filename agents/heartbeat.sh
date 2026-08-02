#!/bin/bash
# Sourceable heartbeat helper (#43). NOT meant to be executed directly — another
# script does `source agents/heartbeat.sh` then calls `hb <name>` at the point in
# its run where it wants to prove-of-life. Each call just touches a marker file
# under store/.hb/<name>; agents/hbcheck.py later reads mtimes off those files to
# spot anything that's gone quiet. Deliberately not wired into morning.sh/watchdog.sh
# here — that's a follow-up decision, not this script's job.
SB_HB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/store/.hb"

hb() {
  local name="${1:?usage: hb <name>}"
  mkdir -p "$SB_HB_DIR" 2>/dev/null
  touch "$SB_HB_DIR/$name" 2>/dev/null
}
