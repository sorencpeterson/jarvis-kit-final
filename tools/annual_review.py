#!/usr/bin/env python3
"""Annual review (#89) — compile a year-in-review from what the system actually
tracked: metrics.jsonl trend, feed 'done' counts, content posted, and git commits.

Why: metrics_rollup.py has been snapshotting nightly numbers, feed.jsonl has been
logging every completion, content/posts.jsonl tracks what actually went out, and
git history is the ground truth for how much shipped, but none of it is ever
rolled up into one human-readable retrospective. This compiles what data exists
into store/annual_review.md, and is explicit with a [thin data] placeholder
anywhere the window's history is too short to say something real, rather than
padding it with a guess.

Runnable any time (not just year-end) — it reports on the trailing 365 days from
'now', so a run in month 2 of the system just honestly shows a thin year.

Read-only against store/metrics.jsonl, store/feed.jsonl, content/posts.jsonl, and
git; only write is store/annual_review.md (full overwrite each run).
Run standalone: .venv/bin/python tools/annual_review.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402

METRICS = ROOT / "store" / "metrics.jsonl"
FEED = ROOT / "store" / "feed.jsonl"
POSTS = ROOT / "content" / "posts.jsonl"
OUT = ROOT / "store" / "annual_review.md"
WINDOW_DAYS = 365
THIN_THRESHOLD_DAYS = 30  # fewer days of metrics history than this -> flag as thin


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


def _cutoff() -> datetime:
    return datetime.now().astimezone() - timedelta(days=WINDOW_DAYS)


def _metrics_in_window() -> list[dict]:
    cutoff_date = _cutoff().strftime("%Y-%m-%d")
    return sorted((r for r in _read_jsonl(METRICS) if (r.get("date") or "") >= cutoff_date),
                  key=lambda r: r.get("date", ""))


def _feed_done_count() -> int:
    n = 0
    cutoff = _cutoff()
    for r in _read_jsonl(FEED):
        if r.get("kind") != "done":
            continue
        try:
            ts = datetime.fromisoformat(r.get("ts", ""))
        except ValueError:
            continue
        if ts >= cutoff:
            n += 1
    return n


def _content_posted_count() -> int:
    return sum(1 for r in _read_jsonl(POSTS) if r.get("status") == "posted")


def _git_commit_count() -> int | None:
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "rev-list", "--count", "HEAD"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return int(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def _thin(days_of_history: int) -> str:
    return " [thin data — short history]" if days_of_history < THIN_THRESHOLD_DAYS else ""


def _fmt_money(v) -> str:
    return f"${v:,.0f}" if isinstance(v, (int, float)) else "[no data]"


def build_report() -> str:
    metrics = _metrics_in_window()
    done_count = _feed_done_count()
    posted_count = _content_posted_count()
    commit_count = _git_commit_count()
    days_of_history = len(metrics)
    thin_tag = _thin(days_of_history)

    first = metrics[0] if metrics else {}
    last = metrics[-1] if metrics else {}

    pipeline_delta = None
    if isinstance(first.get("pipeline_value"), (int, float)) and isinstance(last.get("pipeline_value"), (int, float)):
        pipeline_delta = last["pipeline_value"] - first["pipeline_value"]

    jobs_last = last.get("jobs") or {}
    tokens_total = sum(r.get("tokens") or 0 for r in metrics if isinstance(r.get("tokens"), (int, float)))

    lines = []
    lines.append(f"# Year in Review")
    lines.append(f"Generated {now_iso()} · trailing {WINDOW_DAYS} days\n")

    lines.append("## The numbers")
    lines.append(f"- Metrics history: {days_of_history} day(s) snapshotted{thin_tag}")
    lines.append(f"- Todos completed (feed 'done' entries): {done_count}{_thin(WINDOW_DAYS if done_count else 0) if not metrics else ''}")
    lines.append(f"- Content posted: {posted_count}")
    lines.append(f"- Git commits (all-time, {ROOT.name}): "
                 f"{commit_count if commit_count is not None else '[no data — git call failed]'}")
    lines.append("")

    lines.append("## Pipeline & warm outreach")
    if metrics:
        lines.append(f"- Pipeline value: {_fmt_money(first.get('pipeline_value'))} -> "
                     f"{_fmt_money(last.get('pipeline_value'))}"
                     + (f" ({'+' if pipeline_delta >= 0 else ''}{pipeline_delta:,.0f})" if pipeline_delta is not None else ""))
        lines.append(f"- Warm worked (latest snapshot): {last.get('warm_worked', '[no data]')}")
        lines.append(f"- Warm booked (latest snapshot): {last.get('warm_booked', '[no data]')}")
        lines.append(f"- Replies waiting (latest snapshot): {last.get('replies_waiting', '[no data]')}")
    else:
        lines.append("- [no data — metrics.jsonl has no snapshots in this window yet]")
    lines.append("")

    lines.append("## Job search funnel (latest snapshot)")
    if jobs_last:
        for k in ("submitted", "confirmed", "replied", "interview", "rejected"):
            lines.append(f"- {k}: {jobs_last.get(k, '[no data]')}")
    else:
        lines.append("- [no data — no job funnel snapshot in this window]")
    lines.append("")

    lines.append("## Cold outreach (latest snapshot)")
    if metrics:
        lines.append(f"- Staged: {last.get('cold_staged', '[no data]')}")
        lines.append(f"- Enrolled: {last.get('cold_enrolled', '[no data]')}")
        lines.append(f"- Hooks generated: {last.get('cold_hooks', '[no data]')}")
    else:
        lines.append("- [no data]")
    lines.append("")

    lines.append("## System usage")
    lines.append(f"- Total tokens across snapshotted days: {tokens_total:,}" if tokens_total
                 else "- [no data — no usage snapshots in this window]")
    lines.append("")

    if days_of_history < THIN_THRESHOLD_DAYS:
        lines.append("## Note")
        lines.append(f"This window only has {days_of_history} day(s) of metrics history "
                     f"(metrics_rollup.py runs nightly, so history builds day by day). "
                     f"Numbers above are real, but the trend line is still short. Re-run "
                     f"this later in the year for a fuller picture.")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    report = build_report()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(report)
    print(f"annual_review: wrote report -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
