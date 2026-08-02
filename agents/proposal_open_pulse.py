#!/usr/bin/env python3
"""Proposal open pulse: the MOMENT a sent proposal's opens counter goes 0 -> 1,
tell [OWNER] to pick up the phone. A prospect reading the proposal right now is
the hottest 5 minutes in the whole pipeline, and until this agent existed the
signal sat silently in proposals.jsonl.

WHAT: reads the proposal queue (proposal_factory.load_queue(), fallback to a
      per-line read of store/proposals.jsonl), diffs each proposal's `opens`
      against store/open_pulse_state.json (pid -> last_seen_opens, written
      flocked + atomic every run). A 0 -> 1 transition on a SENT proposal gets
      the hot push ("call now, do not email"); opens jumping by REREAD_DELTA+
      gets a softer re-read pulse. Staged/skipped/superseded proposals never
      push (not sent yet, an open there is [OWNER] previewing his own link), but
      their counts ARE baselined so a pre-send preview can't fire a false
      first-open after the send.
WHEN: every poll cycle (cheap: two file reads, no LLM). Cap MAX_PUSHES per run
      so a backlog flush can't machine-gun his phone.
RAILS: read-only against proposals. Only write is its own state file (+ feed).
      Push is a self-notification via ntfy. --dry-run: print, no push, no
      state write. Missing stores exit 0 (fresh-install safety).

Run: .venv/bin/python agents/proposal_open_pulse.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import _flock, now_iso  # noqa: E402
import planner  # noqa: E402

# ---- tunables ----
STATE = ROOT / "store" / "open_pulse_state.json"
PROPOSALS = ROOT / "store" / "proposals.jsonl"  # fallback path if the factory import fails
SENT_STATUSES = {"sent"}  # only a proposal that actually went out can be "being read"
REREAD_DELTA = 2  # opens jumping by this many since last run = a re-read pulse
MAX_PUSHES = 3  # per run; the rest still land in state so nothing double-fires later


def _read_jsonl_lww(path: Path) -> list[dict]:
    """Per-line read, last-write-wins by id (append-only store convention)."""
    if not path.exists():
        return []
    by_id, order = {}, []
    for line in path.read_text().splitlines():
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


def _load_props() -> list[dict]:
    try:
        import proposal_factory
        return proposal_factory.load_queue()
    except Exception:  # noqa: BLE001 - factory import surprise never kills the pulse
        return _read_jsonl_lww(PROPOSALS)


def _load_state() -> tuple[dict, bool]:
    """(state, usable). usable=False means the state file is missing OR unreadable/
    corrupt: on that first-ever (or post-corruption) run we must baseline ALL current
    opens silently and push NOTHING, or every already-read proposal fires a false
    'reading RIGHT NOW' the moment the agent starts existing."""
    try:
        data = json.loads(STATE.read_text())
        return (data, True) if isinstance(data, dict) else ({}, False)
    except (OSError, json.JSONDecodeError):
        return {}, False


def _save_state(state: dict) -> None:
    """Flocked + atomic: a crash mid-write must never leave a truncated file
    that would re-fire every past open as new."""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with _flock(STATE):
        tmp = STATE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
        os.replace(tmp, STATE)


def _opens(rec: dict) -> int:
    try:
        return max(0, int(rec.get("opens") or 0))
    except (TypeError, ValueError):
        return 0


def detect(props: list[dict], state: dict) -> tuple[list[dict], dict]:
    """Pure diff: returns (events, new_state). Every proposal's opens count is
    baselined into new_state (including staged ones); push events only exist
    for SENT proposals. Event kinds: first_open, reread."""
    new_state = dict(state)
    events: list[dict] = []
    for r in props:
        pid = r.get("id")
        if not pid:
            continue
        opens = _opens(r)
        try:
            last = max(0, int(state.get(pid, 0)))
        except (TypeError, ValueError):
            last = 0
        if opens != last:
            new_state[pid] = opens
        if r.get("status") not in SENT_STATUSES:
            continue  # staged/skipped/etc: baseline only, never push
        company = r.get("company") or r.get("name") or "A prospect"
        try:
            price = float(r.get("price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        opened_at = str(r.get("opened_at") or "")
        if last == 0 and opens >= 1:
            events.append({"kind": "first_open", "pid": pid, "company": company,
                           "price": price, "opens": opens, "delta": opens - last,
                           "opened_at": opened_at})
        elif opens - last >= REREAD_DELTA:
            events.append({"kind": "reread", "pid": pid, "company": company,
                           "price": price, "opens": opens, "delta": opens - last,
                           "opened_at": opened_at})
    # hottest first: fresh first-opens beat re-reads, bigger tiers beat smaller
    events.sort(key=lambda e: (0 if e["kind"] == "first_open" else 1, -e["price"]))
    return events, new_state


def _recency(opened_at: str) -> str:
    """A short 'how fresh is this open' phrase from the proposal's opened_at, or ''
    when it is missing/unparseable. Softens 'RIGHT NOW' to the truth: a first_open
    detected off an hours-old read shouldn't claim they are on the page this second."""
    if not opened_at:
        return ""
    try:
        dt = datetime.fromisoformat(opened_at)
        if not dt.tzinfo:
            dt = dt.astimezone()
    except (ValueError, TypeError):
        return ""
    mins = (datetime.now(dt.tzinfo) - dt).total_seconds() / 60.0
    if mins < 0:
        return ""
    if mins < 10:
        return "just now"
    if mins < 90:
        return f"{int(mins)} min ago"
    hrs = mins / 60.0
    if hrs < 36:
        return f"{int(round(hrs))}h ago"
    return f"{int(hrs // 24)}d ago"


def _push(ev: dict) -> None:
    price = f"${ev['price']:g}"
    rec = _recency(ev.get("opened_at", ""))
    if ev["kind"] == "first_open":
        fresh = rec in ("just now", "")
        title = (f"{ev['company']} is reading your proposal right now" if fresh
                 else f"{ev['company']} opened your proposal ({rec})")
        when = f" Last open {rec}." if rec and rec != "just now" else ""
        body = f"Call now, do not email. {price} tier. Opened {ev['opens']} time(s).{when}"
        tags = "fire"
    else:
        title = f"{ev['company']} came back to your proposal"
        when = f" Last open {rec}." if rec else ""
        body = (f"Opened {ev['opens']} times total, up {ev['delta']} since last check. "
                f"Still warm. {price} tier. Worth a call today.{when}")
        tags = "eyes"
    planner.notify(title, body, tags=tags)
    try:
        planner.feed_add("agent", title, body[:140])
    except Exception:  # noqa: BLE001
        pass


def run(dry_run: bool = False) -> int:
    props = _load_props()
    if not props:
        print("open_pulse: no proposals store yet, nothing to do")
        return 0
    state, usable = _load_state()

    if not usable:
        # First run ever, or the state file is missing/corrupt. Baseline every current
        # open silently (opens that already happened are NOT news) and push nothing, so
        # only opens that occur AFTER this run can ever fire. Without this, last=0 for
        # every proposal -> a wave of false "reading RIGHT NOW" pushes on stale reads.
        _, baseline = detect(props, {})
        print(f"open_pulse: no usable prior state, baselining {len(baseline)} proposal(s) "
              "silently (0 pushes); future opens will fire")
        if dry_run:
            print("[dry-run] no state write")
            return 0
        _save_state(baseline)
        print(f"open_pulse: state initialized ({len(baseline)} proposal(s) tracked) at {now_iso()}")
        return 0

    events, new_state = detect(props, state)

    to_push = events[:MAX_PUSHES]
    skipped = len(events) - len(to_push)
    print(f"open_pulse: {len(props)} proposal(s), {len(events)} open event(s)"
          + (f", capped to {MAX_PUSHES}" if skipped > 0 else ""))
    for ev in events:
        mark = "PUSH" if ev in to_push else "held"
        print(f"  [{mark}] {ev['kind']:<10} {ev['company']} opens={ev['opens']} (+{ev['delta']}) ${ev['price']:g}")

    if dry_run:
        print("[dry-run] no push, no state write")
        return 0

    for ev in to_push:
        _push(ev)
    if new_state != state:
        _save_state(new_state)
        print(f"open_pulse: state updated ({len(new_state)} proposal(s) tracked) at {now_iso()}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Push the instant a sent proposal gets opened")
    ap.add_argument("--dry-run", action="store_true", help="compute and print, no push, no state write")
    args = ap.parse_args()
    if args.dry_run:
        return run(dry_run=True)
    from runlog import track
    with track("proposal_open_pulse"):
        return run(dry_run=False)


if __name__ == "__main__":
    raise SystemExit(main())
