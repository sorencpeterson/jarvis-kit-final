#!/usr/bin/env python3
"""J187: dead-letter queue for failed agent work. When a call (typically an LLM call,
but any fallible fn) fails, the payload is parked in store/dlq.jsonl instead of silently
dropped, so a later --drain pass can retry it.

ADOPTION (opt-in, not retrofitted into existing agents this sprint):

    from dlq import retry_or_park

    def do_the_work(payload):
        result = some_flaky_call(payload)
        if result is None:
            raise RuntimeError("call failed")
        return result

    ok, result = retry_or_park(do_the_work, {"contact_id": "abc"}, "reply_draft")
    if not ok:
        ...  # parked; a later `dlq.py --drain` will retry it

Park record shape (store/dlq.jsonl, append-only, one line per park/attempt):
  {"id": "dlq_<ts>_<hash>", "queue": "reply_draft", "payload": {...}, "parked_ts": "...",
   "attempts": 1, "last_err": "RuntimeError: call failed", "status": "parked"}

--drain mode: re-loads the last-write-wins current state per id (status == "parked"),
calls fn(payload) again for each, and appends either a "resolved" record (removes it from
the parked set) or an updated "parked" record with attempts+1 and the newest error.
Drain needs the SAME fn passed in-process (this module has no registry of agent
functions), so draining happens by importing dlq from the owning agent and calling
drain(fn, queue_name) directly, or via `dlq.py --drain <queue> --dry` for a status-only
report when run as a script (no fn available from the CLI, so CLI drain is report-only).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
DLQ = ROOT / "store" / "dlq.jsonl"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_id(seed: str) -> str:
    day = datetime.now().astimezone().strftime("%Y%m%d")
    h = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
    return f"dlq_{day}_{h}"


def _append(rec: dict) -> None:
    DLQ.parent.mkdir(parents=True, exist_ok=True)
    with DLQ.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_all() -> list[dict]:
    """Last-write-wins by id, same convention as store_lib.load_todos."""
    if not DLQ.exists():
        return []
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for line in DLQ.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = rec.get("id")
        if not rid:
            continue
        if rid not in by_id:
            order.append(rid)
        by_id[rid] = rec
    return [by_id[i] for i in order]


def parked(queue: str | None = None) -> list[dict]:
    out = [r for r in load_all() if r.get("status") == "parked"]
    if queue:
        out = [r for r in out if r.get("queue") == queue]
    return out


def retry_or_park(fn: Callable, payload: dict, queue_name: str, retries: int = 1):
    """Call fn(payload). On success return (True, result). On failure (exception OR fn
    returning a falsy/None result, which many of this codebase's _cli_json-style helpers
    use to signal failure), retry up to `retries` more times, then park to dlq.jsonl and
    return (False, None). Never raises: callers get a clean (ok, result) tuple back."""
    last_err = ""
    for attempt in range(retries + 1):
        try:
            result = fn(payload)
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"[:500]
            continue
        if result:
            return True, result
        last_err = "fn returned falsy/None"
    pid = _new_id(queue_name + json.dumps(payload, sort_keys=True, default=str))
    _append({
        "id": pid, "queue": queue_name, "payload": payload,
        "parked_ts": _now_iso(), "attempts": retries + 1,
        "last_err": last_err, "status": "parked",
    })
    return False, None


def drain(fn: Callable, queue_name: str, dry: bool = False) -> dict:
    """Retry every parked item in `queue_name` with fn(payload). Returns a summary dict.
    dry=True reports what WOULD be attempted without calling fn."""
    items = parked(queue_name)
    summary = {"queue": queue_name, "found": len(items), "resolved": 0, "still_parked": 0}
    if dry:
        return summary
    for rec in items:
        try:
            result = fn(rec["payload"])
        except Exception as e:  # noqa: BLE001
            result = None
            err = f"{type(e).__name__}: {e}"[:500]
        else:
            err = "fn returned falsy/None" if not result else ""
        if result:
            _append({**rec, "status": "resolved", "resolved_ts": _now_iso()})
            summary["resolved"] += 1
        else:
            _append({**rec, "attempts": rec.get("attempts", 1) + 1,
                     "last_err": err, "status": "parked"})
            summary["still_parked"] += 1
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--drain", metavar="QUEUE", help="report parked items in QUEUE (status only; "
                     "real draining needs `import dlq; dlq.drain(fn, queue)` from the owning agent)")
    ap.add_argument("--dry", action="store_true", help="with --drain: report only, don't attempt")
    a = ap.parse_args()

    if a.drain:
        items = parked(a.drain)
        print(f"dlq[{a.drain}]: {len(items)} parked item(s)")
        for r in items[:20]:
            print(f"  {r['id']}  attempts={r.get('attempts')}  last_err={r.get('last_err','')[:80]}")
        if not a.dry and items:
            print("  (CLI has no fn to retry with; call dlq.drain(fn, queue) from the owning agent)")
        return 0

    all_recs = load_all()
    by_status: dict[str, int] = {}
    for r in all_recs:
        by_status[r.get("status", "?")] = by_status.get(r.get("status", "?"), 0) + 1
    print(f"dlq: {len(all_recs)} total record(s) -> {by_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
