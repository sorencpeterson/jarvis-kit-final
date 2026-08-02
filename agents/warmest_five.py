#!/usr/bin/env python3
"""A8 (FABLE-BUILD-QUEUE Section 5, HIGH): the "warmest 5 to apply to today"
picker. The approved queue is a bulk list; his attention should land on the
best five, not scroll seventy.

WHAT: reads store/jobs.jsonl, takes status=approved (the pending apply queue),
      scores each by fit + freshness + salary, and writes the top 5 to
      store/warmest_five.json as {generated, picks:[{id,title,company,fit,why}]}
      plus ONE feed line ("Today's 5: ..."). The composite score:
        fit          the record's own fit score (jobs._fit, already 0-100ish)
        freshness    + (FRESH_MAX_DAYS - age_days), floored at 0; unknown age = 0
        salary       + comp_max / SALARY_DIV, capped at SALARY_CAP
WHEN: daily (morning chain, before the brief so the brief can read the file).
      Cheap: pure local reads, no LLM, no network.
RAILS: read-only against jobs.jsonl. Writes only store/warmest_five.json (full
      overwrite) + one feed line. NO pushes by design: the daily brief reads
      this file, one channel is enough. --dry-run prints instead of writing.
      Fresh install / empty queue writes {generated, picks: []} and skips the
      feed line (an empty shortlist is not news).

Run:  .venv/bin/python agents/warmest_five.py [--dry-run]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402
import jobs  # noqa: E402

# ---- tunables ----
PICK_N = 5           # the whole point: five, not the queue
FRESH_MAX_DAYS = 14  # freshness bonus decays to 0 at this age
SALARY_DIV = 10000   # comp_max / this = salary bonus ($150k -> +15 before cap)
SALARY_CAP = 15      # salary bonus ceiling
FEED_MAX = 180       # feed line length cap

OUT = ROOT / "store" / "warmest_five.json"


def _score(j: dict) -> float:
    """Composite: fit + freshness + salary. Higher = apply to this one first."""
    try:
        fit = float(j.get("fit", 50) or 50)
    except (TypeError, ValueError):
        fit = 50.0
    age = jobs._age_days(j.get("posted"))
    fresh = max(0.0, FRESH_MAX_DAYS - age) if age is not None else 0.0
    try:
        cm = float(j.get("comp_max") or 0)
    except (TypeError, ValueError):
        cm = 0.0
    sal = min(float(SALARY_CAP), cm / SALARY_DIV)
    return fit + fresh + sal


def _why(j: dict) -> str:
    age = jobs._age_days(j.get("posted"))
    bits = [f"fit {j.get('fit', 50)}"]
    bits.append(f"{age}d old" if age is not None else "age unknown")
    cm = j.get("comp_max")
    try:
        if cm:
            bits.append(f"up to ${int(cm) // 1000}k")
    except (TypeError, ValueError):
        pass
    return ", ".join(bits)


def build() -> dict:
    approved = [j for j in jobs.load_jobs() if j.get("status") == "approved"]
    ranked = sorted(approved, key=_score, reverse=True)[:PICK_N]
    picks = [{"id": j.get("id"), "title": j.get("title"), "company": j.get("company"),
              "fit": j.get("fit", 50), "why": _why(j)} for j in ranked]
    return {"generated": now_iso(), "picks": picks}


def run(dry_run: bool = False) -> dict:
    data = build()
    line = "Today's 5: " + "; ".join(f"{p['company']} ({p['title']})" for p in data["picks"])
    if dry_run:
        print(f"[dry-run] would write {OUT}:")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        if data["picks"]:
            print(f"[dry-run] feed line: {line[:FEED_MAX]}")
        else:
            print("[dry-run] no approved jobs, no feed line")
        return data
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    if data["picks"]:
        try:
            planner.feed_add("jobs", line[:FEED_MAX])
        except Exception:  # noqa: BLE001 — feed hiccup must not fail the pick
            pass
    print(f"warmest_five: {len(data['picks'])} pick(s) -> {OUT}")
    for p in data["picks"]:
        print(f"  {p['company']}: {p['title']} ({p['why']})")
    return data


def main() -> int:
    dry = "--dry-run" in sys.argv
    if dry:
        run(dry_run=True)
        return 0
    from runlog import track
    with track("warmest_five"):
        run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
