#!/usr/bin/env python3
"""B12: reactivation triage. The tier-2 warm list (the repliers: people who
answered a cold touch once and then nothing) is one undifferentiated pile, and
"reactivate the repliers" stalls because lane one and lane three need different
first lines. This splits the pile into three workable lanes so the reactivation
drip and [OWNER]'s manual passes each start with the right cohort.

WHAT: reads ~/Claude/WARM-HITLIST.csv rows with tier=2 (tier semantics per the
      hitlist build: 1 = booked calls, 2 = repliers) and buckets each into ONE lane:
        said_no:  suppressed (store/suppress.jsonl email match), dispo'd dead
                  (store/warm_dispo.jsonl), or tags carrying a no-go marker
                  (unsub / dnd / do not / not interested / said no / stop).
                  These are OFF LIMITS for the drip; the lane exists so nobody
                  re-adds them by accident.
        replied_recently_then_quiet: deal age <= RECENT_DAYS. The freshest
                  conversations that went quiet; first-touch manual material.
        old_warm: everything older. Reactivation-campaign material (the 423 drip).
      Writes store/reactivation_triage.json {generated, source_rows, lanes:
      {<lane>: {count, top: [up to TOP_N {name, company, email, age_days, stage}]}}}
      (recent lanes sorted freshest-first) plus ONE feed line. That is ALL: no
      drafts, no enrollment, no GHL, per spec, this run is read-only aggregation.
WHEN: any cadence (morning chain or before a drip build). Sub-second, no LLM.
      Fresh install (no hitlist CSV) prints and exits 0.
RAILS: read-only against the hitlist, suppress and dispo stores. Only writes are
      the output JSON (full overwrite) and one feed line. Nothing is contacted.

HONEST LIMITS: "replied recently" is proxied by the hitlist's deal_age_days (GHL
deal age), not by a per-contact last-message timestamp, which the CSV does not
carry. store/replies.jsonl is consulted only as a said-no source (a remove-intent
reply row), not for recency, since it holds test rows today.

Tunables (change here, nowhere else):
  RECENT_DAYS = 90    deal age at or under this lands in replied_recently_then_quiet
  TOP_N       = 10    contacts listed per lane in the output JSON
  NO_GO_TAGS  = ("unsub", "dnd", "do not", "not interested", "said no", "stop")

Run:  .venv/bin/python agents/reactivation_triage.py [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402

HITLIST = Path.home() / "Claude" / "WARM-HITLIST.csv"
SUPPRESS = ROOT / "store" / "suppress.jsonl"
DISPO = ROOT / "store" / "warm_dispo.jsonl"
REPLIES = ROOT / "store" / "replies.jsonl"
OUT = ROOT / "store" / "reactivation_triage.json"

RECENT_DAYS = 90
TOP_N = 10
NO_GO_TAGS = ("unsub", "dnd", "do not", "not interested", "said no", "stop")

LANES = ("replied_recently_then_quiet", "old_warm", "said_no")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _rid(phone: str, name: str) -> str:
    """warm_block.py's id formula, verbatim, so dispo rows join back to CSV rows."""
    import hashlib
    return "w_" + hashlib.sha1((phone or name).encode()).hexdigest()[:10]


def _no_signals() -> tuple[set[str], set[str]]:
    """(suppressed_emails, dead_ids): every locally recorded 'leave them alone'."""
    suppressed = set()
    for r in _read_jsonl(SUPPRESS):
        em = (r.get("email") or "").strip().lower()
        if em:
            suppressed.add(em)
    for r in _read_jsonl(REPLIES):
        if (r.get("intent") or "") == "remove":
            em = (r.get("email") or "").strip().lower()
            if em:
                suppressed.add(em)
    dead = set()
    for r in _read_jsonl(DISPO):
        if r.get("dispo") == "dead" and r.get("id"):
            dead.add(r["id"])
    return suppressed, dead


def bucket(row: dict, suppressed: set[str], dead_ids: set[str]) -> str:
    """One lane per tier-2 row. said_no wins over everything (never re-touch)."""
    email = (row.get("email") or "").strip().lower()
    tags = (row.get("tags") or "").lower()
    phone = (row.get("phone") or "").strip()
    name = (row.get("name") or "").strip() or (row.get("company") or "").strip()
    if email and email in suppressed:
        return "said_no"
    if _rid(phone, name) in dead_ids:
        return "said_no"
    if any(t in tags for t in NO_GO_TAGS):
        return "said_no"
    try:
        age = int(row.get("deal_age_days") or 0)
    except (TypeError, ValueError):
        age = 0
    return "replied_recently_then_quiet" if age <= RECENT_DAYS else "old_warm"


def build() -> dict:
    if not HITLIST.exists():
        return {}
    rows = [r for r in csv.DictReader(open(HITLIST, newline=""))
            if (r.get("tier") or "").strip() == "2"]
    if not rows:
        return {}
    suppressed, dead_ids = _no_signals()
    lanes: dict[str, list[dict]] = {lane: [] for lane in LANES}
    for r in rows:
        try:
            age = int(r.get("deal_age_days") or 0)
        except (TypeError, ValueError):
            age = 0
        lanes[bucket(r, suppressed, dead_ids)].append({
            "name": (r.get("name") or "").strip(),
            "company": (r.get("company") or "").strip(),
            "email": (r.get("email") or "").strip().lower(),
            "age_days": age, "stage": (r.get("stage") or "").strip()})
    for lane in LANES:
        lanes[lane].sort(key=lambda x: x["age_days"])  # freshest first
    return {"generated": now_iso(), "source_rows": len(rows),
            "recent_days_cutoff": RECENT_DAYS,
            "lanes": {lane: {"count": len(items), "top": items[:TOP_N]}
                      for lane, items in lanes.items()}}


def run(*, dry_run: bool = False) -> int:
    data = build()
    if not data:
        print("reactivation triage: no tier-2 repliers found "
              f"({HITLIST.name} missing or empty), nothing to bucket")
        return 0

    counts = {lane: data["lanes"][lane]["count"] for lane in LANES}
    line = (f"{data['source_rows']} repliers triaged: "
            f"{counts['replied_recently_then_quiet']} recent-then-quiet, "
            f"{counts['old_warm']} old-warm, {counts['said_no']} said-no")
    if dry_run:
        print(f"[dry-run] {line} (nothing written)")
        for lane in LANES:
            tops = ", ".join(x["company"] or x["name"] for x in data["lanes"][lane]["top"][:3])
            print(f"  {lane}: {counts[lane]}" + (f" (e.g. {tops})" if tops else ""))
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(OUT)
    try:
        planner.feed_add("agent", f"Reactivation triage: {line}")
    except Exception:  # noqa: BLE001
        pass
    print(f"reactivation triage: {line} -> {OUT}")
    for lane in LANES:
        tops = ", ".join(x["company"] or x["name"] for x in data["lanes"][lane]["top"][:3])
        print(f"  {lane}: {counts[lane]}" + (f" (e.g. {tops})" if tops else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="bucket the tier-2 repliers into reactivation lanes")
    ap.add_argument("--dry-run", action="store_true", help="print the buckets, write nothing")
    args = ap.parse_args()
    from runlog import track
    with track("reactivation_triage"):
        return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
