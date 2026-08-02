#!/usr/bin/env python3
"""A11 (FABLE-BUILD-QUEUE Section 5, MED): the weekly rejection-pattern digest.
Individual rejections get recorded and forgotten; the pattern across a week
(one ATS rejecting everything, one seniority band bouncing) is where the
fixable signal lives.

WHAT: collects jobs flipped to rejected in the trailing WINDOW_DAYS days
      (via rejected_at, stamped by job_replies.py), groups them by source,
      seniority, and fit band, and writes store/rejection_digest.json plus a
      feed line. If the week had >= LLM_MIN rejections, ONE planner._cli call
      writes a short "pattern read" paragraph from the grouped counts and
      rejection snippets; under that threshold the LLM is skipped (no pattern
      in 1-2 data points, not worth a call) and a deterministic line is used.
WHEN: weekly. Only fires on REPORT_WEEKDAY (Sunday, matching honesty_agent)
      unless --force; idempotent per day via the report file's date.
RAILS: read-only against jobs.jsonl. Writes only its own report file + the
      feed. No pushes, no sends, nothing outward. --dry-run computes and
      prints regardless of weekday, skips the LLM, writes nothing. Fresh
      install (no jobs.jsonl) prints and exits 0.

Run:  .venv/bin/python agents/rejection_digest.py [--dry-run] [--force]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import LOCAL_TZ, humanize, now_iso  # noqa: E402
import planner  # noqa: E402
import jobs  # noqa: E402

# ---- tunables ----
OUT = ROOT / "store" / "rejection_digest.json"
REPORT_WEEKDAY = 6   # Sunday (datetime.weekday(): Mon=0 .. Sun=6), same as honesty_agent
WINDOW_DAYS = 7
LLM_MIN = 3          # fewer rejections than this = no LLM call, deterministic line
FIT_FLOOR = 62       # the jobs-machine fit floor; bands pivot on it
READ_TIMEOUT = 90
SNIPPET_CAP = 6      # rejection snippets fed to the pattern read

READ_PROMPT = """[OWNER]'s job applications got %d rejections in the last %d days. Grouped counts:

by ATS/source: %s
by seniority: %s
by fit band (his approval floor is fit %d): %s

Rejection snippets:
%s

Write ONE short paragraph (2-4 sentences) reading the pattern: what is over-represented, what to stop or change in next week's applications. Plain text, direct, no fluff, no em-dashes, no preamble, no markdown."""


def _in_window(ts: str, since: datetime) -> bool:
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts)
        if not dt.tzinfo:
            dt = dt.astimezone()
        return dt >= since
    except (ValueError, TypeError):
        return False


def fit_band(fit) -> str:
    try:
        f = int(fit)
    except (TypeError, ValueError):
        return "unknown"
    if f < FIT_FLOOR:
        return f"<{FIT_FLOOR}"
    if f < 75:
        return f"{FIT_FLOOR}-74"
    return "75+"


def collect() -> list[dict]:
    since = datetime.now(LOCAL_TZ) - timedelta(days=WINDOW_DAYS)
    return [j for j in jobs.load_jobs()
            if j.get("status") == "rejected" and _in_window(j.get("rejected_at", ""), since)]


def group(rows: list[dict]) -> dict:
    return {
        "by_source": dict(Counter((r.get("source") or "unknown") for r in rows)),
        "by_seniority": dict(Counter((r.get("seniority") or "unknown") for r in rows)),
        "by_fit_band": dict(Counter(fit_band(r.get("fit")) for r in rows)),
    }


def _fmt_counts(d: dict) -> str:
    return ", ".join(f"{k}: {v}" for k, v in sorted(d.items(), key=lambda kv: -kv[1])) or "none"


def pattern_read(rows: list[dict], groups: dict, use_llm: bool = True) -> str:
    """The one-paragraph read. LLM only at >= LLM_MIN rejections AND use_llm
    (dry-run passes False); deterministic fallback everywhere else."""
    n = len(rows)
    if n == 0:
        return f"No rejections in the last {WINDOW_DAYS} days."
    top_src = _fmt_counts(groups["by_source"]).split(",")[0]
    if n < LLM_MIN:
        return (f"{n} rejection(s) this week (top source {top_src}); "
                f"under {LLM_MIN} there is no pattern worth reading yet.")
    if not use_llm:  # dry-run: enough data for a model read, call skipped on purpose
        return (f"{n} rejections this week (top source {top_src}); "
                "LLM pattern read skipped on dry-run.")
    snippets = "\n".join(f"- {r.get('company') or '?'}: {(r.get('rejection_snippet') or '')[:140]}"
                         for r in rows[:SNIPPET_CAP])
    out = planner._cli(
        READ_PROMPT % (n, WINDOW_DAYS, _fmt_counts(groups["by_source"]),
                       _fmt_counts(groups["by_seniority"]), FIT_FLOOR,
                       _fmt_counts(groups["by_fit_band"]), snippets or "(none captured)"),
        timeout=READ_TIMEOUT)
    out = (out or "").strip()
    if not out:
        return (f"{n} rejections this week. Top source {_fmt_counts(groups['by_source'])}. "
                "Model offline, read the counts yourself.")
    return humanize(out)[:700]


def run(dry_run: bool = False, force: bool = False) -> int:
    now = datetime.now(LOCAL_TZ)
    today = now.strftime("%Y-%m-%d")
    if not dry_run and not force:
        if now.weekday() != REPORT_WEEKDAY:
            print(f"rejection_digest: not the report day (weekday {now.weekday()}, "
                  f"fires on {REPORT_WEEKDAY}), skipping (use --force)")
            return 0
        try:
            prev = json.loads(OUT.read_text())
            if prev.get("date") == today:
                print("rejection_digest: already written today, skipping (use --force)")
                return 0
        except (OSError, json.JSONDecodeError):
            pass

    rows = collect()
    groups = group(rows)
    read = pattern_read(rows, groups, use_llm=not dry_run)
    print(f"rejection_digest: {len(rows)} rejection(s) in {WINDOW_DAYS}d")
    print(f"  by source:    {_fmt_counts(groups['by_source'])}")
    print(f"  by seniority: {_fmt_counts(groups['by_seniority'])}")
    print(f"  by fit band:  {_fmt_counts(groups['by_fit_band'])}")
    print(f"  read: {read}")

    if dry_run:
        gate = "would fire" if now.weekday() == REPORT_WEEKDAY else \
            f"weekday gate would block (fires on weekday {REPORT_WEEKDAY})"
        llm = "would call LLM" if len(rows) >= LLM_MIN else \
            f"would SKIP LLM ({len(rows)} < {LLM_MIN} rejections)"
        print(f"[dry-run] no write, no feed; {gate}; {llm}")
        return 0

    OUT.write_text(json.dumps(
        {"date": today, "generated": now_iso(), "window_days": WINDOW_DAYS,
         "total": len(rows), **groups,
         "rejections": [{"job_id": r.get("id"), "company": r.get("company"),
                         "source": r.get("source"), "seniority": r.get("seniority"),
                         "fit": r.get("fit"), "rejected_at": r.get("rejected_at"),
                         "snippet": (r.get("rejection_snippet") or "")[:140]}
                        for r in rows],
         "read": read}, indent=2, ensure_ascii=False))
    try:
        planner.feed_add("jobs", f"Rejection digest: {len(rows)} in {WINDOW_DAYS}d", read[:180])
    except Exception:  # noqa: BLE001 - feed hiccup must not fail the report
        pass
    print(f"rejection_digest: wrote {OUT}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Weekly rejection-pattern digest")
    ap.add_argument("--dry-run", action="store_true",
                    help="compute and print regardless of weekday; no LLM, no write")
    ap.add_argument("--force", action="store_true", help="run regardless of weekday/idempotency")
    args = ap.parse_args()
    if args.dry_run:
        return run(dry_run=True, force=args.force)
    from runlog import track
    with track("rejection_digest"):
        return run(dry_run=False, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
