#!/usr/bin/env python3
"""A14 (FABLE-BUILD-QUEUE Section 5, MED): the multi-round stage coach.
When a job goes live (a human replied, an interview is booked, an offer
lands) the right next move is always the same playbook line, and it is
easiest to forget exactly when it matters. This stamps it on every live job.

WHAT: for each job in a live stage (status in the PLAYBOOK: replied /
      interview / offer), writes the static playbook next-step to
      store/stage_coach.json keyed by job_id, and surfaces ONE feed line
      total (a stage summary + the hottest job's line, not a line per job).
      The playbook is a STATIC dict in this file; no LLM, coaching this
      basic should not cost tokens or depend on a model's mood.
      Note: 'offer' is not a status any current record has reached; the
      mapping is ready for the day one does.
WHEN: daily, morning chain, after job_replies.py has flipped statuses.
      Pure local reads, sub-second.
RAILS: read-only against jobs.jsonl. Writes only its own JSON + at most one
      feed line per run. No pushes, no sends, no LLM. Fresh install (no
      jobs.jsonl) prints and exits 0 without writing.

Run:  .venv/bin/python agents/stage_coach.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402
import jobs  # noqa: E402

# ---- tunables ----
OUT = ROOT / "store" / "stage_coach.json"
# ordered hottest-first; the feed line leads with the hottest stage present
PLAYBOOK = {
    "offer": "Never accept same-day. Anchor [SALARY_ANCHOR], get it in writing, sleep on it.",
    "interview": "War room before the call, rehearse the top 2 STAR stories, follow-up day 5.",
    "replied": "Reply within 4h, propose 2 concrete slots, keep it under 5 sentences.",
}


def build(rows: list[dict]) -> dict:
    coach = {}
    for j in rows:
        status = j.get("status")
        if status not in PLAYBOOK or not j.get("id"):
            continue
        coach[j["id"]] = {"company": j.get("company") or "?",
                          "title": j.get("title") or "?",
                          "status": status, "line": PLAYBOOK[status]}
    return {"generated": now_iso(), "coach": coach}


def feed_line(coach: dict) -> str:
    """ONE line for the whole run: stage counts + the hottest job's play."""
    by_stage: dict[str, list[dict]] = {}
    for c in coach.values():
        by_stage.setdefault(c["status"], []).append(c)
    counts = ", ".join(f"{len(by_stage[s])} {s}" for s in PLAYBOOK if s in by_stage)
    for s in PLAYBOOK:  # hottest stage first (offer > interview > replied)
        if s in by_stage:
            top = by_stage[s][0]
            return f"Stage coach: {counts}. {top['company']}: {top['line']}"
    return f"Stage coach: {counts}"


def run(dry_run: bool = False) -> dict | None:
    rows = jobs.load_jobs()
    if not rows:
        print("stage_coach: no jobs.jsonl yet, nothing to coach")
        return None
    data = build(rows)
    coach = data["coach"]
    if not coach:
        print("stage_coach: no live-stage jobs (replied/interview/offer)")
        if not dry_run:
            OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        return data
    for jid, c in coach.items():
        print(f"  [{c['status']:<9}] {c['company']:<20} {c['line']}")
    line = feed_line(coach)
    if dry_run:
        print(f"[dry-run] would write {OUT} + one feed line: {line}")
        return data
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    try:
        planner.feed_add("jobs", line[:180])
    except Exception:  # noqa: BLE001 - feed hiccup must not fail the coach
        pass
    print(f"stage_coach: {len(coach)} job(s) coached -> {OUT}")
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description="Static next-step coaching per live-stage job")
    ap.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    args = ap.parse_args()
    if args.dry_run:
        run(dry_run=True)
        return 0
    from runlog import track
    with track("stage_coach"):
        run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
