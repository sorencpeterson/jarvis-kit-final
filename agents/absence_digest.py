#!/usr/bin/env python3
"""If [OWNER] hasn't opened the dashboard in 36h, chase him with ONE compact push a day.

The system working perfectly while nobody looks is still a failure — this makes the
brain reach out instead of waiting. Counts only (content-free push policy). Runs from
run.sh every 10 min; the checks are two file stats, so it costs nothing.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import secret  # noqa: E402
import planner  # noqa: E402

STAMP = ROOT / "store" / ".last-open"
SILENCE_H = 36


def _get(path: str) -> dict:
    req = urllib.request.Request("http://127.0.0.1:8765" + path,
                                 headers={"X-Brain-Token": secret("brain_token")})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def run():
    if not STAMP.exists():
        STAMP.touch()  # first run: start the clock, don't nag
        return 0
    idle_h = (time.time() - STAMP.stat().st_mtime) / 3600
    if idle_h < SILENCE_H:
        return 0
    from datetime import datetime
    sent_marker = ROOT / "store" / (".digest-sent-" + datetime.now().strftime("%Y-%m-%d"))
    if sent_marker.exists():
        return 0
    try:
        needs = _get("/api/needs")
        money = _get("/api/money")
        body = (f"{needs.get('total', 0)} items waiting · "
                f"${money.get('pipeline_value', 0):,} pipeline · "
                f"{money.get('replies_waiting', 0)} replies · "
                f"{int(idle_h)}h since you last looked")
    except Exception:  # noqa: BLE001
        body = f"{int(idle_h)}h since you opened the dashboard. It kept working."
    if planner.notify("Your brain misses you", body, tags="brain"):
        sent_marker.touch()
        print("absence digest sent:", body)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
