#!/usr/bin/env python3
"""Daily insight — the one non-obvious thing in the last 14 days of numbers.

Why: metrics_rollup.py snapshots the numbers every night and the dashboard shows
them raw, but nobody's actually looking across the window connecting them ("cold
enrolled doubled the same week warm_worked stalled" kind of thing). This agent
feeds the recent trend + activity feed + job funnel to one cheap Haiku call and
asks for a single sharp observation, then logs it so it accumulates as a record
of what the brain noticed, not just what happened.

Read-only against the stores; only write is an append to store/insights.jsonl
plus a feed_add. Run standalone: .venv/bin/python agents/daily_insight.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402

METRICS = ROOT / "store" / "metrics.jsonl"
FEED = ROOT / "store" / "feed.jsonl"
INSIGHTS = ROOT / "store" / "insights.jsonl"


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


def _recent_metrics(days: int = 14) -> list[dict]:
    cutoff = (datetime.now().astimezone() - timedelta(days=days)).strftime("%Y-%m-%d")
    return sorted((r for r in _read_jsonl(METRICS) if (r.get("date") or "") >= cutoff),
                  key=lambda r: r.get("date", ""))


def _recent_feed(days: int = 2) -> list[dict]:
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    out = []
    for r in _read_jsonl(FEED):
        try:
            ts = datetime.fromisoformat(r.get("ts", ""))
        except (ValueError, TypeError):
            continue
        if ts >= cutoff:
            out.append(r)
    return out


def _jobs_counts() -> dict:
    from collections import Counter
    c = Counter(r.get("status") for r in _read_jsonl(ROOT / "store" / "jobs.jsonl"))
    return dict(c)


PROMPT = """You are [OWNER]'s chief-of-staff. Below is his last 14 days of daily metric
snapshots, his activity feed from the last 2 days, and his current job-application
funnel counts. Find the SINGLE most useful, non-obvious insight, something a
skim of the raw numbers would miss (a correlation, a stall, a ratio shifting).
Do not restate a single day's number. Do not ask questions.

METRICS (oldest -> newest):
%s

RECENT FEED:
%s

JOB FUNNEL COUNTS:
%s

Return ONLY the insight as plain text, exactly 2 sentences, JARVIS tone (crisp,
a little dry, respectful), NO em-dashes."""


def build_insight() -> str | None:
    metrics = _recent_metrics(14)
    if not metrics:
        return None
    feed = _recent_feed(2)
    jobs = _jobs_counts()
    prompt = PROMPT % (
        json.dumps(metrics, indent=1),
        json.dumps(feed, indent=1) or "none",
        json.dumps(jobs, indent=1),
    )
    out = planner._cli(prompt, timeout=120, feature="plan")
    if not out:
        return None
    text = out.strip()
    return text or None


def main() -> int:
    text = build_insight()
    if not text:
        print("daily_insight: no usable output (no metrics history yet or CLI failed)")
        return 0
    rec = {"ts": now_iso(), "text": text}
    INSIGHTS.parent.mkdir(parents=True, exist_ok=True)
    with INSIGHTS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    planner.feed_add("agent", f"Insight: {text[:70]}")
    print(f"daily_insight: wrote insight -> {INSIGHTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
