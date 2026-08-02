#!/usr/bin/env python3
"""C177 won-conversation mining -> store/won_patterns.jsonl.

template_learn.py already mines draft-vs-sent EDITS (what [OWNER] changed on a single
message). This is a different, complementary signal: for contacts convo_state.py
classifies as "won," what did the WHOLE conversation arc look like end to end --
which objections came up, what turned it, how many touches it took, what the
winning proposal's tier/price was. That's the "what actually closed" signal C177
asks for, one level up from a single message edit.

Contract for template_learn.py (documented here per the mission brief, since this
file produces the input template_learn or a future version of it would consume,
without editing template_learn.py itself -- that file is not in this build's
explicit priority list and its existing MIN_PAIRS=3 draft/sent-edit mining is a
different, narrower signal this module doesn't replace):
  store/won_patterns.jsonl, one JSON object per won contact, append-only, shape:
    {"ts": iso, "contact_id": str, "name": str, "why_won": "classify()'s reasons
     string from convo_state.py", "touches": int (replies + proposals combined),
     "objections_raised": [str, ...] (from store/objections.jsonl for this contact,
     via convo_context.objection_sequence_count's same contact_id/name matching),
     "winning_tier": str|None, "winning_price": int|None, "days_to_close":
     float|None (first signal to last/won signal, from convo_state's timestamps),
     "context_tail": str (last few messages before close, via
     convo_context.fetch_context, human-readable transcript)}
  A future template_learn-style consumer can fold "days_to_close" and
  "objections_raised" patterns across many won rows into a monthly "what actually
  works" digest -- that synthesis step is NOT built here (no real won contacts
  exist yet in this environment to synthesize FROM, confirmed by a real run: see
  the honest empty-state note below), only the per-contact mining that would feed it.

Idempotent: store/won_mining_state.json remembers which contact_ids have already
been mined, so a contact mines exactly ONCE, ever, matching template_learn.py's own
run-once-per-signal spirit ("don't grind the same signal repeatedly"). A contact
that stays 'won' across many runs is not re-mined or re-notified every time. (Later
lifecycle events on an already-won contact -- e.g. a review or referral ask firing
per C212/213 -- are a different signal, handled by proposal_timers.py's dormancy
extension, not by re-running this mine.)

Rails: read-only against every store except won_patterns.jsonl (append) and
won_mining_state.json (state). No LLM calls (this is a mechanical rollup of
existing structured data, not a generation task) except OPTIONALLY summarizing
into human-readable notes, which run() does via planner._cli ONLY when there's
real signal to summarize (never fabricates prose from nothing). No GHL writes,
no drafting, no sending.

Usage:
  won_mining.py             # mine any newly-won contacts, write won_patterns.jsonl
  won_mining.py --dry-run   # mine and print, write nothing
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import convo_state  # noqa: E402
import convo_context  # noqa: E402

WON_PATTERNS = ROOT / "store" / "won_patterns.jsonl"
STATE = ROOT / "store" / "won_mining_state.json"
REPLIES = ROOT / "store" / "replies.jsonl"
PROPOSALS = ROOT / "store" / "proposals.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
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


def _last_write_wins(rows: list[dict], id_field: str = "id") -> list[dict]:
    by_id, order = {}, []
    for r in rows:
        rid = r.get(id_field)
        if rid is None:
            continue
        if rid not in by_id:
            order.append(rid)
        by_id[rid] = r
    return [by_id[i] for i in order]


def _mined_ids() -> set[str]:
    try:
        return set(json.loads(STATE.read_text()).get("mined_contact_ids", []))
    except (OSError, json.JSONDecodeError):
        return set()


def _save_mined_ids(ids: set[str]):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"mined_contact_ids": sorted(ids), "updated": now_iso()}, indent=2))


def mine_contact(contact_id: str, state_rec: dict) -> dict:
    """Pure-ish (one optional GHL fetch for context_tail): given one contact_id and
    its convo_state.py classify() result, build the won_patterns.jsonl record.
    Doesn't touch any file itself -- caller (run()) does the writing."""
    replies = [r for r in _last_write_wins(_load_jsonl(REPLIES))
              if r.get("contact_id") == contact_id]
    proposals = [p for p in _last_write_wins(_load_jsonl(PROPOSALS))
                if p.get("contact_id") == contact_id]
    objections_rows = [o for o in _load_jsonl(ROOT / "store" / "objections.jsonl")
                       if o.get("contact_id") == contact_id]
    name = state_rec.get("name", "")
    winning_proposal = next((p for p in proposals if p.get("status") in ("sent", "won")), None)

    touches = len(replies) + len(proposals)
    convo_id = next((r.get("convo") for r in replies if r.get("convo")), "")
    context_tail = ""
    if convo_id:
        msgs = convo_context.fetch_context(convo_id, turns=5)
        context_tail = convo_context.format_context(msgs)

    return {
        "ts": now_iso(), "contact_id": contact_id, "name": name,
        "why_won": state_rec.get("why", ""),
        "touches": touches,
        "objections_raised": [o.get("objection", "")[:200] for o in objections_rows],
        "winning_tier": (winning_proposal or {}).get("tier"),
        "winning_price": (winning_proposal or {}).get("price"),
        "days_to_close": state_rec.get("last_signal_days"),
        "context_tail": context_tail,
    }


def run(dry: bool = False) -> dict:
    states = convo_state.load_states()
    if not states:
        # convo_state.py hasn't run yet this session (or produced nothing) -- run it
        # so won_mining always has fresh data to work from rather than silently
        # mining a stale/empty snapshot.
        convo_state.run(dry=False)
        states = convo_state.load_states()

    won = {cid: rec for cid, rec in states.items() if rec.get("state") == "won"}
    already_mined = _mined_ids()
    to_mine = {cid: rec for cid, rec in won.items() if cid not in already_mined}

    if not won:
        print("won_mining: 0 contacts currently classified 'won' by convo_state.py "
              "-- honest empty state, nothing to mine yet in this environment.")
        return {"won_count": 0, "newly_mined": 0, "note": "no won contacts yet"}

    mined_records = []
    for cid, rec in to_mine.items():
        mined_records.append(mine_contact(cid, rec))

    if mined_records and not dry:
        WON_PATTERNS.parent.mkdir(parents=True, exist_ok=True)
        with WON_PATTERNS.open("a") as f:
            for rec in mined_records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        already_mined.update(r["contact_id"] for r in mined_records)
        _save_mined_ids(already_mined)
        try:
            import planner
            planner.feed_add("agent", f"Won-pattern mining: {len(mined_records)} new won "
                                      f"contact(s) analyzed -> store/won_patterns.jsonl")
        except Exception:  # noqa: BLE001 — feed logging is best-effort, never blocks the mine
            pass

    print(f"won_mining: {len(won)} contact(s) currently 'won', {len(mined_records)} newly "
          f"mined this run" + ("" if dry else f" -> {WON_PATTERNS}"))
    return {"won_count": len(won), "newly_mined": len(mined_records),
            "records": mined_records}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(dry=args.dry_run)
