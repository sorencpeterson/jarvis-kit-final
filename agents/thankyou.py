#!/usr/bin/env python3
"""Interview thank-you drafts (#71) — a thank-you note within a day of an interview
is basic hygiene that's easy to let slip once the adrenaline's gone. This drafts
one automatically so it's ready the moment [OWNER] wants to send it.

Scans jobs at status 'interview' (same signal interview_prep.py uses) and drafts a
short thank-you email per job that hasn't been drafted yet. Deliberately does NOT
flip jobs.py status or touch the jobs store at all — "drafted" state lives entirely
inside store/thankyou_drafts.jsonl (the set of job_ids already present there), so
this stays a pure side-channel with zero risk of colliding with jobs.py's own status
machine.

Read-only against store/jobs.jsonl; writes are store/thankyou_drafts.jsonl (append)
+ one todo per new draft. Run standalone: .venv/bin/python agents/thankyou.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import append_todo, humanize, new_id, now_iso  # noqa: E402
import planner  # noqa: E402
import jobs  # noqa: E402

DRAFTS = ROOT / "store" / "thankyou_drafts.jsonl"


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


def _already_drafted() -> set[str]:
    """The set of job_ids drafted so far — the drafted-flag lives here, not on the
    job record, so a job can go through interview -> offer -> etc without this
    agent ever writing to jobs.jsonl."""
    return {r.get("job_id") for r in _read_jsonl(DRAFTS) if r.get("job_id")}


PROMPT = """[OWNER] just interviewed for this role:

TITLE: %s
COMPANY: %s

Draft a short, genuine thank-you email he could send today. Keep it tight (under
120 words): thank them for their time, reference the role/company specifically
(don't be generic), reaffirm interest in one sentence, no groveling. Direct, human.
Use commas or periods for pauses, never a dash. Include a subject line on the
first line prefixed "Subject:", then a blank line, then the email body. Output
ONLY that: no notes, no alternates, no commentary about these instructions."""


def _drop_trailing_commentary(raw: str) -> str:
    """Defensive parse: models occasionally break format and append meta-commentary
    or a second "corrected" attempt after the actual draft (seen in testing on the
    em-dash instruction specifically). The email body legitimately contains blank
    lines (subject/body separator, paragraph breaks), so this only cuts at an
    explicit self-referential marker rather than at the first blank line."""
    text = (raw or "").strip()
    for marker in ("(Note", "(note", "Wait,", "Here's the corrected",
                  "Here's an alternative", "*(", "[Note"):
        i = text.find(marker)
        if i > 0:
            text = text[:i].strip()
    return text


def build_drafts() -> list[dict]:
    interviews = [j for j in jobs.load_jobs() if j.get("status") == "interview"]
    covered = _already_drafted()
    out = []
    for j in interviews:
        jid = j.get("id")
        if not jid or jid in covered:
            continue
        draft = planner._cli(PROMPT % (j.get("title") or "?", j.get("company") or "?"),
                             timeout=90, feature="content")
        draft = humanize(_drop_trailing_commentary(draft or ""))
        if not draft:
            continue
        out.append({"job_id": jid, "company": j.get("company") or "?",
                    "title": j.get("title") or "?", "draft": draft, "ts": now_iso()})
        covered.add(jid)
    return out


def main() -> int:
    drafts = build_drafts()
    if not drafts:
        print("thankyou: no interview jobs need a thank-you draft")
        return 0
    DRAFTS.parent.mkdir(parents=True, exist_ok=True)
    with DRAFTS.open("a") as f:
        for rec in drafts:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    for rec in drafts:
        append_todo({
            "id": new_id("thankyou_" + rec["job_id"]),
            "text": f"Send thank-you to {rec['company']} (draft ready)",
            "status": "inbox", "created": now_iso(), "source": "thankyou", "source_ref": rec["job_id"],
            "project": None, "priority": 1, "scheduled_time": None, "duration_min": None,
            "gcal_event_id": None, "notes": None,
        })
    planner.feed_add("agent", f"Thank-you drafts ready: {', '.join(r['company'] for r in drafts)}")
    print(f"thankyou: drafted {len(drafts)} thank-you note(s) -> {DRAFTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
