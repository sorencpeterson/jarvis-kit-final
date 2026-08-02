#!/usr/bin/env python3
"""Evening job lane (2026-07-11, [OWNER]: "our systems aren't auto-running; change some
auto runs to work at night, seven or eight PM").

WHY EVENING: the morning lane kept not applying. Forensics: job_morning_chain shipped 0
and was never flipped (so nothing EVER auto-applied; every application was his manual
click), the 6:30 chain routinely finishes 4-8h late on self-heals, and even when kicked
the apply endpoint is geo-gated fail-closed — at 6:30am his US VPN is usually OFF, so
morning is structurally the worst slot. Evening = Mac awake, VPN on, and his European
evening is US business hours, so applications land while recruiters are at their desks.

MECHANICS: run.sh (launchd, every 10 min) calls this every tick; it self-gates:
  - knob `job_evening_chain` > 0 ([OWNER]'s standing go switch, set 1 on 2026-07-11 at his
    explicit ask) and local hour in [evening_hour, 22), default 19.
  - once per day via store/.evening-done-YYYY-MM-DD.
  - fresh scan first (agents/jobs.py; job_auto pre-approves easy ones) so the evening run
    applies to postings from TODAY, then POST /api/launch/job_apply — the SAME endpoint,
    caps, geo-gate, and dashboard stop button as the manual Apply click.
  - geo-held (VPN off): notify ONCE, write NO stamp — the next 10-min tick retries until
    the window closes, so turning the VPN on any time before 22:00 lets it fire.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import secret  # noqa: E402
import planner  # noqa: E402

WINDOW_END_HOUR = 22          # never start applying after this local hour
SCAN_TIMEOUT_S = 420          # fresh-scan budget; on overrun we still apply to the existing queue


def _now() -> datetime:
    return datetime.now().astimezone()


def run() -> int:
    cfg = planner._config()
    if "--rehearse" in sys.argv:
        n = int(cfg.get("job_evening_chain") or 0)
        print(f"[rehearse] evening chain: knob={n}, window {int(cfg.get('evening_hour') or 19)}-{WINDOW_END_HOUR}h"
              f" -> would {'scan + POST /api/launch/job_apply' if n > 0 else 'do nothing (off)'}")
        return 0
    if int(cfg.get("job_evening_chain") or 0) <= 0:
        return 0  # off; silent (this fires every 10 min, don't spam run.log)
    now = _now()
    start_hour = int(cfg.get("evening_hour") or 19)
    if not (start_hour <= now.hour < WINDOW_END_HOUR):
        return 0  # outside the window; silent
    day = now.strftime("%Y-%m-%d")
    stamp = ROOT / "store" / f".evening-done-{day}"
    if stamp.exists():
        return 0  # already ran today
    held_marker = ROOT / "store" / f".evening-held-{day}"

    # Auto-connect the US VPN ([OWNER] uses Mullvad) so the geo-gate passes without him
    # toggling anything — the last human dependency in this lane (2026-07-11). Fail-soft:
    # if it can't, we still POST and the server's geo_check holds + notifies as before.
    try:
        import vpn
        v = vpn.ensure_us()
        print(f"evening chain: VPN {'US up (' + v.get('relay','') + ')' if v.get('ok') else 'NOT US: ' + v.get('detail','')}")
    except Exception as e:  # noqa: BLE001
        print(f"evening chain: vpn step skipped ({type(e).__name__})")

    # Fresh scan so tonight's applies include today's postings. Degrade to the existing
    # queue on any failure — the scan is a bonus, never a blocker.
    try:
        subprocess.run([str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "agents" / "jobs.py")],
                       cwd=str(ROOT), timeout=SCAN_TIMEOUT_S,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:  # noqa: BLE001
        print(f"evening chain: scan skipped ({type(e).__name__}), applying to the existing queue")

    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8765/api/launch/job_apply", data=b"{}", method="POST",
            headers={"X-Brain-Token": secret("brain_token"), "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        print("evening chain: server unreachable:", e)
        return 0  # no stamp: next tick retries

    if j.get("ran"):
        stamp.touch()
        planner.feed_add("jobs", "Evening apply chain started (auto, 7pm lane)")
        planner.notify("Evening apply chain running",
                       "Applying to the approved queue. Stop anytime from the dashboard.")
        print("evening chain: started")
        return 0
    err = (j.get("error") or j.get("note") or "").lower()
    if "not on a us ip" in err or "geo" in err:
        # VPN off: no stamp (each 10-min tick retries until 22:00); nag exactly once
        if not held_marker.exists():
            held_marker.touch()
            planner.notify("Evening applies held: VPN is off",
                           "Turn on the US VPN and tonight's apply chain starts on the next tick.")
        print("evening chain: held (not on a US IP); will retry within the window")
        return 0
    if "already running" in err or "no approved jobs" in err:
        # nothing for tonight (either the manual chain is mid-run, or the queue is empty
        # even after a fresh scan) — stamp so we don't rescan every 10 min for 3 hours
        stamp.touch()
        print(f"evening chain: done for today ({err})")
        return 0
    print("evening chain: not started:", j)
    return 0  # unknown failure: no stamp, next tick retries


if __name__ == "__main__":
    raise SystemExit(run())
