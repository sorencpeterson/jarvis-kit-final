#!/usr/bin/env python3
"""Travel mode (#86) — flags travel state; the reductions themselves live elsewhere.

Why: config `travel: true/false` is the single switch [OWNER] flips when he's on the
road. cold_feeder.py and networking.py already read their own caps straight out of
config (cold_daily_enroll, network.daily.*) each run, so this agent does NOT touch
their behavior or config values, it only manages a store/.travel-mode flag file
those (or future) callers can check cheaply, and announces to the feed when the
travel state actually CHANGES (not on every no-op run, so the feed doesn't get
spammed by an unattended cron re-running this hourly).

When travel is true, prints the reductions it WOULD apply (advisory only — this
agent does not enforce them; that's for the readers of the flag to decide).

Read-only against config.json; only write is store/.travel-mode (create/update
content, or remove). Run standalone: .venv/bin/python agents/travel_mode.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402

CONFIG = ROOT / "store" / "config.json"
FLAG = ROOT / "store" / ".travel-mode"

# Advisory-only reductions travel mode WOULD apply. Not enforced here; a future
# caller of the flag (or a human) decides how to actually dial these down.
REDUCTIONS = [
    "cold_daily_enroll -> 0 (pause new cold enrollments while traveling)",
    "network.daily.connect -> ~5 (half-speed LinkedIn connects)",
    "network.daily.comment -> ~3",
    "job_daily_apply_cap -> half of configured value",
    "gentler morning brief (fewer asks, shorter)",
]


def _config() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _flag_last_state() -> str | None:
    """The flag file's content records the last known state ('on'/'off') as its
    first token, so a state-change can be detected across runs without a separate
    tracking file."""
    if not FLAG.exists():
        return None
    try:
        first = FLAG.read_text().splitlines()[0].strip()
    except (OSError, IndexError):
        return None
    return first.split()[0] if first else None


def run() -> int:
    travel = bool(_config().get("travel", False))
    last_state = _flag_last_state()
    now_state = "on" if travel else "off"
    changed = last_state != now_state

    if travel:
        FLAG.parent.mkdir(parents=True, exist_ok=True)
        FLAG.write_text(f"on {now_iso()}\n")
        print("travel_mode: travel=true -> flag SET. Reductions it WOULD apply:")
        for r in REDUCTIONS:
            print(f"  - {r}")
        if changed:
            planner.feed_add("agent", "Travel mode turned ON",
                             "Advisory reductions ready for cold/network/jobs; not auto-enforced.")
    else:
        removed = FLAG.exists()
        FLAG.unlink(missing_ok=True)
        state_desc = "removed" if removed else "already absent"
        print(f"travel_mode: travel=false -> flag {state_desc}")
        if changed:
            planner.feed_add("agent", "Travel mode turned OFF", "Back to normal caps.")

    if not changed:
        print(f"travel_mode: no state change ({now_state}, same as last run)")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
