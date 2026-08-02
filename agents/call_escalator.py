#!/usr/bin/env python3
"""Call escalator: warm_block.py loads 10 warm calls every morning; this agent
makes it expensive to ignore them. 58 warm leads went uncalled while the
machine hummed. The machine now taps him on the shoulder at 3pm and files the
receipt at end of day.

WHAT: reads store/warm_block.json (today's picks, built by warm_block.py) and
      store/warm_dispo.jsonl (dispositions logged from call mode, shape
      {id, dispo, note, ts}). Past AFTERNOON_HOUR with 0 dispositions today:
      push naming the first uncalled lead (name/company/phone, phone looked up
      from ~/Claude/WARM-HITLIST.csv by the same id hash warm_block uses).
      Past EOD_HOUR and still 0: a blunt self-accountability note in the feed
      plus one more push. Any dispo logged today silences both stages.
WHEN: cron a few times after 15:00 and once after 20:00; each stage fires at
      most once per day (store/.call_escalator_state.json).
RAILS: read-only against block/dispos/hitlist. Only writes its own state file
      and the feed. Push is a self-notification. --dry-run prints, writes
      nothing. Missing or stale block exits 0 (nothing to escalate).

Run: .venv/bin/python agents/call_escalator.py [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import LOCAL_TZ, _flock  # noqa: E402
import planner  # noqa: E402

# ---- tunables ----
WARM_BLOCK = ROOT / "store" / "warm_block.json"
DISPO = ROOT / "store" / "warm_dispo.jsonl"
STATE = ROOT / "store" / ".call_escalator_state.json"
HITLIST = Path.home() / "Claude" / "WARM-HITLIST.csv"
AFTERNOON_HOUR = 15  # first shove
EOD_HOUR = 20        # receipt filed


def _now() -> datetime:
    """Local now; module-level so tests can freeze the clock."""
    return datetime.now(LOCAL_TZ)


def _rid(phone: str, name: str) -> str:
    """Same id formula as warm_block._rid, kept local so a warm_block refactor
    cannot silently break the phone lookup."""
    return "w_" + hashlib.sha1((phone or name).encode()).hexdigest()[:10]


def _block(today: str) -> dict | None:
    """Today's block, or None if it is missing/stale/empty (nothing built today
    means nothing to escalate; warm_block owns building it)."""
    try:
        b = json.loads(WARM_BLOCK.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if b.get("date") != today or not b.get("picks"):
        return None
    return b


def _dispos_today(today: str) -> list[dict]:
    out = []
    if not DISPO.exists():
        return out
    for line in DISPO.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (r.get("ts") or "")[:10] == today:
            out.append(r)
    return out


def _first_uncalled(picks: list[dict], done_ids: set[str]) -> dict | None:
    """First pick without a dispo, enriched with phone/company from the
    hitlist CSV when available (picks only carry id/name/niche)."""
    target = next((p for p in picks if p.get("id") not in done_ids), None)
    if not target:
        return None
    target = dict(target)
    try:
        for r in csv.DictReader(open(HITLIST, newline="")):
            phone = (r.get("phone") or "").strip()
            name = (r.get("name") or "").strip() or (r.get("company") or "").strip()
            if _rid(phone, name) == target.get("id"):
                target["phone"] = phone
                target["company"] = (r.get("company") or "").strip()
                break
    except OSError:
        pass  # no CSV, push still names the lead
    return target


def _load_state() -> dict:
    try:
        data = json.loads(STATE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with _flock(STATE):
        tmp = STATE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
        os.replace(tmp, STATE)


def run(dry_run: bool = False) -> int:
    now = _now()
    today = now.strftime("%Y-%m-%d")

    if now.hour < AFTERNOON_HOUR:
        print(f"call_escalator: before {AFTERNOON_HOUR}:00, nothing to check yet")
        return 0

    block = _block(today)
    if not block:
        print("call_escalator: no warm block built for today, nothing to escalate")
        return 0

    picks = block.get("picks") or []
    n = len(picks)
    dispos = _dispos_today(today)
    if dispos:
        print(f"call_escalator: {len(dispos)} dispo(s) logged today, he is dialing, standing down")
        return 0

    stage = "eod" if now.hour >= EOD_HOUR else "afternoon"
    state = _load_state()
    if state.get("date") != today:
        state = {"date": today, "fired": []}
    if stage in state.get("fired", []):
        print(f"call_escalator: {stage} stage already fired today, skipping")
        return 0

    first = _first_uncalled([dict(p) for p in picks], {d.get("id") for d in dispos})
    who = ""
    if first:
        bits = [first.get("name") or "?"]
        if first.get("company") and first.get("company", "").lower() != (first.get("name") or "").lower():
            bits.append(first["company"])
        if first.get("phone"):
            bits.append(first["phone"])
        who = ", ".join(bits)

    if stage == "afternoon":
        title = f"0 of {n} warm calls and it is {now.hour - 12}pm"
        body = f"0 of {n} warm calls made and it is {now.strftime('%-H:%M')}."
        if who:
            body += f" First one: {who}. Two minutes. Dial it."
        feed_line = None
    else:
        title = "Day ended with 0 warm calls"
        body = (f"0 of {n} warm calls today. The block was loaded this morning and never touched. "
                "$0 moved. Tomorrow the first dial happens before anything else.")
        if who:
            body += f" Start with {who}."
        feed_line = (f"Self-accountability: 0 of {n} warm calls made today. "
                     f"The list was ready at 8am. The machine was not the bottleneck.")

    print(f"call_escalator: firing {stage} stage")
    print(f"  push: {title} | {body}")
    if feed_line:
        print(f"  feed: {feed_line}")

    if dry_run:
        print("[dry-run] no push, no state write")
        return 0

    planner.notify(title, body, tags="telephone_receiver")
    try:
        if feed_line:
            planner.feed_add("warm", "0 warm calls today", feed_line)
        else:
            planner.feed_add("warm", title, body[:140])
    except Exception:  # noqa: BLE001
        pass
    state.setdefault("fired", []).append(stage)
    _save_state(state)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Escalate when the daily warm-call block goes untouched")
    ap.add_argument("--dry-run", action="store_true", help="compute and print, no push, no state write")
    args = ap.parse_args()
    if args.dry_run:
        return run(dry_run=True)
    from runlog import track
    with track("call_escalator"):
        return run(dry_run=False)


if __name__ == "__main__":
    raise SystemExit(main())
