#!/usr/bin/env python3
"""J181: agent run ledger. Every agent that opts in logs start/end/duration/result to
store/runs.jsonl (append-only), so a watchdog can spot a slow-degrading agent (rising
duration, rising error rate) before it dies outright.

THIS IS OPT-IN. Existing agents are NOT retrofitted (not this sprint's files to touch) —
the one-line adoption pattern for a NEW or EXISTING agent is:

    from runlog import track
    with track("agent_name"):
        ...  # the agent's normal work

track() is a context manager: it writes exactly one line to runs.jsonl when the `with`
block exits, whether it exited cleanly or via exception. On exception, the record has
ok=False and err=<str(exception)>, and the exception is RE-RAISED (track never swallows
errors) so the agent's own exit code / traceback behavior is unchanged.

Record shape (one JSON object per line):
  {"agent": "cold_feeder", "start": "2026-07-03T08:00:00-07:00",
   "end": "2026-07-03T08:00:03-07:00", "dur_s": 3.21, "ok": true, "err": null}
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "store" / "runs.jsonl"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _append(rec: dict) -> None:
    RUNS.parent.mkdir(parents=True, exist_ok=True)
    with RUNS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


@contextmanager
def track(agent: str):
    """Context manager: times the block, appends one record to runs.jsonl, re-raises
    any exception from the block after logging it (never swallows errors)."""
    start_iso = _now_iso()
    t0 = time.monotonic()
    err = None
    ok = True
    try:
        yield
    except SystemExit as e:
        # `raise SystemExit(main())` with main() returning 0/None is a CLEAN exit — the
        # standard tail of every agent here. Logging it as a failure poisoned the error
        # stats ("SystemExit: 0" showed as thread_memory/meeting_prep fails, 2026-07-11).
        if e.code not in (0, None):
            ok = False
            err = f"SystemExit: {e.code}"[:500]
        raise
    except BaseException as e:  # noqa: BLE001 - intentionally broad, we log then re-raise
        ok = False
        err = f"{type(e).__name__}: {e}"[:500]
        raise
    finally:
        dur_s = round(time.monotonic() - t0, 3)
        _append({
            "agent": agent,
            "start": start_iso,
            "end": _now_iso(),
            "dur_s": dur_s,
            "ok": ok,
            "err": err,
        })


def recent(n: int = 50) -> list[dict]:
    """Read the last n run records (for a future watchdog / dashboard panel)."""
    if not RUNS.exists():
        return []
    out = []
    for line in RUNS.read_text().splitlines()[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


if __name__ == "__main__":
    # Smoke test: log one fake run, print it back.
    with track("runlog_selftest"):
        time.sleep(0.05)
    print(f"wrote 1 record to {RUNS}")
    print(recent(1))
