#!/usr/bin/env python3
"""List source scorecard (Q254) — every contact-touch source (cold campaign tag,
warm hitlist, referral marker) mapped to closed revenue, so we can double down
on whatever source actually pays instead of guessing.

Sources counted (all read-only):
- store/cold_pipeline.jsonl: campaign field -> "cold:<campaign>" source
- ~/Claude/WARM-HITLIST.csv: every row -> "warm" source (tier noted)
- store/ledger.jsonl: notes mentioning "referral" -> "referral" source (crude
  marker match, same honesty bar as everything else here)
- store/ledger.jsonl kind in (won, payment, closed) -> revenue events, matched
  back to a source by fuzzy company/name match against cold_pipeline + hitlist

Fuzzy match is deliberately dumb (normalized substring match on company/name)
because the ledger has no contact_id field to join on cleanly. When it can't
find a source it says so under "(unmatched)" rather than guessing one.

Writes store/source_scores.json (full overwrite) + a feed line when there's
revenue to report (monthly cadence intended -- caller decides when to invoke,
this agent itself doesn't gate on date so --fixture / manual runs work anytime).

Run standalone: .venv/bin/python agents/source_scorecard.py
                 .venv/bin/python agents/source_scorecard.py --fixture
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402

COLD_PIPELINE = ROOT / "store" / "cold_pipeline.jsonl"
WARM_CSV = Path.home() / "Claude" / "WARM-HITLIST.csv"
LEDGER = ROOT / "store" / "ledger.jsonl"
OUT = ROOT / "store" / "source_scores.json"

REFERRAL_MARKERS = ("referral", "referred", "intro from", "friend of")


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


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _cold_sources() -> dict[str, dict]:
    """company/email -> {source, campaign} for fuzzy revenue matching."""
    out = {}
    for r in _read_jsonl(COLD_PIPELINE):
        campaign = (r.get("campaign") or "unknown").strip()
        key_company = _norm(r.get("company") or "")
        key_email = _norm(r.get("email") or "")
        rec = {"source": f"cold:{campaign}", "company": r.get("company"), "email": r.get("email")}
        if key_company:
            out[f"co:{key_company}"] = rec
        if key_email:
            out[f"em:{key_email}"] = rec
    return out


def _warm_sources() -> dict[str, dict]:
    out = {}
    if not WARM_CSV.exists():
        return out
    for r in csv.DictReader(open(WARM_CSV, newline="")):
        name = (r.get("name") or "").strip()
        company = (r.get("company") or "").strip()
        tier = (r.get("tier") or "").strip()
        rec = {"source": f"warm:tier{tier or '?'}", "company": company or name}
        if _norm(name):
            out[f"co:{_norm(name)}"] = rec
        if _norm(company):
            out[f"co:{_norm(company)}"] = rec
    return out


def _touch_counts(cold_idx: dict, warm_idx: dict) -> Counter:
    """How many contacts were TOUCHED per source (not closed -- just reached),
    so the scorecard can show a full funnel, not just wins."""
    counts = Counter()
    seen_cold = set()
    for r in _read_jsonl(COLD_PIPELINE):
        campaign = (r.get("campaign") or "unknown").strip()
        cid = r.get("contact_id") or r.get("email")
        if cid and cid in seen_cold:
            continue
        seen_cold.add(cid)
        counts[f"cold:{campaign}"] += 1
    if WARM_CSV.exists():
        for r in csv.DictReader(open(WARM_CSV, newline="")):
            tier = (r.get("tier") or "").strip()
            counts[f"warm:tier{tier or '?'}"] += 1
    return counts


def _match_ledger_to_source(ledger_rows: list[dict], cold_idx: dict, warm_idx: dict) -> list[dict]:
    matched = []
    for row in ledger_rows:
        if row.get("kind") not in ("won", "payment", "closed"):
            continue
        note = (row.get("note") or "")
        amount = float(row.get("amount") or 0)
        norm_note = _norm(note)
        source = None
        # referral marker check first (explicit signal beats fuzzy match)
        if any(m in note.lower() for m in REFERRAL_MARKERS):
            source = "referral"
        else:
            for idx in (cold_idx, warm_idx):
                for key, rec in idx.items():
                    company_norm = _norm(rec.get("company") or "")
                    if company_norm and len(company_norm) >= 4 and company_norm in norm_note:
                        source = rec["source"]
                        break
                if source:
                    break
        matched.append({"ts": row.get("ts"), "amount": amount, "note": note,
                        "source": source or "(unmatched)"})
    return matched


def build(cold_idx: dict, warm_idx: dict, touch_counts: Counter, ledger_rows: list[dict]) -> dict:
    matched = _match_ledger_to_source(ledger_rows, cold_idx, warm_idx)
    revenue_by_source: dict[str, float] = defaultdict(float)
    closes_by_source: Counter = Counter()
    for m in matched:
        revenue_by_source[m["source"]] += m["amount"]
        closes_by_source[m["source"]] += 1

    all_sources = set(touch_counts) | set(revenue_by_source)
    scorecard = {}
    for src in sorted(all_sources):
        touches = touch_counts.get(src, 0)
        revenue = round(revenue_by_source.get(src, 0.0), 2)
        closes = closes_by_source.get(src, 0)
        scorecard[src] = {
            "touches": touches,
            "closes": closes,
            "revenue": revenue,
            "revenue_per_touch": round(revenue / touches, 2) if touches else None,
            "close_rate": round(closes / touches, 4) if touches else None,
        }
    unmatched_revenue = round(revenue_by_source.get("(unmatched)", 0.0), 2)
    return {
        "generated": now_iso(),
        "sources": scorecard,
        "unmatched_revenue_events": closes_by_source.get("(unmatched)", 0),
        "unmatched_revenue": unmatched_revenue,
        "total_revenue_events": len(matched),
        "match_method": "fuzzy normalized-substring match of source company name "
                         "against ledger note text (ledger has no contact_id to join on cleanly)",
        "note": "revenue events with kind in (won,payment,closed) only; referral "
                "detected by keyword marker in ledger note, not a dedicated field.",
    }


def _fixture_data() -> tuple[dict, dict, Counter, list[dict]]:
    cold_idx = {"co:acmehvac": {"source": "cold:webfix", "company": "Acme HVAC"},
                "em:hello@acmehvac.com": {"source": "cold:webfix", "company": "Acme HVAC"}}
    warm_idx = {"co:brightsalon": {"source": "warm:tier1", "company": "Bright Salon"}}
    touch_counts = Counter({"cold:webfix": 40, "warm:tier1": 15, "warm:tier2": 10})
    ledger_rows = [
        {"ts": "2026-06-01T10:00:00", "kind": "won", "amount": 2500, "note": "Acme HVAC signed, cold outreach"},
        {"ts": "2026-06-15T10:00:00", "kind": "won", "amount": 3500, "note": "Bright Salon closed off warm call"},
        {"ts": "2026-06-20T10:00:00", "kind": "won", "amount": 500, "note": "referred by a past client, quick landing page"},
        {"ts": "2026-06-25T10:00:00", "kind": "won", "amount": 900, "note": "some deal, no clean source in the note"},
    ]
    return cold_idx, warm_idx, touch_counts, ledger_rows


def run(fixture: bool = False) -> dict:
    if fixture:
        cold_idx, warm_idx, touch_counts, ledger_rows = _fixture_data()
        source = "FIXTURE"
    else:
        cold_idx, warm_idx = _cold_sources(), _warm_sources()
        touch_counts = _touch_counts(cold_idx, warm_idx)
        ledger_rows = _read_jsonl(LEDGER)
        source = "REAL"
    result = build(cold_idx, warm_idx, touch_counts, ledger_rows)
    result["source"] = source
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))
    top = sorted(result["sources"].items(), key=lambda kv: -kv[1]["revenue"])[:3]
    top_str = ", ".join(f"{k}:${v['revenue']:,.0f}" for k, v in top) or "none"
    print(f"source_scorecard [{source}]: {len(result['sources'])} sources, "
          f"{result['total_revenue_events']} revenue events, top: {top_str} -> {OUT}")
    if source == "REAL" and result["total_revenue_events"] > 0:
        planner.feed_add("money", f"source scorecard: {top_str or 'no clean leader yet'}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", action="store_true")
    args = ap.parse_args()
    run(fixture=args.fixture)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
