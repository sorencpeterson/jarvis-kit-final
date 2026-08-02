#!/usr/bin/env python3
"""Operator-run transcript reader — A20.

The WRITE side of A20 lives in agents/prompts/li_operator_brief.md (Step 2.5):
every operator run appends one summary line to store/li_operator_runs.jsonl.
This module is the READ side: a small aggregator over that log so "what has
the Chrome operator actually been doing" is one function call, not a manual
jsonl scan. [E] until an operator run actually produces rows — see
summary()'s empty-state handling, same honest-zero pattern as li_digest.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

RUNS = ROOT / "store" / "li_operator_runs.jsonl"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def load_runs() -> list[dict]:
    return _read_jsonl(RUNS)


def summary(n: int = 10) -> dict:
    """Last n runs + rollup totals. Empty state is honest (0s, not invented)
    per this system's no-invented-data rule."""
    runs = load_runs()
    recent = runs[-n:]
    return {
        "total_runs": len(runs),
        "recent": recent,
        "totals": {
            "done": sum(r.get("done", 0) or 0 for r in runs),
            "skipped": sum(r.get("skipped", 0) or 0 for r in runs),
            "accepted_captured": sum(r.get("accepted_captured", 0) or 0 for r in runs),
        },
        "flagged_notes": [r.get("notes") for r in recent if r.get("notes")],
    }


if __name__ == "__main__":
    s = summary()
    print(json.dumps(s, indent=2, ensure_ascii=False))
