#!/usr/bin/env python3
"""Correlation scan — pure-python check across the metrics history for pairs that
move together, so nobody has to eyeball a growing metrics.jsonl to notice one.

Why: warm_worked and pipeline_value are collected independently every night with
no analysis layer between them. If two series are actually moving in lockstep (or
in lockstep opposite), that's a lever worth knowing about; if not, saying so is
still useful (stops [OWNER] chasing a pattern that isn't there). Deliberately no
LLM call here, this is a closed-form stats check with no ambiguity to interpret.

Read-only against store/metrics.jsonl; only writes are an append to
store/insights.jsonl and a feed_add, and only when a pair actually correlates.
Run standalone: .venv/bin/python agents/correlate.py
"""
from __future__ import annotations

import json
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402

METRICS = ROOT / "store" / "metrics.jsonl"
INSIGHTS = ROOT / "store" / "insights.jsonl"
MIN_POINTS = 7
THRESHOLD = 0.6

# series name -> how to pull a numeric value out of one metrics.jsonl record.
# jobs.{submitted,confirmed,...} is a nested dict per metrics_rollup.py's schema.
SERIES = {
    "pipeline_value": lambda r: r.get("pipeline_value"),
    "warm_worked": lambda r: r.get("warm_worked"),
    "submitted": lambda r: (r.get("jobs") or {}).get("submitted"),
    "cold_enrolled": lambda r: r.get("cold_enrolled"),
    "tokens": lambda r: r.get("tokens"),
}


def _read_metrics() -> list[dict]:
    if not METRICS.exists():
        return []
    out = []
    for line in METRICS.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return sorted(out, key=lambda r: r.get("date", ""))


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return None
    return cov / (vx ** 0.5 * vy ** 0.5)


def find_correlations() -> list[dict]:
    rows = _read_metrics()
    hits = []
    for name_a, name_b in combinations(SERIES, 2):
        pairs = []
        for r in rows:
            a, b = SERIES[name_a](r), SERIES[name_b](r)
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                pairs.append((float(a), float(b)))
        if len(pairs) < MIN_POINTS:
            continue
        xs, ys = zip(*pairs)
        r = _pearson(list(xs), list(ys))
        if r is not None and abs(r) > THRESHOLD:
            hits.append({"a": name_a, "b": name_b, "r": round(r, 3), "n": len(pairs)})
    return hits


def main() -> int:
    hits = find_correlations()
    if not hits:
        print("no strong correlations yet (need more days)")
        return 0
    for h in hits:
        direction = "move together" if h["r"] > 0 else "move opposite"
        text = (f"{h['a']} and {h['b']} {direction} (r={h['r']}, n={h['n']} days). "
                "Worth checking whether one is actually driving the other.")
        rec = {"ts": now_iso(), "text": text}
        INSIGHTS.parent.mkdir(parents=True, exist_ok=True)
        with INSIGHTS.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        planner.feed_add("agent", f"Correlation: {h['a']} <-> {h['b']} (r={h['r']})")
        print(f"correlate: {h['a']} <-> {h['b']} r={h['r']} n={h['n']} -> {INSIGHTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
