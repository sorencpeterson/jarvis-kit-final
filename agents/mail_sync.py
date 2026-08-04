#!/usr/bin/env python3
"""B81: incremental Gmail sync via historyId cursor (10x fewer API calls than a full
search every poll). B109: falls back to a bounded full search when the cursor is
stale (Gmail expires history ~7-14d back) or missing.

Cursor lives in store/mail_cursor.json: {"history_id": "...", "last_sync": "iso",
"last_run_new": N}. sync() is the entrypoint for a simple caller that fully
consumes every returned id — it returns message ids only (cheap); callers fetch
metadata/bodies themselves via gmail_api.get_messages_metadata / get_message so
a classify-only pass never pays for bodies it won't read (B102).

R2-46: a caller that might only process a SUBSET of the returned ids (a
[:limit] slice, a batch loop that can fail partway through) must NOT use
sync() — it acks the cursor for the whole delta immediately. Use peek() (no
side effects) + advance_cursor(result) instead, calling advance_cursor() only
once everything peek() returned has actually been fetched and classified (see
mail_brain.run()). Advancing early used to permanently ack mail the caller
never got to.

READ-ONLY against Gmail. No labels touched here (that's mail_brain.py's job after
classification). Safe to run repeatedly; idempotent (re-running with the same cursor
just returns the same delta again, doesn't double-count anything since this module
writes nothing but the cursor file).

Run:  .venv/bin/python agents/mail_sync.py           # sync + print delta
      .venv/bin/python agents/mail_sync.py --dry-run  # don't advance the cursor
"""
from __future__ import annotations

import argparse
import os
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", Path(os.environ.get("GMAIL_LIB") or (ROOT / "gmail"))):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402
import gmail_api  # noqa: E402
from runlog import track  # noqa: E402

CURSOR = ROOT / "store" / "mail_cursor.json"
# Full-search fallback window when the cursor is stale/missing (B109). Bounded so a
# cold start or a reset never tries to pull the whole 126k-message mailbox at once.
FALLBACK_QUERY = "newer_than:3d"
FALLBACK_MAX = 150


def _load_cursor() -> dict:
    try:
        return json.loads(CURSOR.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cursor(history_id: str, new_count: int, mode: str) -> None:
    CURSOR.parent.mkdir(parents=True, exist_ok=True)
    CURSOR.write_text(json.dumps({
        "history_id": history_id,
        "last_sync": now_iso(),
        "last_run_new": new_count,
        "last_mode": mode,  # "history" | "fallback_search" | "seed"
    }, indent=2))


def peek() -> dict:
    """Returns {"message_ids": [...], "mode": "...", "history_id": "..."} WITHOUT
    advancing the cursor.

    R2-46: the cursor used to advance as soon as the delta was computed, before
    the caller had actually fetched+classified anything. A caller that only
    processes a SUBSET of message_ids (a [:limit] slice, a crash mid-batch)
    still permanently acked the full delta, so the untouched remainder was
    silently gone -- next run's history diff starts AFTER that point and never
    returns those ids again.

    Callers that might not fully consume message_ids MUST call peek() +
    advance_cursor() themselves, and only invoke advance_cursor() once every id
    returned here has actually been fetched and classified (see
    mail_brain.run(), the R2-46 fix). Callers that always fully consume the
    result can keep using sync() below."""
    cur = _load_cursor()
    start_id = cur.get("history_id")

    if not start_id:
        # First-ever run: seed the cursor from the mailbox's current historyId and
        # do one bounded fallback search so the very first run still yields recent
        # mail instead of an empty delta (nothing to diff against yet).
        seed = gmail_api.current_history_id()
        ids = [m["id"] for m in gmail_api.search(FALLBACK_QUERY, FALLBACK_MAX)]
        return {"message_ids": ids, "mode": "seed", "history_id": seed}

    try:
        res = gmail_api.list_history(start_id)
        return {"message_ids": res["message_ids"], "mode": "history", "history_id": res["history_id"]}
    except gmail_api.HistoryStale:
        # B109: cursor too old for the History API. Fall back to a bounded search,
        # then re-seed the cursor from the CURRENT historyId (not the stale one) so
        # the next run goes back to cheap incremental sync.
        seed = gmail_api.current_history_id()
        ids = [m["id"] for m in gmail_api.search(FALLBACK_QUERY, FALLBACK_MAX)]
        return {"message_ids": ids, "mode": "fallback_search", "history_id": seed}


def advance_cursor(result: dict) -> None:
    """Persist the cursor from a peek()/sync() result. R2-46 contract: call this
    ONLY after every id in result['message_ids'] has actually been fetched AND
    classified/processed -- never after a partial/[:limit] slice, or the
    untouched remainder is acked and never seen again."""
    _save_cursor(result["history_id"], len(result["message_ids"]), result["mode"])


def sync(dry_run: bool = False) -> dict:
    """Convenience wrapper for simple callers that fully consume every id
    returned: peek() then immediately advance_cursor() (unless dry_run). Any
    caller that might process only a SUBSET of message_ids must call peek() +
    advance_cursor() itself, after processing actually completes -- see
    mail_brain.run(), the R2-46 fix, instead of this wrapper."""
    result = peek()
    if not dry_run:
        advance_cursor(result)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="don't advance the cursor")
    args = ap.parse_args()

    with track("mail_sync"):
        before = _load_cursor().get("history_id")
        result = sync(dry_run=args.dry_run)
        after = result["history_id"]

    print(f"mail_sync: mode={result['mode']} new={len(result['message_ids'])} "
          f"cursor {before or '(none)'} -> {after}{' [dry-run, not saved]' if args.dry_run else ''}")
    if result["message_ids"]:
        planner.feed_add("agent", f"Mail sync: {len(result['message_ids'])} new message(s)",
                          f"mode={result['mode']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
