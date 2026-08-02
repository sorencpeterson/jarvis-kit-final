#!/usr/bin/env python3
"""Send-finger nag: staged work that only needs [OWNER]'s click does not get to
age quietly. This is the agent version of THE-COLD-READ's finding: $46,800 sat
staged and unsent for days because nothing complained.

WHAT: finds (a) proposals with status=staged (proposal_factory.load_queue())
      and (b) replies with status=pending (reply_watch._load()) whose `created`
      timestamp is past LEVEL1_H / LEVEL2_H hours.
      Level 1 (>24h): one push naming the oldest offender + the total $ staged.
      Level 2 (>48h): a blunter push AND a todo staged into store/todos.jsonl
      ("Send the <company> proposal, staged <n> days") so it shows up in every
      surface that reads todos. The todo is staged once per item ever (deduped
      by source_ref); the pushes repeat daily until he clears the item.
WHEN: a few times a day is fine; store/.nag_state.json makes each item nag at
      most once per level per day, so cadence does not multiply pushes.
RAILS: read-only against proposals/replies. Writes: its own state file, the
      todo append (under the todos flock, same shape load_todos returns), the
      feed. Pushes are self-notifications. --dry-run prints everything and
      writes nothing. Missing stores exit 0.

Run: .venv/bin/python agents/send_finger_nag.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import (LOCAL_TZ, _flock, append_todo, load_todos,  # noqa: E402
                       new_id, now_iso)
import planner  # noqa: E402

# ---- tunables ----
NAG_STATE = ROOT / "store" / ".nag_state.json"
TODOS = ROOT / "store" / "todos.jsonl"
PROPOSALS = ROOT / "store" / "proposals.jsonl"  # fallback if factory import fails
REPLIES = ROOT / "store" / "replies.jsonl"      # fallback if reply_watch import fails
LEVEL1_H = 24
LEVEL2_H = 48
MAX_TODOS_PER_RUN = 5  # first live run would otherwise stage 15 at once; a todo flood
                       # erodes trust in the list. Overflow lands on later runs (dedupe
                       # by source_ref means nothing is ever lost, just paced).


def _read_jsonl_lww(path: Path) -> list[dict]:
    if not path.exists():
        return []
    by_id, order = {}, []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("id"):
            if r["id"] not in by_id:
                order.append(r["id"])
            by_id[r["id"]] = r
    return [by_id[i] for i in order]


def _staged_proposals() -> list[dict]:
    try:
        import proposal_factory
        rows = proposal_factory.load_queue()
    except Exception:  # noqa: BLE001
        rows = _read_jsonl_lww(PROPOSALS)
    return [r for r in rows if r.get("status") == "staged"]


def _pending_replies() -> list[dict]:
    try:
        import reply_watch
        rows = reply_watch._load()
    except Exception:  # noqa: BLE001
        rows = _read_jsonl_lww(REPLIES)
    return [r for r in rows if r.get("status") == "pending"]


def _age_hours(created: str, now: datetime) -> float:
    if not created:
        return 0.0
    try:
        dt = datetime.fromisoformat(created)
        if not dt.tzinfo:
            dt = dt.astimezone()
        return max(0.0, (now - dt).total_seconds() / 3600.0)
    except (ValueError, TypeError):
        return 0.0


def collect(now: datetime | None = None) -> tuple[list[dict], float]:
    """All nag-eligible items with their escalation level, plus total $ staged
    across ALL staged proposals (the headline number, not just the old ones)."""
    now = now or datetime.now(LOCAL_TZ)
    items: list[dict] = []
    total_staged = 0.0
    for r in _staged_proposals():
        try:
            price = float(r.get("price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        total_staged += price
        age_h = _age_hours(r.get("created", ""), now)
        level = 2 if age_h > LEVEL2_H else (1 if age_h > LEVEL1_H else 0)
        if level:
            items.append({"id": r.get("id", ""), "kind": "proposal",
                          "label": r.get("company") or r.get("name") or "a prospect",
                          "age_h": age_h, "price": price, "level": level})
    for r in _pending_replies():
        age_h = _age_hours(r.get("created", ""), now)
        level = 2 if age_h > LEVEL2_H else (1 if age_h > LEVEL1_H else 0)
        if level:
            items.append({"id": r.get("id", ""), "kind": "reply",
                          "label": r.get("name") or "someone",
                          "age_h": age_h, "price": 0.0, "level": level})
    items.sort(key=lambda x: -x["age_h"])  # oldest first
    return items, total_staged


def _days(age_h: float) -> int:
    return max(1, int(age_h // 24))


def _todo_text(item: dict) -> str:
    d = _days(item["age_h"])
    if item["kind"] == "proposal":
        return f"Send the {item['label']} proposal, staged {d} days"
    return f"Answer {item['label']}'s reply, pending {d} days"


def _load_nag_state() -> dict:
    try:
        data = json.loads(NAG_STATE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_nag_state(state: dict) -> None:
    NAG_STATE.parent.mkdir(parents=True, exist_ok=True)
    with _flock(NAG_STATE):
        tmp = NAG_STATE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
        os.replace(tmp, NAG_STATE)


def _stage_todo(item: dict) -> bool:
    """Append the level-2 todo once per item ever (deduped by source_ref),
    matching the shape store_lib.load_todos returns."""
    ref = f"nag48_{item['id']}"
    existing = {t.get("source_ref") for t in load_todos(TODOS)}
    if ref in existing:
        return False
    text = _todo_text(item)
    rec = {"id": new_id(ref + text), "text": text, "status": "inbox",
           "created": now_iso(), "source": "send_finger_nag", "source_ref": ref,
           "project": None, "priority": 1, "scheduled_time": None,
           "duration_min": None, "gcal_event_id": None, "notes": None}
    append_todo(rec, TODOS)
    return True


def run(dry_run: bool = False) -> int:
    now = datetime.now(LOCAL_TZ)
    today = now.strftime("%Y-%m-%d")
    items, total_staged = collect(now)
    if not items:
        print("send_finger_nag: nothing staged or pending past 24h, all clear")
        return 0

    state = _load_nag_state()
    lvl1 = [i for i in items if i["level"] == 1
            and state.get(i["id"], {}).get("1") != today]
    lvl2 = [i for i in items if i["level"] == 2
            and state.get(i["id"], {}).get("2") != today]

    n1_all = sum(1 for i in items if i["level"] == 1)
    n2_all = sum(1 for i in items if i["level"] == 2)
    print(f"send_finger_nag: {n2_all} item(s) past {LEVEL2_H}h, {n1_all} past {LEVEL1_H}h, "
          f"${total_staged:,.0f} staged total; eligible now: {len(lvl2)} L2, {len(lvl1)} L1")
    for i in items:
        print(f"  L{i['level']} {i['kind']:<8} {i['label']} ({i['age_h']:.0f}h, ${i['price']:g})")

    pushes: list[tuple[str, str]] = []
    todos_to_stage: list[dict] = []
    if lvl2:
        oldest = lvl2[0]
        d = _days(oldest["age_h"])
        title = f"{d} days staged and still not sent"
        body = (f"The {oldest['label']} {oldest['kind']} has waited {d} days. "
                f"{n2_all} item(s) are past {LEVEL2_H}h. ${total_staged:,.0f} is staged and unsent. "
                f"A todo is now on your list. Send it.")
        pushes.append((title, body))
        todos_to_stage = lvl2[:MAX_TODOS_PER_RUN]
    if lvl1:
        oldest = lvl1[0]
        title = "Staged and unsent past 24h"
        body = (f"Oldest: the {oldest['label']} {oldest['kind']}, {oldest['age_h']:.0f}h old. "
                f"${total_staged:,.0f} staged total. One click each.")
        pushes.append((title, body))

    if dry_run:
        for title, body in pushes:
            print(f"[dry-run] would push: {title} | {body}")
        for i in todos_to_stage:
            print(f"[dry-run] would stage todo: {_todo_text(i)}")
        print("[dry-run] no push, no state write")
        return 0

    for title, body in pushes:
        planner.notify(title, body, tags="hourglass_flowing_sand")
        try:
            planner.feed_add("agent", title, body[:140])
        except Exception:  # noqa: BLE001
            pass
    staged_n = 0
    for i in todos_to_stage:
        try:
            if _stage_todo(i):
                staged_n += 1
        except OSError:
            pass
    for i in lvl2:
        state.setdefault(i["id"], {})["2"] = today
    for i in lvl1:
        state.setdefault(i["id"], {})["1"] = today
    if lvl1 or lvl2:
        _save_nag_state(state)
    print(f"send_finger_nag: {len(pushes)} push(es), {staged_n} todo(s) staged")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Nag about staged proposals and pending replies going stale")
    ap.add_argument("--dry-run", action="store_true", help="compute and print, no push, no state write")
    args = ap.parse_args()
    if args.dry_run:
        return run(dry_run=True)
    from runlog import track
    with track("send_finger_nag"):
        return run(dry_run=False)


if __name__ == "__main__":
    raise SystemExit(main())
