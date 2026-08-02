#!/usr/bin/env python3
"""Sent-links monitor (red-team attack 4): if prospects' proposal links go dark,
[OWNER] finds out in 30 minutes, not when a deal dies.

Probes ONE live link (newest sent-or-staged proposal) through the PUBLIC path every
run (rides reply_watch's 30-min cadence). Detects: tailscale down, funnel unconfigured,
server down, laptop-asleep symptoms. Alerts once per outage (state-flap guard).
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import planner  # noqa: E402
import proposal_factory  # noqa: E402
from store_lib import now_iso  # noqa: E402

STATE = ROOT / "store" / "link_monitor.json"


def _probe() -> tuple[str, str]:
    """Returns (status, detail). Status: up | funnel_down | ts_down | no_links | local_only."""
    live = [r for r in proposal_factory.load_queue()
            if r.get("status") in ("sent", "staged") and r.get("link")]
    if not live:
        return "no_links", "nothing staged or sent"
    url = live[-1]["link"]
    if "127.0.0.1" in url or "localhost" in url:
        return "local_only", "links point at localhost (public_base_url unset)"
    ts = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    try:
        st = subprocess.run([ts, "status"], capture_output=True, text=True, timeout=6)
        if "stopped" in (st.stdout + st.stderr).lower():
            return "ts_down", "tailscale is stopped"
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        req = urllib.request.Request(url, method="GET",
                                     headers={"User-Agent": "brain-link-monitor"})
        r = urllib.request.urlopen(req, timeout=12)
        if r.status == 200:
            return "up", url
        return "funnel_down", f"HTTP {r.status}"
    except Exception as e:  # noqa: BLE001
        return "funnel_down", str(e)[:100]


def run() -> str:
    status, detail = _probe()
    prev = {}
    try:
        prev = json.loads(STATE.read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    STATE.write_text(json.dumps({"status": status, "detail": detail, "ts": now_iso()}))
    if status in ("ts_down", "funnel_down", "local_only") and prev.get("status") != status:
        planner.notify("Proposal links are DARK",
                       f"Prospects cannot open sent links right now ({status}: {detail}). "
                       "Fix: MONEY-DAY.md press 0 (tailscale funnel commands).",
                       tags="rotating_light")
        planner.feed_add("system", f"link monitor: links dark ({status})")
    elif status == "up" and prev.get("status") not in ("up", None, ""):
        planner.notify("Proposal links are back", "Public links serving again.")
    print(f"link monitor: {status} ({detail[:70]})")
    return status


if __name__ == "__main__":
    run()
