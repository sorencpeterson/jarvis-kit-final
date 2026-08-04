#!/usr/bin/env python3
"""Channel CAC truth (tech #266 / biz Q254 sibling) — hours proxy + token spend
per lane, divided by closed revenue for that lane, monthly. Run for real: this
WILL show costs against $0 or near-$0 revenue right now, and that's the honest
point of the item (name says "truth", not "flattering number").

Cost inputs (all read-only):
- hours proxy: store/runs.jsonl when present (shape confirmed: {agent, start,
  end, dur_s, ok, err} -- an agent run-log, dur_s summed per agent gives a real
  wall-clock-seconds proxy for machine time, not [OWNER]'s hours, but the closest
  real signal available). Falls back to event counts (store/events.jsonl
  request timestamps) as a cruder activity-volume proxy if runs.jsonl is ever
  empty/missing. Neither is [OWNER]'s actual hours (nothing logs those), so both
  are labeled proxy, not hours, in the output.
- token spend: store/usage.jsonl, real (in+out tokens per feature call), priced
  at documented per-model rates below (rough -- update if pricing changes).

Lane inputs:
- cold: store/cold_pipeline.jsonl campaign field
- warm: WARM-HITLIST.csv tiers
- jobs: store/usage.jsonl feature names starting with "job" (job_replies.py etc)
Revenue: store/ledger.jsonl kind in (won,payment,closed), matched to lane by
the same fuzzy company-name approach source_scorecard.py uses (kept local here
rather than importing that module, so this agent has no fragile cross-agent
dependency -- if source_scorecard.py's matching logic changes, this one still
runs standalone).

Writes store/cac.json (full overwrite). Run standalone:
.venv/bin/python agents/channel_cac.py
.venv/bin/python agents/channel_cac.py --fixture
"""
from __future__ import annotations

import argparse
import csv
import os
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from store_lib import now_iso  # noqa: E402

USAGE = ROOT / "store" / "usage.jsonl"
RUNS = ROOT / "store" / "runs.jsonl"
EVENTS = ROOT / "store" / "events.jsonl"
COLD_PIPELINE = ROOT / "store" / "cold_pipeline.jsonl"
WARM_CSV = Path(os.environ.get("WARM_CSV") or (ROOT / "store" / "warm-hitlist.csv"))
LEDGER = ROOT / "store" / "ledger.jsonl"
OUT = ROOT / "store" / "cac.json"

# Rough $/1M token rates by model family (documented, approximate -- update if
# pricing changes; this is a cost ESTIMATE for internal CAC tracking, not a
# billing reconciliation).
RATE_PER_M_TOKENS = {
    "haiku": {"in": 1.00, "out": 5.00},
    "sonnet": {"in": 3.00, "out": 15.00},
    "opus": {"in": 15.00, "out": 75.00},
}
DEFAULT_RATE = RATE_PER_M_TOKENS["sonnet"]

# feature name -> lane
FEATURE_LANE = {
    "reply": "warm", "interpret": "internal", "content": "content",
    "networking": "warm", "job": "jobs",
}


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


def _rate_for(model: str) -> dict:
    m = (model or "").lower()
    for key, rate in RATE_PER_M_TOKENS.items():
        if key in m:
            return rate
    return DEFAULT_RATE


def _lane_for_feature(feature: str) -> str:
    for prefix, lane in FEATURE_LANE.items():
        if (feature or "").startswith(prefix):
            return lane
    return "internal"


def _token_cost_by_lane(usage_rows: list[dict]) -> dict[str, float]:
    cost_by_lane: dict[str, float] = defaultdict(float)
    for r in usage_rows:
        rate = _rate_for(r.get("model", ""))
        cost = (r.get("in", 0) / 1_000_000) * rate["in"] + (r.get("out", 0) / 1_000_000) * rate["out"]
        lane = _lane_for_feature(r.get("feature", ""))
        cost_by_lane[lane] += cost
    return dict(cost_by_lane)


RUNS_AGENT_LANE = {
    "cold": "cold", "warm": "warm", "job": "jobs", "proposal": "cold",
    "reply": "warm", "content": "content",
}


def _lane_for_agent(agent: str) -> str:
    for prefix, lane in RUNS_AGENT_LANE.items():
        if (agent or "").startswith(prefix):
            return lane
    return "internal"


def _machine_seconds_by_lane(runs_rows: list[dict]) -> dict[str, float] | None:
    """Real hours-proxy source when store/runs.jsonl has rows: sums dur_s per
    agent, bucketed to a lane by agent-name prefix. This is machine wall-clock
    time, not [OWNER]'s hours (nothing in this repo logs those), but it's the
    closest real signal that exists -- returns None (not {}) when runs.jsonl
    is empty so the caller can tell 'no rows yet' apart from 'genuinely zero
    activity', and fall back to the events.jsonl proxy only in the former case."""
    if not runs_rows:
        return None
    out: dict[str, float] = defaultdict(float)
    for r in runs_rows:
        lane = _lane_for_agent(r.get("agent", ""))
        out[lane] += float(r.get("dur_s") or 0)
    return dict(out)


def _activity_proxy_by_lane(cold_rows: list[dict], warm_row_count: int) -> dict[str, int]:
    """Cruder fallback proxy (used only when runs.jsonl is empty/missing):
    raw touch-event counts as an activity-volume stand-in. Labeled a proxy,
    not hours, in the output -- see module docstring."""
    cold_count = len(cold_rows)
    return {"cold": cold_count, "warm": warm_row_count}


def _event_volume() -> int:
    """Total request-event count from events.jsonl, as a rough overall
    system-activity number (used only for context in the note, not divided
    into a lane -- events.jsonl carries no lane tag)."""
    return len(_read_jsonl(EVENTS))


def _closed_by_lane(ledger_rows: list[dict], cold_idx: set, warm_idx: set) -> dict[str, float]:
    revenue: dict[str, float] = defaultdict(float)
    for row in ledger_rows:
        if row.get("kind") not in ("won", "payment", "closed"):
            continue
        note_norm = _norm(row.get("note") or "")
        amount = float(row.get("amount") or 0)
        lane = "(unmatched)"
        for company_norm in cold_idx:
            if company_norm and len(company_norm) >= 4 and company_norm in note_norm:
                lane = "cold"
                break
        if lane == "(unmatched)":
            for company_norm in warm_idx:
                if company_norm and len(company_norm) >= 4 and company_norm in note_norm:
                    lane = "warm"
                    break
        revenue[lane] += amount
    return dict(revenue)


def _cold_company_index() -> set[str]:
    return {_norm(r.get("company") or "") for r in _read_jsonl(COLD_PIPELINE) if r.get("company")}


def _warm_company_index() -> set[str]:
    out = set()
    if WARM_CSV.exists():
        for r in csv.DictReader(open(WARM_CSV, newline="")):
            for field in ("name", "company"):
                n = _norm(r.get(field) or "")
                if n:
                    out.add(n)
    return out


def build(token_cost: dict[str, float], activity: dict[str, float], revenue: dict[str, float],
          event_volume: int, hours_proxy_kind: str) -> dict:
    all_lanes = set(token_cost) | set(activity) | set(revenue) | {"cold", "warm", "internal", "content", "jobs"}
    lanes = {}
    for lane in sorted(all_lanes):
        if lane == "(unmatched)":
            continue
        cost = round(token_cost.get(lane, 0.0), 2)
        rev = round(revenue.get(lane, 0.0), 2)
        touches = activity.get(lane, 0)
        lanes[lane] = {
            "token_cost_usd": cost,
            "activity_proxy": round(touches, 2) if hours_proxy_kind == "runs_seconds" else touches,
            "closed_revenue": rev,
            "cac_per_close": None,
            "net": round(rev - cost, 2),
        }
    unmatched = round(revenue.get("(unmatched)", 0.0), 2)
    return {
        "generated": now_iso(),
        "hours_proxy_kind": hours_proxy_kind,
        "lanes": lanes,
        "unmatched_revenue": unmatched,
        "total_token_cost_usd": round(sum(token_cost.values()), 2),
        "total_closed_revenue": round(sum(v for k, v in revenue.items() if k != "(unmatched)"), 2),
        "event_volume_context": event_volume,
        "note": ("activity_proxy is summed dur_s (wall-clock seconds) per agent "
                "from store/runs.jsonl, bucketed to a lane -- machine time, not "
                "[OWNER]'s hours, but the closest real signal that exists." if hours_proxy_kind == "runs_seconds"
                else "activity_proxy falls back to raw touch-event counts "
                "(store/runs.jsonl had no rows) -- a cruder activity-volume "
                "stand-in, not hours.") + (
                " token_cost_usd uses documented approximate per-model rates, "
                "not a billing reconciliation. Revenue matched to lane by fuzzy "
                "company-name substring match against ledger notes, same "
                "honesty caveat as source_scorecard.py."),
    }


def _fixture_data():
    usage_rows = [
        {"feature": "reply", "model": "claude-sonnet-4-6", "in": 5000, "out": 2000},
        {"feature": "job_replies", "model": "claude-haiku-4-5", "in": 20000, "out": 5000},
        {"feature": "content", "model": "claude-sonnet-4-6", "in": 8000, "out": 4000},
    ]
    cold_rows = [{"company": "Acme HVAC"}, {"company": "Delta Plumbing"}]
    warm_count = 15
    cold_idx = {"acmehvac", "deltaplumbing"}
    warm_idx = {"brightsalon"}
    ledger_rows = [
        {"kind": "won", "amount": 2500, "note": "Acme HVAC signed off cold outreach"},
        {"kind": "won", "amount": 3500, "note": "Bright Salon closed off warm call"},
    ]
    return usage_rows, cold_rows, warm_count, cold_idx, warm_idx, ledger_rows


def run(fixture: bool = False) -> dict:
    if fixture:
        usage_rows, cold_rows, warm_count, cold_idx, warm_idx, ledger_rows = _fixture_data()
        runs_rows = [{"agent": "cold_feeder", "dur_s": 12.4}, {"agent": "warm_block", "dur_s": 3.1}]
        event_volume = 0
        source = "FIXTURE"
    else:
        usage_rows = _read_jsonl(USAGE)
        cold_rows = _read_jsonl(COLD_PIPELINE)
        warm_count = 0
        if WARM_CSV.exists():
            warm_count = sum(1 for _ in csv.DictReader(open(WARM_CSV, newline="")))
        cold_idx = _cold_company_index()
        warm_idx = _warm_company_index()
        ledger_rows = _read_jsonl(LEDGER)
        runs_rows = _read_jsonl(RUNS)
        event_volume = _event_volume()
        source = "REAL"

    token_cost = _token_cost_by_lane(usage_rows)
    machine_seconds = _machine_seconds_by_lane(runs_rows)
    if machine_seconds is not None:
        activity, hours_proxy_kind = machine_seconds, "runs_seconds"
    else:
        activity, hours_proxy_kind = _activity_proxy_by_lane(cold_rows, warm_count), "event_count_fallback"
    revenue = _closed_by_lane(ledger_rows, cold_idx, warm_idx)
    result = build(token_cost, activity, revenue, event_volume, hours_proxy_kind)

    # fill in cac_per_close now that lanes dict exists
    closes_by_lane = Counter()
    for row in ledger_rows:
        if row.get("kind") not in ("won", "payment", "closed"):
            continue
        note_norm = _norm(row.get("note") or "")
        for lane, idx in (("cold", cold_idx), ("warm", warm_idx)):
            if any(c and len(c) >= 4 and c in note_norm for c in idx):
                closes_by_lane[lane] += 1
                break
    for lane, n in closes_by_lane.items():
        if lane in result["lanes"] and n > 0:
            result["lanes"][lane]["cac_per_close"] = round(result["lanes"][lane]["token_cost_usd"] / n, 2)

    result["source"] = source
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))
    lane_str = ", ".join(f"{k}: cost=${v['token_cost_usd']:.2f} rev=${v['closed_revenue']:.0f}"
                         for k, v in result["lanes"].items() if v["token_cost_usd"] or v["closed_revenue"])
    print(f"channel_cac [{source}]: total_cost=${result['total_token_cost_usd']:.2f} "
          f"total_revenue=${result['total_closed_revenue']:.0f} | {lane_str or 'no lane activity'} -> {OUT}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", action="store_true")
    args = ap.parse_args()
    run(fixture=args.fixture)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
