#!/usr/bin/env python3
"""Best-posting-time scaffold (#82) — eventually this should say which hour of
day [OWNER]'s posts actually perform best, but that needs engagement data (likes/
comments/impressions) joined back onto each post, and nothing pulls that back
from LinkedIn yet.

Rather than fake a recommendation off zero signal, this is honest collecting
machinery: it tallies WHEN posts have gone out so far (a hour-of-day histogram),
writes it with status 'collecting', and says plainly what's still missing. Once
something reads engagement back onto content/posts.jsonl (see content_readback.py
for the shape that would need to grow an engagement field), a later pass here can
join hour -> engagement and promote status to 'ready'.

Read-only against content/posts.jsonl; writes store/besttime.json (full overwrite
each run, no CLI call needed). Run standalone: .venv/bin/python agents/besttime.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from store_lib import now_iso  # noqa: E402

POSTS = ROOT / "content" / "posts.jsonl"
OUT = ROOT / "store" / "besttime.json"


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


def _load_posted() -> list[dict]:
    """last-write-wins by id (same discipline as repurpose.py / content_readback.py),
    filtered to status == 'posted'."""
    by_id: dict[str, dict] = {}
    for r in _read_jsonl(POSTS):
        if r.get("id"):
            by_id[r["id"]] = r
    return [r for r in by_id.values() if r.get("status") == "posted"]


def _hour_histogram(posted: list[dict]) -> dict[str, int]:
    hours = Counter()
    for r in posted:
        ts = r.get("posted_at") or ""
        try:
            # posted_at is stored as an ISO string (often with a trailing Z from
            # the GHL/LinkedIn readback); fromisoformat needs +00:00 not Z.
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        hours[f"{dt.astimezone().hour:02d}:00"] += 1
    return dict(sorted(hours.items()))


def build_state() -> dict:
    posted = _load_posted()
    return {
        "status": "collecting",
        "generated": now_iso(),
        "posted_count": len(posted),
        "posted_hours": _hour_histogram(posted),
        "note": "engagement join pending readback of likes — this is only a "
                "posting-time histogram, not a performance recommendation yet.",
    }


def main() -> int:
    state = build_state()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(state, indent=2))
    print(f"besttime: {state['status']} — {state['posted_count']} posted, "
          f"{len(state['posted_hours'])} distinct hour(s) seen -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
