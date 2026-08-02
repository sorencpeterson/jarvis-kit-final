#!/usr/bin/env python3
"""A12 (FABLE-BUILD-QUEUE Section 5, MED): the interview post-mortem stager.
Interviews happen and the lessons evaporate; nothing in the system asks
"what worked, what to fix" after the call. This stages that ask, once, at
the right moment.

WHAT: for every job at status=interview whose war-room doc
      (store/war_room/<job_id>.md, built by interview_war_room.py) is older
      than WAIT_DAYS days (proxy for "the interview date has passed": the
      doc is assembled when the interview lands), stages:
        - ONE todo "Post-mortem: <company> - what worked, what to fix" into
          store/todos.jsonl (append under store_lib's _flock via append_todo,
          deduped forever by source_ref "postmortem_<job_id>")
        - a template at store/postmortems/<job_id>.md with the 5 prompt
          questions (never overwritten once it exists; his answers win)
      No LLM: the questions are fixed, his answers are the content.
WHEN: daily, morning chain, after interview_war_room.py. A job with no
      war-room doc is not due yet (the doc IS the interview marker).
RAILS: read-only against jobs.jsonl and war_room/. Writes only the todo line
      and its own template file. No pushes, no sends, no LLM. Fresh install
      (no jobs, no war rooms) prints and exits 0.

Run:  .venv/bin/python agents/interview_postmortem.py [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import append_todo, existing_source_refs, new_id, now_iso  # noqa: E402
import jobs  # noqa: E402

# ---- tunables ----
WAR_DIR = ROOT / "store" / "war_room"
OUT_DIR = ROOT / "store" / "postmortems"
TODOS = ROOT / "store" / "todos.jsonl"
WAIT_DAYS = 2.0  # war-room doc older than this = the interview has happened

QUESTIONS = [
    "What questions did they ask that I fumbled, and what is the better answer?",
    "Which story landed hardest, and which one fell flat?",
    "What did I learn about the role or team that changes my pitch?",
    "Did money come up, and did I hold the anchor without naming a number first?",
    "What is the ONE thing to fix before the next round (or the next company)?",
]


def _safe(jid: str) -> str:
    """Filesystem-safe file stem for a job id (ids carry ':' and arbitrary chars)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", jid)[:140]


def _doc_age_days(jid: str) -> float | None:
    """Age of the war-room doc in days, or None if it does not exist. Uses the SAME
    _safe() stem interview_war_room writes with (job ids carry ':'/arbitrary chars);
    if these two disagree the post-mortem never sees the doc and never fires."""
    p = WAR_DIR / (_safe(jid) + ".md")
    try:
        return max(0.0, (time.time() - p.stat().st_mtime) / 86400.0)
    except OSError:
        return None


def due_jobs() -> list[dict]:
    out = []
    for j in jobs.load_jobs():
        if j.get("status") != "interview" or not j.get("id"):
            continue
        age = _doc_age_days(j["id"])
        if age is not None and age > WAIT_DAYS:
            out.append(j)
    return out


def template(job: dict) -> str:
    company = job.get("company") or "?"
    lines = [
        f"# Post-mortem: {company}, {job.get('title') or '?'}",
        f"_staged {now_iso()[:16]}; fill this within a day of the interview, "
        "while it still stings or shines_",
        "",
    ]
    for i, q in enumerate(QUESTIONS, 1):
        lines += [f"## {i}. {q}", "", "(your answer)", ""]
    lines += ["## Next action", "", "(one concrete change, dated)", ""]
    return "\n".join(lines)


def stage(job: dict, refs: set[str], dry_run: bool = False) -> bool:
    """Stage the todo + template for one job. Returns True if the todo was new."""
    jid = job["id"]
    company = job.get("company") or "?"
    ref = f"postmortem_{jid}"
    md_path = OUT_DIR / (_safe(jid) + ".md")
    if ref in refs:
        # todo already staged ever; only backfill a missing template silently
        if not md_path.exists() and not dry_run:
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            md_path.write_text(template(job))
        return False
    text = f"Post-mortem: {company} - what worked, what to fix"
    if dry_run:
        print(f"[dry-run] would stage todo '{text}' + template {md_path}")
        return True
    rec = {"id": new_id(ref + text), "text": text, "status": "inbox",
           "created": now_iso(), "source": "interview_postmortem", "source_ref": ref,
           "project": None, "priority": 1, "scheduled_time": None,
           "duration_min": None, "gcal_event_id": None, "notes": None}
    append_todo(rec, TODOS)  # append_todo takes _flock itself
    if not md_path.exists():  # never clobber a template he already filled
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        md_path.write_text(template(job))
    print(f"interview_postmortem: staged '{text}' + {md_path}")
    return True


def run(dry_run: bool = False) -> list[str]:
    due = due_jobs()
    if not due:
        print(f"interview_postmortem: no interview with a war-room doc older than "
              f"{WAIT_DAYS:g}d, nothing due")
        return []
    refs = existing_source_refs(TODOS)
    staged = []
    for j in due:
        if stage(j, refs, dry_run=dry_run):
            staged.append(j.get("company") or j["id"])
    if not staged:
        print(f"interview_postmortem: {len(due)} due, all already staged (dedup by source_ref)")
    return staged


def main() -> int:
    ap = argparse.ArgumentParser(description="Stage post-interview post-mortem todos + templates")
    ap.add_argument("--dry-run", action="store_true", help="print what would stage, write nothing")
    args = ap.parse_args()
    if args.dry_run:
        run(dry_run=True)
        return 0
    from runlog import track
    with track("interview_postmortem"):
        run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
