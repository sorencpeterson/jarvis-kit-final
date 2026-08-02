#!/usr/bin/env python3
"""The niche database (Q253) — every audit/proposal/close feeds per-niche stats
(audits seen, avg faults, proposals sent, opens, closes, avg price). The idea:
after 100 audits this is proprietary market data ("HVAC sites average 4.2 faults,
close at 18%, $2,100 avg"); right now it's the same machinery running honestly
on the handful of rows that actually exist.

Sources (all read-only):
- store/proposals.jsonl: niche, tier/price, opens, status -> proposals/opens/closes/avg_price
- store/warm_dispo.jsonl: niche tag if present -> booked count per niche (audits proxy)
- ~/Claude/elementor-recoder/qa-*.md (if any): per-niche audit/fault counts, when that
  naming convention exists
- store/cold_pipeline.jsonl: site_note faults mentioned -> a crude fault-count proxy
  when qa-*.md reports don't exist yet (counts comma-separated fault clauses in
  site_note as a stand-in for "faults found", clearly labeled as an estimate)

Also folds in Q263 (seasonal demand curves): a month-bucket count per niche from
proposals.jsonl created timestamps. Marked [E] until a year of data exists (one
month of history right now can't say anything about seasonality).

Writes store/niche_db.json (full overwrite each run, no CLI call). Degrades to
n=0 lines per niche cleanly rather than fabricating rates. Run standalone:
.venv/bin/python agents/niche_db.py
.venv/bin/python agents/niche_db.py --fixture   (synthetic proof-of-pipeline)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from store_lib import now_iso  # noqa: E402

PROPOSALS = ROOT / "store" / "proposals.jsonl"
WARM_DISPO = ROOT / "store" / "warm_dispo.jsonl"
COLD_PIPELINE = ROOT / "store" / "cold_pipeline.jsonl"
QA_DIR = Path.home() / "Claude" / "elementor-recoder"
OUT = ROOT / "store" / "niche_db.json"

MIN_FOR_RATE = 5  # below this many proposals, don't report a close/open RATE as real


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
    """last-write-wins by id, same discipline as every other agent in this repo
    (proposal_factory/reply_watch/win_loss all append-only, latest row wins)."""
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


def _norm_niche(n: str) -> str:
    n = (n or "").strip().lower()
    return n or "(unknown)"


def _qa_reports() -> dict[str, dict]:
    """Scan ~/Claude/elementor-recoder for qa-*.md reports, if the naming
    convention exists there. Best-effort: these reports aren't guaranteed to
    carry a niche tag, so this only contributes when one is found."""
    out: dict[str, list[int]] = defaultdict(list)
    if not QA_DIR.is_dir():
        return {}
    for p in QA_DIR.glob("qa-*.md"):
        try:
            text = p.read_text()
        except OSError:
            continue
        m = re.search(r"niche:\s*([a-zA-Z_\- ]+)", text, re.I)
        if not m:
            continue
        niche = _norm_niche(m.group(1))
        faults = len(re.findall(r"^\s*[-*]\s+FAULT", text, re.M | re.I)) or \
            len(re.findall(r"^\s*[-*]\s+", text, re.M))
        out[niche].append(faults)
    return {k: {"reports": len(v), "avg_faults": round(sum(v) / len(v), 1)} for k, v in out.items()}


def _cold_pipeline_fault_proxy() -> dict[str, dict]:
    """When no qa-*.md reports exist, site_note in cold_pipeline.jsonl carries a
    prose fault summary per contact (e.g. "broken link, 28 images with no alt
    text, ..."). Counting comma-separated clauses is a crude proxy for fault
    count, not a real audit tally -- labeled ESTIMATE in the output. No niche
    field lives on cold_pipeline rows today, so this rolls up under
    "(unassigned)" rather than fabricating a niche guess."""
    rows = _read_jsonl(COLD_PIPELINE)
    faults = []
    for r in rows:
        note = (r.get("site_note") or "").strip()
        if not note:
            continue
        clauses = [c for c in re.split(r",| and ", note) if c.strip()]
        faults.append(len(clauses))
    if not faults:
        return {}
    return {"(unassigned)": {"reports": len(faults), "avg_faults": round(sum(faults) / len(faults), 1),
                             "source": "ESTIMATE: cold_pipeline.jsonl site_note clause count, not a real audit"}}


def build(proposals: list[dict], warm_dispo: list[dict], qa: dict, cold_proxy: dict) -> dict:
    proposals = _dedup_by_id(proposals)
    by_niche: dict[str, dict] = defaultdict(lambda: {
        "proposals": 0, "opens": 0, "closes": 0, "prices": [], "tiers": Counter(),
        "months": Counter(),
    })
    for p in proposals:
        niche = _norm_niche(p.get("niche"))
        b = by_niche[niche]
        b["proposals"] += 1
        if (p.get("opens") or 0) > 0:
            b["opens"] += 1
        if p.get("status") == "accepted":
            b["closes"] += 1
        price = p.get("price")
        if isinstance(price, (int, float)) and price > 0:
            b["prices"].append(float(price))
        tier = p.get("tier")
        if tier:
            b["tiers"][tier] += 1
        created = (p.get("created") or "")[:7]  # YYYY-MM
        if created:
            b["months"][created] += 1

    warm_dispo = _dedup_by_id(warm_dispo)
    warm_by_niche = Counter(_norm_niche(r.get("niche")) for r in warm_dispo if r.get("niche"))

    niches: dict[str, dict] = {}
    all_keys = set(by_niche) | set(warm_by_niche) | set(qa) | set(cold_proxy)
    for niche in sorted(all_keys):
        b = by_niche.get(niche, {"proposals": 0, "opens": 0, "closes": 0, "prices": [],
                                  "tiers": Counter(), "months": Counter()})
        n_props = b["proposals"]
        entry = {
            "audits_from_qa_reports": (qa.get(niche) or {}).get("reports", 0),
            "avg_faults": (qa.get(niche) or {}).get("avg_faults")
                          if niche in qa else (cold_proxy.get(niche) or {}).get("avg_faults"),
            "avg_faults_source": "qa-*.md reports" if niche in qa
                                  else ((cold_proxy.get(niche) or {}).get("source") if niche in cold_proxy else None),
            "proposals": n_props,
            "opens": b["opens"],
            "closes": b["closes"],
            "warm_booked": warm_by_niche.get(niche, 0),
            "avg_price": round(sum(b["prices"]) / len(b["prices"]), 2) if b["prices"] else None,
            "tier_mix": dict(b["tiers"]),
            "seasonal_by_month": dict(sorted(b["months"].items())),
        }
        if n_props >= MIN_FOR_RATE:
            entry["open_rate"] = round(b["opens"] / n_props, 3)
            entry["close_rate"] = round(b["closes"] / n_props, 3)
        else:
            entry["open_rate"] = None
            entry["close_rate"] = None
            entry["rate_note"] = f"insufficient data (n={n_props}, need {MIN_FOR_RATE}) for a real rate"
        distinct_months = len(entry["seasonal_by_month"])
        entry["seasonal_status"] = ("[E] needs ~12 months of history, have "
                                    f"{distinct_months} month(s)" if distinct_months < 12 else "ready")
        niches[niche] = entry

    total_props = sum(n["proposals"] for n in niches.values())
    return {
        "generated": now_iso(),
        "niche_count": len(niches),
        "total_proposals_seen": total_props,
        "min_n_for_rate": MIN_FOR_RATE,
        "niches": niches,
        "note": "avg_faults from qa-*.md reports when present; otherwise a crude "
                "clause-count ESTIMATE off cold_pipeline site_note, never fabricated. "
                "Rates (open_rate/close_rate) are null below min_n_for_rate.",
    }


def _fixture_data() -> tuple[list[dict], list[dict]]:
    """Synthetic but realistic rows proving the pipeline end-to-end: 3 niches,
    enough proposals in one niche to clear MIN_FOR_RATE and show a real rate,
    two niches left thin to show the honest insufficient-data path."""
    props = []
    # hvac: 8 proposals, clears the min-n bar
    for i in range(8):
        props.append({
            "id": f"fx_hvac_{i}", "niche": "hvac", "tier": "booking", "price": 2500,
            "status": "accepted" if i < 2 else ("staged" if i < 6 else "skipped"),
            "opens": 1 if i < 5 else 0, "created": f"2026-0{(i % 3) + 1}-15",
        })
    # salon: 3 proposals, stays below the bar on purpose
    for i in range(3):
        props.append({
            "id": f"fx_salon_{i}", "niche": "salon", "tier": "whiteglove", "price": 3500,
            "status": "accepted" if i == 0 else "staged",
            "opens": 1, "created": "2026-06-10",
        })
    # plumbing: 1 proposal, near-zero data
    props.append({"id": "fx_plumb_0", "niche": "plumbing", "tier": "landing", "price": 800,
                  "status": "staged", "opens": 0, "created": "2026-07-01"})
    warm = [{"id": "fx_w_1", "niche": "hvac", "dispo": "booked"},
            {"id": "fx_w_2", "niche": "hvac", "dispo": "booked"}]
    return props, warm


def run(fixture: bool = False) -> dict:
    if fixture:
        proposals, warm_dispo = _fixture_data()
        qa, cold_proxy = {}, {}
        source = "FIXTURE"
    else:
        proposals = _read_jsonl(PROPOSALS)
        warm_dispo = _read_jsonl(WARM_DISPO)
        qa = _qa_reports()
        cold_proxy = _cold_pipeline_fault_proxy() if not qa else {}
        source = "REAL"
    result = build(proposals, warm_dispo, qa, cold_proxy)
    result["source"] = source
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))
    top = sorted(result["niches"].items(), key=lambda kv: -kv[1]["proposals"])[:3]
    top_str = ", ".join(f"{k}:{v['proposals']}p/{v['closes']}c" for k, v in top) or "none"
    print(f"niche_db [{source}]: {result['niche_count']} niches, "
          f"{result['total_proposals_seen']} proposals total, top: {top_str} -> {OUT}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", action="store_true", help="run against synthetic data instead of real stores")
    args = ap.parse_args()
    run(fixture=args.fixture)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
