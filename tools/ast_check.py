#!/usr/bin/env python3
"""Syntax gate for `make doctor`: ast.parse every agents/*.py and app/*.py, report any
file with a syntax error, exit 1 if any found. This is the cheapest possible check (no
imports, no execution) so a broken edit is caught before it ever gets a chance to run.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    # every .py in the repo, not just agents/+app/ — a syntax error in store_lib.py or
    # tools/ or dashboard/ (all run in production) used to slip past doctor (2026-07-07)
    skip = {".venv", "__pycache__", "node_modules", "video"}
    files = sorted(f for f in ROOT.rglob("*.py") if not any(p in skip for p in f.parts))
    errors = []
    for f in files:
        try:
            ast.parse(f.read_text())
        except SyntaxError as e:
            errors.append((f, e))

    if errors:
        print(f"FAIL ast-check: {len(errors)} file(s) with syntax errors")
        for f, e in errors:
            print(f"  {f.relative_to(ROOT)}: {e}")
        return 1

    print(f"PASS ast-check: {len(files)} file(s) parse cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
