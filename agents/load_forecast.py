#!/usr/bin/env python3
"""Load forecast — cheap early-warning on token spend trending up (#47).

store/usage.jsonl has one record per model call: {ts, feature, model, in, out,
cache_read, cache_write}. This rolls those into a daily total token count for the
last 7 days, fits a trend (simple linear slope) across those daily totals, and
projects tomorrow's total from it. Nothing here calls the CLI or the API, so the
forecast itself can never contribute to the load it's measuring.

Written for a human or the dashboard to glance at; if tomorrow's projection jumps
well past the 7-day average, that's the signal to go look at what agent got noisy
before it shows up as a surprise bill.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "store"
USAGE = STORE / "usage.jsonl"
OUT = STORE / "forecast.json"


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


def _record_tokens(r: dict) -> int:
    return sum(int(r.get(k) or 0) for k in ("in", "out", "cache_read", "cache_write"))


def _record_day(r: dict) -> str | None:
    ts = r.get("ts")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).date().isoformat()
    except ValueError:
        return None


def daily_totals(records: list[dict], days: int = 7) -> dict[str, int]:
    """Last `days` calendar days -> summed tokens, oldest first. Missing days = 0
    so a quiet Sunday doesn't just vanish from the trend line."""
    today = datetime.now().astimezone().date()
    buckets = {(today - timedelta(days=d)).isoformat(): 0 for d in range(days - 1, -1, -1)}
    for r in records:
        day = _record_day(r)
        if day in buckets:
            buckets[day] += _record_tokens(r)
    return buckets


def linear_slope(ys: list[float]) -> float:
    """Least-squares slope of ys against x = 0..n-1. Pure stdlib, no numpy dependency
    for a one-line trend estimate."""
    n = len(ys)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    return num / den if den else 0.0


def forecast() -> dict:
    records = _load_records()
    totals = daily_totals(records)
    days = list(totals.keys())
    values = list(totals.values())
    slope = linear_slope(values)
    avg = sum(values) / len(values) if values else 0.0
    last = values[-1] if values else 0.0
    predicted_tomorrow = max(0.0, last + slope)
    trend = "flat"
    if avg > 0:
        if slope > avg * 0.05:
            trend = "up"
        elif slope < -avg * 0.05:
            trend = "down"
    return {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "days": days,
        "daily_totals": values,
        "avg_daily_tokens": round(avg, 1),
        "slope_tokens_per_day": round(slope, 1),
        "trend": trend,
        "predicted_tomorrow_tokens": round(predicted_tomorrow, 1),
    }


def main() -> int:
    out = forecast()
    STORE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"load_forecast: trend={out['trend']} avg/day={out['avg_daily_tokens']:.0f} "
          f"predicted_tomorrow={out['predicted_tomorrow_tokens']:.0f} tokens -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
