#!/usr/bin/env python3
"""B96: digest email quality — build a mail digest that shows WHY each item matters,
not just a list of subjects. Reads store/mail_triage.jsonl (already classified),
store/mail_thread_summaries.jsonl (long threads get their summary instead of a raw
subject), store/mail_drafts.jsonl (flags which response_needed items already have a
draft ready), store/mail_task_suggestions.jsonl (extracted asks).

BRIEF-INTEGRATION CONTRACT (documented here per the mission — this module does NOT
edit agents/daily_brief.py, that file is owned by whoever built the brief lane):
  build() returns a dict:
    {"date": "YYYY-MM-DD", "top_line": "<one sentence, the single most important
      mail-related thing right now, or '' if nothing urgent>",
     "sections": {"vip": [...], "response_needed": [...], "business": [...],
                  "jobs": [...], "receipts": [...], "newsletter_count": N,
                  "flags": {"tone_risky": N, "legal_flagged": N,
                            "dup_subject_threads": N, "line": "<preformatted
                            one-liner, '' when all three are zero>"}},
     "generated": "<iso ts>"}
  The "flags" block (D4) surfaces the tone_flag/legal_flag/dup_subject signals
  mail_brain.py computes per triage record; before this they were written to the
  store and read by nothing. Anything already reading store/mail_digest.json
  (daily_brief.py's _mail_line among them) gets them for free.
  Each item in a section list is {"id","from","subject","why","deadline","draft_ready"}
  (draft_ready=true means store/mail_drafts.jsonl already has a pending draft for it,
  so a UI can show a "review draft" action instead of "needs a reply from scratch").
  A future daily_brief.py could read store/mail_digest.json (this module's write
  target) and fold top_line + a 1-2 line summary of sections into the morning brief
  text, the same way it already reads store/attention.json for HIS TOP MOVE. That
  wiring is a one-line addition on their side; not made here since app/daily_brief.py
  isn't in this fleet's file list.

Newsletter lane is deliberately collapsed to a COUNT, never itemized (B85: newsletters
go to a digest pile, never treated as individual important items).

READ-ONLY (reads local JSONL stores only, no Gmail calls). Only write is
store/mail_digest.json.

Run:  .venv/bin/python agents/mail_digest.py             # real digest from real triage data
      .venv/bin/python agents/mail_digest.py --fixture    # sample triage rows, no store deps
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import now_iso, humanize, LOCAL_TZ  # noqa: E402
import planner  # noqa: E402
from runlog import track  # noqa: E402
from datetime import datetime  # noqa: E402

TRIAGE = ROOT / "store" / "mail_triage.jsonl"
SUMMARIES = ROOT / "store" / "mail_thread_summaries.jsonl"
DRAFTS = ROOT / "store" / "mail_drafts.jsonl"
OUT = ROOT / "store" / "mail_digest.json"

ITEM_SECTIONS = ("vip", "response_needed", "business", "jobs")
MAX_PER_SECTION = 8


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


def _latest_by_id(records: list[dict], key: str = "id") -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    for r in records:
        rid = r.get(key)
        if rid:
            by_id[rid] = r  # later lines win, same convention as store_lib.load_todos
    return by_id


def _summaries_by_thread() -> dict[str, str]:
    return {r["thread_id"]: r["summary"] for r in _read_jsonl(SUMMARIES) if r.get("thread_id")}


def _drafted_ids() -> set[str]:
    return {r["id"] for r in _read_jsonl(DRAFTS) if r.get("id") and r.get("status") == "pending"}


def build(fixture: bool = False) -> dict:
    if fixture:
        triage_rows = [
            {"id": "d1", "thread_id": "t1", "lane": "vip", "sender_email": "vip@example.com",
             "from": "VIP Person <vip@example.com>", "subject": "Need this by Friday",
             "why": "VIP sender with a deadline", "deadline": "by Friday",
             "response_needed": True, "classified_at": now_iso()},
            {"id": "d2", "thread_id": "t2", "lane": "response_needed",
             "sender_email": "client@example.com", "from": "Client <client@example.com>",
             "subject": "Question about invoice", "why": "direct question", "deadline": None,
             "response_needed": True, "classified_at": now_iso()},
            {"id": "d3", "thread_id": "t3", "lane": "newsletter", "sender_email": "n@example.com",
             "from": "Newsletter <n@example.com>", "subject": "Weekly roundup", "why": "bulk mail",
             "deadline": None, "response_needed": False, "classified_at": now_iso()},
            {"id": "d4", "thread_id": "t4", "lane": "jobs", "sender_email": "no-reply@ashbyhq.com",
             "from": "Hiring <no-reply@ashbyhq.com>", "subject": "Application received",
             "why": "ATS confirmation", "deadline": None, "response_needed": False,
             "classified_at": now_iso()},
        ]
    else:
        triage_rows = list(_latest_by_id(_read_jsonl(TRIAGE)).values())

    thread_summaries = _summaries_by_thread()
    drafted = _drafted_ids()

    sections: dict[str, list] = {s: [] for s in ITEM_SECTIONS}
    newsletter_count = 0
    receipts_count = 0

    triage_rows.sort(key=lambda r: r.get("classified_at", ""), reverse=True)

    for r in triage_rows:
        lane = r.get("lane", "business")
        if lane == "newsletter":
            newsletter_count += 1
            continue
        if lane == "receipts":
            receipts_count += 1
            continue
        if lane == "noise":
            continue
        if lane not in ITEM_SECTIONS:
            continue
        if len(sections[lane]) >= MAX_PER_SECTION:
            continue
        why = thread_summaries.get(r.get("thread_id", ""), r.get("why", ""))
        sections[lane].append({
            "id": r["id"],
            "from": r.get("sender_email", r.get("from", "")),
            "subject": r.get("subject", ""),
            "why": why,
            "deadline": r.get("deadline"),
            "draft_ready": r["id"] in drafted,
        })

    # D4: surface the computed-but-unread flags as one counts block. Counted over
    # ALL latest-by-id triage rows (not just the capped section items) so a
    # legal-flagged mail buried past MAX_PER_SECTION still shows in the count.
    flags = {
        "tone_risky": sum(1 for r in triage_rows if r.get("tone_flag")),
        "legal_flagged": sum(1 for r in triage_rows if r.get("legal_flag")),
        "dup_subject_threads": sum(1 for r in triage_rows if r.get("dup_subject")),
    }
    flags["line"] = ("" if not (flags["tone_risky"] or flags["legal_flagged"]
                                or flags["dup_subject_threads"])
                     else f"{flags['tone_risky']} tone-risky, "
                          f"{flags['legal_flagged']} legal-flagged, "
                          f"{flags['dup_subject_threads']} duplicate-subject threads")

    # top_line: the single most important thing, preferring vip+deadline, then any
    # response_needed with a deadline, then just the first vip/response_needed item.
    top_line = ""
    urgent = [i for i in sections["vip"] + sections["response_needed"] if i.get("deadline")]
    if urgent:
        it = urgent[0]
        top_line = f"{it['from']} needs a reply ({it['deadline']}): {it['why']}"
    elif sections["vip"]:
        it = sections["vip"][0]
        top_line = f"VIP mail from {it['from']}: {it['why']}"
    elif sections["response_needed"]:
        it = sections["response_needed"][0]
        top_line = f"{it['from']} needs a reply: {it['why']}"

    digest = {
        "date": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d"),
        "top_line": humanize(top_line),
        "sections": {**sections, "newsletter_count": newsletter_count,
                     "receipts_count": receipts_count, "flags": flags},
        "generated": now_iso(),
    }
    if not fixture:
        # atomic tmp + os.replace: daily_brief.py reads this file; it must never
        # see a half-written JSON from a run in progress.
        tmp = OUT.with_suffix(OUT.suffix + ".tmp")
        tmp.write_text(json.dumps(digest, indent=2))
        os.replace(tmp, OUT)
    return digest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fixture", action="store_true")
    args = ap.parse_args()

    with track("mail_digest"):
        digest = build(fixture=args.fixture)

    total_items = sum(len(v) for k, v in digest["sections"].items() if isinstance(v, list))
    print(f"mail_digest: {total_items} item(s) across "
          f"{[k for k in ITEM_SECTIONS if digest['sections'][k]]}, "
          f"{digest['sections']['newsletter_count']} newsletter(s) collapsed, "
          f"{digest['sections']['receipts_count']} receipt(s) collapsed"
          f"{'' if args.fixture else f' -> {OUT}'}")
    if digest["top_line"]:
        print(f"  top_line: {digest['top_line']}")
    if digest["sections"]["flags"]["line"]:
        print(f"  flags: {digest['sections']['flags']['line']}")
    if total_items and not args.fixture:
        planner.feed_add("agent", "Mail digest built", digest["top_line"] or f"{total_items} item(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
