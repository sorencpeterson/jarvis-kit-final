#!/usr/bin/env python3
"""Triage / edit todos in the store. The brain uses this to classify (set project
+ priority), time-block (set --at/--dur, which flips status to 'scheduled'), add
a manual todo from the Mac, or mark one done. Edits append a same-id line; the
store compacts to last-write-wins.

  uv run python triage.py ls [--all]
  uv run python triage.py add "Call the lawyer back" --project ghl-dbr --priority 1
  uv run python triage.py set <id|text-substr> --project web-automation --priority 2
  uv run python triage.py set <id|text-substr> --at 2026-06-25T10:00 --dur 60
  uv run python triage.py done <id|text-substr>
  uv run python triage.py drop <id|text-substr>

<id|text-substr> matches a todo by exact id or a unique case-insensitive substring
of its text.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store_lib import (  # noqa: E402
    LOCAL_TZ, append_todo, compact, load_todos, new_id, now_iso,
)

PROJECTS = ("ghl-dbr", "agency-cold-outreach", "web-automation")


def resolve(todos, needle: str) -> dict:
    for t in todos:
        if t["id"] == needle:
            return t
    matches = [t for t in todos if needle.lower() in t["text"].lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        sys.exit(f"No todo matches '{needle}'.")
    sys.exit("Ambiguous — matches:\n" + "\n".join(f"  {m['id']}  {m['text']}" for m in matches))


def with_offset(at: str) -> str:
    """Accept 'YYYY-MM-DDTHH:MM' (local) and stamp the local (Eastern) offset."""
    off = LOCAL_TZ.utcoffset(None)
    sign = "-" if off.days < 0 else "+"
    return f"{at}:00{sign}{abs(off).seconds // 3600:02d}:00" if len(at) == 16 else at


def cmd_ls(args):
    todos = load_todos()
    if not args.all:
        todos = [t for t in todos if t["status"] not in ("done", "dropped")]
    if not todos:
        print("(empty)")
        return
    for t in todos:
        bits = [f"[{t['status']}]"]
        if t.get("priority"):
            bits.append(f"P{t['priority']}")
        if t.get("project"):
            bits.append(t["project"])
        if t.get("scheduled_time"):
            bits.append(f"@{t['scheduled_time'][:16]}")
        print(f"  {t['id']}  {t['text']}  {' '.join(bits[1:]) and '· ' + ' '.join(bits)}")


def cmd_add(args):
    text = args.text.strip()
    at = with_offset(args.at) if args.at else None
    rec = {
        "id": new_id(text), "text": text,
        "status": "scheduled" if at else "inbox",
        "created": now_iso(), "source": "manual", "source_ref": None,
        "project": args.project, "priority": args.priority,
        "scheduled_time": at, "duration_min": args.dur if at else None,
        "gcal_event_id": None, "notes": None,
    }
    append_todo(rec)
    compact()
    print(f"Added {rec['id']}: {text}")


def cmd_set(args):
    t = dict(resolve(load_todos(), args.target))
    if args.project is not None:
        t["project"] = args.project
    if args.priority is not None:
        t["priority"] = args.priority
    if args.at is not None:
        t["scheduled_time"] = with_offset(args.at)
        t["status"] = "scheduled"
        t["duration_min"] = args.dur or t.get("duration_min") or 30
    elif args.dur is not None:
        t["duration_min"] = args.dur
    if args.status is not None:
        t["status"] = args.status
    append_todo(t)
    compact()
    print(f"Updated {t['id']}: {t['text']}  [{t['status']}]"
          f"{' P' + str(t['priority']) if t.get('priority') else ''}"
          f"{' ' + t['project'] if t.get('project') else ''}"
          f"{' @' + t['scheduled_time'][:16] if t.get('scheduled_time') else ''}")


def _close(target, status):
    t = dict(resolve(load_todos(), target))
    t["status"] = status
    append_todo(t)
    compact()
    print(f"{status}: {t['text']}")


def main():
    p = argparse.ArgumentParser(description="Triage the second-brain store.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ls"); s.add_argument("--all", action="store_true"); s.set_defaults(fn=cmd_ls)

    s = sub.add_parser("add"); s.add_argument("text")
    s.add_argument("--project", choices=PROJECTS); s.add_argument("--priority", type=int, choices=[1, 2, 3])
    s.add_argument("--at"); s.add_argument("--dur", type=int); s.set_defaults(fn=cmd_add)

    s = sub.add_parser("set"); s.add_argument("target")
    s.add_argument("--project", choices=PROJECTS); s.add_argument("--priority", type=int, choices=[1, 2, 3])
    s.add_argument("--at"); s.add_argument("--dur", type=int)
    s.add_argument("--status", choices=["inbox", "scheduled", "doing", "done", "dropped"])
    s.set_defaults(fn=cmd_set)

    s = sub.add_parser("done"); s.add_argument("target")
    s.set_defaults(fn=lambda a: _close(a.target, "done"))
    s = sub.add_parser("drop"); s.add_argument("target")
    s.set_defaults(fn=lambda a: _close(a.target, "dropped"))

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
