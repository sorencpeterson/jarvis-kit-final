#!/usr/bin/env python3
"""Is the process on port 8765 running the code that is on disk?

A green test suite proves the FILE is correct; it says nothing about the
PROCESS. A sibling install spent most of a day shipping fixes into a file
that a ten-day-old launchd process never re-read: the one queue-protection
guard that mattered was sitting inert on disk while the old broken one ran,
and 80 queued jobs burned (field report 2026-08-12, A3, "the most expensive
defect"). No test in a 2000-test suite can catch this class of error, so
check it directly.

    python3 tools/check_server_fresh.py

Exit 0 = fresh, or no server running (nothing to check).
Exit 1 = STALE: app/server.py was edited after the process started.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "app" / "server.py"


def main() -> int:
    try:
        pids = subprocess.run(["lsof", "-ti:8765"], capture_output=True,
                              text=True, timeout=10).stdout.split()
    except Exception:  # noqa: BLE001
        pids = []
    if not pids:
        print("server-fresh: nothing listening on :8765 (server not running); nothing to check")
        return 0
    try:
        lstart = subprocess.run(["ps", "-o", "lstart=", "-p", pids[0]],
                                capture_output=True, text=True, timeout=10).stdout.strip()
        # lstart is e.g. 'Tue Aug 12 10:11:12 2026'; collapse runs of spaces first
        started = datetime.strptime(" ".join(lstart.split()),
                                    "%a %b %d %H:%M:%S %Y").timestamp()
    except Exception as e:  # noqa: BLE001
        print(f"server-fresh: could not read process start time ({e}); skipping")
        return 0
    try:
        edited = SRC.stat().st_mtime
    except OSError:
        print("server-fresh: no app/server.py here; skipping")
        return 0
    if edited > started:
        print("server-fresh: STALE."
              f" app/server.py was edited {datetime.fromtimestamp(edited):%Y-%m-%d %H:%M}"
              f" but the process on :8765 started {datetime.fromtimestamp(started):%Y-%m-%d %H:%M}."
              " Your changes are on disk and NOT running; restart the server."
              " (If :8765 belongs to a DIFFERENT install on this machine, ignore this.)")
        return 1
    print("server-fresh: ok (the process is newer than app/server.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
