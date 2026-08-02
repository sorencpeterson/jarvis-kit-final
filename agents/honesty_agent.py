#!/usr/bin/env python3
"""Honesty agent: the weekly build-vs-close audit. The recurring version of
THE-COLD-READ. Nothing else in the system compares what got BUILT this week
against what got CLOSED, so a week of furious commits and zero sends can feel
productive. This agent says the quiet part in two or three sentences.

WHAT: computes four numbers for the trailing WEEK_DAYS days:
        - commits in this repo (git log --since)
        - $ closed (store/ledger.jsonl, kind=won, sum of amount)
        - $ staged-but-unsent (proposals with status=staged, sum of price)
        - warm calls made (store/warm_dispo.jsonl count)
      then composes 2-3 blunt sentences in [OWNER]'s voice (deterministic
      template, humanize()-filtered, no em-dashes, no softening), pushes them,
      logs to the feed, and writes store/honesty_report.json.
WHEN: weekly. Only fires on REPORT_WEEKDAY (default Sunday) unless --force.
      Idempotent per day via the report file's date (a rerun the same day
      skips unless --force).
RAILS: read-only against ledger/proposals/dispos/git. Writes only its own
      report file + the feed. Push is a self-notification. --dry-run computes
      and prints regardless of weekday, writes nothing.

Run: .venv/bin/python agents/honesty_agent.py [--dry-run] [--force]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import LOCAL_TZ, humanize, now_iso  # noqa: E402
import planner  # noqa: E402

# ---- tunables ----
OUT = ROOT / "store" / "honesty_report.json"
LEDGER = ROOT / "store" / "ledger.jsonl"
DISPO = ROOT / "store" / "warm_dispo.jsonl"
PROPOSALS = ROOT / "store" / "proposals.jsonl"  # fallback if factory import fails
REPORT_WEEKDAY = 6  # Sunday (datetime.weekday(): Mon=0 .. Sun=6)
WEEK_DAYS = 7


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


def _in_week(ts: str, since: datetime) -> bool:
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts)
        if not dt.tzinfo:
            dt = dt.astimezone()
        return dt >= since
    except (ValueError, TypeError):
        return False


def _commits_this_week() -> int:
    """Commit count in this repo over the trailing week; 0 on any git surprise
    (not a git repo, git missing) rather than a crash."""
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "log", f"--since={WEEK_DAYS} days ago", "--oneline"],
            capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return 0
        return len([l for l in out.stdout.splitlines() if l.strip()])
    except (OSError, subprocess.SubprocessError):
        return 0


def _staged_proposals() -> list[dict]:
    try:
        import proposal_factory
        rows = proposal_factory.load_queue()
    except Exception:  # noqa: BLE001
        by_id = {}
        for r in _read_jsonl(PROPOSALS):
            if r.get("id"):
                by_id[r["id"]] = r
        rows = list(by_id.values())
    return [r for r in rows if r.get("status") == "staged"]


def compute(commits: int | None = None) -> dict:
    """The four honest numbers. `commits` is injectable for tests."""
    since = datetime.now(LOCAL_TZ) - timedelta(days=WEEK_DAYS)
    closed = 0.0
    for r in _read_jsonl(LEDGER):
        if r.get("kind") == "won" and _in_week(r.get("ts", ""), since):
            try:
                closed += float(r.get("amount") or 0)
            except (TypeError, ValueError):
                pass
    staged = _staged_proposals()
    staged_total = 0.0
    for r in staged:
        try:
            staged_total += float(r.get("price") or 0)
        except (TypeError, ValueError):
            pass
    calls = sum(1 for r in _read_jsonl(DISPO) if _in_week(r.get("ts", ""), since))
    return {"commits": _commits_this_week() if commits is None else commits,
            "closed": round(closed, 2),
            "staged_total": round(staged_total, 2),
            "staged_count": len(staged),
            "warm_calls": calls}


def compose(s: dict) -> str:
    """2-3 blunt sentences, [OWNER]'s voice, no softening. Deterministic on
    purpose: an audit should not depend on a model being in a good mood."""
    lines = [f"You built {s['commits']} commits and closed ${s['closed']:,.0f} this week."]
    if s["staged_total"] > 0:
        lines.append(f"${s['staged_total']:,.0f} is staged and unsent across "
                     f"{s['staged_count']} proposal(s).")
    if s["warm_calls"] == 0:
        lines.append("You made 0 warm calls. The phone is the bottleneck, not the machine.")
    elif s["closed"] == 0:
        lines.append(f"{s['warm_calls']} warm calls and nothing closed yet. Keep dialing, "
                     "the math only works if the reps continue.")
    else:
        lines.append(f"{s['warm_calls']} warm calls, ${s['closed']:,.0f} in. "
                     "That is the loop working. Do it again.")
    return humanize(" ".join(lines))


def run(dry_run: bool = False, force: bool = False) -> int:
    now = datetime.now(LOCAL_TZ)
    today = now.strftime("%Y-%m-%d")

    if not dry_run and not force:
        if now.weekday() != REPORT_WEEKDAY:
            print(f"honesty_agent: not the report day (weekday {now.weekday()}, "
                  f"fires on {REPORT_WEEKDAY}), skipping (use --force)")
            return 0
        try:
            prev = json.loads(OUT.read_text())
            if prev.get("date") == today:
                print("honesty_agent: report already written today, skipping (use --force)")
                return 0
        except (OSError, json.JSONDecodeError):
            pass

    stats = compute()
    text = compose(stats)
    print(f"honesty_agent: commits={stats['commits']} closed=${stats['closed']:,.0f} "
          f"staged=${stats['staged_total']:,.0f} ({stats['staged_count']}) "
          f"warm_calls={stats['warm_calls']}")
    print(text)

    if dry_run:
        gate = "would fire" if (now.weekday() == REPORT_WEEKDAY) else \
            f"weekday gate would block (fires on weekday {REPORT_WEEKDAY})"
        print(f"[dry-run] no push, no write; {gate}")
        return 0

    OUT.write_text(json.dumps({"date": today, "generated": now_iso(),
                               **stats, "text": text}, indent=2, ensure_ascii=False))
    planner.notify("The weekly honesty report", text, tags="scales")
    try:
        planner.feed_add("agent", "Weekly honesty report", text[:180])
    except Exception:  # noqa: BLE001
        pass
    print(f"honesty_agent: pushed + wrote {OUT}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Weekly build-vs-close honesty audit")
    ap.add_argument("--dry-run", action="store_true", help="compute and print, no push, no write")
    ap.add_argument("--force", action="store_true", help="run regardless of weekday/idempotency")
    args = ap.parse_args()
    if args.dry_run:
        return run(dry_run=True, force=args.force)
    from runlog import track
    with track("honesty_agent"):
        return run(dry_run=False, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
