#!/usr/bin/env python3
"""E403: interruption log — what pulled [OWNER] off his planned day, captured
automatically rather than relying on him noticing and writing it down
himself.

WHAT: a todo counts as a candidate INTERRUPTION when all of: (1) captured
      TODAY, (2) source is "manual" or "siri" (i.e. [OWNER] actively typed or
      spoke it — NOT an agent-generated todo, which is expected system
      output, not a distraction), (3) its id is NOT among today's
      store/plan.json planned action ids (the 3 things he'd already decided
      to do), and (4) captured during WORK_HOURS local time (a 2am siri
      note isn't "pulled off-plan," it's just... 2am). Each match is logged
      once (dedup by todo id) to store/interruptions.jsonl.
WHEN: run any time (a good end-of-day or morning-chain candidate — it only
      ever looks at TODAY's captures, so running it multiple times same day
      just re-checks for new ones since the last run, safe and idempotent).
      build_weekly_pattern() separately summarizes the last 7 days of
      logged interruptions into store/interruption_pattern.md — call this
      weekly (the mission's own "weekly pattern" ask).
RAILS: read-only against store/todos.jsonl and store/plan.json. Only writes
      are an APPEND to store/interruptions.jsonl (append-only ledger, same
      discipline as every other store here) and a full-overwrite of
      store/interruption_pattern.md when --weekly is passed. No GHL writes,
      no sends.

CAPTURE HOOK DESIGN (documented, not implemented here — the actual capture
paths, capture/quick-add.sh -> POST /api/todo and Siri -> this lane's own
capture/pull_reminders.py, are either outside this lane's exclusive files
or already fire-and-forget with no "was this planned" context available at
capture time). The clean hook point, if/when someone wants this to fire
IN THE MOMENT rather than in this batch sweep, is right where
capture/quick-add.sh's POST /api/todo lands (app/server.py's api_add,
outside this lane) — that handler already builds the exact todo record this
file scans for; it could call interruption_log.check_one_todo(rec) inline
and push a notify() immediately instead of waiting for this file's next
sweep. Not wired in here per the "document integration, don't touch
server.py" pattern this lane has used throughout (see agents/thread_memory.py
and agents/agent_cadence_checker.py for the same pattern).

Run:  .venv/bin/python agents/interruption_log.py
      .venv/bin/python agents/interruption_log.py --weekly
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import LOCAL_TZ, load_todos, now_iso  # noqa: E402
import planner  # noqa: E402
from runlog import track  # noqa: E402

INTERRUPTIONS = ROOT / "store" / "interruptions.jsonl"
PLAN = ROOT / "store" / "plan.json"
WEEKLY_PATTERN = ROOT / "store" / "interruption_pattern.md"
WORK_HOUR_START = 8
WORK_HOUR_END = 19
CAPTURE_SOURCES = {"manual", "siri"}


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


def _today_plan_ids() -> set[str]:
    try:
        data = json.loads(PLAN.read_text())
    except (OSError, json.JSONDecodeError):
        return set()
    today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    if data.get("date") != today:
        return set()  # stale plan (yesterday's), not today's -> no planned ids to compare against
    return {a.get("id") for a in data.get("actions", []) if a.get("id")}


def _already_logged() -> set[str]:
    return {r.get("todo_id") for r in _read_jsonl(INTERRUPTIONS) if r.get("todo_id")}


def _in_work_hours(created: str) -> bool:
    try:
        dt = datetime.fromisoformat(created)
        if not dt.tzinfo:
            dt = dt.astimezone(LOCAL_TZ)
        else:
            dt = dt.astimezone(LOCAL_TZ)
    except (ValueError, TypeError):
        return False
    return WORK_HOUR_START <= dt.hour < WORK_HOUR_END


def check_one_todo(t: dict, plan_ids: set[str], *, today: str | None = None) -> bool:
    """True if this todo record looks like an off-plan interruption. Pure
    (given plan_ids and an optional pinned `today` for testability)."""
    today = today or datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    if t.get("source") not in CAPTURE_SOURCES:
        return False
    created = t.get("created") or ""
    if created[:10] != today:
        return False
    if t.get("id") in plan_ids:
        return False
    return _in_work_hours(created)


def find_interruptions() -> list[dict]:
    plan_ids = _today_plan_ids()
    logged = _already_logged()
    today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    out = []
    for t in load_todos():
        if t.get("id") in logged:
            continue
        if check_one_todo(t, plan_ids, today=today):
            out.append({"todo_id": t["id"], "text": t.get("text", ""),
                        "source": t.get("source"), "created": t.get("created"),
                        "logged_at": now_iso()})
    return out


def _append(records: list[dict]) -> None:
    if not records:
        return
    INTERRUPTIONS.parent.mkdir(parents=True, exist_ok=True)
    with INTERRUPTIONS.open("a") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def build_weekly_pattern() -> str:
    """E403's 'weekly pattern' ask: how many interruptions per day over the
    last 7 days, and the raw list, in plain markdown. Pure text assembly, no
    LLM call needed for a count-and-list report."""
    cutoff = (datetime.now(LOCAL_TZ) - timedelta(days=7)).strftime("%Y-%m-%d")
    rows = [r for r in _read_jsonl(INTERRUPTIONS) if (r.get("created") or "")[:10] >= cutoff]
    by_day: dict[str, int] = {}
    for r in rows:
        d = (r.get("created") or "")[:10]
        if d:
            by_day[d] = by_day.get(d, 0) + 1

    lines = ["# Interruption pattern — last 7 days", ""]
    if not rows:
        lines.append("No interruptions logged in the last 7 days.")
    else:
        lines.append(f"{len(rows)} interruption(s) across {len(by_day)} day(s).")
        lines.append("")
        for d in sorted(by_day.keys()):
            lines.append(f"- {d}: {by_day[d]}")
        lines.append("")
        lines.append("## Raw list")
        for r in sorted(rows, key=lambda x: x.get("created", "")):
            lines.append(f"- {r.get('created', '')[:16]} ({r.get('source')}): {r.get('text', '')[:80]}")
    lines.append("")
    lines.append(f"_generated {now_iso()}_")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--weekly", action="store_true", help="also build the weekly pattern report")
    args = ap.parse_args()

    with track("interruption_log"):
        new_records = find_interruptions()
        _append(new_records)

    print(f"interruption_log: {len(new_records)} new interruption(s) logged -> {INTERRUPTIONS}")
    for r in new_records:
        print(f"  + {r['text'][:70]}")
    if new_records:
        try:
            planner.feed_add("agent", f"{len(new_records)} interruption(s) logged")
        except Exception:  # noqa: BLE001
            pass

    if args.weekly:
        report = build_weekly_pattern()
        WEEKLY_PATTERN.write_text(report)
        print(f"weekly pattern -> {WEEKLY_PATTERN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
