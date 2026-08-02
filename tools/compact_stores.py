#!/usr/bin/env python3
"""J186: compact id-keyed append-only jsonl stores once they get big.

The stores in this codebase (todos.jsonl, replies.jsonl, proposals.jsonl, jobs.jsonl,
and others) are append-only: every edit writes a NEW line with the same id, and loaders
do last-write-wins by id (see store_lib.load_todos / compact_jsonl for the canonical
pattern this mirrors). That's simple and crash-safe, but a file that's edited often grows
forever. This tool rewrites a store to keep only the LAST record per id, but only once
the file has actually grown past a size threshold (default 2MB) — small stores are left
alone entirely, untouched, not even opened for write.

Safety:
  - DRY-RUN by default. Pass --commit to actually rewrite files.
  - Every rewrite makes a .bak of the original FIRST (only in --commit mode).
  - Atomic rename (write to .tmp, os.replace over the original) so a crash mid-write
    can't leave a half-written store.
  - Auto-detects the id field per file (tries "id" first, the field every store in this
    codebase currently uses; add more field names to ID_FIELD_CANDIDATES if a future
    store uses something else). A file where NO line has any candidate id field is
    skipped with a warning, never blindly rewritten.

Usage:
  tools/compact_stores.py                  # dry-run: report what WOULD happen
  tools/compact_stores.py --commit         # actually compact oversized stores
  tools/compact_stores.py --min-mb 5       # raise the size threshold
  tools/compact_stores.py --dir /some/store --commit   # point at a different store/ dir
    (used by the fixture test so this NEVER runs --commit against the live store/ by
    accident from a test harness)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "store"

sys.path.insert(0, str(ROOT))
from store_lib import _flock  # noqa: E402

ID_FIELD_CANDIDATES = ("id",)
DEFAULT_MIN_MB = 2.0


def detect_id_field(path: Path, sample_lines: int = 50) -> str | None:
    """Return the id field name this file uses, or None if it doesn't look id-keyed
    (e.g. events.jsonl is a pure append log with no compaction-safe id semantics)."""
    checked = 0
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        checked += 1
        if checked > sample_lines:
            break
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            return None
        for cand in ID_FIELD_CANDIDATES:
            if cand in rec and rec[cand]:
                return cand
        return None  # first valid record has none of the candidate fields -> not id-keyed
    return None


def compact_one(path: Path, id_field: str) -> dict:
    """Rewrite `path` keeping only the last record per id_field. Returns a summary dict.
    Caller is responsible for the .bak + dry-run gating; this always writes for real.
    J: holds store_lib._flock across read -> decide -> replace so a concurrent
    append isn't silently erased by the atomic rewrite (same race class as
    archiver's R2-48)."""
    with _flock(path):
        by_id: dict[str, dict] = {}
        order: list[str] = []
        total_lines = 0
        bad_lines = 0
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            total_lines += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                bad_lines += 1
                continue
            rid = rec.get(id_field) if isinstance(rec, dict) else None
            if rid is None:
                bad_lines += 1
                continue
            if rid not in by_id:
                order.append(rid)
            by_id[rid] = rec
        tmp = path.with_suffix(path.suffix + ".compact.tmp")
        with tmp.open("w") as f:
            for rid in order:
                f.write(json.dumps(by_id[rid], ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    return {
        "lines_before": total_lines, "records_after": len(order),
        "bad_lines_skipped": bad_lines,
    }


def run(store_dir: Path, min_mb: float, commit: bool) -> list[dict]:
    results = []
    if not store_dir.is_dir():
        print(f"compact_stores: {store_dir} is not a directory, nothing to do")
        return results
    threshold_bytes = min_mb * 1024 * 1024
    for path in sorted(store_dir.glob("*.jsonl")):
        size = path.stat().st_size
        if size < threshold_bytes:
            continue  # below threshold: not even opened for write, left completely alone
        id_field = detect_id_field(path)
        if not id_field:
            print(f"  SKIP {path.name}: {size/1024/1024:.2f}MB but no id-keyed record shape detected "
                  f"(not a candidate for last-write-wins compaction, e.g. a pure append log)")
            results.append({"file": path.name, "action": "skipped_not_id_keyed", "size_mb": round(size / 1024 / 1024, 2)})
            continue
        if not commit:
            print(f"  DRY-RUN {path.name}: {size/1024/1024:.2f}MB, id_field={id_field!r} "
                  f"-> would compact (rerun with --commit)")
            results.append({"file": path.name, "action": "dry_run_would_compact",
                            "size_mb": round(size / 1024 / 1024, 2), "id_field": id_field})
            continue
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)
        summary = compact_one(path, id_field)
        new_size = path.stat().st_size
        print(f"  COMPACTED {path.name}: {summary['lines_before']} lines -> "
              f"{summary['records_after']} records "
              f"({size/1024/1024:.2f}MB -> {new_size/1024/1024:.2f}MB), "
              f"backup at {bak.name}, {summary['bad_lines_skipped']} bad/unkeyed lines skipped")
        results.append({"file": path.name, "action": "compacted", "id_field": id_field,
                        "size_mb_before": round(size / 1024 / 1024, 2),
                        "size_mb_after": round(new_size / 1024 / 1024, 2), **summary})
    if not results:
        print(f"compact_stores: no *.jsonl files in {store_dir} reached the {min_mb}MB threshold")
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(STORE), help="store directory to scan (default: live store/)")
    ap.add_argument("--min-mb", type=float, default=DEFAULT_MIN_MB, help="size threshold in MB")
    ap.add_argument("--commit", action="store_true", help="actually rewrite files (default: dry-run report only)")
    a = ap.parse_args()

    mode = "COMMIT" if a.commit else "DRY-RUN"
    print(f"compact_stores [{mode}]: scanning {a.dir} (threshold {a.min_mb}MB)")
    run(Path(a.dir), a.min_mb, a.commit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
