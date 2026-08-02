#!/usr/bin/env python3
"""Backup verify (#38) — proves the git remote (if any) actually has a restorable
copy, instead of just trusting that `git push` succeeded at some point.

If the repo has an `origin` remote configured: shallow-clones it into a mktemp
directory, checks that the key files (app/server.py, store_lib.py) exist in that
fresh clone, then deletes the temp clone. That's the real test — a remote can
exist and even accept pushes while still missing files (wrong branch pushed,
.gitignore accidentally covering something critical, etc.), so only a from-scratch
clone proves the backup is actually usable.

If there's no remote configured (true as of this writing — this repo has no
`origin`), writes {"status": "no_remote"} and prints a reminder rather than
silently reporting success for a backup that doesn't exist.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

REPO = Path.home() / "Claude" / "second-brain"
STORE = REPO / "store"
OUT = STORE / "backup_verify.json"
KEY_FILES = ["app/server.py", "store_lib.py"]
# E (2026-07-13 audit): code existing in the clone proves nothing about the DATA.
# A backup can have every .py file and still have lost/never-tracked the actual
# business state (wrong branch, a .gitignore that swallowed store/, a push that
# silently excluded it) and this used to report "ok" forever. These are the
# load-bearing stores: jobs = the pipeline, ledger = the money, replies/proposals =
# the humans who wrote back and what's staged to send. Present AND parseable, not
# just present.
KEY_STORE_FILES = ["store/jobs.jsonl", "store/ledger.jsonl", "store/replies.jsonl",
                   "store/proposals.jsonl"]
REMINDER = ("No git remote configured for ~/Claude/second-brain — there is currently "
            "NO off-machine backup of this repo. Add one (e.g. a private GitHub repo) "
            "and re-run backup_verify.py.")


def _remote_url() -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(REPO), "remote", "get-url", "origin"],
                           capture_output=True, text=True, timeout=15)
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
    except Exception:  # noqa: BLE001
        return None


def _check_store_files(clone_path: Path) -> dict:
    """Present + parseable (every non-blank line is valid JSON) AND non-empty
    (>=1 real record) for each of KEY_STORE_FILES inside a cloned backup. A
    missing file means the backup never got the data; a present-but-unparseable
    OR present-but-empty file means it arrived corrupted/truncated -- either way
    "the code exists" was hiding a real gap.

    R3#6: a zero-byte (or all-blank-lines) file used to pass this check
    vacuously -- splitlines() on empty content is [], so the parse loop never
    runs and never raises, and "present + parseable" was trivially true for a
    file with ZERO records. These are load-bearing business stores that should
    never legitimately be completely empty on a live install; require at least
    one record or report it exactly like a corrupt/truncated file."""
    missing, corrupt = [], []
    for rel in KEY_STORE_FILES:
        p = clone_path / rel
        if not p.exists():
            missing.append(rel)
            continue
        try:
            records = 0
            for line in p.read_text().splitlines():
                line = line.strip()
                if line:
                    json.loads(line)
                    records += 1
            if records == 0:
                corrupt.append(rel)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            corrupt.append(rel)
    return {"missing": missing, "corrupt": corrupt}


def _verify_clone(url: str) -> dict:
    tmpdir = tempfile.mkdtemp(prefix="sb-backup-verify-")
    try:
        clone_path = Path(tmpdir) / "clone"
        r = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(clone_path)],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            return {"status": "clone_failed", "remote": url,
                    "error": r.stderr.strip()[:500]}

        missing = [f for f in KEY_FILES if not (clone_path / f).exists()]
        store_check = _check_store_files(clone_path)
        if missing or store_check["missing"]:
            status = "missing_files"
        elif store_check["corrupt"]:
            status = "corrupt_store_files"
        else:
            status = "ok"
        return {"status": status, "remote": url, "key_files_checked": KEY_FILES,
                "missing": missing, "store_files_checked": KEY_STORE_FILES,
                "store_files_missing": store_check["missing"],
                "store_files_corrupt": store_check["corrupt"]}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def verify() -> dict:
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    url = _remote_url()
    if not url:
        return {"ts": ts, "status": "no_remote", "reminder": REMINDER}
    result = _verify_clone(url)
    result["ts"] = ts
    return result


def main() -> int:
    result = verify()
    STORE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    if result["status"] == "no_remote":
        print(REMINDER)
        print(f"backup_verify: no_remote -> {OUT}")
        return 0
    ok = result["status"] == "ok"
    print(f"backup_verify: {result['status']} -> {OUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
