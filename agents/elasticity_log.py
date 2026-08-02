#!/usr/bin/env python3
"""Price-elasticity log (tech #264) — every quote (proposal staged) becomes a
quote row, and quarterly the machine asks "raise the anchor?" with real
evidence once outcomes exist. Two parts:

1. scaffold(): derives store/quotes.jsonl from store/proposals.jsonl -- one
   row per proposal ever staged (niche, tier, price, outcome so far). Safe to
   re-run: rebuilt fresh from proposals.jsonl each time (proposals.jsonl is
   itself the source of truth, quotes.jsonl is a derived view, not a second
   ledger that could drift).
2. analyze(): the quarterly stub. The MATH is fully implemented (price
   elasticity of demand: %change in close-rate / %change in price, bucketed)
   so the day outcomes pile up this just runs -- but with the current n it
   degrades to an explicit [E] "insufficient data" line instead of a fake
   coefficient.

HONESTY NOTE: elasticity needs price VARIATION at comparable scope (same
niche/tier, different price, comparable outcome) to mean anything. Right now
prices are closer to fixed per tier (landing/booking/whiteglove each have one
price point) so there may never be enough natural variation without [OWNER]
deliberately testing anchors -- that's flagged in the output too, not hidden.

Writes store/quotes.jsonl (rebuilt fresh) + store/elasticity.json (quarterly
analysis). Run standalone: .venv/bin/python agents/elasticity_log.py
.venv/bin/python agents/elasticity_log.py --fixture
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from store_lib import now_iso  # noqa: E402

PROPOSALS = ROOT / "store" / "proposals.jsonl"
QUOTES_OUT = ROOT / "store" / "quotes.jsonl"
ANALYSIS_OUT = ROOT / "store" / "elasticity.json"

MIN_QUOTES_PER_BUCKET = 10  # need this many quotes at a given (niche,tier) price point before trusting a rate
MIN_PRICE_POINTS = 2  # need at least 2 distinct prices in a bucket to compute elasticity at all


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


def scaffold(proposals: list[dict]) -> list[dict]:
    """One quote row per proposal ever staged, auto-derived. status carries
    through as the outcome (staged/sent/accepted/skipped/expired -- whatever
    proposal_factory.py's state machine uses)."""
    proposals = _dedup_by_id(proposals)
    quotes = []
    for p in proposals:
        price = p.get("price")
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        quotes.append({
            "quote_id": "q_" + p.get("id", ""),
            "proposal_id": p.get("id"),
            "niche": (p.get("niche") or "").strip().lower() or "(unknown)",
            "tier": p.get("tier") or "(unknown)",
            "price": float(price),
            "status": p.get("status"),
            "outcome_closed": p.get("status") == "accepted",
            "created": p.get("created"),
        })
    return quotes


def _bucket_key(q: dict) -> tuple:
    return (q["niche"], q["tier"])


def analyze(quotes: list[dict]) -> dict:
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for q in quotes:
        buckets[_bucket_key(q)].append(q)

    bucket_reports = {}
    ready_count = 0
    for key, rows in sorted(buckets.items()):
        niche, tier = key
        by_price: dict[float, list[dict]] = defaultdict(list)
        for r in rows:
            by_price[r["price"]].append(r)
        n_total = len(rows)
        distinct_prices = len(by_price)
        label = f"{niche}/{tier}"

        if n_total < MIN_QUOTES_PER_BUCKET or distinct_prices < MIN_PRICE_POINTS:
            bucket_reports[label] = {
                "n": n_total, "distinct_prices": distinct_prices,
                "status": "[E] insufficient data "
                          f"(n={n_total}, need {MIN_QUOTES_PER_BUCKET}; "
                          f"price_points={distinct_prices}, need {MIN_PRICE_POINTS})",
                "elasticity": None,
            }
            continue

        # Real math, run once there's real variation: point elasticity between
        # the lowest and highest observed price in this bucket.
        # E = %change in close-rate / %change in price
        prices_sorted = sorted(by_price)
        p_low, p_high = prices_sorted[0], prices_sorted[-1]
        rate_low = sum(1 for r in by_price[p_low] if r["outcome_closed"]) / len(by_price[p_low])
        rate_high = sum(1 for r in by_price[p_high] if r["outcome_closed"]) / len(by_price[p_high])
        pct_price_change = (p_high - p_low) / p_low if p_low else 0
        pct_rate_change = (rate_high - rate_low) / rate_low if rate_low else (
            0 if rate_high == 0 else float("inf"))
        elasticity = round(pct_rate_change / pct_price_change, 3) if pct_price_change else None
        ready_count += 1
        bucket_reports[label] = {
            "n": n_total, "distinct_prices": distinct_prices,
            "status": "ready",
            "price_low": p_low, "price_high": p_high,
            "close_rate_at_low": round(rate_low, 3), "close_rate_at_high": round(rate_high, 3),
            "elasticity": elasticity,
            "read": ("raising the anchor barely moved close rate -- room to push price" if
                     elasticity is not None and abs(elasticity) < 0.3 else
                     "close rate is price-sensitive here -- be careful raising the anchor" if
                     elasticity is not None else "inconclusive"),
        }

    return {
        "generated": now_iso(),
        "quote_count": len(quotes),
        "bucket_count": len(bucket_reports),
        "buckets_ready": ready_count,
        "buckets": bucket_reports,
        "structural_note": "prices are closer to fixed per (niche,tier) tier point "
                           "today -- real elasticity needs [OWNER] deliberately testing "
                           "anchors (per-tier price A/B) to generate variation, not just "
                           "volume. Flagged, not hidden.",
        "min_quotes_per_bucket": MIN_QUOTES_PER_BUCKET,
        "min_price_points": MIN_PRICE_POINTS,
    }


def _fixture_quotes() -> list[dict]:
    """Synthetic quotes proving the math runs end-to-end: one bucket with real
    price variation and enough n to clear both bars, one bucket left thin."""
    quotes = []
    # hvac/booking: two price points, 12 quotes each, real rate difference -> should go "ready"
    for i in range(12):
        quotes.append({"quote_id": f"fx_low_{i}", "proposal_id": f"fx_low_{i}", "niche": "hvac",
                       "tier": "booking", "price": 2000.0, "status": "accepted" if i < 6 else "skipped",
                       "outcome_closed": i < 6, "created": "2026-05-01"})
    for i in range(12):
        quotes.append({"quote_id": f"fx_high_{i}", "proposal_id": f"fx_high_{i}", "niche": "hvac",
                       "tier": "booking", "price": 3000.0, "status": "accepted" if i < 3 else "skipped",
                       "outcome_closed": i < 3, "created": "2026-06-01"})
    # salon/whiteglove: thin, stays [E]
    quotes.append({"quote_id": "fx_salon_0", "proposal_id": "fx_salon_0", "niche": "salon",
                   "tier": "whiteglove", "price": 3500.0, "status": "staged",
                   "outcome_closed": False, "created": "2026-06-15"})
    return quotes


def run(fixture: bool = False) -> dict:
    if fixture:
        quotes = _fixture_quotes()
        source = "FIXTURE"
    else:
        proposals = _read_jsonl(PROPOSALS)
        quotes = scaffold(proposals)
        source = "REAL"
        # only persist quotes.jsonl on real runs -- fixture mode proves the
        # analysis math without touching the real derived store.
        QUOTES_OUT.parent.mkdir(parents=True, exist_ok=True)
        QUOTES_OUT.write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in quotes))

    result = analyze(quotes)
    result["source"] = source
    ANALYSIS_OUT.parent.mkdir(parents=True, exist_ok=True)
    ANALYSIS_OUT.write_text(json.dumps(result, indent=2))
    print(f"elasticity_log [{source}]: {result['quote_count']} quotes, "
          f"{result['bucket_count']} buckets, {result['buckets_ready']} ready for real elasticity "
          f"-> {QUOTES_OUT if source == 'REAL' else '(fixture, quotes.jsonl untouched)'}, {ANALYSIS_OUT}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", action="store_true")
    args = ap.parse_args()
    run(fixture=args.fixture)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
