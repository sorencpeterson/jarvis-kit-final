#!/usr/bin/env python3
"""B83/B99: response-needed detection (reads mail_brain.py's classify output) + voice-true
reply DRAFTS for every response_needed item, written to store/mail_drafts.jsonl.

DRAFTS ONLY. This module never sends anything — gmail_api.py has no send function at
all (by design, per the workspace's read-only-except-labels rail), so there is no send
path to accidentally call. Output contract (for whoever wires the comms surface):

  {"id": "<gmail message id>", "thread_id": "...", "to": "<sender email>",
   "subject": "Re: <original subject>", "draft": "<voice-true reply text>",
   "context": "<one-line why this needs a reply>", "status": "pending",
   "created": "<iso ts>"}

status starts "pending"; a future UI marks it "sent"/"dismissed" on [OWNER]'s click —
this module never re-drafts an id already present in the store (checked via last-write
id lookup, same convention as mail_triage.jsonl).

Uses store_lib.voice_spec() (the hard VOICE-SPEC.md rules: no em-dashes, short
sentences, contractions, no fluff) injected into every draft prompt, then runs
store_lib.humanize() on the output as a second hard filter (belt + suspenders, same
double-layer pattern planner.accept() already uses for the same reason: models ignore
the prompt instruction often enough that a mechanical filter has to backstop it).

READ-ONLY against Gmail (only reads gmail_api.get_message for the full body of items
that need a body-aware draft — mail_triage.jsonl's snippet is often too short to draft
a real reply from). Only write is store/mail_drafts.jsonl.

Run:  .venv/bin/python agents/mail_drafts.py             # real drafts from real triage data
      .venv/bin/python agents/mail_drafts.py --limit 10
      .venv/bin/python agents/mail_drafts.py --fixture   # draft from sample records, no Gmail
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", Path.home() / "Claude" / "gmail"):
    sys.path.insert(0, str(p))
from store_lib import now_iso, humanize, voice_spec, _flock  # noqa: E402
import planner  # noqa: E402
import gmail_api  # noqa: E402
from runlog import track  # noqa: E402

TRIAGE = ROOT / "store" / "mail_triage.jsonl"
DRAFTS = ROOT / "store" / "mail_drafts.jsonl"
DRAFT_LANES = ("response_needed", "vip")  # both lanes can carry a real question needing a reply


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
    return {r["id"] for r in _read_jsonl(DRAFTS) if r.get("id")}


def _append(rec: dict) -> bool:
    """Append under store_lib._flock with the duplicate check repeated INSIDE the lock.
    Three writers share mail_drafts.jsonl (this module, mail_threads.py's ghost pass,
    outbox.py's status writeback) and the candidate list here is computed minutes before
    the append (an LLM call sits in between), so the run-start id check goes stale — a
    LIVE duplicate draft was reproduced from exactly that window (2026-07 P0). Returns
    False when another writer got the id in first."""
    DRAFTS.parent.mkdir(parents=True, exist_ok=True)
    with _flock(DRAFTS):
        if rec["id"] in _already_drafted():
            return False
        with DRAFTS.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def _candidates(limit: int) -> list[dict]:
    """response_needed=true items from the triage store, not yet drafted, most recent
    classify wins per id (triage store can have re-classify appends in theory, even
    though the current classify pass skips already-triaged ids — this stays correct
    either way)."""
    by_id: dict[str, dict] = {}
    for r in _read_jsonl(TRIAGE):
        if r.get("id"):
            by_id[r["id"]] = r
    drafted = _already_drafted()
    out = [r for r in by_id.values()
           if r.get("response_needed") and r["id"] not in drafted
           and r.get("lane") in DRAFT_LANES]
    out.sort(key=lambda r: r.get("classified_at", ""), reverse=True)
    return out[:limit]


DRAFT_PROMPT = """Write a short reply email from [OWNER] ([OWNER_COMPANY] — white-label
web builds + agency ops for agencies) to the message below. Answer their actual question
or ask first, THEN anything else, if it's a real reply. If it's a legal/notice email,
draft a brief acknowledgment that he'll review and respond properly, not a full answer.

VOICE RULES (hard, non-negotiable):
%s

WHY THIS NEEDS A REPLY: %s

ORIGINAL MESSAGE:
From: %s
Subject: %s
Body: %s

NAME RULE (hard): if you greet by name, use the sender's display-name first name EXACTLY
as it appears in From:. If From: shows only an email address, use NO name at all. NEVER
derive or guess a name from an email address (alaa@ is not "Alex", jsmith@ is not "John").

Output ONLY the reply body text, no subject line, no "Dear/Hi [name]" boilerplate beyond
a natural greeting if warranted, no signature block (that's added separately). Under 90
words per the email calibration rule."""


def _fixture_candidates() -> list[dict]:
    return [
        {"id": "fxd1", "thread_id": "fxd1", "sender_email": "client@example.com",
         "from": "A Client <client@example.com>", "subject": "Can you resend the invoice?",
         "why": "Direct request to resend invoice", "lane": "response_needed"},
    ]


def draft_one(rec: dict, fixture: bool = False) -> dict | None:
    if fixture:
        body = "Hey. Attaching the invoice now. Let me know if the numbers look off."
    else:
        try:
            full = gmail_api.get_message(rec["id"])
        except Exception:
            full = {"body": rec.get("subject", ""), "from": rec.get("from", ""),
                     "subject": rec.get("subject", "")}
        prompt = DRAFT_PROMPT % (
            voice_spec(1800), rec.get("why", ""), full.get("from", rec.get("from", ""))[:80],
            full.get("subject", rec.get("subject", ""))[:100], (full.get("body", "") or "")[:1200])
        out = planner._cli(prompt, timeout=120, feature="reply")
        if not out:
            return None
        body = humanize(out.strip())
        if not body:
            return None

    subject = rec.get("subject", "")
    subj_out = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    return {
        "id": rec["id"],
        "thread_id": rec.get("thread_id", rec["id"]),
        "to": rec.get("sender_email", ""),
        "subject": subj_out,
        "draft": body,
        "context": rec.get("why", ""),
        "status": "pending",
        "created": now_iso(),
    }


def run(limit: int = 20, fixture: bool = False) -> dict:
    candidates = _fixture_candidates() if fixture else _candidates(limit)
    if not candidates:
        return {"drafted": 0}
    n = 0
    for rec in candidates:
        draft = draft_one(rec, fixture=fixture)
        if draft and _append(draft):
            n += 1
    return {"drafted": n}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--fixture", action="store_true")
    args = ap.parse_args()

    with track("mail_drafts"):
        result = run(limit=args.limit, fixture=args.fixture)

    print(f"mail_drafts: {result['drafted']} draft(s) written -> {DRAFTS}")
    if result["drafted"]:
        planner.feed_add("agent", f"Mail drafts ready: {result['drafted']} reply(ies) staged for review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
