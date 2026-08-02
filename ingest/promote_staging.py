#!/usr/bin/env python3
"""Review chat-ingested items in store/inbox_staging.jsonl and promote them into
the trusted store (todos.jsonl). Normalizes fields, dedups by text, then clears
staging. Run this (or let the brain run it) when you want to pull chat to-dos in.

  uv run python ingest/promote_staging.py          # promote all staged
  uv run python ingest/promote_staging.py --list    # just show what's staged
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from store_lib import STAGING, append_todo, compact, load_todos, new_id, now_iso  # noqa: E402


def read_staging() -> list[dict]:
    if not STAGING.exists():
        return []
    items = []
    for line in STAGING.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("text"):
            items.append(rec)
    return items


def normalize(rec: dict, i: int) -> dict:
    text = str(rec["text"]).strip()
    return {
        "id": rec.get("id") or new_id(f"chat{text}{i}"),
        "text": text,
        "status": "inbox",
        "created": rec.get("created") or now_iso(),
        "source": "chat",
        "source_ref": rec.get("source_ref"),
        "project": rec.get("project"),
        "priority": rec.get("priority"),
        "scheduled_time": rec.get("scheduled_time"),
        "duration_min": rec.get("duration_min"),
        "gcal_event_id": None,
        "notes": rec.get("notes"),
    }


def main() -> int:
    staged = read_staging()
    if not staged:
        print("Nothing staged.")
        return 0
    if "--list" in sys.argv:
        for s in staged:
            print(f"  · {s['text']}")
        print(f"{len(staged)} staged. Run without --list to promote.")
        return 0

    existing_texts = {t["text"].strip().lower() for t in load_todos()}
    promoted = 0
    for i, rec in enumerate(staged):
        if rec["text"].strip().lower() in existing_texts:
            continue
        append_todo(normalize(rec, i))
        existing_texts.add(rec["text"].strip().lower())
        promoted += 1
        print(f"  + {rec['text']}")
    STAGING.write_text("")  # clear staging
    kept = compact()
    print(f"Promoted {promoted}. Store holds {kept} todo(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
