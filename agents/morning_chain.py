#!/usr/bin/env python3
"""Config-gated morning apply-chain. SHIPS OFF (job_morning_chain: 0).

When [OWNER] sets config job_morning_chain > 0, the morning routine kicks ONE apply
chain via the same endpoint the dashboard button uses — identical caps, identical
operators, identical stop button. Flipping the knob is his standing authorization;
every run still pushes a notification so nothing happens silently.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import secret  # noqa: E402
import planner  # noqa: E402


def run():
    if "--rehearse" in sys.argv:
        n = int(planner._config().get("job_morning_chain") or 0)
        print(f"[rehearse] morning chain: knob={n} -> would {'POST /api/launch/job_apply' if n > 0 else 'do nothing (off)'}")
        return 0

    if int(planner._config().get("job_morning_chain") or 0) <= 0:
        print("morning chain: off (job_morning_chain=0)")
        return 0
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8765/api/launch/job_apply", data=b"{}", method="POST",
            headers={"X-Brain-Token": secret("brain_token"), "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            j = json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        print("morning chain: server unreachable:", e)
        return 0
    if j.get("ran"):
        planner.feed_add("jobs", "Morning apply chain started (config-enabled)")
        planner.notify("Morning chain running", "Applying to the approved queue. Stop anytime from the dashboard.")
        print("morning chain: started")
    else:
        print("morning chain: not started:", j)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
