#!/usr/bin/env python3
"""B87 + B94 + B93: three thread/task-level passes over already-classified mail
(reads store/mail_triage.jsonl, never re-hits Gmail search — only per-thread metadata
fetches for threads that clear the length bar).

B87 thread summarizer: threads with >5 messages get a 2-line summary, written to
  store/mail_thread_summaries.jsonl (thread-level state per B112, not message-level).

B94 email->task extraction: "can you / please / by Friday" style asks in
  response_needed-lane bodies -> suggestion records in store/mail_task_suggestions.jsonl
  (a SUGGESTIONS store, not auto-added to store/todos.jsonl — that store belongs to
  the inbox-capture lane, not this fleet, and B94 is explicit that this is a
  suggestions list [OWNER] approves, mirroring B97's auto-archive-suggestions-only rule).

B93 ghosted-thread resurrector: threads where the LAST message was sent BY [OWNER]
  (in:sent, so no reply came back) more than 7 days ago and the thread looks
  business-relevant (not a newsletter/receipt sender) -> a follow-up DRAFT in
  store/mail_drafts.jsonl (same store/shape mail_drafts.py writes, so the comms
  surface only needs to read one drafts file regardless of which agent produced it).

READ-ONLY against Gmail (thread metadata + occasional full-body fetch for context).
No sends, no label writes in this module.

Run:  .venv/bin/python agents/mail_threads.py            # all three passes, real data
      .venv/bin/python agents/mail_threads.py --skip-ghost
      .venv/bin/python agents/mail_threads.py --fixture   # sample data, no Gmail calls
"""
from __future__ import annotations

import argparse
import os
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", Path(os.environ.get("GMAIL_LIB") or (ROOT / "gmail"))):
    sys.path.insert(0, str(p))
from store_lib import now_iso, humanize, voice_spec, _flock  # noqa: E402
import planner  # noqa: E402
import gmail_api  # noqa: E402
from runlog import track  # noqa: E402

TRIAGE = ROOT / "store" / "mail_triage.jsonl"
SUMMARIES = ROOT / "store" / "mail_thread_summaries.jsonl"
TASKS = ROOT / "store" / "mail_task_suggestions.jsonl"
DRAFTS = ROOT / "store" / "mail_drafts.jsonl"  # shared with mail_drafts.py, same shape

MIN_THREAD_LEN = 5
GHOST_DAYS = 7
TASK_PHRASES = ("can you", "could you", "please", "would you mind", "need you to",
                 "by friday", "by monday", "by eod", "by end of day", "asap", "deadline")


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


def _append(path: Path, rec: dict, key: str = "id") -> bool:
    """Append under store_lib._flock with the duplicate check repeated INSIDE the lock
    on `key`. Each pass computes its 'already' set up front, then spends seconds-to-
    minutes in LLM/Gmail calls before writing — stale enough for a concurrent run (or
    mail_drafts.py, which shares the drafts store) to double-write; a live duplicate
    draft was reproduced from that window (2026-07 P0). Returns False on duplicate."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _flock(path):
        if rec.get(key) in {r.get(key) for r in _read_jsonl(path)}:
            return False
        with path.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def _triaged_threads() -> dict[str, list[dict]]:
    by_thread: dict[str, list[dict]] = {}
    for r in _read_jsonl(TRIAGE):
        tid = r.get("thread_id")
        if tid:
            by_thread.setdefault(tid, []).append(r)
    return by_thread


# --- B87: thread summarizer -------------------------------------------------

SUMMARY_PROMPT = """Summarize this email thread in EXACTLY 2 lines, plain text, no
preamble, no em-dashes:
line 1: what the thread is about / who's involved
line 2: current status or the obvious next step

THREAD (oldest first):
%s"""


def _format_thread_for_summary(msgs: list[dict]) -> str:
    lines = []
    for m in msgs:
        lines.append(f"From: {m.get('from','')[:50]} | Subject: {m.get('subject','')[:70]} | "
                      f"{(m.get('snippet','') or '')[:160]}")
    return "\n".join(lines)


def summarize_threads(fixture: bool = False) -> int:
    already = {r["thread_id"] for r in _read_jsonl(SUMMARIES) if r.get("thread_id")}
    if fixture:
        candidates = {"fx-thread-1": [
            {"from": "a@b.com", "subject": "Project kickoff", "snippet": "Let's start Monday"},
            {"from": "b@a.com", "subject": "Re: Project kickoff", "snippet": "Sounds good, I'll send scope"},
            {"from": "a@b.com", "subject": "Re: Project kickoff", "snippet": "Scope looks right"},
            {"from": "b@a.com", "subject": "Re: Project kickoff", "snippet": "Starting build now"},
            {"from": "a@b.com", "subject": "Re: Project kickoff", "snippet": "Any ETA?"},
            {"from": "b@a.com", "subject": "Re: Project kickoff", "snippet": "Thursday, on track"},
        ]}
    else:
        by_thread = _triaged_threads()
        long_threads = {tid: recs for tid, recs in by_thread.items()
                         if len(recs) > MIN_THREAD_LEN and tid not in already}
        candidates = {}
        for tid in long_threads:
            msgs = gmail_api.get_thread_metadata(tid)
            if len(msgs) > MIN_THREAD_LEN:
                candidates[tid] = msgs
    if fixture and "fx-thread-1" in already:
        return 0

    n = 0
    for tid, msgs in candidates.items():
        formatted = _format_thread_for_summary(msgs)
        summary = planner._cli(SUMMARY_PROMPT % formatted, timeout=100, feature="plan")
        summary = humanize((summary or "").strip())
        if not summary:
            continue
        if _append(SUMMARIES, {"thread_id": tid, "message_count": len(msgs),
                                "summary": summary, "ts": now_iso()}, key="thread_id"):
            n += 1
    return n


# --- B94: email -> task extraction ------------------------------------------

def extract_tasks(fixture: bool = False) -> int:
    already = {r["id"] for r in _read_jsonl(TASKS) if r.get("id")}
    if fixture:
        rows = [{"id": "fxt1", "sender_email": "boss@example.com",
                  "subject": "Quick ask", "why": "test",
                  "_body": "Can you send me the report by Friday? Also please cc Dana."}]
    else:
        triage = [r for r in _read_jsonl(TRIAGE)
                  if r.get("lane") in ("response_needed", "vip", "business")
                  and r.get("id") not in already]
        rows = triage

    n = 0
    for r in rows:
        text = r.get("_body") if fixture else None
        if text is None:
            try:
                full = gmail_api.get_message(r["id"])
                text = (full.get("subject", "") + " " + full.get("body", ""))[:1500]
            except Exception:
                continue
        lo = text.lower()
        if not any(p in lo for p in TASK_PHRASES):
            continue
        # cheap extraction: the sentence containing the trigger phrase, not a full LLM
        # call per message (B94 doesn't need language-model depth, just a phrase hit +
        # the surrounding sentence for context — keeps this pass free/instant).
        sentences = re.split(r"(?<=[.!?])\s+", text)
        hit_sentence = next((s.strip() for s in sentences
                              if any(p in s.lower() for p in TASK_PHRASES)), text[:160])
        if _append(TASKS, {
            "id": r["id"],
            "from": r.get("sender_email", r.get("from", "")),
            "subject": r.get("subject", ""),
            "suggested_task": hit_sentence[:200],
            "status": "suggested",  # [OWNER] approves -> a future click promotes to todos
            "created": now_iso(),
        }):
            n += 1
    return n


# --- B93: ghosted-thread resurrector -----------------------------------------

GHOST_EXCLUDE_HINTS = ("noreply", "no-reply", "notification", "newsletter", "unsubscribe")


def _is_business_relevant(sender_email: str) -> bool:
    lo = (sender_email or "").lower()
    return not any(h in lo for h in GHOST_EXCLUDE_HINTS)


def _parse_internal_date(ms_str: str) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(ms_str) / 1000, tz=timezone.utc)
    except (ValueError, TypeError):
        return None


def _has_inbound_reply_after(thread_msgs: list[dict], sent_dt: datetime, sent_id: str) -> bool:
    """True when any INBOUND message in the thread arrived after [OWNER]'s send.

    Fixes the D4 reply-detection defects the old inline any() had:
    (1) [OWNER]'s own later messages (labelIds carries SENT) counted as "replies",
        so a thread where he followed up himself twice looked answered;
    (2) a thread message with a missing/unparseable internalDate fell back to
        `cutoff` (now - GHOST_DAYS), which compares as after sent_dt for every
        candidate, silently marking genuinely-ghosted threads as replied-to.
    A message with no parseable date now counts as NOT a reply (can't prove it
    came after the send), and only non-SENT messages count as inbound."""
    for tm in thread_msgs:
        if tm.get("id") == sent_id:
            continue
        if "SENT" in (tm.get("labelIds") or []):
            continue  # his own message, not a reply from them
        tm_dt = _parse_internal_date(tm.get("internalDate", ""))
        if tm_dt is not None and tm_dt > sent_dt:
            return True
    return False


RESURRECT_PROMPT = """Write a brief, low-pressure follow-up email from [OWNER] checking
back in on a thread he sent and never heard back on. Not pushy, no guilt-tripping.

VOICE RULES (hard, non-negotiable):
%s

ORIGINAL LAST MESSAGE (sent by [OWNER], %s days ago, no reply since):
Subject: %s
Body: %s

Output ONLY the reply body text, under 90 words, no subject line, no signature block."""


def resurrect_ghosted(fixture: bool = False) -> int:
    already_drafted = {r["id"] for r in _read_jsonl(DRAFTS) if r.get("id")}
    if fixture:
        candidates = [{"id": "fxg1", "sender_email": "prospect@example.com",
                        "subject": "Following up on the proposal", "days_ago": 9,
                        "_body": "Hey, wanted to follow up on the proposal I sent over. Let me know if you have questions."}]
    else:
        sent_ids = [m["id"] for m in gmail_api.search("in:sent newer_than:60d", 60)]
        meta = gmail_api.get_messages_metadata(sent_ids, fields=("To", "Subject"))
        cutoff = datetime.now(timezone.utc) - timedelta(days=GHOST_DAYS)
        candidates = []
        for m in meta:
            if m["id"] in already_drafted:
                continue
            sent_dt = _parse_internal_date(m.get("internalDate", ""))
            if not sent_dt or sent_dt > cutoff:
                continue
            to_email = m.get("to", "")
            if not _is_business_relevant(to_email):
                continue
            # "no reply since" check: does the thread have any inbound after this send?
            thread_msgs = gmail_api.get_thread_metadata(m.get("threadId", m["id"]))
            if not thread_msgs:
                continue  # thread fetch failed; can't prove it's ghosted, so don't draft
            if _has_inbound_reply_after(thread_msgs, sent_dt, m["id"]):
                continue
            candidates.append({"id": m["id"], "sender_email": to_email,
                                "subject": m.get("subject", ""),
                                "days_ago": (datetime.now(timezone.utc) - sent_dt).days})

    n = 0
    for c in candidates:
        if fixture:
            body = "Just checking back in. No pressure. Let me know if you want to move forward."
        else:
            try:
                full = gmail_api.get_message(c["id"])
                body_text = full.get("body", "")[:1000]
            except Exception:
                body_text = c.get("subject", "")
            prompt = RESURRECT_PROMPT % (voice_spec(1800), c["days_ago"],
                                          c.get("subject", ""), body_text)
            out = planner._cli(prompt, timeout=110, feature="reply")
            body = humanize((out or "").strip())
            if not body:
                continue
        subj = c.get("subject", "")
        if _append(DRAFTS, {
            "id": c["id"],
            "thread_id": c["id"],
            "to": c.get("sender_email", ""),
            "subject": subj if subj.lower().startswith("re:") else f"Re: {subj}",
            "draft": body,
            "context": f"ghosted thread resurrector: no reply {c['days_ago']}d after his last send",
            "status": "pending",
            "created": now_iso(),
        }):
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--skip-ghost", action="store_true", help="skip the ghosted-thread pass (heavier: N sent-mail thread fetches)")
    ap.add_argument("--fixture", action="store_true")
    args = ap.parse_args()

    with track("mail_threads"):
        summarized = summarize_threads(fixture=args.fixture)
        tasks = extract_tasks(fixture=args.fixture)
        ghosts = 0 if args.skip_ghost else resurrect_ghosted(fixture=args.fixture)

    print(f"mail_threads: {summarized} thread summary(ies) -> {SUMMARIES}")
    print(f"mail_threads: {tasks} task suggestion(s) -> {TASKS}")
    print(f"mail_threads: {ghosts} ghosted-thread draft(s) -> {DRAFTS}")
    if summarized or tasks or ghosts:
        planner.feed_add("agent", "Mail threads pass",
                          f"summaries={summarized} tasks={tasks} ghost_drafts={ghosts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
