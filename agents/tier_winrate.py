#!/usr/bin/env python3
"""B8: tier win-rate. "Which price point actually closes" is a question the
proposal store can already answer and nobody was asking it. This aggregates
proposals by tier/price so pricing decisions come from counts, not vibes, and
says so PLAINLY when the counts are too small to mean anything.

WHAT: reads store/proposals.jsonl (last-write-wins by id), groups live records by
      tier (webfix/landing/standard/booking/whiteglove/agencyfirst) with their
      price point, and counts per tier: staged, sent (sending counted with sent:
      it is a transient claim state), accepted, plus skipped/superseded as
      "discarded" for context. Acceptance rate = accepted / (sent + accepted),
      the deals that actually REACHED a prospect; staged proposals are inventory,
      not at-bats, so they never inflate the denominator. Any tier whose
      denominator is under MIN_N gets acceptance_rate=null and the honest caveat
      "too little data to read (n=X)" instead of a fake percentage, and when EVERY
      tier is under MIN_N the top-level note says the whole table is directional
      at best. Writes store/tier_winrate.json (full overwrite) + one feed line.
WHEN: any cadence (morning chain, Sunday retro, ad hoc before repricing). Pure
      local aggregation, no LLM, sub-second. Fresh install (no proposals) prints
      and exits 0.
RAILS: read-only against the proposal store. Only writes are the output JSON and
      one feed line. No pushes, no sends, no GHL.

Tunables (change here, nowhere else):
  MIN_N  = 5    denominator below this gets a caveat instead of a rate

Run:  .venv/bin/python agents/tier_winrate.py [--dry-run]
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

PROPOSALS = ROOT / "store" / "proposals.jsonl"
OUT = ROOT / "store" / "tier_winrate.json"

MIN_N = 5


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


def _rows() -> list[dict]:
    by_id: dict[str, dict] = {}
    for r in _read_jsonl(PROPOSALS):
        if r.get("id"):
            by_id[r["id"]] = r
    return list(by_id.values())


def build(rows: list[dict] | None = None) -> dict:
    """Pure: rows (or the real store) -> the winrate table. Kept separate from
    run() so tests feed synthetic rows straight in."""
    if rows is None:
        rows = _rows()
    if not rows:
        return {}
    tiers: dict[str, dict] = {}
    for r in rows:
        tier = (r.get("tier") or "(untiered)").strip() or "(untiered)"
        t = tiers.setdefault(tier, {"price": None, "staged": 0, "sent": 0,
                                    "accepted": 0, "discarded": 0})
        try:
            price = float(r.get("price") or 0)
            if price and t["price"] is None:
                t["price"] = price
        except (TypeError, ValueError):
            pass
        status = r.get("status") or ""
        if status == "staged":
            t["staged"] += 1
        elif status in ("sent", "sending"):
            t["sent"] += 1
        elif status == "accepted":
            t["accepted"] += 1
        elif status in ("skipped", "superseded"):
            t["discarded"] += 1

    all_thin = True
    for t in tiers.values():
        n = t["sent"] + t["accepted"]
        t["n"] = n
        if n >= MIN_N:
            t["acceptance_rate"] = round(t["accepted"] / n, 3)
            t["caveat"] = None
            all_thin = False
        else:
            t["acceptance_rate"] = None
            t["caveat"] = f"too little data to read (n={n})"

    note = None
    if all_thin:
        note = (f"every tier is under n={MIN_N} sent-or-accepted; nothing here "
                "is a win rate yet, it is a count of what exists")
    return {"generated": now_iso(), "min_n": MIN_N, "total_records": len(rows),
            "tiers": dict(sorted(tiers.items(), key=lambda kv: -(kv[1]["price"] or 0))),
            "note": note}


def run(*, dry_run: bool = False) -> int:
    data = build()
    if not data:
        print("tier winrate: no proposals on record, nothing to aggregate")
        return 0

    lines = []
    for tier, t in data["tiers"].items():
        rate = (f"{t['acceptance_rate'] * 100:.0f}%" if t["acceptance_rate"] is not None
                else t["caveat"])
        price = f"${t['price']:,.0f}" if t.get("price") else "$?"
        lines.append(f"  {tier} {price}: staged {t['staged']}, sent {t['sent']}, "
                     f"accepted {t['accepted']} -> {rate}")
    print(f"tier winrate: {data['total_records']} proposal record(s) across "
          f"{len(data['tiers'])} tier(s)")
    for ln in lines:
        print(ln)
    if data.get("note"):
        print(f"  NOTE: {data['note']}")
    if dry_run:
        print("[dry-run] nothing written")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(OUT)
    try:
        readable = data["note"] or ", ".join(
            f"{tier} {t['acceptance_rate'] * 100:.0f}%" for tier, t in data["tiers"].items()
            if t["acceptance_rate"] is not None)
        planner.feed_add("agent", f"Tier winrate updated: {readable[:120]}")
    except Exception:  # noqa: BLE001
        pass
    print(f"tier winrate -> {OUT}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="proposal acceptance rate per tier/price")
    ap.add_argument("--dry-run", action="store_true", help="print the table, write nothing")
    args = ap.parse_args()
    from runlog import track
    with track("tier_winrate"):
        return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
