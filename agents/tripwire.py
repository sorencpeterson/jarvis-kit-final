#!/usr/bin/env python3
"""Runaway-agent tripwire (#100) — catches a feature calling way more than usual.

Reads store/usage.jsonl (same shape as load_forecast.py: {ts, feature, model, in,
out, cache_read, cache_write}), buckets calls per feature into the last 1 hour vs.
the average-per-hour rate over the trailing 7 days, and flags a feature as runaway
if BOTH:
  - last-hour calls > 10x its 7-day hourly average, AND
  - last-hour calls > 30 (absolute floor, so a feature that normally makes 1 call/day
    doesn't trip the alarm just for making 2 in an hour)

On a trip, writes store/.tripwire (a flag file — advisory only, nothing reads it yet)
and calls planner.notify() to push [OWNER]'s phone. --dry prints what WOULD happen
and skips both the flag-file write and the notify call, for safe testing.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "store"
USAGE = STORE / "usage.jsonl"
FLAG = STORE / ".tripwire"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

TRIP_MULTIPLIER = 10
TRIP_MIN_CALLS = 30


def _load_records() -> list[dict]:
    if not USAGE.exists():
        return []
    out = []
    for line in USAGE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _parse_ts(rec: dict) -> datetime | None:
    ts = rec.get("ts")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt


def analyze(records: list[dict], now: datetime | None = None) -> list[dict]:
    """Per feature: last-1h call count vs. avg calls/hour over the trailing 7 days.
    Returns only features that cross the trip thresholds."""
    now = now or datetime.now().astimezone()
    since_7d = now - timedelta(days=7)
    since_1h = now - timedelta(hours=1)

    counts_7d: dict[str, int] = {}
    counts_1h: dict[str, int] = {}
    for r in records:
        dt = _parse_ts(r)
        if dt is None or dt < since_7d:
            continue
        feature = r.get("feature") or "unknown"
        counts_7d[feature] = counts_7d.get(feature, 0) + 1
        if dt >= since_1h:
            counts_1h[feature] = counts_1h.get(feature, 0) + 1

    hits = []
    for feature, last_hour in counts_1h.items():
        total_7d = counts_7d.get(feature, 0)
        avg_per_hour = total_7d / (7 * 24)
        threshold = max(avg_per_hour * TRIP_MULTIPLIER, 0.0)
        if last_hour > threshold and last_hour > TRIP_MIN_CALLS:
            hits.append({
                "feature": feature,
                "last_hour_calls": last_hour,
                "avg_hourly_7d": round(avg_per_hour, 3),
                "multiplier": round(last_hour / avg_per_hour, 1) if avg_per_hour else None,
            })
    return hits


def main() -> int:
    dry = "--dry" in sys.argv[1:]
    records = _load_records()
    hits = analyze(records)

    if not hits:
        print("tripwire: no runaway features detected" + (" (--dry)" if dry else ""))
        return 0

    detail = "; ".join(
        f"{h['feature']}: {h['last_hour_calls']} calls/1h vs avg {h['avg_hourly_7d']}/h"
        f" ({h['multiplier']}x)" for h in hits
    )
    payload = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
               "features": hits}

    if dry:
        print(f"tripwire --dry: WOULD flag and notify -> {detail}")
        return 0

    STORE.mkdir(parents=True, exist_ok=True)
    FLAG.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    try:
        from planner import notify
        notify("Runaway agent paused", detail, tags="warning,brain")
    except Exception:  # noqa: BLE001 — notify is best-effort, never block the flag write
        pass
    print(f"tripwire: TRIPPED -> {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
