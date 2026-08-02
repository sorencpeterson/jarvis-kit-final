#!/usr/bin/env python3
"""Archiver — move old append-only records out of the hot files (#36).

feed.jsonl / insights.jsonl / events.jsonl grow forever and nothing needs to scan
their full history in production (the dashboard only ever reads "recent"). Records
older than 90 days get moved into store/archive/<name>-YYYYQ.jsonl, bucketed by the
quarter the record's own ts fell in, so old data stays findable by date instead of
landing in one ever-growing dump file.

Explicitly NEVER touches jobs.jsonl, todos.jsonl, replies.jsonl, or
cold_pipeline.jsonl — those loaders (store_lib.load_todos, jobs.py, etc.) do
last-write-wins compaction by id across the *entire* file, so silently truncating
old rows would resurrect stale state or drop the only copy of a since-superseded
record. Only the pure-log files are safe to trim.

Rewrite of the live file is atomic (write to .tmp, os.replace) so a crash mid-run
can't leave the file half-written or duplicate an archived block on a re-run.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "store"
ARCHIVE = STORE / "archive"

sys.path.insert(0, str(ROOT))
from store_lib import _flock  # noqa: E402

# NEVER add jobs.jsonl / todos.jsonl / replies.jsonl / cold_pipeline.jsonl / proposals.jsonl
# here — those loaders need the full history for last-write-wins compaction by id (janitor.sh
# compacts those instead). Deliberately EXCLUDES usage.jsonl / runs.jsonl too: a 2026-07-07
# audit weighed adding them (unbounded logs) and REJECTED it — they grow only ~KB/day
# (negligible), and retro.py + channel_cac.py read their FULL history (lifetime feature
# tallies / CAC), so a 90-day archive would silently truncate those numbers for no real win.
ARCHIVABLE = ["feed.jsonl", "insights.jsonl", "events.jsonl"]
CUTOFF_DAYS = 90


def _quarter_tag(dt: datetime) -> str:
    q = (dt.month - 1) // 3 + 1
    return f"{dt.year}Q{q}"


def _parse_ts(rec: dict) -> datetime | None:
    ts = rec.get("ts")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt


def archive_file(name: str, cutoff: datetime) -> dict:
    path = STORE / name
    if not path.exists():
        return {"file": name, "status": "missing", "moved": 0, "kept": 0}

    # R2-48: hold the lock across read -> decide -> archive-append -> atomic
    # replace. Unlocked, a line appended by another writer between the read and
    # os.replace physically lands on disk but is never in our in-memory `kept`
    # snapshot, so the replace silently erases it (my test didn't cover this).
    # Matches the house pattern (store_lib._flock + tmp + os.replace).
    with _flock(path):
        lines = path.read_text().splitlines()
        kept: list[str] = []
        moved_by_quarter: dict[str, list[str]] = {}
        unparseable = 0

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                # Can't tell its age — keep it in the live file rather than risk losing it.
                kept.append(line)
                unparseable += 1
                continue
            dt = _parse_ts(rec)
            if dt is None or dt >= cutoff:
                kept.append(line)
                continue
            tag = _quarter_tag(dt)
            moved_by_quarter.setdefault(tag, []).append(line)

        moved_count = sum(len(v) for v in moved_by_quarter.values())
        if moved_count == 0:
            return {"file": name, "status": "ok", "moved": 0, "kept": len(kept),
                    "unparseable_kept": unparseable}

        ARCHIVE.mkdir(parents=True, exist_ok=True)
        stem = name[:-len(".jsonl")] if name.endswith(".jsonl") else name
        for tag, recs in moved_by_quarter.items():
            archive_path = ARCHIVE / f"{stem}-{tag}.jsonl"
            with archive_path.open("a") as f:
                for line in recs:
                    f.write(line + "\n")

        # Atomic rewrite of the live file: write to a sibling .tmp then os.replace so a
        # crash mid-write can't leave feed.jsonl truncated or half-written.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("".join(l + "\n" for l in kept))
        os.replace(tmp, path)

        return {"file": name, "status": "ok", "moved": moved_count, "kept": len(kept),
                "unparseable_kept": unparseable,
                "quarters": {t: len(v) for t, v in moved_by_quarter.items()}}


def main() -> int:
    cutoff = datetime.now().astimezone() - timedelta(days=CUTOFF_DAYS)
    results = [archive_file(name, cutoff) for name in ARCHIVABLE]
    for r in results:
        if r["status"] == "missing":
            print(f"archiver: {r['file']} not found, skipped")
        else:
            print(f"archiver: {r['file']} moved={r['moved']} kept={r['kept']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
