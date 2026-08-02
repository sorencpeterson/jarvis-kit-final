#!/usr/bin/env python3
"""Task futures (#45): condition-triggered todos. 'When X replies, remind me to ...'
Watches pending replies + comms for the named sender; fires the todo once."""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import append_todo, new_id, now_iso  # noqa: E402
import reply_watch  # noqa: E402
import planner  # noqa: E402

FUT = ROOT / "store" / "futures.jsonl"

def _sender_matches(who: str, candidate: str) -> bool:
    """D7: word-boundary/email-aware match for when_reply_from. The old bare
    substring check (`who in n`) fired "Dan" on "Danielle Smith" and "Jordan
    Banks", so the wrong person's reply triggered the future. Emails compare
    exactly; names must match as whole words within the candidate."""
    who = (who or "").strip().lower()
    cand = (candidate or "").strip().lower()
    if not who or not cand:
        return False
    if "@" in who or "@" in cand:
        return who == cand
    return re.search(r"(?<![a-z0-9])" + re.escape(who) + r"(?![a-z0-9])", cand) is not None

def run():
    if not FUT.exists():
        return 0
    rows = {}
    for line in FUT.read_text().splitlines():
        try:
            r = json.loads(line)
            rows[r["id"]] = r
        except (json.JSONDecodeError, KeyError):
            continue
    waiting = [r for r in rows.values() if r.get("status") == "waiting"]
    if not waiting:
        return 0
    senders = set()
    for x in reply_watch._load():
        for field in ("name", "email"):
            v = (x.get(field) or "").strip()
            if v:
                senders.add(v)
    fired = 0
    with FUT.open("a") as f:
        for r in waiting:
            # tolerate a malformed record (hand-edited / legacy / any writer other than the
            # one Pydantic-validated endpoint): a bare r["when_reply_from"] KeyError here used
            # to bubble out, exit futures_check non-zero, and under run.sh's `set -e` abort the
            # whole 10-min tick before money_session/evening_chain ever ran (2026-07-12 hunt).
            who = (r.get("when_reply_from") or "").strip()
            text = r.get("text") or ""
            if not who or not text:
                continue
            if any(_sender_matches(who, n) for n in senders):
                append_todo({"id": new_id(text), "text": text + f" ({who} replied)",
                             "status": "inbox", "source": "future", "created": now_iso()})
                f.write(json.dumps({**r, "status": "fired", "fired_ts": now_iso()}) + "\n")
                planner.feed_add("agent", "Future fired: " + text[:60])
                fired += 1
    print(f"futures: {fired} fired, {len(waiting)-fired} still waiting")
    return 0

if __name__ == "__main__":
    raise SystemExit(run())
