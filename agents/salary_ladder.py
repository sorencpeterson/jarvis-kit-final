#!/usr/bin/env python3
"""A13 (FABLE-BUILD-QUEUE Section 5, MED): the salary-ladder tracker.
The queue applies across a wide comp spread; nobody has checked which bands
actually answer. This is the pure-aggregation view: applications, replies,
and interviews per posted-comp band, so next week's applications aim where
the conversions are.

WHAT: buckets every job in store/jobs.jsonl that was actually applied to
      (APPLIED_STATUSES; statuses are last-write-wins funnel stages, so
      replied/interview/rejected all imply applied) by posted comp_max into
      fixed BANDS (plus an "unknown comp" band for postings with no number),
      counts applied / replied (status replied|interview, a human wrote
      back) / interviewed (status interview) per band, writes
      store/salary_ladder.json {generated, bands: [{range, applied, replied,
      interviewed}], read} and feeds the one-line read.
WHEN: daily or weekly, anywhere in the morning chain after job_replies.py.
      Pure local aggregation, no LLM, sub-second.
RAILS: read-only against jobs.jsonl. Writes only its own JSON + the feed
      line. No pushes, no sends, no LLM. Fresh install (no jobs.jsonl)
      prints and exits 0 without writing.

Run:  .venv/bin/python agents/salary_ladder.py [--dry-run]
"""
from __future__ import annotations

import argparse
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
OUT = ROOT / "store" / "salary_ladder.json"
APPLIED_STATUSES = ("applied", "confirmed", "replied", "interview", "rejected")
REPLIED_STATUSES = ("replied", "interview")
MIN_APPLIED = 3  # a band needs this many applications before the read trusts it
BANDS = [  # (lo inclusive, hi exclusive, label) on comp_max
    (0, 100_000, "<$100k"),
    (100_000, 120_000, "$100-120k"),
    (120_000, 140_000, "$120-140k"),
    (140_000, 160_000, "$140-160k"),
    (160_000, 200_000, "$160-200k"),
    (200_000, None, "$200k+"),
]
UNKNOWN_BAND = "unknown comp"


def band_of(comp_max) -> str:
    try:
        cm = int(comp_max)
    except (TypeError, ValueError):
        return UNKNOWN_BAND
    if cm <= 0:
        return UNKNOWN_BAND
    for lo, hi, label in BANDS:
        if cm >= lo and (hi is None or cm < hi):
            return label
    return UNKNOWN_BAND


def build(rows: list[dict]) -> dict:
    counts = {label: {"range": label, "applied": 0, "replied": 0, "interviewed": 0}
              for *_x, label in BANDS}
    counts[UNKNOWN_BAND] = {"range": UNKNOWN_BAND, "applied": 0, "replied": 0, "interviewed": 0}
    for j in rows:
        if j.get("status") not in APPLIED_STATUSES:
            continue
        b = counts[band_of(j.get("comp_max"))]
        b["applied"] += 1
        if j.get("status") in REPLIED_STATUSES:
            b["replied"] += 1
        if j.get("status") == "interview":
            b["interviewed"] += 1
    bands = list(counts.values())
    return {"generated": now_iso(), "bands": bands, "read": _read(bands)}


def _read(bands: list[dict]) -> str:
    """One line: the band that converts best, by interview rate then reply rate,
    among bands with at least MIN_APPLIED applications."""
    eligible = [b for b in bands if b["applied"] >= MIN_APPLIED]
    if not eligible:
        return (f"Not enough per-band data yet (no band has {MIN_APPLIED}+ applications). "
                "Keep applying, the ladder fills itself.")
    best = max(eligible, key=lambda b: (b["interviewed"] / b["applied"],
                                        b["replied"] / b["applied"], b["applied"]))
    if best["replied"] == 0 and best["interviewed"] == 0:
        total = sum(b["applied"] for b in bands)
        return (f"No comp band is converting yet ({total} applications, 0 human replies "
                "across all bands). The problem is not the band, it is the funnel.")
    return (f"Best band: {best['range']}, {best['interviewed']} interview(s) and "
            f"{best['replied']} human repl(ies) from {best['applied']} applications. "
            "Aim next week's batch there.")


def run(dry_run: bool = False) -> dict | None:
    rows = jobs.load_jobs()
    if not rows:
        print("salary_ladder: no jobs.jsonl yet, nothing to aggregate")
        return None
    data = build(rows)
    for b in data["bands"]:
        if b["applied"]:
            print(f"  {b['range']:<14} applied {b['applied']:>4}  replied {b['replied']:>3}  "
                  f"interviewed {b['interviewed']:>3}")
    print(f"salary_ladder: {data['read']}")
    if dry_run:
        print("[dry-run] no write, no feed")
        return data
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    try:
        planner.feed_add("jobs", ("Salary ladder: " + data["read"])[:180])
    except Exception:  # noqa: BLE001 - feed hiccup must not fail the aggregation
        pass
    print(f"salary_ladder: wrote {OUT}")
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description="Comp-band conversion aggregation")
    ap.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    args = ap.parse_args()
    if args.dry_run:
        run(dry_run=True)
        return 0
    from runlog import track
    with track("salary_ladder"):
        run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
