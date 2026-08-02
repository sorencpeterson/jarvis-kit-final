#!/usr/bin/env python3
"""Close-probability scoring (tech #263) — every open GHL deal gets a live 0-1
score instead of [OWNER] eyeballing a flat pipeline total. Logistic-shaped blend
of three documented heuristics (age, value, proposal opens); NOT fit on real
outcomes yet because store/ledger.jsonl barely has closed-deal history (see
forecast_close.py's docstring, same honesty note applies here). Replace the
WEIGHTS below with a real fit once n>30 closed deals exist to fit against, per
the item description.

Heuristics (each maps a raw signal into a 0..1 factor before the logistic):
- age_factor: deals decay -- freshness helps early, then flattens. Uses the
  same "oldest decays fastest" belief warm_block.py already encodes, but here
  it's inverted into a probability curve: very fresh (0-3d) scores high,
  very stale (90d+) scores low, sigmoid-ish falloff in between.
- value_factor: mirrors forecast_close.py's bucket priors directly (low value
  closes more often than big-ticket, per the existing PROB_LOW/MID/HIGH
  constants) so the two models don't quietly disagree.
- opens_factor: any recorded proposal open for that contact is a real
  engagement signal (opened_at "read it" per open-tracking heartbeat item)
  and lifts the score; matched by fuzzy name against proposals.jsonl since
  GHL opportunities don't carry proposal ids.

Score = logistic(w_age*age_factor + w_value*value_factor + w_opens*opens_factor + bias),
weights documented below, not tuned -- flagged the same way forecast_close.py
flags its priors.

Read-only against GHL (via ghl_social._api, the same call app/server.py's
/api/deals makes) + store/proposals.jsonl. Writes store/close_prob.json (full
overwrite). Run standalone against the live 60 deals:
.venv/bin/python agents/close_prob.py
.venv/bin/python agents/close_prob.py --fixture
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402

PROPOSALS = ROOT / "store" / "proposals.jsonl"
OUT = ROOT / "store" / "close_prob.json"

# Documented weights (priors, not fit -- see module docstring). Bias tuned so a
# mid-age, mid-value, no-open deal lands near forecast_close.py's PROB_MID (0.15).
W_AGE = 1.1
W_VALUE = 0.9
W_OPENS = 1.4
BIAS = -2.35

BUCKET_LOW_MAX = 1500  # mirrors forecast_close.py's buckets exactly
BUCKET_MID_MAX = 5000


def _logistic(x: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def _age_factor(age_days: float) -> float:
    """1.0 at day 0, ~0.5 around 30d, tapering toward ~0.1 past 90d. A deal
    that's been open 6 months is functionally dead even if never marked so."""
    if age_days <= 0:
        return 1.0
    return max(0.05, 1.0 / (1.0 + (age_days / 30.0) ** 1.3))


def _value_factor(value: float) -> float:
    """Mirrors forecast_close.py buckets directly: small deals close more
    often in this business (fast trust-building offers) than big-ticket ones."""
    if value <= BUCKET_LOW_MAX:
        return 0.9
    if value <= BUCKET_MID_MAX:
        return 0.5
    return 0.25


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _proposal_open_index() -> set[str]:
    """normalized company/name -> has at least one recorded proposal open."""
    if not PROPOSALS.exists():
        return set()
    opened = set()
    for line in PROPOSALS.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (r.get("opens") or 0) > 0:
            for field in ("company", "name"):
                key = _norm(r.get(field) or "")
                if key:
                    opened.add(key)
    return opened


def _opens_factor(deal_name: str, opened_index: set[str]) -> float:
    key = _norm(deal_name)
    if not key:
        return 0.0
    for opened_key in opened_index:
        if opened_key and (opened_key in key or key in opened_key) and len(opened_key) >= 4:
            return 1.0
    return 0.0


def _deal_age_days(updated: str) -> float:
    """GHL deals only carry updatedAt, not createdAt, in the shape /api/deals
    returns -- age here is "days since last touched", a proxy for staleness,
    not true deal age. Documented, not hidden."""
    if not updated:
        return 30.0  # unknown -> assume a middling age rather than 0 (which would over-score)
    try:
        d = datetime.fromisoformat(updated[:10]).replace(tzinfo=timezone.utc)
    except ValueError:
        return 30.0
    return max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 86400.0)


def score_deal(deal: dict, opened_index: set[str]) -> dict:
    value = float(deal.get("value") or 0)
    age = _deal_age_days(deal.get("updated") or "")
    af = _age_factor(age)
    vf = _value_factor(value)
    of = _opens_factor(deal.get("name") or "", opened_index)
    x = W_AGE * af + W_VALUE * vf + W_OPENS * of + BIAS
    prob = round(_logistic(x), 4)
    return {
        "id": deal.get("id"), "name": deal.get("name"), "value": value,
        "age_days": round(age, 1), "prob": prob,
        "factors": {"age_factor": round(af, 3), "value_factor": round(vf, 3),
                    "opens_factor": round(of, 3), "had_proposal_open": of > 0},
    }


def _fetch_live_deals() -> list[dict]:
    """Same call app/server.py's /api/deals makes -- READ-ONLY GET against
    /opportunities/search, no writes. Mirrors the pattern documented in the
    task brief (replicate via ghl_social._api directly)."""
    import ghl_social  # noqa: E402  (app/ is on sys.path above)
    loc = ""
    try:
        for line in (ghl_social.GHL / ".env").read_text().splitlines():
            if line.startswith("GHL_LOCATION_ID="):
                loc = line.split("=", 1)[1].strip()
                break
    except OSError:
        pass
    if not loc:
        return []
    out = ghl_social._api(["GET", f"/opportunities/search?location_id={loc}&limit=100"])
    j = json.loads(out[out.find("{"):], strict=False)
    deals = []
    for o in j.get("opportunities", []):
        if o.get("status") != "open":
            continue
        deals.append({"id": o.get("id"), "name": o.get("name") or o.get("contact", {}).get("name", "?"),
                      "value": o.get("monetaryValue") or 0,
                      "updated": (o.get("updatedAt") or "")[:10]})
    return deals


def _fixture_deals() -> list[dict]:
    return [
        {"id": "fx1", "name": "Acme HVAC", "value": 900, "updated": now_iso()[:10]},  # fresh, small -> high prob
        {"id": "fx2", "name": "Legacy Plumbing", "value": 12000, "updated": "2026-01-05"},  # stale, huge -> low prob
        {"id": "fx3", "name": "Bright Salon", "value": 3500, "updated": "2026-06-20"},  # mid everything
    ]


def build(deals: list[dict], opened_index: set[str]) -> dict:
    scored = [score_deal(d, opened_index) for d in deals]
    scored.sort(key=lambda s: -s["prob"])
    total_expected = round(sum(s["prob"] * s["value"] for s in scored), 2)
    return {
        "generated": now_iso(),
        "deal_count": len(scored),
        "pipeline_value": round(sum(s["value"] for s in scored), 2),
        "expected_value": total_expected,
        "weights": {"w_age": W_AGE, "w_value": W_VALUE, "w_opens": W_OPENS, "bias": BIAS,
                    "status": "DOCUMENTED PRIORS, not fit on real outcomes -- see module docstring"},
        "deals": scored,
    }


def run(fixture: bool = False) -> dict:
    opened_index = _proposal_open_index()
    if fixture:
        deals, source = _fixture_deals(), "FIXTURE"
    else:
        try:
            deals = _fetch_live_deals()
        except Exception as e:  # noqa: BLE001
            print(f"close_prob: could not reach GHL ({e}), writing empty result")
            deals = []
        source = "REAL"
    result = build(deals, opened_index)
    result["source"] = source
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))
    top = result["deals"][:3]
    top_str = ", ".join(f"{d['name']}:{d['prob']:.0%}" for d in top) or "none"
    print(f"close_prob [{source}]: {result['deal_count']} deals, "
          f"pipeline=${result['pipeline_value']:,.0f} expected=${result['expected_value']:,.0f}, "
          f"top: {top_str} -> {OUT}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", action="store_true")
    args = ap.parse_args()
    run(fixture=args.fixture)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
