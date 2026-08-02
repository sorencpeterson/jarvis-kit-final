#!/usr/bin/env python3
"""Heartbeat check (#43) — companion to agents/heartbeat.sh.

Any script that does `source agents/heartbeat.sh` and calls `hb <name>` leaves a
marker file at store/.hb/<name> with an mtime updated on every run. This just lists
those markers and flags any whose mtime is older than 26 hours (a bit past a strict
24h so one slow/late daily run doesn't false-positive) as stale, printing a WARN line
per stale name.

Read-only check, WIRED into watchdog.sh (every 5 min): watchdog greps this output for
WARN and pushes [OWNER]'s phone on a stale heartbeat. morning.sh sources heartbeat.sh and
fires `hb morning-chain` at the completion stamp, so there is a real heartbeat to check.
(Docstring corrected 2026-07-07 H-audit — it previously claimed "not wired", a class-14 lie.)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HB_DIR = ROOT / "store" / ".hb"
STALE_AFTER_HOURS = 26


def check() -> list[dict]:
    if not HB_DIR.exists():
        return []
    now = time.time()
    stale = []
    for p in sorted(HB_DIR.iterdir()):
        if not p.is_file():
            continue
        age_hours = (now - p.stat().st_mtime) / 3600
        if age_hours > STALE_AFTER_HOURS:
            stale.append({"name": p.name, "age_hours": round(age_hours, 1)})
    return stale


def main() -> int:
    stale = check()
    if not stale:
        print("hbcheck: no stale heartbeats" if HB_DIR.exists()
              else "hbcheck: no heartbeats recorded yet (store/.hb/ doesn't exist)")
        return 0
    for s in stale:
        print(f"WARN: heartbeat '{s['name']}' stale ({s['age_hours']}h since last touch)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
