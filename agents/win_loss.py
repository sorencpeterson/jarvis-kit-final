#!/usr/bin/env python3
"""Win/loss rollup on the warm-call cockpit — counts every disposition [OWNER] has
logged on his 58 booked-call hitlist, and separately lists the dead ones with
whatever reason he left, so a pattern in WHY calls die is visible instead of
buried in a jsonl he'd have to grep.

Why: server.py's /api/warm already counts "booked" for the money panel, but
every other dispo (no_answer, not_interested, callback, dead, etc.) just
accumulates with no rollup. This reads the raw log directly (last-write-wins
per id, same discipline as store_lib.load_todos) since it needs the full
per-dispo breakdown the API doesn't expose, not just the booked count.

Read-only against store/warm_dispo.jsonl; only write is store/winloss.json
(full overwrite each run). Run standalone: .venv/bin/python agents/win_loss.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT,):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402

WARM_DISPO = ROOT / "store" / "warm_dispo.jsonl"
OUT = ROOT / "store" / "winloss.json"
DEAD_DISPOS = {"dead", "not_interested", "wrong_number", "do_not_call"}


def _load_dispos() -> list[dict]:
    """last-write-wins by id: server.py's /api/warm/{wid}/dispo appends a fresh
    line every time [OWNER] re-disposes the same contact, so only the latest
    dispo per id should count toward the rollup."""
    if not WARM_DISPO.exists():
        return []
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for line in WARM_DISPO.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = r.get("id")
        if not rid:
            continue
        if rid not in by_id:
            order.append(rid)
        by_id[rid] = r
    return [by_id[i] for i in order]


def build_rollup() -> dict:
    rows = _load_dispos()
    counts = Counter(r.get("dispo", "(none)") for r in rows)
    dead = [
        {"id": r.get("id"), "dispo": r.get("dispo"),
         # server.py's WarmDispo model calls this field "note"; fall back to
         # "reason" defensively in case an older/other writer used that key.
         "reason": r.get("note") or r.get("reason") or ""}
        for r in rows if r.get("dispo") in DEAD_DISPOS
    ]
    return {"generated": now_iso(), "total_worked": len(rows), "counts": dict(counts), "dead": dead}


def main() -> int:
    rollup = build_rollup()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rollup, indent=2))
    print(f"win_loss: {rollup['total_worked']} worked, {dict(rollup['counts'])}, "
          f"{len(rollup['dead'])} dead -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
