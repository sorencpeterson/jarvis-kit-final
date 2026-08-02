#!/usr/bin/env python3
"""Lost-to-competitor tracking (Q262) — who beat us, at what price, with what.
The item calls this "the only competitive analysis that matters", scoped to
warm_dispo notes + a competitor-mention autopsy log. Scaffold only: there is
no autopsy_log store in this repo yet (no store/autopsy*.jsonl exists), and
store/warm_dispo.jsonl is currently EMPTY (0 rows) -- so this is [E] by
construction, honestly, per the task brief ("scaffold reading warm_dispo
notes + autopsy_log for competitor mentions -> store/lost_to.json ([E],
fixture-tested)").

Detection approach (ready to run the moment real notes exist):
- scans warm_dispo 'note' field (win_loss.py's DEAD_DISPOS list -- dead,
  not_interested, wrong_number, do_not_call -- are the dispos worth scanning,
  since those are the losses) for competitor-mention markers: "went with",
  "using X instead", "already has a site from", "hired someone else", a
  trailing "$<number>" near those phrases as a price signal.
- scans an autopsy_log if/when one exists (store/autopsy_log.jsonl, guessed
  shape: {ts, deal, note}) with the same marker scan.

Writes store/lost_to.json (full overwrite). Run standalone:
.venv/bin/python agents/lost_to.py
.venv/bin/python agents/lost_to.py --fixture
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from store_lib import now_iso  # noqa: E402

WARM_DISPO = ROOT / "store" / "warm_dispo.jsonl"
AUTOPSY_LOG = ROOT / "store" / "autopsy_log.jsonl"  # doesn't exist yet -- scaffold reads it if it ever does
OUT = ROOT / "store" / "lost_to.json"

DEAD_DISPOS = {"dead", "not_interested", "wrong_number", "do_not_call"}  # matches win_loss.py exactly

COMPETITOR_MARKERS = (
    r"went with\s+([A-Za-z0-9 &'\-]{2,40})",
    r"using\s+([A-Za-z0-9 &'\-]{2,40})\s+instead",
    r"already (?:has|got) a site (?:from|by|via)\s+([A-Za-z0-9 &'\-]{2,40})",
    r"hired\s+([A-Za-z0-9 &'\-]{2,40})\s+instead",
    r"signed with\s+([A-Za-z0-9 &'\-]{2,40})",
)
PRICE_MARKER = re.compile(r"\$([0-9][0-9,]*(?:\.[0-9]{2})?)")


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


def _dedup_by_id(rows: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        rid = r.get("id")
        if rid is None:
            continue
        if rid not in by_id:
            order.append(rid)
        by_id[rid] = r
    return [by_id[i] for i in order]


def _scan_note(note: str) -> dict | None:
    if not note:
        return None
    for pattern in COMPETITOR_MARKERS:
        m = re.search(pattern, note, re.I)
        if m:
            price_m = PRICE_MARKER.search(note)
            return {
                "competitor_mention": m.group(1).strip(),
                "price_mentioned": float(price_m.group(1).replace(",", "")) if price_m else None,
                "raw_note": note,
            }
    return None


def scan_warm_dispo(rows: list[dict]) -> list[dict]:
    out = []
    for r in _dedup_by_id(rows):
        if r.get("dispo") not in DEAD_DISPOS:
            continue
        note = r.get("note") or ""
        hit = _scan_note(note)
        if hit:
            out.append({"source": "warm_dispo", "id": r.get("id"), "dispo": r.get("dispo"),
                       "ts": r.get("ts"), **hit})
    return out


def scan_autopsy_log(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        note = r.get("note") or r.get("reason") or ""
        hit = _scan_note(note)
        if hit:
            out.append({"source": "autopsy_log", "id": r.get("id") or r.get("deal"),
                       "ts": r.get("ts"), **hit})
    return out


def build(warm_hits: list[dict], autopsy_hits: list[dict], warm_total: int, autopsy_exists: bool) -> dict:
    all_hits = warm_hits + autopsy_hits
    from collections import Counter
    by_competitor = Counter(h["competitor_mention"].lower() for h in all_hits)
    prices = [h["price_mentioned"] for h in all_hits if h["price_mentioned"] is not None]
    status = "[E] insufficient data" if len(all_hits) < 3 else "ready"
    return {
        "generated": now_iso(),
        "status": status,
        "warm_dispo_dead_count": warm_total,
        "warm_dispo_hits": len(warm_hits),
        "autopsy_log_exists": autopsy_exists,
        "autopsy_log_hits": len(autopsy_hits),
        "total_hits": len(all_hits),
        "by_competitor": dict(by_competitor.most_common()),
        "avg_price_lost_at": round(sum(prices) / len(prices), 2) if prices else None,
        "hits": all_hits,
        "note": ("[E]: store/autopsy_log.jsonl doesn't exist in this repo yet, and "
                "warm_dispo has too few dead-dispo notes with a competitor marker "
                "to say anything real. This is ready to run the moment either store "
                "fills in -- no code changes needed, just real notes to scan." if status.startswith("[E]")
                else "enough hits to report a real competitor breakdown."),
    }


def _fixture_data() -> tuple[list[dict], list[dict]]:
    warm_rows = [
        {"id": "fx1", "dispo": "dead", "note": "went with WebFlowGuys, said they were cheaper", "ts": now_iso()},
        {"id": "fx2", "dispo": "not_interested", "note": "already has a site from their nephew, not touching it", "ts": now_iso()},
        {"id": "fx3", "dispo": "dead", "note": "signed with LocalSiteCo for $1800 last month", "ts": now_iso()},
        {"id": "fx4", "dispo": "wrong_number", "note": "bad number, no competitor info", "ts": now_iso()},
    ]
    autopsy_rows = [
        {"id": "deal_fx9", "deal": "Fixture Plumbing Co", "note": "hired FastWebGuy instead, price wasn't the issue", "ts": now_iso()},
    ]
    return warm_rows, autopsy_rows


def run(fixture: bool = False) -> dict:
    if fixture:
        warm_rows, autopsy_rows = _fixture_data()
        warm_total = sum(1 for r in warm_rows if r.get("dispo") in DEAD_DISPOS)
        autopsy_exists = True
        source = "FIXTURE"
    else:
        warm_rows = _read_jsonl(WARM_DISPO)
        warm_total = sum(1 for r in _dedup_by_id(warm_rows) if r.get("dispo") in DEAD_DISPOS)
        autopsy_exists = AUTOPSY_LOG.exists()
        autopsy_rows = _read_jsonl(AUTOPSY_LOG)
        source = "REAL"

    warm_hits = scan_warm_dispo(warm_rows)
    autopsy_hits = scan_autopsy_log(autopsy_rows)
    result = build(warm_hits, autopsy_hits, warm_total, autopsy_exists)
    result["source"] = source
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))
    print(f"lost_to [{source}]: {result['status']}, {result['total_hits']} competitor hit(s) "
          f"across {result['warm_dispo_dead_count']} dead-dispo notes "
          f"(autopsy_log exists: {autopsy_exists}) -> {OUT}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", action="store_true")
    args = ap.parse_args()
    run(fixture=args.fixture)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
