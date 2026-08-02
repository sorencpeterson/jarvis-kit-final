#!/usr/bin/env python3
"""Competitor watch (#66): weekly WebSearch pass over named competitors; one-line
deltas land in insights for the Sunday retro. Read-only; skips cleanly with no list."""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402

def run():
    comps = planner._config().get("competitors") or []
    if not comps:
        print("competitor watch: set config competitors:[names] to enable")
        return 0
    cli = planner._find_claude_cli()
    if not cli:
        return 0
    prompt = ("Use WebSearch briefly. For each competitor, ONE line: anything new in the last month "
              "(pricing, positioning, offer) or 'no visible change'. Competitors: " + ", ".join(comps[:5])
              + ". Plain lines, no em-dashes.")
    try:
        out = subprocess.run(["perl", "-e", "alarm 170; exec @ARGV", cli, "-p", prompt,
                              "--model", "claude-haiku-4-5-20251001", "--allowedTools", "WebSearch"],
                             capture_output=True, text=True, timeout=190, cwd="/tmp").stdout.strip()
    except Exception:  # noqa: BLE001
        out = ""
    if out:
        with (ROOT / "store" / "insights.jsonl").open("a") as f:
            f.write(json.dumps({"ts": now_iso(), "text": "Competitor watch:\n" + out[:900]}) + "\n")
        planner.feed_add("agent", "Competitor watch updated")
    print("competitor watch:", "wrote" if out else "no output")
    return 0

if __name__ == "__main__":
    raise SystemExit(run())
