#!/usr/bin/env python3
"""E409: promise tracker — scans [OWNER]'s own SENT-adjacent messages for date
phrases ("by Friday", "next week", "tomorrow", "end of month") and turns each
into a tracked promise with a resolved calendar date, so a promise made in
passing on a warm call follow-up doesn't quietly slip.

WHAT: scans store/replies.jsonl (status == "sent", the 'draft' field — this
      repo has no 'sent_text' field yet, see agents/template_learn.py's own
      documented finding; 'draft' on a status=="sent" record IS what went out
      until a future writer starts recording edits) and
      agents/proposal_factory.load_queue() (status == "sent", 'email_draft'
      field) for date-commitment phrases in [OWNER]'s own outbound text. Each
      match becomes a promise record with the phrase resolved to a real
      YYYY-MM-DD (relative to the message's own sent/created timestamp, not
      "today", so an old sent message resolves correctly).
WHEN: run daily (morning chain candidate) or ad hoc. Idempotent: re-running
      never duplicates a promise already tracked for the same source id +
      phrase (dedup key), and only ever APPENDS new resolved promises.
RAILS: read-only against replies.jsonl and proposals.jsonl. Only write is an
      APPEND to store/promises.jsonl (never rewrites existing lines) — this is
      an append-only ledger like every other store in this repo. The 48h-before
      warning path calls planner.notify() (a phone push), never sends anything
      to a contact; no GHL writes, no outbound sends, ever.

DATE PHRASE GRAMMAR (regex-based, no LLM call — this is small enough and
frequent enough that an LLM call per scan would be needless cost; see
E343 haiku-routing-audit: every feature should be justified or downgraded,
and pure regex is the downgrade here):
  "tomorrow"                      -> sent_date + 1
  "today" / "eod" / "end of day"  -> sent_date + 0
  "by <weekday>" / "<weekday>"    -> the next occurrence of that weekday
                                      on/after sent_date (same-day counts)
  "next week"                     -> sent_date + 7
  "next <weekday>"                -> the occurrence of that weekday in the
                                      NEXT calendar week (always +7 or more,
                                      distinct from bare "<weekday>")
  "end of month" / "eom"          -> the last day of sent_date's month

Run:  .venv/bin/python agents/promises.py
      .venv/bin/python agents/promises.py --fixture   (deterministic fixture
      run against a frozen scenario, prints without touching the real store)
"""
from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso, new_id  # noqa: E402
import planner  # noqa: E402

REPLIES = ROOT / "store" / "replies.jsonl"
PROMISES = ROOT / "store" / "promises.jsonl"
WARN_WINDOW_HOURS = 48  # notify when a promise's due date is within this many hours

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6}
_WD_ALT = "|".join(WEEKDAYS.keys())

# Order matters: more specific patterns (next <weekday>, by <weekday>) before
# bare "<weekday>" so "by friday" doesn't ALSO match as a second bare hit.
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("tomorrow", re.compile(r"\btomorrow\b", re.I)),
    ("today_eod", re.compile(r"\b(today|eod|end of day)\b", re.I)),
    ("next_week", re.compile(r"\bnext week\b", re.I)),
    ("end_of_month", re.compile(r"\b(end of month|eom)\b", re.I)),
    ("next_weekday", re.compile(rf"\bnext ({_WD_ALT})\b", re.I)),
    ("by_weekday", re.compile(rf"\bby ({_WD_ALT})\b", re.I)),
    ("bare_weekday", re.compile(rf"\b({_WD_ALT})\b", re.I)),
]

# D7: a bare weekday word with NO commitment verb near it is not a promise.
# "On Friday, John wrote:" in a quoted reply, "Fridays are busy", etc. were all
# being tracked as due dates. Only the bare_weekday pattern gets this gate; the
# explicit forms ("by friday", "next friday", "tomorrow") already carry intent.
_COMMIT_NEAR = re.compile(
    r"\b(i'?ll|we'?ll|will|can|gonna|going to|call|text|email|send|get|have|deliver|"
    r"finish|wrap|ship|share|update|follow|circle|reach|book|schedule|talk|chat|"
    r"meet|review|done|ready|live|launch|expect|by)\b", re.I)
_COMMIT_WINDOW = 60  # chars of context each side of the weekday word


def _has_commitment_near(text: str, start: int, end: int) -> bool:
    ctx = text[max(0, start - _COMMIT_WINDOW):min(len(text), end + _COMMIT_WINDOW)]
    return bool(_COMMIT_NEAR.search(ctx))


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


def _next_weekday_on_or_after(d: date, target_wd: int) -> date:
    delta = (target_wd - d.weekday()) % 7
    return d + timedelta(days=delta)


def _next_weekday_strictly_next_week(d: date, target_wd: int) -> date:
    """The occurrence in the NEXT calendar week (distinct from the nearest
    same-or-later occurrence): always at least (7 - d.weekday()) days out.

    Note this can legitimately equal by_weekday's answer: if target_wd already
    passed EARLIER in the current calendar week (e.g. saying "next monday" or
    just "monday" on a Wednesday, when this week's Monday is behind us), the
    nearest occurrence already falls in next week, so both phrasings agree,
    same as they would if a person said either one out loud."""
    this_occurrence = _next_weekday_on_or_after(d, target_wd)
    if this_occurrence <= d + timedelta(days=6 - d.weekday()):
        # this_occurrence falls within the current week (Mon-Sun) -> push one more week
        return this_occurrence + timedelta(days=7)
    return this_occurrence


def _end_of_month(d: date) -> date:
    last_day = calendar.monthrange(d.year, d.month)[1]
    return d.replace(day=last_day)


def resolve_date(kind: str, match_text: str, sent_date: date) -> date | None:
    if kind == "tomorrow":
        return sent_date + timedelta(days=1)
    if kind == "today_eod":
        return sent_date
    if kind == "next_week":
        return sent_date + timedelta(days=7)
    if kind == "end_of_month":
        return _end_of_month(sent_date)
    if kind == "next_weekday":
        wd = WEEKDAYS.get(match_text.lower())
        return _next_weekday_strictly_next_week(sent_date, wd) if wd is not None else None
    if kind in ("by_weekday", "bare_weekday"):
        wd = WEEKDAYS.get(match_text.lower())
        return _next_weekday_on_or_after(sent_date, wd) if wd is not None else None
    return None


def find_promises(text: str, sent_date: date) -> list[dict]:
    """Scan `text` for date-commitment phrases, resolving each relative to
    sent_date. Stops after the first match per pattern kind (a message rarely
    makes two promises of the exact same phrase kind; this keeps output sane
    rather than one row per weekday word repeated in casual text)."""
    found = []
    matched_spans: list[tuple[int, int]] = []
    for kind, pattern in PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        # skip if this span overlaps one already claimed by a more-specific
        # pattern earlier in PATTERNS (e.g. "by friday" already claimed by
        # by_weekday shouldn't ALSO fire bare_weekday on the same "friday")
        if any(not (m.end() <= s or m.start() >= e) for s, e in matched_spans):
            continue
        # D7: bare weekday needs a commitment verb nearby to count as a promise
        if kind == "bare_weekday" and not _has_commitment_near(text, m.start(), m.end()):
            continue
        group = m.group(1) if m.groups() else m.group(0)
        resolved = resolve_date(kind, group, sent_date)
        if resolved is None:
            continue
        matched_spans.append((m.start(), m.end()))
        found.append({"phrase": m.group(0), "kind": kind, "due_date": resolved.isoformat()})
    return found


def _sent_date_of(ts: str) -> date:
    try:
        return datetime.fromisoformat(ts).date()
    except (ValueError, TypeError):
        return datetime.now().astimezone().date()


def _dedup_key(source_id: str, phrase: str, due_date: str) -> str:
    return f"{source_id}::{phrase.lower().strip()}::{due_date}"


def _already_tracked() -> set[str]:
    keys = set()
    for r in _read_jsonl(PROMISES):
        k = r.get("dedup_key")
        if k:
            keys.add(k)
    return keys


def scan_sources() -> list[dict]:
    """Returns candidate (text, sent_ts, source_kind, source_id, contact) tuples
    from every sent-adjacent store this repo has. Honest about what's missing:
    replies.jsonl has no distinct sent_text field (draft IS the sent text for
    status=='sent' records, per template_learn.py's documented finding), and
    agreements.jsonl / store/agreements/ are currently empty on this account
    (checked directly; nothing to scan there yet, not a bug)."""
    candidates = []
    for r in _read_jsonl(REPLIES):
        if r.get("status") != "sent":
            continue
        text = (r.get("draft") or "").strip()
        if not text:
            continue
        candidates.append({
            "text": text, "sent_ts": r.get("created") or "", "source_kind": "reply",
            "source_id": r.get("id", ""), "contact": r.get("name") or r.get("contact_id") or "",
        })
    try:
        import proposal_factory
        prop_rows = proposal_factory.load_queue()
    except Exception:  # noqa: BLE001
        prop_rows = _read_jsonl(ROOT / "store" / "proposals.jsonl")
    for r in prop_rows:
        if r.get("status") != "sent":
            continue
        text = (r.get("email_draft") or "").strip()
        if not text:
            continue
        candidates.append({
            "text": text, "sent_ts": r.get("sent_at") or r.get("created") or "",
            "source_kind": "proposal", "source_id": r.get("id", ""),
            "contact": r.get("company") or r.get("name") or "",
        })
    return candidates


def build(candidates: list[dict] | None = None) -> list[dict]:
    """Pure: given candidate messages (or the real scan if None), returns the
    list of NEW promise records to append (already deduped against existing).
    Kept separate from run()/main() so a fixture can call this directly."""
    if candidates is None:
        candidates = scan_sources()
    tracked = _already_tracked()
    new_records = []
    for c in candidates:
        sent_date = _sent_date_of(c["sent_ts"])
        for p in find_promises(c["text"], sent_date):
            key = _dedup_key(c["source_id"], p["phrase"], p["due_date"])
            if key in tracked:
                continue
            tracked.add(key)  # guard against dupes WITHIN this same run too
            new_records.append({
                "id": new_id(key), "dedup_key": key,
                "source_kind": c["source_kind"], "source_id": c["source_id"],
                "contact": c["contact"], "phrase": p["phrase"], "resolved_from": p["kind"],
                "due_date": p["due_date"], "text_snippet": c["text"][:160],
                "sent_ts": c["sent_ts"], "created": now_iso(),
                "warned_48h": False, "status": "open",
            })
    return new_records


def _append(records: list[dict]):
    if not records:
        return
    PROMISES.parent.mkdir(parents=True, exist_ok=True)
    with PROMISES.open("a") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def check_warnings(*, dry_run: bool = False) -> list[dict]:
    """Existing open promises due within WARN_WINDOW_HOURS that haven't been
    warned yet -> planner.notify() once, then flag warned_48h so it never
    repeats. Returns the list of promises warned this run (for tests/dry-run
    inspection without hitting the network)."""
    all_promises = _read_jsonl(PROMISES)
    by_id = {r["id"]: r for r in all_promises if r.get("id")}
    now = datetime.now().astimezone()
    warned = []
    for rec in by_id.values():
        if rec.get("status") != "open" or rec.get("warned_48h"):
            continue
        try:
            due = datetime.fromisoformat(rec["due_date"]).replace(
                hour=23, minute=59, second=0, tzinfo=now.tzinfo)
        except (ValueError, KeyError, TypeError):
            continue
        hours_until = (due - now).total_seconds() / 3600.0
        if 0 <= hours_until <= WARN_WINDOW_HOURS:
            warned.append(rec)
            if not dry_run:
                planner.notify(
                    "Promise coming due",
                    f"\"{rec['phrase']}\" to {rec.get('contact') or 'someone'} — due {rec['due_date']}",
                    tags="promise",
                )
                updated = dict(rec)
                updated["warned_48h"] = True
                _append([updated])  # append-only: the newer line wins on next load
    return warned


def run(*, fixture: bool = False, dry_run_notify: bool = False) -> dict:
    if fixture:
        new_records = build(_fixture_candidates())
        return {"new": new_records, "warned": [], "fixture": True}
    new_records = build()
    _append(new_records)
    warned = check_warnings(dry_run=dry_run_notify)
    return {"new": new_records, "warned": warned, "fixture": False}


def _fixture_candidates() -> list[dict]:
    """Frozen scenario for --fixture / tests: no store I/O, deterministic dates
    relative to a fixed sent_ts so output never depends on when this runs."""
    return [
        {"text": "I'll get you the updated site by Friday, no rush after that.",
         "sent_ts": "2026-07-01T10:00:00-07:00", "source_kind": "reply",
         "source_id": "fx_reply_1", "contact": "Fixture Contact"},
        {"text": "Circling back next week once the mockups are in.",
         "sent_ts": "2026-07-01T10:00:00-07:00", "source_kind": "proposal",
         "source_id": "fx_prop_1", "contact": "Fixture Co"},
        {"text": "Can send that over tomorrow morning.",
         "sent_ts": "2026-07-01T10:00:00-07:00", "source_kind": "reply",
         "source_id": "fx_reply_2", "contact": "Fixture Contact 2"},
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fixture", action="store_true",
                     help="run against a frozen scenario, print only, no store writes")
    ap.add_argument("--dry-run-notify", action="store_true",
                     help="check for 48h warnings but don't actually push or flag them")
    args = ap.parse_args()

    from runlog import track
    with track("promises"):
        result = run(fixture=args.fixture, dry_run_notify=args.dry_run_notify)

    tag = " [FIXTURE]" if result["fixture"] else ""
    print(f"promises{tag}: {len(result['new'])} new promise(s) found, "
          f"{len(result['warned'])} 48h-warning(s) fired")
    for rec in result["new"]:
        print(f"  + \"{rec['phrase']}\" -> due {rec['due_date']} ({rec['contact'] or 'unknown'})")
    for rec in result["warned"]:
        print(f"  ! warned: \"{rec['phrase']}\" due {rec['due_date']} ({rec.get('contact', '')})")
    if result["new"] and not result["fixture"]:
        try:
            planner.feed_add("agent", f"Tracked {len(result['new'])} new promise(s)")
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
