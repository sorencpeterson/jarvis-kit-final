#!/usr/bin/env python3
"""ATS source stats — which job boards actually convert vs. which are a black hole.

Why: jobs.py applies through a handful of ATS sources (ashby, workable, lever,
etc.) but nothing rolls up conversion BY source, so a source that's 0-for-30 on
confirmations looks the same as one converting well until someone manually greps.
This groups jobs.jsonl by source and flags any source with a real sample size
(>=10 submitted) and zero confirmations as a blacklist candidate, matching the
knob agents/retro.py already knows how to propose (job_blacklist_source).

"submitted" here = jobs.jsonl status "applied" (the literal value the sourcing
pipeline writes); "confirmed"/"interview" are read as their own literal statuses
since jobs.jsonl status is last-write-wins per id, never double counted.

Read-only against store/jobs.jsonl; only write is store/ats_stats.json (full
overwrite each run) plus a feed_add IF a blacklist candidate is found.
Run standalone: .venv/bin/python agents/atsstats.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
import planner  # noqa: E402
from store_lib import now_iso  # noqa: E402

JOBS = ROOT / "store" / "jobs.jsonl"
OUT = ROOT / "store" / "ats_stats.json"
# jobs.jsonl's own status vocabulary; "submitted" bucket = applied (what the
# sourcing pipeline actually writes once it fires off an application).
SUBMITTED_STATUSES = {"applied", "confirmed", "interview"}
BLACKLIST_MIN_SUBMITTED = 10


def _read_jobs() -> list[dict]:
    if not JOBS.exists():
        return []
    out = []
    for line in JOBS.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def build_stats() -> dict:
    # last-write-wins by id, same discipline as store_lib.load_todos
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for rec in _read_jobs():
        rid = rec.get("id")
        if not rid:
            continue
        if rid not in by_id:
            order.append(rid)
        by_id[rid] = rec
    jobs = [by_id[i] for i in order]

    per_source = defaultdict(lambda: {"submitted": 0, "confirmed": 0, "interview": 0, "total": 0})
    for j in jobs:
        src = j.get("source") or "unknown"
        status = j.get("status")
        d = per_source[src]
        d["total"] += 1
        if status in SUBMITTED_STATUSES:
            d["submitted"] += 1
        if status == "confirmed":
            d["confirmed"] += 1
        if status == "interview":
            d["interview"] += 1

    sources = {}
    blacklist_candidates = []
    for src, d in per_source.items():
        submitted = d["submitted"]
        confirm_rate = round(d["confirmed"] / submitted, 3) if submitted else None
        interview_rate = round(d["interview"] / submitted, 3) if submitted else None
        sources[src] = {
            "submitted": submitted, "confirmed": d["confirmed"], "interview": d["interview"],
            "total": d["total"], "confirm_rate": confirm_rate, "interview_rate": interview_rate,
        }
        if submitted >= BLACKLIST_MIN_SUBMITTED and d["confirmed"] == 0:
            blacklist_candidates.append(src)

    return {"generated": now_iso(), "sources": sources, "blacklist_candidates": blacklist_candidates}


def main() -> int:
    stats = build_stats()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(stats, indent=2))
    n_sources = len(stats["sources"])
    print(f"atsstats: {n_sources} source(s) -> {OUT}")
    for src in stats["blacklist_candidates"]:
        line = (f"blacklist suggestion: {src} has >= {BLACKLIST_MIN_SUBMITTED} submitted "
                "with 0 confirmed, consider job_blacklist_source")
        print(line)
        planner.feed_add("warn", f"ATS blacklist candidate: {src}",
                         f"{stats['sources'][src]['submitted']} submitted, 0 confirmed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
