#!/usr/bin/env python3
"""E401 (star pick): the attention router — ONE priority score across every
queue [OWNER] has, so "what actually matters right now" is a single ranked list
instead of six panels he has to mentally merge himself.

WHAT: reads store/replies.jsonl (pending warm/proposal replies), proposals.jsonl
      (staged, unsent proposals), jobs.jsonl (needs_manual), networking.jsonl
      (LinkedIn queue via networking.load_queue), and todos.jsonl (overdue
      scheduled items) directly (no HTTP, no server dependency) and scores every
      item with one weighted formula so they can be ranked against each other.
WHEN: run any time (morning chain, or ad hoc "what's my #1 thing"). Cheap, pure
      local reads, no LLM call, sub-second.
RAILS: read-only against every store it touches. Only write is store/attention.json
      (full overwrite each run). No GHL writes, no sends, no LLM calls needed.

Score weights (tune here, not scattered across the codebase):
  REPLY_BASE          = 40    base score for a pending reply needing approval
  REPLY_AGE_PER_HOUR  = 1.5   + per hour it's sat pending (capped, see AGE_CAP_HOURS)
  REPLY_INTENT_BONUS  = {"interested": 25, "objection": 15, "followup": 5, "remove": 20}
  PROPOSAL_STAGED_BASE= 25    base score for a staged-but-unsent proposal
  PROPOSAL_VALUE_DIV  = 200   + price/PROPOSAL_VALUE_DIV (a $2000 tier adds +10)
  EMAIL_BASE          = 20    base for a response-needed email-channel reply
  EMAIL_AGE_PER_HOUR  = 1.0
  JOBS_MANUAL_BASE    = 8     base per manual-finish job application (CAPTCHA/login wall)
  LINKEDIN_QUEUE_BASE = 6     base per pending LinkedIn queue item
  TODO_OVERDUE_BASE   = 30    base for a scheduled todo whose time has passed
  TODO_OVERDUE_PER_DAY= 10    + per day overdue (capped, see AGE_CAP_HOURS days-equivalent)
  AGE_CAP_HOURS       = 96    age bonuses stop accruing past this many hours (4 days);
                              old-and-ignored shouldn't out-rank fresh-and-urgent forever

Run:  .venv/bin/python agents/attention.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso, load_todos  # noqa: E402
import planner  # noqa: E402

OUT = ROOT / "store" / "attention.json"

# ---- tunable weights (documented above; change here, nowhere else) ----
REPLY_BASE = 40
REPLY_AGE_PER_HOUR = 1.5
REPLY_INTENT_BONUS = {"interested": 25, "objection": 15, "followup": 5, "remove": 20}
PROPOSAL_STAGED_BASE = 25
PROPOSAL_VALUE_DIV = 200
EMAIL_BASE = 20
EMAIL_AGE_PER_HOUR = 1.0
JOBS_MANUAL_BASE = 8
LINKEDIN_QUEUE_BASE = 6
TODO_OVERDUE_BASE = 30
TODO_OVERDUE_PER_DAY = 10
PROMISE_BASE = 35              # a made promise outranks a routine todo, sits below hot replies
PROMISE_OVERDUE_PER_HOUR = 1.0
AGE_CAP_HOURS = 96


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


def _age_hours(ts: str) -> float:
    """Hours since an ISO timestamp; 0.0 on any parse failure (never negative,
    never crashes the run over one bad record)."""
    if not ts:
        return 0.0
    try:
        dt = datetime.fromisoformat(ts)
        if not dt.tzinfo:
            dt = dt.astimezone()
        now = datetime.now(dt.tzinfo)
        return max(0.0, (now - dt).total_seconds() / 3600.0)
    except (ValueError, TypeError):
        return 0.0


def _capped(hours: float) -> float:
    return min(hours, AGE_CAP_HOURS)


def score_replies() -> list[dict]:
    """store/replies.jsonl: pending SMS/warm replies needing approval.
    reply_watch.py is off-limits to edit but safe to import (read-only _load())."""
    out = []
    try:
        import reply_watch
        rows = reply_watch._load()
    except Exception:  # noqa: BLE001 — missing module/import surprise shouldn't kill the router
        rows = _read_jsonl(ROOT / "store" / "replies.jsonl")
    for r in rows:
        if r.get("status") != "pending":
            continue
        # email-channel replies are score_emails' lane; counting them here too
        # double-ranked every email (D7 audit)
        if (r.get("channel") or "").strip().lower() == "email":
            continue
        age_h = _age_hours(r.get("created", ""))
        intent = (r.get("intent") or "other").lower()
        score = REPLY_BASE + REPLY_AGE_PER_HOUR * _capped(age_h) + REPLY_INTENT_BONUS.get(intent, 0)
        label = f"Reply pending: {r.get('name') or 'someone'} ({intent})"
        why = f"pending {age_h:.1f}h, intent={intent}"
        out.append({"kind": "reply", "id": r.get("id", ""), "label": label,
                    "score": round(score, 1), "why": why})
    return out


def score_proposals() -> list[dict]:
    """store/proposals.jsonl: staged-but-not-sent proposals — value = expected
    revenue sitting idle, so it's weighted by price on top of a flat base."""
    out = []
    try:
        import proposal_factory
        rows = proposal_factory.load_queue()
    except Exception:  # noqa: BLE001
        rows = _read_jsonl(ROOT / "store" / "proposals.jsonl")
    for r in rows:
        if r.get("status") != "staged":
            continue
        price = r.get("price") or 0
        try:
            price = float(price)
        except (TypeError, ValueError):
            price = 0.0
        score = PROPOSAL_STAGED_BASE + price / PROPOSAL_VALUE_DIV
        biz = r.get("company") or r.get("name") or "a prospect"
        out.append({"kind": "proposal", "id": r.get("id", ""),
                    "label": f"Proposal staged, not sent: {biz}",
                    "score": round(score, 1), "why": f"staged, ${price:g} tier"})
    return out


def score_emails() -> list[dict]:
    """Email-channel entries in replies.jsonl that need a response (same store
    as SMS/warm replies; channel distinguishes them). Scored separately per
    the spec (response-needed emails age) with its own lighter base/decay."""
    out = []
    try:
        import reply_watch
        rows = reply_watch._load()
    except Exception:  # noqa: BLE001
        rows = _read_jsonl(ROOT / "store" / "replies.jsonl")
    for r in rows:
        if r.get("status") != "pending":
            continue
        if (r.get("channel") or "").strip().lower() != "email":
            continue
        age_h = _age_hours(r.get("created", ""))
        score = EMAIL_BASE + EMAIL_AGE_PER_HOUR * _capped(age_h)
        out.append({"kind": "email", "id": r.get("id", ""),
                    "label": f"Email reply needed: {r.get('name') or 'someone'}",
                    "score": round(score, 1), "why": f"pending {age_h:.1f}h"})
    return out


def score_jobs_manual() -> list[dict]:
    """Job applications the bot couldn't finish (CAPTCHA/login wall) — one
    flat-scored bucket item rather than N individual entries, since they're
    typically batch-cleared in one sitting."""
    try:
        import jobs
        manual = jobs.needs_manual()
    except Exception:  # noqa: BLE001
        manual = []
    if not manual:
        return []
    # cap the bucket: a big backlog is one chore, not N urgent items. Uncapped, 29 manual
    # jobs scored 232 and buried a $3,500 proposal (2026-07-07 D7 audit). The first cap
    # (min(N,8)=64) still buried it (a $3,500 proposal scores 42.5) -- a test-quality audit
    # caught that the cap didn't achieve its own stated intent. min(N,4)=32 keeps a maxed
    # jobs pile below a real deal-mover so money-first actually holds.
    score = JOBS_MANUAL_BASE * min(len(manual), 4)
    return [{"kind": "jobs_manual", "id": "jobs_manual_bucket",
             "label": f"{len(manual)} job application(s) need manual finish",
             "score": round(score, 1), "why": f"{len(manual)} CAPTCHA/login-blocked"}]


def score_linkedin_queue() -> list[dict]:
    """LinkedIn (networking.py) pending queue depth — one bucket item, same
    reasoning as jobs_manual (batch-worked, not one-by-one)."""
    try:
        import networking
        rows = networking.load_queue()
    except Exception:  # noqa: BLE001
        rows = []
    pending = [r for r in rows if r.get("status") == "pending"]
    if not pending:
        return []
    score = LINKEDIN_QUEUE_BASE * min(len(pending), 4)  # capped like jobs_manual (money-first)
    return [{"kind": "linkedin_queue", "id": "linkedin_queue_bucket",
             "label": f"{len(pending)} LinkedIn item(s) awaiting approval",
             "score": round(score, 1), "why": f"{len(pending)} pending in queue"}]


def score_overdue_todos() -> list[dict]:
    """Scheduled todos whose scheduled_time has already passed and are still
    open (inbox/scheduled/doing) — these are promises to himself slipping."""
    out = []
    now = datetime.now().astimezone()
    for t in load_todos():
        if t.get("status") not in ("inbox", "scheduled", "doing"):
            continue
        sched = t.get("scheduled_time")
        if not sched:
            continue
        try:
            dt = datetime.fromisoformat(sched)
            if not dt.tzinfo:
                dt = dt.astimezone()
        except (ValueError, TypeError):
            continue
        if dt >= now:
            continue  # not overdue
        days_over = (now - dt).total_seconds() / 86400.0
        score = TODO_OVERDUE_BASE + TODO_OVERDUE_PER_DAY * min(days_over, AGE_CAP_HOURS / 24.0)
        out.append({"kind": "todo_overdue", "id": t.get("id", ""),
                    "label": f"Overdue: {t.get('text', '')[:70]}",
                    "score": round(score, 1), "why": f"{days_over:.1f}d overdue"})
    return out


def score_promises() -> list[dict]:
    """store/promises.jsonl (promises.py): commitments [OWNER] made in SENT messages
    ('I'll send it Friday'). An open promise is a warm relationship on a timer;
    breaking one silently costs more than any queue item (D7 audit: computed, unread)."""
    out = []
    for r in _read_jsonl(ROOT / "store" / "promises.jsonl"):
        if r.get("status") != "open":
            continue
        overdue_h = _age_hours(r.get("due_date", ""))  # 0.0 until the due date passes
        score = PROMISE_BASE + PROMISE_OVERDUE_PER_HOUR * _capped(overdue_h)
        who = r.get("contact") or "someone"
        out.append({"kind": "promise", "id": r.get("id", ""),
                    "label": f"You promised {who}: {(r.get('phrase') or '')[:60]}",
                    "score": round(score, 1),
                    "why": (f"{overdue_h / 24:.1f}d past due" if overdue_h else "open, not yet due")})
    return out


def build() -> dict:
    ranked: list[dict] = []
    for fn in (score_replies, score_proposals, score_emails, score_jobs_manual,
               score_linkedin_queue, score_overdue_todos, score_promises):
        try:
            ranked.extend(fn())
        except Exception as e:  # noqa: BLE001 — one bad source should never blank the whole router
            print(f"attention: {fn.__name__} failed non-fatally: {e}", file=sys.stderr)
    ranked.sort(key=lambda x: -x["score"])

    if ranked:
        top = ranked[0]
        top_line = f"{top['label']} (score {top['score']})"
    else:
        top_line = "Nothing pending — every queue is clear."

    return {"generated": now_iso(), "ranked": ranked, "top_line": top_line,
            "weights": {"REPLY_BASE": REPLY_BASE, "REPLY_AGE_PER_HOUR": REPLY_AGE_PER_HOUR,
                        "REPLY_INTENT_BONUS": REPLY_INTENT_BONUS,
                        "PROPOSAL_STAGED_BASE": PROPOSAL_STAGED_BASE,
                        "PROPOSAL_VALUE_DIV": PROPOSAL_VALUE_DIV,
                        "EMAIL_BASE": EMAIL_BASE, "EMAIL_AGE_PER_HOUR": EMAIL_AGE_PER_HOUR,
                        "JOBS_MANUAL_BASE": JOBS_MANUAL_BASE,
                        "LINKEDIN_QUEUE_BASE": LINKEDIN_QUEUE_BASE,
                        "TODO_OVERDUE_BASE": TODO_OVERDUE_BASE,
                        "TODO_OVERDUE_PER_DAY": TODO_OVERDUE_PER_DAY,
                        "AGE_CAP_HOURS": AGE_CAP_HOURS}}


def main() -> int:
    from runlog import track
    with track("attention"):
        data = build()
        OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    n = len(data["ranked"])
    print(f"attention: {n} item(s) ranked -> {OUT}")
    print(f"top: {data['top_line']}")
    for item in data["ranked"][:5]:
        print(f"  [{item['score']:>6.1f}] {item['kind']:<14} {item['label']}")
    try:
        planner.feed_add("agent", f"Attention router: {data['top_line'][:80]}")
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
