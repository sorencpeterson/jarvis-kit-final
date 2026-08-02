#!/usr/bin/env python3
"""Client LTV projection (tech #270) — per-client projected lifetime value =
build $ + (care attach probability × care $/mo × expected retention months).
Assumptions documented below, sourced from business-library/operating-model.md
where it names real numbers (care MRR target of 120 clients × $110 avg), and
flagged as ASSUMPTION where the operating model doesn't specify.

Real data check: proposals.jsonl has 1 accepted proposal (Client A Salon,
$3500, no care attach signal anywhere yet), ledger.jsonl has 1 test row. There
is no care-plan store in this repo yet (no store/care.jsonl), so care-attach
is entirely assumption-driven until one exists. Rather than invent care
signals, this reads whatever accepted proposals exist as "clients" and
projects LTV off the DOCUMENTED assumptions -- the real run will be small and
honest, per the task brief ("real = the few won/none, honest").

Assumptions (edit here if [OWNER] gives better numbers):
- CARE_ATTACH_PROB = 0.30: no real attach data exists yet; operating-model.md
  targets 120 care clients off a much larger client base over ~18 months, which
  implies an attach rate but doesn't state one directly -- 30% is a documented
  placeholder, ASSUMPTION-flagged, not derived.
- CARE_MRR = $110/mo: taken directly from operating-model.md's stated care
  average ("Care MRR | 120 clients × $110 avg").
- EXPECTED_RETENTION_MONTHS = 14: not stated in operating-model.md at all;
  ASSUMPTION-flagged, picked as a conservative >1yr figure for a services
  business with no churn data yet.

Writes store/ltv.json (full overwrite). Run standalone:
.venv/bin/python agents/ltv_model.py
.venv/bin/python agents/ltv_model.py --fixture
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from store_lib import now_iso  # noqa: E402

PROPOSALS = ROOT / "store" / "proposals.jsonl"
OUT = ROOT / "store" / "ltv.json"

# Documented assumptions -- see module docstring for sourcing/honesty notes.
CARE_ATTACH_PROB = 0.30    # ASSUMPTION: no real attach data exists yet
CARE_MRR = 110.0           # SOURCED: operating-model.md "Care MRR | 120 clients × $110 avg"
EXPECTED_RETENTION_MONTHS = 14  # ASSUMPTION: not stated anywhere, conservative pick


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


def project_ltv(build_value: float, care_attach_prob: float = CARE_ATTACH_PROB,
                care_mrr: float = CARE_MRR, retention_months: float = EXPECTED_RETENTION_MONTHS) -> dict:
    care_expected = care_attach_prob * care_mrr * retention_months
    total = build_value + care_expected
    return {
        "build_value": round(build_value, 2),
        "care_expected_value": round(care_expected, 2),
        "projected_ltv": round(total, 2),
        "assumptions": {
            "care_attach_prob": care_attach_prob, "care_mrr": care_mrr,
            "expected_retention_months": retention_months,
        },
    }


def build(accepted_proposals: list[dict]) -> dict:
    clients = []
    for p in accepted_proposals:
        build_value = float(p.get("price") or 0)
        proj = project_ltv(build_value)
        clients.append({
            "client": p.get("company") or p.get("name") or "(unknown)",
            "proposal_id": p.get("id"), "niche": p.get("niche"), "tier": p.get("tier"),
            **proj,
        })
    clients.sort(key=lambda c: -c["projected_ltv"])
    total_ltv = round(sum(c["projected_ltv"] for c in clients), 2)
    avg_ltv = round(total_ltv / len(clients), 2) if clients else None
    return {
        "generated": now_iso(),
        "client_count": len(clients),
        "clients": clients,
        "total_projected_ltv": total_ltv,
        "avg_projected_ltv": avg_ltv,
        "assumptions": {
            "care_attach_prob": {"value": CARE_ATTACH_PROB, "status": "ASSUMPTION -- no real attach data exists"},
            "care_mrr": {"value": CARE_MRR, "status": "SOURCED from business-library/operating-model.md"},
            "expected_retention_months": {"value": EXPECTED_RETENTION_MONTHS,
                                          "status": "ASSUMPTION -- not stated in operating-model.md, conservative pick"},
        },
        "note": "no store/care.jsonl or equivalent care-plan store exists yet -- "
                "care_attach_prob is entirely assumption-driven until real attach "
                "events can be read. This projects off ACCEPTED proposals only "
                "(status == 'accepted'), i.e. real closed clients, not staged ones.",
    }


def _fixture_data() -> list[dict]:
    return [
        {"id": "fx_1", "company": "Acme HVAC", "niche": "hvac", "tier": "booking", "price": 2500, "status": "accepted"},
        {"id": "fx_2", "company": "Bright Salon", "niche": "salon", "tier": "whiteglove", "price": 3500, "status": "accepted"},
        {"id": "fx_3", "company": "Delta Plumbing", "niche": "plumbing", "tier": "landing", "price": 800, "status": "accepted"},
    ]


def run(fixture: bool = False) -> dict:
    if fixture:
        accepted = _fixture_data()
        source = "FIXTURE"
    else:
        proposals = _dedup_by_id(_read_jsonl(PROPOSALS))
        accepted = [p for p in proposals if p.get("status") == "accepted"]
        source = "REAL"
    result = build(accepted)
    result["source"] = source
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))
    if result["clients"]:
        top = result["clients"][0]
        print(f"ltv_model [{source}]: {result['client_count']} client(s), "
              f"total_ltv=${result['total_projected_ltv']:,.0f}, avg=${result['avg_projected_ltv']:,.0f}, "
              f"top: {top['client']} (${top['projected_ltv']:,.0f}) -> {OUT}")
    else:
        print(f"ltv_model [{source}]: 0 accepted clients yet -- honest zero, no fabricated LTV -> {OUT}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", action="store_true")
    args = ap.parse_args()
    run(fixture=args.fixture)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
