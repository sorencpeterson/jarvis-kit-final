#!/usr/bin/env python3
"""Chaos drill (#102) — SIMULATED, restore-safe exercise of the "a log file
disappeared" failure mode.

What it actually does, in order:
  1. Temporarily moves agents/morning.log to agents/morning.log.chaos-bak.
  2. Curls the server's /api/health endpoint (5s timeout) to confirm the server is
     still alive and unaffected by the missing log file — the actual thing being
     tested is "does losing this log file take anything else down with it."
     /api/health requires the same X-Brain-Token every other agent in this repo
     sends (see store_lib.secret("brain_token")), so this sends it too — otherwise
     every run would report a false FAILED on the auth check alone, not liveness.
  3. Moves the file back to its original location.
  4. Verifies restoration by comparing file size before/after the round trip.

Never touches launchd, never kills or restarts any live process, never deletes
anything (a move + move-back, not a copy + delete). If anything goes wrong
mid-drill the file is restored before raising, so a bad run never leaves
morning.log missing.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"
STORE = ROOT / "store"
OUT = STORE / "chaos.json"
TARGET = AGENTS / "morning.log"
BACKUP = AGENTS / "morning.log.chaos-bak"
HEALTH_URL = "http://127.0.0.1:8765/api/health"

sys.path.insert(0, str(ROOT))


def _brain_token() -> str:
    """Same secret() resolution every other agent uses (env -> .env -> config.json),
    duplicated minimally here rather than importing store_lib for one call."""
    try:
        from store_lib import secret
        return secret("brain_token")
    except Exception:  # noqa: BLE001
        return ""


def _curl_health() -> tuple[bool, str]:
    token = _brain_token()
    cmd = ["curl", "-m", "5", "-sf", HEALTH_URL]
    if token:
        cmd[3:3] = ["-H", f"X-Brain-Token: {token}"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        return r.returncode == 0, (r.stdout[:200] if r.returncode == 0 else r.stderr[:200])
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def run_drill() -> dict:
    notes = []
    passed = True

    if not TARGET.exists():
        return {"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                "passed": False,
                "notes": [f"{TARGET} does not exist — nothing to drill against"]}

    original_size = TARGET.stat().st_size
    notes.append(f"target={TARGET.name} original_size={original_size}")

    try:
        TARGET.rename(BACKUP)
        notes.append("moved morning.log -> morning.log.chaos-bak")

        alive, detail = _curl_health()
        notes.append(f"health check during outage: {'OK' if alive else 'FAILED'} ({detail})")
        if not alive:
            passed = False

    finally:
        # Restore no matter what happened above — this is the one step that must
        # never be skipped, so it lives in `finally` rather than the happy path.
        if BACKUP.exists():
            BACKUP.rename(TARGET)
            notes.append("restored morning.log.chaos-bak -> morning.log")
        elif not TARGET.exists():
            notes.append("CRITICAL: neither morning.log nor its backup exist after drill")
            passed = False

    if TARGET.exists():
        restored_size = TARGET.stat().st_size
        size_match = restored_size == original_size
        notes.append(f"restored_size={restored_size} match={size_match}")
        if not size_match:
            passed = False
    else:
        notes.append("CRITICAL: morning.log missing after restore attempt")
        passed = False

    return {"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "passed": passed, "notes": notes}


def main() -> int:
    result = run_drill()
    STORE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"chaos_drill: {'PASSED' if result['passed'] else 'FAILED'} -> {OUT}")
    for n in result["notes"]:
        print(f"  {n}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
