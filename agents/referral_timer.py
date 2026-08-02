#!/usr/bin/env python3
"""Referral timer (#20 / roadmap idea 64-lite) — a booked warm call that's had
30 days to turn into a relationship is a booked call [OWNER] should be asking for a
referral on, and nothing currently reminds him to ask.

Scans store/warm_dispo.jsonl (last-write-wins by id, same discipline win_loss.py
uses) for contacts disposed 'booked' more than 30 days ago that haven't had a
referral ask logged yet, drafts a short referral-ask message per contact, and
raises a todo. store/referral_log.json tracks which ids have already been asked
so this never asks the same contact twice.

Read-only against store/warm_dispo.jsonl; writes are store/referral_drafts.jsonl
(append) + store/referral_log.json (append id) + one todo per new ask.
Run standalone: .venv/bin/python agents/referral_timer.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import append_todo, humanize, new_id, now_iso  # noqa: E402
import planner  # noqa: E402

WARM_DISPO = ROOT / "store" / "warm_dispo.jsonl"
DRAFTS = ROOT / "store" / "referral_drafts.jsonl"
LOG = ROOT / "store" / "referral_log.json"
BOOKED_DAYS = 30


def _load_dispos() -> list[dict]:
    """last-write-wins by id, same as win_loss.py — a contact re-disposed later
    (e.g. booked -> dead) should not still look 'booked' here."""
    if not WARM_DISPO.exists():
        return []
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for line in WARM_DISPO.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = r.get("id")
        if not rid:
            continue
        if rid not in by_id:
            order.append(rid)
        by_id[rid] = r
    return [by_id[i] for i in order]


def _asked() -> set[str]:
    try:
        data = json.loads(LOG.read_text())
        return set(data.get("referral_asked", [])) if isinstance(data, dict) else set()
    except (OSError, json.JSONDecodeError):
        return set()


def _stale_booked(rows: list[dict]) -> list[dict]:
    cutoff = datetime.now().astimezone() - timedelta(days=BOOKED_DAYS)
    out = []
    for r in rows:
        if r.get("dispo") != "booked":
            continue
        ts = r.get("ts") or ""
        try:
            dt = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            continue
        if dt < cutoff:
            out.append(r)
    return out


PROMPT = """[OWNER] runs [OWNER_COMPANY] (white-label website builds for agencies,
$1K+, 48-72h turnaround; also agency ops/fractional-COO help). A contact of his booked
a call about a month ago and it went well enough to be worth staying warm with.

Their name isn't available to you, an internal CRM id is the only reference (do NOT
print it, look anything up, or mention that a name is missing, just write the message
without a greeting name, e.g. start with "Hey," or "Hey there,").

Draft a short, low-pressure referral-ask message [OWNER] could send. Exactly 2-3
sentences: a genuine check-in, then ask if they know anyone else who could use
what he does. Direct, human, not salesy. Use commas or periods for pauses, never
a dash. Output ONLY the message itself, nothing else: no notes, no alternates, no
commentary about these instructions or about missing information."""


# Phrases that mean the model started reasoning ABOUT the task instead of just
# producing the message (seen in testing: it tried to "look up" an opaque id and
# narrated that instead of drafting). If the first line contains one of these,
# the whole response is unusable, not just the format broke, and should be dropped
# rather than saved as a draft.
_HEDGE_MARKERS = ("i wasn't able", "i was not able", "i don't have", "i do not have",
                  "since \"", "here's the message with a placeholder", "i couldn't find",
                  "i cannot ", "i can't find", "as an ai")


def _first_message(raw: str) -> str:
    """Defensive parse: models occasionally break format and append meta-commentary
    or a second "corrected" attempt after the actual draft (seen in testing on the
    em-dash instruction specifically, in the sibling agent defib.py). Keep only the
    text before the first such break."""
    text = (raw or "").strip()
    for marker in ("\n\n", "(Note", "(note", "Wait,", "Here's the corrected",
                  "Here's an alternative", "*("):
        i = text.find(marker)
        if i > 0:
            text = text[:i].strip()
    return text


def _is_usable(text: str) -> bool:
    """Reject outputs where the model narrated about the task instead of drafting
    (see _HEDGE_MARKERS) rather than silently saving unusable text as a 'draft'."""
    if not text:
        return False
    head = text.lower()[:80]
    return not any(m in head for m in _HEDGE_MARKERS)


def build_drafts() -> list[dict]:
    rows = _load_dispos()
    stale = _stale_booked(rows)
    asked = _asked()
    out = []
    for r in stale:
        rid = r.get("id")
        if not rid or rid in asked:
            continue
        draft = planner._cli(PROMPT, timeout=90, feature="content")
        draft = _first_message(draft or "")
        if not _is_usable(draft):
            continue
        draft = humanize(draft)
        if not draft:
            continue
        out.append({"id": rid, "draft": draft, "ts": now_iso()})
        asked.add(rid)
    return out


def main() -> int:
    drafts = build_drafts()
    if not drafts:
        print("referral_timer: no booked contacts past 30 days need a referral ask")
        return 0
    DRAFTS.parent.mkdir(parents=True, exist_ok=True)
    with DRAFTS.open("a") as f:
        for rec in drafts:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    for rec in drafts:
        append_todo({
            "id": new_id("referral_" + rec["id"]),
            "text": f"Ask for referral: {rec['id']} (draft ready)",
            "status": "inbox", "created": now_iso(), "source": "referral_timer", "source_ref": rec["id"],
            "project": None, "priority": 3, "scheduled_time": None, "duration_min": None,
            "gcal_event_id": None, "notes": None,
        })
    existing = set(_asked())
    existing.update(r["id"] for r in drafts)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text(json.dumps({"referral_asked": sorted(existing)}, indent=2))
    planner.feed_add("agent", f"Referral timer: {len(drafts)} referral ask(s) drafted")
    print(f"referral_timer: drafted {len(drafts)} referral ask(s) -> {DRAFTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
