#!/usr/bin/env python3
"""Weekly anomaly agent (tech #271) — any numeric series in store/metrics.jsonl
that drifts ±2 sigma from its trailing 8-week mean gets exactly one feed line,
capped at 3/day so it can't spam. No dashboard needed to notice a real swing.

store/metrics.jsonl is one JSON object per day (see metrics_rollup.py's shape:
pipeline_value, pipeline_open, warm_worked, warm_booked, replies_waiting,
cold_staged, cold_enrolled, cold_hooks, tokens, calls, plus nested jobs.*).
This walks every top-level numeric field (flattening one level of nesting for
dicts like "jobs") and flags any day whose value sits outside mean ± 2*stdev
of the trailing 8-week (56-day) window BEFORE that day.

HONESTY NOTE: with only 2 rows in metrics.jsonl right now there's no 8-week
window to compute a mean/stdev against -- this degrades to "insufficient
history (n=2, need 14+)" rather than flagging noise as a signal. The bar is
set at 14 (2 weeks) as an absolute floor before ANY z-score is trusted, short
of the full 56-day/8-week target, so it doesn't stay silent for 2 months
straight while data accumulates -- but even at 14-55 days it labels the
z-score as "provisional" until it clears the full 56.

Read-only against store/metrics.jsonl; writes one feed line per anomaly (max
3/day) via planner.feed_add, plus store/anomalies.json (last run's full
detail, full overwrite, for inspection). Run standalone:
.venv/bin/python agents/anomaly_watch.py
.venv/bin/python agents/anomaly_watch.py --fixture
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402

METRICS = ROOT / "store" / "metrics.jsonl"
OUT = ROOT / "store" / "anomalies.json"

FULL_WINDOW_DAYS = 56  # 8 weeks, the target window per the item description
MIN_HISTORY_DAYS = 14  # absolute floor before trusting ANY z-score
Z_THRESHOLD = 2.0
MAX_FEED_LINES_PER_DAY = 3


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


def _flatten(rec: dict, prefix: str = "") -> dict[str, float]:
    """One level of nesting flattened (jobs.submitted, jobs.confirmed, ...).
    Non-numeric / date fields are skipped."""
    out = {}
    for k, v in rec.items():
        if k in ("date", "ts"):
            continue
        key = f"{prefix}{k}"
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[key] = float(v)
        elif isinstance(v, dict):
            out.update(_flatten(v, prefix=f"{key}."))
    return out


def _series_by_field(rows: list[dict]) -> dict[str, list[tuple[str, float]]]:
    """field -> [(date, value), ...] in row order (assumed chronological, same
    append-only discipline as every other jsonl store here)."""
    series: dict[str, list[tuple[str, float]]] = {}
    for rec in rows:
        date = rec.get("date") or rec.get("ts", "")[:10]
        flat = _flatten(rec)
        for field, value in flat.items():
            series.setdefault(field, []).append((date, value))
    return series


def detect(rows: list[dict]) -> dict:
    n = len(rows)
    if n < MIN_HISTORY_DAYS:
        return {
            "generated": now_iso(), "history_days": n,
            "status": f"insufficient history (n={n}, need {MIN_HISTORY_DAYS}+ for even a provisional z-score, "
                      f"{FULL_WINDOW_DAYS} for the full 8-week window)",
            "anomalies": [],
        }

    series = _series_by_field(rows)
    provisional = n < FULL_WINDOW_DAYS
    anomalies = []
    for field, points in series.items():
        if len(points) < MIN_HISTORY_DAYS + 1:
            continue
        # trailing window BEFORE the most recent point, capped at FULL_WINDOW_DAYS
        latest_date, latest_val = points[-1]
        history = [v for _, v in points[:-1]][-FULL_WINDOW_DAYS:]
        if len(history) < MIN_HISTORY_DAYS:
            continue
        mean = statistics.fmean(history)
        try:
            stdev = statistics.stdev(history)
        except statistics.StatisticsError:
            stdev = 0.0
        if stdev == 0:
            continue  # a flat series can't have a meaningful z-score
        z = (latest_val - mean) / stdev
        if abs(z) >= Z_THRESHOLD:
            anomalies.append({
                "field": field, "date": latest_date, "value": latest_val,
                "trailing_mean": round(mean, 2), "trailing_stdev": round(stdev, 2),
                "z_score": round(z, 2), "window_days": len(history),
                "provisional": provisional,
                "direction": "spike" if z > 0 else "drop",
            })
    anomalies.sort(key=lambda a: -abs(a["z_score"]))
    return {
        "generated": now_iso(), "history_days": n,
        "status": "provisional (window < 8 weeks)" if provisional else "ready",
        "anomalies": anomalies,
    }


def _feed_lines(result: dict) -> list[str]:
    lines = []
    for a in result["anomalies"][:MAX_FEED_LINES_PER_DAY]:
        tag = " [provisional]" if a.get("provisional") else ""
        lines.append(f"{a['field']} {a['direction']}ed to {a['value']:g} on {a['date']} "
                    f"(8wk mean {a['trailing_mean']:g}, z={a['z_score']:+.1f}){tag}")
    return lines


def _fixture_rows() -> list[dict]:
    """56 days of quiet baseline + one deliberate spike on the last day, to
    prove detection fires with real math, not fake output."""
    import random
    rng = random.Random(42)
    rows = []
    for i in range(FULL_WINDOW_DAYS):
        rows.append({"date": f"2026-01-{(i % 28) + 1:02d}", "pipeline_value": 20000 + rng.randint(-1500, 1500),
                    "warm_worked": 8 + rng.randint(-2, 2), "tokens": 200000 + rng.randint(-20000, 20000)})
    # deliberate spike: pipeline_value triples on the final day
    rows.append({"date": "2026-07-03", "pipeline_value": 65000, "warm_worked": 8, "tokens": 210000})
    return rows


def run(fixture: bool = False) -> dict:
    if fixture:
        rows = _fixture_rows()
        source = "FIXTURE"
    else:
        rows = _read_jsonl(METRICS)
        source = "REAL"
    result = detect(rows)
    result["source"] = source
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))

    pushed = 0
    if source == "REAL":
        for line in _feed_lines(result):
            planner.feed_add("anomaly", line)
            pushed += 1

    print(f"anomaly_watch [{source}]: {result['status']}, {len(result['anomalies'])} anomal{'y' if len(result['anomalies']) == 1 else 'ies'} found"
          + (f", {pushed} feed line(s) pushed" if source == "REAL" else " (fixture run, no feed writes)")
          + f" -> {OUT}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", action="store_true")
    args = ap.parse_args()
    run(fixture=args.fixture)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
