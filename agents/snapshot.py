#!/usr/bin/env python3
"""Daily state snapshot — dumps everything the dashboard shows into a dated file,
then diffs against yesterday so "what actually changed" is a 5-line read instead
of a memory exercise.

Why: metrics_rollup.py already keeps a thin numeric trend line, but the full
state (state/money/jobs/cold/usage) is never preserved, so there's no way to
answer "what did the cold panel look like before I changed X" without asking
[OWNER] to remember. This hits the same live server APIs metrics_rollup.py hits
(single source of truth, no re-deriving dashboard logic here) and keeps one
full JSON per day.

E336 (snapshot diffing: weekly what-changed report across all stores):
diff_with_yesterday() below generalizes into diff_with_n_days_ago(n), and
build_weekly_report() calls it with n=7 to produce store/weekly_report.md.
HONEST about data depth: this repo only started keeping daily snapshots
recently, so a true 7-day-back comparison may have NO prior snapshot to diff
against yet — when that's the case, the report says so explicitly (falls back
to the OLDEST snapshot actually on file and states how many days back that
really is) rather than silently comparing against nothing or pretending a
week of history exists that doesn't.

Only writes are store/snapshots/YYYY-MM-DD.json (today's, always overwritten if
re-run same day) and store/weekly_report.md (full overwrite each run, E336).
Run standalone: .venv/bin/python agents/snapshot.py
       weekly:   .venv/bin/python agents/snapshot.py --weekly
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT,):
    sys.path.insert(0, str(p))
from store_lib import secret  # noqa: E402
sys.path.insert(0, str(ROOT / "agents"))
from runlog import track  # noqa: E402  (E353: runlog adoption)

BASE_URL = "http://127.0.0.1:8765"
ENDPOINTS = ("/api/state", "/api/money", "/api/jobs", "/api/cold", "/api/usage")
SNAP_DIR = ROOT / "store" / "snapshots"
WEEKLY_REPORT = ROOT / "store" / "weekly_report.md"
TIMEOUT = 15


def _get(path: str, token: str) -> dict | None:
    req = urllib.request.Request(BASE_URL + path, headers={"X-Brain-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError, OSError):
        return None


def build_snapshot() -> dict:
    token = secret("brain_token")
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    snap = {"date": today, "ts": datetime.now().astimezone().isoformat(timespec="seconds")}
    for ep in ENDPOINTS:
        key = ep.rsplit("/", 1)[-1]  # /api/state -> state
        snap[key] = _get(ep, token)
    return snap


def _all_endpoints_null(snap: dict) -> bool:
    """True if every endpoint came back None (server unreachable / bad token).
    R2-51: a snapshot this empty must never overwrite a GOOD prior snapshot for
    the same day -- that's an empty shell, not real data, and today's file is
    "always overwritten if re-run same day" by design for LEGITIMATE reruns."""
    return all(snap.get(ep.rsplit("/", 1)[-1]) is None for ep in ENDPOINTS)


def _any_endpoint_null(snap: dict) -> bool:
    """True if AT LEAST ONE endpoint came back None. R3#8 (2026-07-14): the
    original overwrite guard (_all_endpoints_null) only blocked a same-day
    rerun when EVERY endpoint failed -- a single transient failure (4 of 5
    endpoints fine, one timeout/one route erroring) still passed that check and
    silently replaced a COMPLETE prior snapshot with a PARTIAL one, permanently
    losing that one field's data for the day (same-day reruns overwrite by
    design). Any null is enough to withhold today's write and keep the last
    complete snapshot rather than degrade it."""
    return any(snap.get(ep.rsplit("/", 1)[-1]) is None for ep in ENDPOINTS)


def _flatten_numbers(d: dict, prefix: str = "") -> dict[str, float]:
    """Pull every top-level-ish numeric field out of a snapshot's endpoint dicts
    for diffing. Deliberately shallow (one level of nesting) so the diff stays
    to human-scannable top-level numbers, not a recursive tree walk."""
    out = {}
    for k, v in (d or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out[key] = v
        elif isinstance(v, dict):
            for k2, v2 in v.items():
                if isinstance(v2, (int, float)) and not isinstance(v2, bool):
                    out[f"{key}.{k2}"] = v2
    return out


def _oldest_snapshot() -> tuple[str, dict] | None:
    """The oldest snapshot actually on disk (date, data), or None if there
    are zero snapshots yet. Used as an honest fallback when a requested
    n-days-back comparison target doesn't exist."""
    files = sorted(SNAP_DIR.glob("*.json"))
    if not files:
        return None
    oldest = files[0]
    try:
        return oldest.stem, json.loads(oldest.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _diff_numbers(today_snap: dict, other_snap: dict, limit: int = 5) -> list[str]:
    today_nums, other_nums = {}, {}
    for ep in ENDPOINTS:
        key = ep.rsplit("/", 1)[-1]
        today_nums.update(_flatten_numbers(today_snap.get(key), f"{key}."))
        other_nums.update(_flatten_numbers(other_snap.get(key), f"{key}."))

    changed = []
    for k in sorted(set(today_nums) | set(other_nums)):
        t, o = today_nums.get(k), other_nums.get(k)
        if t != o:
            delta = ""
            if isinstance(t, (int, float)) and isinstance(o, (int, float)):
                d = t - o
                delta = f" ({'+' if d >= 0 else ''}{d:g})"
            changed.append(f"  {k}: {o} -> {t}{delta}")
    return changed[:limit]


def diff_with_n_days_ago(today_snap: dict, n: int, *, limit: int = 5) -> dict:
    """Compare today_snap against the snapshot from exactly n days ago, if one
    exists. Returns {"found": bool, "actual_days_back": int|None, "lines": [...]}
    — "found" False means no snapshot n days back exists (not necessarily zero
    history: distinguishes 'nothing to diff at all' from 'diffed against the
    closest thing we have' via actual_days_back on the fallback path)."""
    target_date = (datetime.now().astimezone() - timedelta(days=n)).strftime("%Y-%m-%d")
    tpath = SNAP_DIR / f"{target_date}.json"
    if tpath.exists():
        try:
            other_snap = json.loads(tpath.read_text())
            return {"found": True, "actual_days_back": n,
                    "lines": _diff_numbers(today_snap, other_snap, limit)}
        except (OSError, json.JSONDecodeError):
            pass
    return {"found": False, "actual_days_back": None, "lines": []}


def diff_with_yesterday(today_snap: dict) -> list[str]:
    """Kept for backward compatibility / the daily CLI path: same as
    diff_with_n_days_ago(today_snap, 1) but returns just the lines list."""
    return diff_with_n_days_ago(today_snap, 1)["lines"]


def build_weekly_report(today_snap: dict) -> str:
    """E336: markdown report comparing today against 7 days ago, honestly
    falling back to the OLDEST snapshot on file (and saying so) if a true
    7-day-back snapshot doesn't exist yet."""
    result = diff_with_n_days_ago(today_snap, 7, limit=20)
    lines = [f"# Weekly what-changed report — {today_snap.get('date', '')}", ""]

    if result["found"]:
        lines.append(f"Comparing against {result['actual_days_back']} day(s) ago (a real week-old snapshot).")
        lines.append("")
        if result["lines"]:
            lines.extend(result["lines"])
        else:
            lines.append("No numeric changes across the tracked endpoints in the last 7 days.")
    else:
        fallback = _oldest_snapshot()
        if fallback is None:
            lines.append("No prior snapshots exist yet — this is the first one. "
                         "Nothing to compare against; check back once a few days of history build up.")
        else:
            oldest_date, oldest_snap = fallback
            try:
                days_back = (datetime.strptime(today_snap["date"], "%Y-%m-%d")
                            - datetime.strptime(oldest_date, "%Y-%m-%d")).days
            except (ValueError, KeyError):
                days_back = None
            lines.append(f"No snapshot from exactly 7 days ago yet (this repo hasn't been "
                        f"tracking snapshots that long). Falling back to the OLDEST one on "
                        f"file: {oldest_date}"
                        + (f" ({days_back} day(s) back)." if days_back is not None else "."))
            lines.append("")
            fallback_lines = _diff_numbers(today_snap, oldest_snap, limit=20)
            if fallback_lines:
                lines.extend(fallback_lines)
            else:
                lines.append(f"No numeric changes between today and {oldest_date}.")

    lines.append("")
    lines.append(f"_generated {datetime.now().astimezone().isoformat(timespec='seconds')}_")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--weekly", action="store_true",
                     help="also build store/weekly_report.md (E336)")
    args = ap.parse_args()

    with track("snapshot"):  # E353: runlog adoption
        snap = build_snapshot()
        SNAP_DIR.mkdir(parents=True, exist_ok=True)
        out_path = SNAP_DIR / f"{snap['date']}.json"
        # R2-51/R3#8: ANY endpoint null (server down / bad token / one route
        # erroring) must never overwrite a GOOD snapshot already on disk for
        # today with a null-shell OR PARTIAL one -- refuse the write rather than
        # silently reporting "wrote" (DR corruption; R3#8 tightened this from
        # "only when ALL endpoints are null", which let a 4-good-1-null write
        # silently replace a complete prior snapshot and permanently lose that
        # one field's data for the day). Write is also atomic (tmp + os.replace)
        # so a crash mid-write can't leave today's snapshot half-written.
        if _any_endpoint_null(snap) and out_path.exists():
            wrote = False
        else:
            tmp = out_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(snap, indent=2))
            os.replace(tmp, out_path)
            wrote = True

    if not wrote:
        print(f"snapshot: ABORTED write to {out_path}, at least one endpoint came back "
              f"null (server down, one route erroring, or a bad token) and a real "
              f"snapshot for today already exists; refusing to replace complete data "
              f"with a partial shell. Nothing changed.")
        return 1

    lines = diff_with_yesterday(snap)
    print(f"snapshot: wrote {out_path}")
    if lines:
        print(f"changed vs yesterday ({len(lines)} shown):")
        for ln in lines:
            print(ln)
    else:
        print("no prior-day snapshot to diff, or nothing changed")

    if args.weekly:
        report = build_weekly_report(snap)
        WEEKLY_REPORT.write_text(report)
        print(f"\nweekly report -> {WEEKLY_REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
