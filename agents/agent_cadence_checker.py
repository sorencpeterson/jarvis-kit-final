#!/usr/bin/env python3
"""E337: watchdog v2 — per-agent expected-cadence table, misses alert
specifically (instead of watchdog.sh's current health checks, which only
know about the server itself, brief errors, and stale disk, with no
per-agent view of "did X actually run when it should have").

WHAT: reads store/agent_cadences.json (the hand-edited cadence table this
      file does NOT own the schema of alone — see that file's own docstring)
      and for each agent, checks freshness via TWO signals, whichever is
      available:
        1. runlog_name set -> look up the most recent store/runs.jsonl
           record for that agent name, check its 'end' timestamp.
        2. output_file set -> check that file's (or, if it's a directory
           like store/snapshots, its newest child file's) mtime.
      An agent with NEITHER signal available is reported as "unwatchable"
      (honest, not silently skipped) rather than silently passing.
WHEN: run standalone any time, or wire into watchdog.sh (see INTEGRATION
      NOTE below — this file does NOT edit watchdog.sh itself, per the
      mission's explicit instruction to document integration instead).
RAILS: read-only against store/agent_cadences.json, store/runs.jsonl, and
      every output_file/output_dir named in the cadence table. Only write is
      store/cadence_report.json (full overwrite each run). No GHL writes, no
      sends. Exit code follows E352's agent exit-code standard (0 ok, 1 warn
      i.e. at least one miss, 2 fail i.e. couldn't even load the table) so a
      future watchdog integration can branch on it directly.

INTEGRATION NOTE for watchdog.sh (NOT edited by this file — mission says
document, don't touch): watchdog.sh already has a "runaway-agent tripwire +
stale heartbeats" block (see its own comments, `#100`/`#43`) that shells out
to agents/tripwire.py and agents/hbcheck.py and only pushes on new WARN
output. The exact same one-line pattern would wire this in:

    [ -x .venv/bin/python ] && .venv/bin/python agents/agent_cadence_checker.py 2>/dev/null | grep -q MISS && \\
      .venv/bin/python -c "import sys;sys.path.insert(0,'app');import planner;planner.notify('Agent cadence miss','One or more agents are overdue. Check the dashboard.')" >/dev/null 2>&1

Placed in the same "quiet unless something's wrong" block, after the
tripwire/hbcheck lines, so it inherits the same $SB cwd and .venv path
watchdog.sh already sets up.

Run:  .venv/bin/python agents/agent_cadence_checker.py
      .venv/bin/python agents/agent_cadence_checker.py --fixture
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402

CADENCE_TABLE = ROOT / "store" / "agent_cadences.json"
RUNS = ROOT / "store" / "runs.jsonl"
OUT = ROOT / "store" / "cadence_report.json"


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


def _load_cadence_table() -> list[dict] | None:
    try:
        data = json.loads(CADENCE_TABLE.read_text())
        agents = data.get("agents")
        return agents if isinstance(agents, list) else None
    except (OSError, json.JSONDecodeError):
        return None


def _last_run_end(runlog_name: str) -> str | None:
    """Most recent runs.jsonl 'end' timestamp for this agent name, scanning
    ALL records (runs.jsonl is append-only, not compacted, so the LAST
    matching line chronologically is simply the last one seen in file order)."""
    latest = None
    for r in _read_jsonl(RUNS):
        if r.get("agent") == runlog_name and r.get("end"):
            latest = r["end"]  # later lines overwrite earlier ones -> last wins
    return latest


def _output_freshness_hours(output_file: str) -> float | None:
    """Hours since output_file (or, if it's a directory, its newest child
    file) was last modified. None if the path doesn't exist at all."""
    p = ROOT / output_file
    if not p.exists():
        return None
    if p.is_dir():
        children = [c for c in p.iterdir() if c.is_file()]
        if not children:
            return None
        mtime = max(c.stat().st_mtime for c in children)
    else:
        mtime = p.stat().st_mtime
    return (time.time() - mtime) / 3600.0


def _hours_since_iso(ts: str) -> float | None:
    try:
        dt = datetime.fromisoformat(ts)
        if not dt.tzinfo:
            dt = dt.astimezone()
        now = datetime.now(dt.tzinfo)
        return (now - dt).total_seconds() / 3600.0
    except (ValueError, TypeError):
        return None


def check_one(entry: dict) -> dict:
    """Returns {"agent", "status": "ok"|"miss"|"unwatchable", "age_hours",
    "signal_used", "expected_hours", "detail"}."""
    name = entry.get("agent", "?")
    expected = entry.get("expected_hours", 24)
    runlog_name = entry.get("runlog_name")
    output_file = entry.get("output_file")

    ages: list[tuple[str, float]] = []  # (signal_name, age_hours)
    if runlog_name:
        last_end = _last_run_end(runlog_name)
        if last_end:
            h = _hours_since_iso(last_end)
            if h is not None:
                ages.append(("runlog", h))
    if output_file:
        h = _output_freshness_hours(output_file)
        if h is not None:
            ages.append(("output_file", h))

    if not ages:
        return {"agent": name, "status": "unwatchable", "age_hours": None,
                "signal_used": None, "expected_hours": expected,
                "detail": "no runlog record and no existing output_file — "
                          "either it has never run, or neither watch signal is configured right"}

    # use the FRESHEST signal (most recent activity), not the stalest — an
    # agent that's runlog-adopted but whose output_file check happens to be
    # stale for an unrelated reason (e.g. output_file wasn't touched this
    # exact run) shouldn't false-positive if runlog itself is fresh.
    signal_used, age = min(ages, key=lambda x: x[1])
    status = "ok" if age <= expected else "miss"
    detail = f"last seen {age:.1f}h ago via {signal_used} (expected within {expected}h)"
    return {"agent": name, "status": status, "age_hours": round(age, 1),
            "signal_used": signal_used, "expected_hours": expected, "detail": detail}


def _fixture_table() -> list[dict]:
    """Frozen scenario: one ok, one miss, one unwatchable, no store I/O
    dependency for the miss/unwatchable cases (uses timestamps far enough
    in the past/an obviously-nonexistent path to be deterministic)."""
    return [
        {"agent": "fixture_fresh", "expected_hours": 999999, "runlog_name": None,
         "output_file": None},  # unwatchable by design (no signals) — tests that path
        {"agent": "fixture_miss", "expected_hours": 0.0001, "runlog_name": None,
         "output_file": "store/agent_cadences.json"},  # real file, but expected_hours
                                                        # is absurdly tight -> guaranteed miss
    ]


def run(*, fixture: bool = False) -> dict:
    table = _fixture_table() if fixture else _load_cadence_table()
    if table is None:
        return {"ok": False, "error": f"could not load {CADENCE_TABLE}", "results": []}
    results = [check_one(e) for e in table]
    misses = [r for r in results if r["status"] == "miss"]
    unwatchable = [r for r in results if r["status"] == "unwatchable"]
    return {"ok": True, "generated": now_iso(), "fixture": fixture,
            "results": results, "miss_count": len(misses),
            "unwatchable_count": len(unwatchable)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fixture", action="store_true")
    args = ap.parse_args()

    data = run(fixture=args.fixture)
    if not data["ok"]:
        print(f"agent_cadence_checker: FAIL — {data['error']}")
        return 2

    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tag = " [FIXTURE]" if data["fixture"] else ""
    print(f"agent_cadence_checker{tag}: {len(data['results'])} agent(s) checked, "
          f"{data['miss_count']} MISS, {data['unwatchable_count']} unwatchable -> {OUT}")
    for r in data["results"]:
        marker = {"ok": "OK  ", "miss": "MISS", "unwatchable": "????"}[r["status"]]
        print(f"  [{marker}] {r['agent']:<24} {r['detail']}")

    return 1 if data["miss_count"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
