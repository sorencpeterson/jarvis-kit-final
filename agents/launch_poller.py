#!/usr/bin/env python3
"""Helper for the dashboard launch-button queue (store/requests.jsonl).

Buttons in the dashboard queue browser actions here; a scheduled poller (running
in the Claude app, where Chrome is reachable) drains them. Status is last-write-
wins by id, like the other stores.

  python agents/launch_poller.py list            -> JSON of queued requests (<60 min old)
  python agents/launch_poller.py running <id>    -> mark running
  python agents/launch_poller.py done <id>       -> mark done
  python agents/launch_poller.py failed <id>     -> mark failed
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from store_lib import LOCAL_TZ, now_iso  # noqa: E402

REQ = ROOT / "store" / "requests.jsonl"


def _load() -> list[dict]:
    if not REQ.exists():
        return []
    by_id, order = {}, []
    for line in REQ.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("id"):
            if r["id"] not in by_id:
                order.append(r["id"])
            by_id[r["id"]] = r
    return [by_id[i] for i in order]


def _set(rid: str, status: str):
    with REQ.open("a") as f:
        f.write(json.dumps({"id": rid, "status": status, "ts": now_iso()}) + "\n")


def _pending() -> list[dict]:
    cutoff = datetime.now(LOCAL_TZ) - timedelta(minutes=60)
    out = []
    for r in _load():
        if r.get("status") != "queued":
            continue
        try:
            fresh = datetime.fromisoformat(r.get("ts", "")) >= cutoff
        except (ValueError, TypeError):
            fresh = True
        if fresh:
            out.append(r)
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        print(json.dumps(_pending()))
    elif cmd in ("running", "done", "failed") and len(sys.argv) > 2:
        _set(sys.argv[2], cmd)
        print("ok")
    else:
        print("usage: launch_poller.py list|running|done|failed [id]")
