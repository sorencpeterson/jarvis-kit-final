#!/usr/bin/env python3
"""Weekly model bake-off (#42): same task to two tiers, outputs stored side by side
so the Sunday retro (and [OWNER]) can judge whether the cheap tier is good enough.
Read-only otherwise; nothing outward."""
from __future__ import annotations
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402

TASK = ("Summarize in 3 sentences what [OWNER]'s automation system should focus on next week, "
        "given: pipeline flat, cold campaigns awaiting publish, 0 warm calls worked. Direct voice, no em-dashes.")

def run():
    outs = {}
    models = planner._models()
    for label, feature in (("cheap", "default"), ("strong", "content")):
        outs[label] = {"model": models.get(feature, "?"),
                       "out": (planner._cli(TASK, timeout=90, feature=feature) or "")[:500]}
    with (ROOT / "store" / "bakeoff.jsonl").open("a") as f:
        f.write(json.dumps({"ts": now_iso(), **outs}, ensure_ascii=False) + "\n")
    print("bakeoff: wrote cheap-vs-strong sample -> store/bakeoff.jsonl")
    return 0

if __name__ == "__main__":
    raise SystemExit(run())
