#!/usr/bin/env python3
"""Sleep-aware mode (#83) — flag a gentle morning when last night's sleep was short.

Why: store/wellness.jsonl already ingests sleep_h (Apple Health via Shortcuts POST,
see app/server.py /api/wellness), but nothing acts on it. This reads the latest
wellness record; if sleep_h < 6, it drops a store/.gentle-morning flag file (so a
future brief/morning step can dial down the ask), and removes the flag once sleep
recovers. This agent ONLY manages the flag — it does not itself soften anything
(daily_brief.py etc. are off-limits to edit); wiring is left for later, on purpose.

Read-only against wellness.jsonl; only write is store/.gentle-morning (create/touch
or remove). Run standalone: .venv/bin/python agents/sleep_aware.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402

WELLNESS = ROOT / "store" / "wellness.jsonl"
FLAG = ROOT / "store" / ".gentle-morning"
SLEEP_THRESHOLD_H = 6.0


def _latest_wellness() -> dict | None:
    if not WELLNESS.exists():
        return None
    lines = [l for l in WELLNESS.read_text().splitlines() if l.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return None


def run() -> int:
    latest = _latest_wellness()
    if latest is None:
        print("sleep_aware: no wellness data yet (store/wellness.jsonl empty/missing) — no action")
        return 0

    sleep_h = latest.get("sleep_h")
    if sleep_h is None:
        print("sleep_aware: latest wellness record has no sleep_h — no action")
        return 0

    today = now_iso()[:10]
    if sleep_h < SLEEP_THRESHOLD_H:
        FLAG.parent.mkdir(parents=True, exist_ok=True)
        FLAG.write_text(f"{today} sleep_h={sleep_h}\n")
        print(f"sleep_aware: sleep_h={sleep_h} < {SLEEP_THRESHOLD_H} -> gentle-morning flag SET ({FLAG})")
    else:
        removed = FLAG.exists()
        FLAG.unlink(missing_ok=True)
        state = "removed" if removed else "already absent"
        print(f"sleep_aware: sleep_h={sleep_h} >= {SLEEP_THRESHOLD_H} -> gentle-morning flag {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
