#!/usr/bin/env python3
"""Energy blocks (#84) — histogram of when [OWNER] actually finishes things.

Why: store/feed.jsonl already logs a 'done' entry for every completed todo
(app writes "✓ <text>" or "removed: <text>" kinds), but nothing has ever
looked at WHEN those completions happen. This buckets every 'done' feed entry
by hour-of-day (local time, from the entry's own ts) and writes the histogram
plus the top-3 peak hours, raw signal for a future "schedule the hard stuff in
your peak hours" nudge. Pure python, no LLM call, no network.

Read-only against feed.jsonl; only write is store/energy.json (full overwrite
each run). Run standalone: .venv/bin/python agents/energy_blocks.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402

FEED = ROOT / "store" / "feed.jsonl"
OUT = ROOT / "store" / "energy.json"


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


def _done_hours() -> list[int]:
    hours = []
    for r in _read_jsonl(FEED):
        if r.get("kind") != "done":
            continue
        try:
            ts = datetime.fromisoformat(r.get("ts", ""))
        except ValueError:
            continue
        hours.append(ts.hour)
    return hours


def build_histogram() -> dict:
    hours = _done_hours()
    counts = Counter(hours)
    by_hour = {str(h): counts.get(h, 0) for h in range(24)}
    peak_hours = [h for h, _ in counts.most_common(3)]
    return {
        "generated": now_iso(),
        "total_done": len(hours),
        "by_hour": by_hour,
        "peak_hours": sorted(peak_hours),
    }


def main() -> int:
    data = build_histogram()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2))
    if data["total_done"] == 0:
        print(f"energy_blocks: no 'done' entries in feed.jsonl yet — wrote empty histogram -> {OUT}")
    else:
        peaks = ", ".join(f"{h:02d}:00" for h in data["peak_hours"]) or "none"
        print(f"energy_blocks: {data['total_done']} done entries, peak hour(s) {peaks} -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
