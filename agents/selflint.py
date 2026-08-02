#!/usr/bin/env python3
"""Self-lint — cheap static hygiene sweep over the repo (#46).

Two checks, both read-only:
  1. TODO/FIXME/XXX markers left in py/sh/html source (skips .venv/.git/node_modules
     so vendored code doesn't drown out [OWNER]'s own markers).
  2. config.json keys the code actually *reads* (config.get("x") / _config().get("x"))
     but that are missing from store/config.json — the classic "I added the read but
     forgot to add the default" bug that silently degrades a feature to '' or None.

Writes store/lint.json for the dashboard to surface later; prints a one-line count
so a human running it by hand gets an instant signal. No CLI calls, no network.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "store"
CONFIG = STORE / "config.json"
OUT = STORE / "lint.json"

SKIP_DIRS = {".venv", ".git", "node_modules", "__pycache__", "tts-cache"}
SOURCE_EXTS = {".py", ".sh", ".html"}
MARKER_RE = re.compile(r"\b(TODO|FIXME|XXX)\b[:\s]?(.*)")
# Matches config.get("key") and _config().get("key") — the two call shapes used
# throughout agents/ and app/ for reading store/config.json.
CONFIG_GET_RE = re.compile(r'(?:_config\(\)|config)\.get\(\s*["\']([A-Za-z0-9_]+)["\']')


def _iter_source_files():
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix not in SOURCE_EXTS:
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        yield p


def _load_config_keys() -> set[str]:
    try:
        cfg = json.loads(CONFIG.read_text())
        return set(cfg.keys())
    except (OSError, json.JSONDecodeError):
        return set()


def find_markers() -> list[dict]:
    findings = []
    for path in _iter_source_files():
        try:
            lines = path.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        rel = str(path.relative_to(ROOT))
        for i, line in enumerate(lines, start=1):
            m = MARKER_RE.search(line)
            if not m:
                continue
            findings.append({"file": rel, "line": i, "kind": m.group(1),
                              "text": line.strip()[:200]})
    return findings


def find_missing_config_keys() -> list[dict]:
    known = _load_config_keys()
    if not known:
        # config.json unreadable/missing — nothing to compare against, skip rather
        # than falsely flag every referenced key as "missing".
        return []
    referenced: dict[str, tuple[str, int]] = {}
    for path in _iter_source_files():
        if path.suffix != ".py":
            continue
        try:
            lines = path.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        rel = str(path.relative_to(ROOT))
        for i, line in enumerate(lines, start=1):
            for m in CONFIG_GET_RE.finditer(line):
                key = m.group(1)
                if key not in known and key not in referenced:
                    referenced[key] = (rel, i)
    return [{"file": f, "line": ln, "kind": "missing_config_key", "text": key}
            for key, (f, ln) in referenced.items()]


def main() -> int:
    findings = find_markers() + find_missing_config_keys()
    out = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
           "findings": findings}
    STORE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"selflint: {len(findings)} findings -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
