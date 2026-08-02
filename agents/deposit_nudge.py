#!/usr/bin/env python3
"""B11: deposit nudge. A signed agreement with no deposit is not a deal, it is a
warm feeling. The pricing tree's hard rule #1 is "50% deposit books the slot,
build starts on deposit", and today nothing watches the gap between "they typed
their name on /agree" and "money hit the ledger". This is that watcher.

WHAT: for every proposal with status=accepted (the /agree acceptance flips it),
      checks store/ledger.jsonl for a matching money row: kind in PAYMENT_KINDS
      with amount > 0 whose note mentions the proposal id or the company. If none
      exists NUDGE_AFTER_H hours after accepted_at, it fires ONE push to [OWNER]
      ("SIGNED but no deposit: <company>, $<50% deposit>. Send the payment link
      again or call.") and stages one todo (once per proposal EVER, deduped by
      source_ref). CX9: the amount shown is the 50% deposit actually owed right
      now, not the full build price.
      The push repeats at most once per proposal per NUDGE_AFTER_H window
      (store/deposit_nudge_state.json remembers the last nudge per pid), so a
      stuck deposit nags every 2 days instead of every run or never.
WHEN: daily (morning chain). Sub-second, pure local reads, no LLM. Fresh install
      (no proposals or none accepted) prints and exits 0.
RAILS: read-only against proposals and the ledger. Writes: the state file, at most
      one todo per proposal ever, one feed line. The push goes to HIS phone
      (planner.notify), never to a client; nothing outward is sent, no GHL writes.

HONEST LIMIT: ledger matching is by note text (pid or normalized company inside
the note). A deposit logged with an unrelated note ("wire from LLC") will look
missing and nudge anyway; the fix is logging wins/payments with the company name,
which every current writer already does.

Tunables (change here, nowhere else):
  NUDGE_AFTER_H = 48     hours after acceptance before the first nudge, and the
                         minimum gap between repeat nudges per proposal
  PAYMENT_KINDS = ("won", "payment", "closed", "deposit")

Run:  .venv/bin/python agents/deposit_nudge.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import LOCAL_TZ, append_todo, load_todos, new_id, now_iso  # noqa: E402
import planner  # noqa: E402

PROPOSALS = ROOT / "store" / "proposals.jsonl"
LEDGER = ROOT / "store" / "ledger.jsonl"
TODOS = ROOT / "store" / "todos.jsonl"
STATE = ROOT / "store" / "deposit_nudge_state.json"

NUDGE_AFTER_H = 48
PAYMENT_KINDS = ("won", "payment", "closed", "deposit")


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


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _read_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _write_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    tmp.replace(STATE)


def _accepted() -> list[dict]:
    by_id: dict[str, dict] = {}
    for r in _read_jsonl(PROPOSALS):
        if r.get("id"):
            by_id[r["id"]] = r
    return [r for r in by_id.values() if r.get("status") == "accepted"]


def _company_terms(company: str) -> list[str]:
    """Word-boundary match term for a company name: the FULL name (whitespace-
    flexible, so "acme_co soft" matches "Acme Co  Soft"), compiled once as a
    \\b-anchored regex on the raw note.

    CX7: this used to ALSO match on just the company's first distinctive token
    ("north" from "North Star Roofing"), so a payment note for a DIFFERENT company
    sharing that first word ("North Star Plumbing") false-matched and marked
    Roofing's deposit paid. Full-name-only match is exact-company; the separate pid
    check in has_payment() still catches a note that only names the proposal id."""
    name = (company or "").strip().lower()
    if len(name) < 4:
        return []
    return [re.compile(r"\b" + r"\s+".join(re.escape(w) for w in name.split()) + r"\b")]


def has_payment(prop: dict, ledger_rows: list[dict]) -> bool:
    """True when a money row (PAYMENT_KINDS, amount > 0) mentions this proposal's
    id or company. Company match is word-boundary on the raw note (see _company_terms),
    so "Acme Co" matches "Acme Co Soft - WL Webdev" but a generic note like "cash from
    sale" never false-matches "Wholesale Depot"."""
    pid = (prop.get("id") or "").lower()
    terms = _company_terms(prop.get("company") or prop.get("name") or "")
    for r in ledger_rows:
        if r.get("kind") not in PAYMENT_KINDS:
            continue
        try:
            if float(r.get("amount") or 0) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        note = (r.get("note") or "").lower()
        if pid and pid in note:
            return True
        if any(t.search(note) for t in terms):
            return True
    return False


def _hours_since(ts: str, now: datetime) -> float | None:
    try:
        dt = datetime.fromisoformat(ts)
        if not dt.tzinfo:
            dt = dt.astimezone()
        return (now - dt).total_seconds() / 3600.0
    except (ValueError, TypeError):
        return None


def _stage_todo(prop: dict) -> bool:
    ref = f"depnudge_{prop.get('id', '')}"
    if ref in {t.get("source_ref") for t in load_todos(TODOS)}:
        return False
    company = prop.get("company") or prop.get("name") or prop.get("id", "")
    try:
        price = float(prop.get("price") or 0)
    except (TypeError, ValueError):
        price = 0.0
    deposit = price / 2  # CX9: the pricing tree's ask is a 50% deposit, not the
                          # full build price -- showing the full price as "unpaid"
                          # overstates what's actually owed right now by 2x
    rec = {"id": new_id(ref), "text": f"Collect the deposit: {company} signed, "
                                      f"${deposit:,.0f} deposit unpaid. Send the payment link or call.",
           "status": "inbox", "created": now_iso(), "source": "deposit_nudge",
           "source_ref": ref, "project": None, "priority": 1, "scheduled_time": None,
           "duration_min": None, "gcal_event_id": None, "notes": None}
    append_todo(rec, TODOS)
    return True


def run(*, dry_run: bool = False) -> int:
    accepted = _accepted()
    if not accepted:
        print("deposit nudge: no accepted proposals on record, nothing to watch")
        return 0

    ledger_rows = _read_jsonl(LEDGER)
    state = _read_state()
    now = datetime.now(LOCAL_TZ)
    nudged = 0
    for prop in accepted:
        pid = prop.get("id", "")
        company = prop.get("company") or prop.get("name") or pid
        if has_payment(prop, ledger_rows):
            continue
        age_h = _hours_since(prop.get("accepted_at") or prop.get("created") or "", now)
        if age_h is None or age_h < NUDGE_AFTER_H:
            continue
        last = _hours_since((state.get(pid) or {}).get("last_nudge", ""), now)
        if last is not None and last < NUDGE_AFTER_H:
            continue  # inside the current window, already nudged
        try:
            price = float(prop.get("price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        deposit = price / 2  # CX9: 50% deposit owed now, not the full build price
        if dry_run:
            print(f"[dry-run] would nudge: {company} signed {age_h / 24:.1f}d ago, "
                  f"${deposit:,.0f} deposit unpaid")
            continue
        try:
            sent = planner.notify("SIGNED but no deposit",
                                  f"{company}, ${deposit:,.0f} deposit unpaid. Send the payment link again or call.",
                                  tags="moneybag")
        except Exception:  # noqa: BLE001
            sent = False
        if not sent:
            # ntfy outage / no topic: do NOT stamp last_nudge, or this day's nudge is
            # silently eaten. Leaving state unstamped re-fires next run (self-heals).
            print(f"  push failed for {company}, not stamped (will retry next run)")
            continue
        _stage_todo(prop)
        state[pid] = {"last_nudge": now.isoformat(timespec="seconds"), "company": company}
        nudged += 1
        print(f"  nudged: {company} (signed {age_h / 24:.1f}d ago, ${deposit:,.0f} deposit unpaid)")

    if dry_run:
        print("deposit nudge: dry run complete, nothing written")
        return 0
    _write_state(state)
    if nudged:
        try:
            planner.feed_add("money", f"Deposit nudge: {nudged} signed-but-unpaid deal(s) flagged")
        except Exception:  # noqa: BLE001
            pass
    print(f"deposit nudge: {len(accepted)} accepted checked, {nudged} nudge(s) fired")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="nag on signed-but-unpaid deposits")
    ap.add_argument("--dry-run", action="store_true", help="print what would fire, write nothing")
    args = ap.parse_args()
    from runlog import track
    with track("deposit_nudge"):
        return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
