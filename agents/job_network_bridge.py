#!/usr/bin/env python3
"""Apply->LinkedIn pairing bridge (2026-07-12, the rejection-map verdict: 28/46 rejections
are generic competitive "no"s — the cold-ATS ceiling. The one lever left is a human touch:
when a high-fit application goes out, put [OWNER]'s face in the hiring manager's notifications
the same week).

FLOW (rides existing rails end to end, every outward step human-gated):
  1. This agent (morning JOBS lane): picks jobs APPLIED in the last PAIR_WINDOW_H hours with
     fit >= pair_fit_min, not already paired, caps at pair_daily_cap/day (LinkedIn connect
     budget is 15/day total — pairing must never eat it all), writes store/pair_targets.json
     and stamps paired=true on the job records.
  2. networking-daily-source (6:02am Chrome operator): reads pair_targets.json, finds ONE
     right person per company (recruiter / hiring manager / head of the function), returns
     them with the "(applied: <title>)" context in the headline.
  3. [OWNER] approves in the green NETWORK tab (nothing sends without him), 6pm engage
     executes the connect (noteless, his standing preference).

Nothing here touches LinkedIn or sends anything — it only stages sourcing targets.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import jobs  # noqa: E402
import planner  # noqa: E402

TARGETS = ROOT / "store" / "pair_targets.json"
PAIR_WINDOW_H = 36


def _cfg(key: str, default: int) -> int:
    try:
        return int(planner._config().get(key) or default)
    except (ValueError, TypeError):
        return default


def _load_pending_targets() -> list[dict]:
    """Targets already staged but not yet consumed by the LinkedIn sourcing operator (which
    reads then deletes this file out-of-process). MUST be merged into, never replaced by, a
    new run (2026-07-13 fix, R2-24): the old write overwrote the whole file the moment ANY new
    job qualified, silently dropping every pending target the sourcing operator hadn't gotten
    to yet -- and since those jobs were already stamped paired=True, they became permanently
    unstageable (never re-picked, never in a targets file again)."""
    try:
        data = json.loads(TARGETS.read_text())
        t = data.get("targets")
        return [x for x in t if isinstance(x, dict) and x.get("job_id")] if isinstance(t, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def run() -> int:
    fit_min = _cfg("pair_fit_min", 72)
    cap = _cfg("pair_daily_cap", 5)
    cut = (datetime.now().astimezone() - timedelta(hours=PAIR_WINDOW_H)).isoformat()
    picks = []
    for j in jobs.load_jobs():
        if j.get("status") not in ("applied", "confirmed"):
            continue
        if j.get("paired") or (j.get("applied_at") or "") < cut:
            continue
        if (j.get("fit") or 0) < fit_min:
            continue
        picks.append(j)
    picks.sort(key=lambda x: -(x.get("fit") or 0))
    picks = picks[:cap]
    if not picks:
        # leave any prior targets in place until the sourcing operator consumes them
        print("pair bridge: nothing new to pair")
        return 0
    new_targets = [{"company": p.get("company") or "", "title": p.get("title") or "",
                    "job_id": p["id"], "fit": p.get("fit")} for p in picks]
    # atomic write (2026-07-13 hunt): the LinkedIn sourcing skill reads AND deletes this file
    # out-of-process; a bare write_text could hand it a half-written file. tmp+replace means the
    # reader always sees either the whole old file or the whole new one, never a truncation.
    import os
    from store_lib import _flock
    with _flock(TARGETS):
        # merge, don't replace (2026-07-13 fix, R2-24) -- see _load_pending_targets
        existing = _load_pending_targets()
        have = {t["job_id"] for t in existing}
        merged = existing + [t for t in new_targets if t["job_id"] not in have]
        _payload = json.dumps({"date": datetime.now().strftime("%Y-%m-%d"), "targets": merged},
                              ensure_ascii=False, indent=1)
        _tmp = TARGETS.parent / (TARGETS.name + ".tmp")
        _tmp.write_text(_payload)
        os.replace(_tmp, TARGETS)
    # stamp paired=True onto the CURRENT record, not the stale `picks` snapshot read at the top
    # of run() (Codex end-to-end pass, 2026-07-14): jobs._save({**p, ...}) rewrote the whole stale
    # record last-write-wins, so a job whose status advanced meanwhile (e.g. applied -> interview
    # from a reply landing during this run) got silently reverted. Re-read fresh right before the
    # append so the status carried forward is the live one, and skip a job already flagged.
    _current = {j["id"]: j for j in jobs.load_jobs()}
    for p in picks:
        cur = _current.get(p["id"])
        if cur and not cur.get("paired"):
            jobs._save({**cur, "paired": True})
    names = ", ".join(t["company"][:22] for t in new_targets)
    planner.feed_add("jobs", f"Pairing bridge: {len(new_targets)} applied companies staged for "
                             f"LinkedIn hiring-manager sourcing ({names})")
    print(f"pair bridge: staged {len(new_targets)} new (total pending {len(merged)}) "
          f"-> {TARGETS.name}: {names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
