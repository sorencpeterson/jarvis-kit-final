#!/usr/bin/env python3
"""Salary intel — turns the free-text salary strings on every job posting into
median/p25/p75 by title keyword, so "is this offer actually good" has a number
behind it instead of a gut check.

Why: jobs.jsonl's salary field is whatever string the source ATS wrote
("$80k-$95k", sometimes an hourly rate, sometimes blank) and nothing aggregates
it. This regex-parses every non-empty salary string across ALL jobs (any status,
not just applied, more signal that way), buckets by title keyword, and reports
the distribution. Pure stdlib, no LLM: salary parsing is pattern matching, not
judgment.

Read-only against store/jobs.jsonl; only write is store/salary_intel.json
(full overwrite each run). Run standalone: .venv/bin/python agents/salary_intel.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT,):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402

JOBS = ROOT / "store" / "jobs.jsonl"
OUT = ROOT / "store" / "salary_intel.json"
KEYWORDS = ("marketing", "seo", "wordpress", "web", "director", "manager")

# "$80k - $95k" / "$80,000-$95,000" / "$45/hr - $55/hr" / "$45/hr" (single figure).
# k-suffix and /hr are mutually exclusive per number; /hr converts at 2080 hrs/yr
# (standard full-time annualization) so every parsed figure lands in $/yr.
_NUM = r"\$?\s*([\d,]+(?:\.\d+)?)\s*(k)?\s*(/\s*hr|/\s*hour|per\s*hour)?"
_RANGE_RE = re.compile(_NUM + r"\s*(?:-|to|–|—)\s*" + _NUM, re.I)
_SINGLE_RE = re.compile(_NUM, re.I)


def _to_yearly(raw: str, k: str, hourly: str) -> float | None:
    try:
        n = float(raw.replace(",", ""))
    except ValueError:
        return None
    if hourly:
        return n * 2080
    if k:
        return n * 1000
    # bare number with no k/hr marker: only trust it if it's already
    # salary-shaped (>= 1000), otherwise it's noise (e.g. a stray "40").
    return n if n >= 1000 else None


def parse_salary(s: str) -> tuple[float, float] | None:
    """Return (low, high) annualized, or None if nothing parseable. A single
    figure (no range) is returned as (x, x)."""
    if not s:
        return None
    m = _RANGE_RE.search(s)
    if m:
        lo = _to_yearly(m.group(1), m.group(2), m.group(3))
        hi = _to_yearly(m.group(4), m.group(5), m.group(6))
        if lo is not None and hi is not None:
            return (lo, hi) if lo <= hi else (hi, lo)
    m = _SINGLE_RE.search(s)
    if m:
        v = _to_yearly(m.group(1), m.group(2), m.group(3))
        if v is not None:
            return (v, v)
    return None


def _read_jobs() -> list[dict]:
    if not JOBS.exists():
        return []
    out = []
    for line in JOBS.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = pct * (len(sorted_vals) - 1)
    lo_i, hi_i = int(idx), min(int(idx) + 1, len(sorted_vals) - 1)
    frac = idx - lo_i
    return sorted_vals[lo_i] + (sorted_vals[hi_i] - sorted_vals[lo_i]) * frac


def build_intel() -> dict:
    by_kw: dict[str, list[float]] = {kw: [] for kw in KEYWORDS}
    parsed_n, total_n = 0, 0
    for j in _read_jobs():
        total_n += 1
        rng = parse_salary(j.get("salary") or "")
        if rng is None:
            continue
        parsed_n += 1
        mid = (rng[0] + rng[1]) / 2
        title = (j.get("title") or "").lower()
        for kw in KEYWORDS:
            if kw in title:
                by_kw[kw].append(mid)

    by_title_keyword = {}
    for kw, vals in by_kw.items():
        vals.sort()
        by_title_keyword[kw] = {
            "median": round(_percentile(vals, 0.5)) if vals else None,
            "p25": round(_percentile(vals, 0.25)) if vals else None,
            "p75": round(_percentile(vals, 0.75)) if vals else None,
            "n": len(vals),
        }
    return {"generated": now_iso(), "jobs_total": total_n, "jobs_with_salary_parsed": parsed_n,
            "by_title_keyword": by_title_keyword}


def main() -> int:
    intel = build_intel()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(intel, indent=2))
    parts = [f"{kw}=${d['median']:,}" if d["median"] else f"{kw}=n/a"
             for kw, d in intel["by_title_keyword"].items()]
    print(f"salary_intel: parsed {intel['jobs_with_salary_parsed']}/{intel['jobs_total']} "
          f"jobs, medians: {', '.join(parts)} -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
