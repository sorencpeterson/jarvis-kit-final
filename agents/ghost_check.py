#!/usr/bin/env python3
"""Ghost check (#74) — flags applications that went quiet after confirmation. A
job sitting at 'confirmed' for 10+ days with no status movement (no interview,
no rejection) is a company that's gone dark, and nothing currently surfaces that
so it just rots silently in the queue.

No contact email is known for these (confirmed just means an ATS auto-reply
landed, not that a human replied), so there's nothing safe to draft — this only
raises a todo nudging [OWNER] to follow up himself. Each ghosted job gets exactly
ONE todo ever: store/ghosted.json tracks job_ids already flagged so re-runs don't
spam the inbox with the same nudge.

Read-only against store/jobs.jsonl; writes are store/ghosted.json (list of job_ids
flagged) + up to 5 new todos per run. Run standalone: .venv/bin/python agents/ghost_check.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import append_todo, new_id, now_iso  # noqa: E402
import planner  # noqa: E402
import jobs  # noqa: E402

GHOSTED = ROOT / "store" / "ghosted.json"
GHOST_DAYS = 10
CAP = 5


def _flagged() -> list[str]:
    try:
        data = json.loads(GHOSTED.read_text())
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _confirmed_at(j: dict) -> str:
    """confirmed_at if the record has one (it doesn't today per the job schema),
    else fall back to applied_at (what actually gets stamped when status flips),
    else created. Whichever is the best-available signal for 'how long since we
    last heard anything real'."""
    return j.get("confirmed_at") or j.get("applied_at") or j.get("created") or ""


def _stale_confirmed(all_jobs: list[dict]) -> list[dict]:
    cutoff = datetime.now().astimezone() - timedelta(days=GHOST_DAYS)
    out = []
    for j in all_jobs:
        if j.get("status") != "confirmed":
            continue
        ts = _confirmed_at(j)
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            continue
        if dt < cutoff:
            out.append(j)
    return out


def build_flags() -> list[dict]:
    already = set(_flagged())
    stale = _stale_confirmed(jobs.load_jobs())
    out = []
    for j in stale:
        jid = j.get("id")
        if not jid or jid in already:
            continue
        out.append(j)
        already.add(jid)
        if len(out) >= CAP:
            break
    return out


def main() -> int:
    new_ghosts = build_flags()
    if not new_ghosts:
        print("ghost_check: nothing new gone silent")
        return 0
    for j in new_ghosts:
        append_todo({
            "id": new_id("ghost_" + j["id"]),
            "text": f"Nudge {j.get('company') or '?'} re {j.get('title') or '?'} (10+ days silent)",
            "status": "inbox", "created": now_iso(), "source": "ghost_check", "source_ref": j["id"],
            "project": None, "priority": 2, "scheduled_time": None, "duration_min": None,
            "gcal_event_id": None, "notes": None,
        })
    all_flagged = _flagged() + [j["id"] for j in new_ghosts]
    GHOSTED.parent.mkdir(parents=True, exist_ok=True)
    GHOSTED.write_text(json.dumps(all_flagged, indent=2))
    planner.feed_add("agent", f"Ghost check: {len(new_ghosts)} silent application(s) flagged")
    print(f"ghost_check: flagged {len(new_ghosts)} job(s) -> {GHOSTED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
