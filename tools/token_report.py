#!/usr/bin/env python3
"""What the system actually spent, by feature and by day.

    python3 tools/token_report.py            last 7 days
    python3 tools/token_report.py --days 30
    python3 tools/token_report.py --apply    cost per application

Reads store/usage.jsonl, which now includes the apply operator under feature
"job_apply". Before that was metered, the largest consumer in the system was the one
thing missing from this ledger, so every total here understated reality by an unknown
amount and "what does an application cost" had no answer.

Numbers here are what the CLI reported, not estimates.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USAGE = ROOT / "store" / "usage.jsonl"


def _rows(days: int) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    try:
        for line in USAGE.read_text().splitlines():
            try:
                r = json.loads(line)
            except (ValueError, json.JSONDecodeError):
                continue          # one bad line must not sink the report
            if (r.get("ts") or "")[:10] >= cutoff:
                out.append(r)
    except OSError:
        pass
    return out


def _tok(r: dict) -> int:
    return sum(int(r.get(k) or 0) for k in ("in", "out", "cache_read", "cache_write"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--apply", action="store_true", help="cost per application")
    a = ap.parse_args()

    rows = _rows(a.days)
    if not rows:
        print(f"\n  No usage recorded in the last {a.days} day(s).")
        print(f"  ({USAGE} is written by planner._cli and by the apply operator.)\n")
        return 0

    if a.apply:
        ops = [r for r in rows if r.get("feature") == "job_apply"]
        if not ops:
            print("\n  No metered applications yet. Run the apply chain once; each")
            print("  operator now records its own usage under feature 'job_apply'.\n")
            return 0
        toks = sorted(_tok(r) for r in ops)
        total = sum(toks)
        mid = toks[len(toks) // 2]
        print(f"\n  APPLICATIONS: {len(ops)} metered operator run(s)")
        print(f"    total    {total:>12,} tokens")
        print(f"    median   {mid:>12,} tokens per run")
        print(f"    range    {toks[0]:,} to {toks[-1]:,}")
        print(f"\n  A deterministic application (agents/apply_direct.py) costs 0 of these.")
        print(f"  At the median above, every job moved onto that path saves ~{mid:,} tokens.\n")
        return 0

    by_day = defaultdict(int)
    by_feat = defaultdict(int)
    for r in rows:
        by_day[(r.get("ts") or "")[:10]] += _tok(r)
        by_feat[r.get("feature") or "?"] += _tok(r)

    print(f"\n  TOKENS, last {a.days} day(s)\n")
    for d in sorted(by_day):
        print(f"    {d}   {by_day[d]:>12,}")
    print(f"\n  BY FEATURE\n")
    total = sum(by_feat.values()) or 1
    for f, t in sorted(by_feat.items(), key=lambda x: -x[1])[:14]:
        print(f"    {f:<20} {t:>12,}   {t * 100 // total:>3}%")
    if "job_apply" not in by_feat:
        print("\n  NOTE: no 'job_apply' rows. Either no applications have run since")
        print("  metering landed, or the server is running pre-metering code.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
