#!/usr/bin/env python3
"""Close forecast (#59) — a p10/p50/p90 range on how much of the open pipeline
actually closes, instead of [OWNER] eyeballing a raw pipeline total that overstates
reality (deals sit "open" long after they've gone cold).

Simple Monte-Carlo: each open deal gets a close probability from a value bucket,
then 1000 trials each independently "close" (or don't) every deal per its
probability and sum the wins. p10/p50/p90 of that trial distribution gives a
realistic range instead of one point estimate.

HONESTY NOTE: the per-bucket probabilities below are PRIORS, not measured rates.
store/ledger.jsonl (win/loss history) barely has data yet (one test row as of
writing) — there's nothing to fit real probabilities to. Once ledger.jsonl has
enough closed-deal history, replace these constants with actual bucket win rates
computed from it. Until then this is an honest guess, not a calibrated model, and
this docstring is the flag that it needs revisiting.

Read-only against /api/deals + store/ledger.jsonl; writes store/forecast_close.json
(full overwrite each run). Run standalone: .venv/bin/python agents/forecast_close.py
"""
from __future__ import annotations

import json
import random
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import now_iso, secret  # noqa: E402

OUT = ROOT / "store" / "forecast_close.json"
LEDGER = ROOT / "store" / "ledger.jsonl"
TRIALS = 1000

# Priors by deal-value bucket (see docstring: honest guesses until ledger.jsonl
# has enough closed-deal history to compute real per-bucket win rates from).
BUCKET_LOW_MAX = 1500
BUCKET_MID_MAX = 5000
PROB_LOW = 0.25   # <= $1500
PROB_MID = 0.15   # <= $5000
PROB_HIGH = 0.10  # > $5000


def _get(path: str) -> dict:
    req = urllib.request.Request("http://127.0.0.1:8765" + path,
                                 headers={"X-Brain-Token": secret("brain_token")})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _prob_for(value: float) -> float:
    if value <= BUCKET_LOW_MAX:
        return PROB_LOW
    if value <= BUCKET_MID_MAX:
        return PROB_MID
    return PROB_HIGH


def _ledger_history() -> list[dict]:
    """Read what win/loss history exists so far. Not yet used to fit the priors
    above (too thin), but loaded + counted so the output can say honestly how
    much history backs the model."""
    if not LEDGER.exists():
        return []
    out = []
    for line in LEDGER.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = int(round(pct * (len(sorted_vals) - 1)))
    idx = max(0, min(len(sorted_vals) - 1, idx))
    return sorted_vals[idx]


def run_forecast(deals: list[dict], trials: int = TRIALS, seed: int | None = None) -> dict:
    open_deals = [{"value": float(d.get("value") or 0), "prob": _prob_for(float(d.get("value") or 0))}
                  for d in deals]
    rng = random.Random(seed)
    totals = []
    for _ in range(trials):
        total = 0.0
        for d in open_deals:
            if rng.random() < d["prob"]:
                total += d["value"]
        totals.append(total)
    totals.sort()
    return {
        "generated": now_iso(),
        "deal_count": len(open_deals),
        "pipeline_value": round(sum(d["value"] for d in open_deals), 2),
        "trials": trials,
        "p10": round(_percentile(totals, 0.10), 2),
        "p50": round(_percentile(totals, 0.50), 2),
        "p90": round(_percentile(totals, 0.90), 2),
        "priors": {"low_max": BUCKET_LOW_MAX, "mid_max": BUCKET_MID_MAX,
                   "prob_low": PROB_LOW, "prob_mid": PROB_MID, "prob_high": PROB_HIGH,
                   "status": "PRIORS, not measured — see module docstring"},
        "ledger_history_rows": len(_ledger_history()),
    }


def main() -> int:
    try:
        deals = _get("/api/deals").get("deals", [])
    except Exception as e:  # noqa: BLE001
        print(f"forecast_close: could not reach /api/deals ({e})")
        return 0
    result = run_forecast(deals)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))
    print(f"forecast_close: {result['deal_count']} open deals, "
          f"p10=${result['p10']:,.0f} p50=${result['p50']:,.0f} p90=${result['p90']:,.0f} -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
