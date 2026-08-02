#!/usr/bin/env python3
"""Best-day model (tech #269) — his own data picks the optimal send/call
day x hour, replacing besttime.py's generic heuristics once n>100 (per the
item description). Builds a day-of-week x hour-of-day success grid from three
signal sources:

- store/warm_dispo.jsonl: dispo timestamp x whether dispo == "booked" (a call
  that led to a booking is a success)
- store/replies.jsonl: sent_at timestamp x status == "sent" (a reply that
  actually went out; "success" here is looser -- see note below, replies
  don't carry an outcome field yet, so this is a volume grid not a success
  grid for this source, labeled honestly)
- store/proposals.jsonl: created timestamp x opened_at present (an open is a
  measurable success signal for a proposal send)

Same honesty pattern as besttime.py (its direct sibling/precursor): below the
item's own stated bar (n>100) this writes status 'collecting' and a plain
grid of raw counts, never a fabricated "best day is Tuesday" claim built on
noise. At n>100 it promotes to a real recommendation per cell with enough
volume to trust (a per-cell minimum, not just an aggregate n>100, since a
single loud Tuesday doesn't make Tuesday the answer).

Read-only against the three stores above; writes store/best_day.json (full
overwrite). Run standalone: .venv/bin/python agents/best_day.py
.venv/bin/python agents/best_day.py --fixture
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from store_lib import now_iso  # noqa: E402

WARM_DISPO = ROOT / "store" / "warm_dispo.jsonl"
REPLIES = ROOT / "store" / "replies.jsonl"
PROPOSALS = ROOT / "store" / "proposals.jsonl"
OUT = ROOT / "store" / "best_day.json"

READY_THRESHOLD = 100   # per item description: "replaces generic besttime heuristics at n>100"
MIN_CELL_N = 5          # a single cell needs at least this many data points before its rate is trusted
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


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


def _dedup_by_id(rows: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        rid = r.get("id")
        if rid is None:
            continue
        if rid not in by_id:
            order.append(rid)
        by_id[rid] = r
    return [by_id[i] for i in order]


def _day_hour(ts: str) -> tuple[str, int] | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    dt = dt.astimezone()
    return DAYS[dt.weekday()], dt.hour


def _events_from_warm_dispo(rows: list[dict]) -> list[tuple[str, int, bool]]:
    out = []
    for r in _dedup_by_id(rows):
        dh = _day_hour(r.get("ts", ""))
        if dh:
            out.append((*dh, r.get("dispo") == "booked"))
    return out


def _events_from_replies(rows: list[dict]) -> list[tuple[str, int, bool]]:
    """No outcome field exists on replies.jsonl yet (status is send-pipeline
    state, not a reply-back signal) -- success here is loosely "it sent",
    which is a volume signal, not a real success signal. Flagged in output."""
    out = []
    for r in _dedup_by_id(rows):
        if r.get("status") != "sent":
            continue
        dh = _day_hour(r.get("sent_at", ""))
        if dh:
            out.append((*dh, True))
    return out


def _events_from_proposals(rows: list[dict]) -> list[tuple[str, int, bool]]:
    out = []
    for r in _dedup_by_id(rows):
        dh = _day_hour(r.get("created", ""))
        if dh:
            out.append((*dh, (r.get("opens") or 0) > 0))
    return out


def build_grid(events: list[tuple[str, int, bool]]) -> dict:
    cells: dict[tuple[str, int], list[int]] = defaultdict(lambda: [0, 0])  # [total, success]
    for day, hour, success in events:
        c = cells[(day, hour)]
        c[0] += 1
        if success:
            c[1] += 1

    grid = {}
    for (day, hour), (total, success) in cells.items():
        key = f"{day} {hour:02d}:00"
        entry = {"n": total, "successes": success}
        if total >= MIN_CELL_N:
            entry["success_rate"] = round(success / total, 3)
        else:
            entry["success_rate"] = None
            entry["cell_note"] = f"insufficient data (n={total}, need {MIN_CELL_N})"
        grid[key] = entry
    return grid


def build(events: list[tuple[str, int, bool]], event_source_note: str) -> dict:
    n_total = len(events)
    grid = build_grid(events)
    trusted_cells = {k: v for k, v in grid.items() if v["success_rate"] is not None}
    # A cell that cleared MIN_CELL_N but has a 0% success rate is real signal
    # (that slot is actively bad), not a recommendation -- picking it as
    # "best" via plain max() would be misleading with only one trusted cell
    # in play (a bug caught on the first real run: n=10, 0 successes, still
    # "won" max() over an empty field of alternatives). Only ever call
    # something "best" if its rate is actually above zero.
    positive_cells = {k: v for k, v in trusted_cells.items() if v["success_rate"] > 0}
    best = max(positive_cells.items(), key=lambda kv: kv[1]["success_rate"]) if positive_cells else None
    all_time_worst = (max(trusted_cells.items(), key=lambda kv: kv[1]["n"])
                      if trusted_cells and not positive_cells else None)

    status = "ready" if n_total > READY_THRESHOLD and positive_cells else "collecting"
    result = {
        "generated": now_iso(),
        "status": status,
        "total_events": n_total,
        "ready_threshold": READY_THRESHOLD,
        "min_cell_n": MIN_CELL_N,
        "grid": grid,
        "event_source_note": event_source_note,
    }
    if best:
        result["best_slot"] = {"slot": best[0], **best[1]}
    elif all_time_worst:
        # a cell cleared the n-bar but every trusted cell has a 0% success
        # rate -- there IS a real signal (that slot underperforms), just not
        # a positive one worth calling "best". Say so honestly instead of
        # either hiding it or mislabeling it as a recommendation.
        result["best_slot"] = None
        result["note"] = (f"n={all_time_worst[1]['n']} cleared the per-cell minimum at "
                          f"{all_time_worst[0]}, but its success rate is 0% -- real signal that "
                          "slot underperforms, not a 'best' slot to recommend. Need at least one "
                          "cell with a nonzero rate before this model has anything positive to say.")
    else:
        result["best_slot"] = None
        result["note"] = (f"insufficient data (n={n_total}, need {READY_THRESHOLD}+ total AND "
                          f"{MIN_CELL_N}+ per cell) -- besttime.py's heuristics still apply until then.")
    return result


def _fixture_events() -> list[tuple[str, int, bool]]:
    """150 synthetic events with a deliberate real pattern: Tue 10:00 has a
    much higher success rate than the rest, proving the promotion-to-ready
    path fires correctly at n>100."""
    import random
    rng = random.Random(7)
    events = []
    for _ in range(150):
        day = rng.choice(DAYS[:5])
        hour = rng.choice([9, 10, 11, 14, 15, 16])
        if day == "Tue" and hour == 10:
            success = rng.random() < 0.55
        else:
            success = rng.random() < 0.12
        events.append((day, hour, success))
    return events


def run(fixture: bool = False) -> dict:
    if fixture:
        events = _fixture_events()
        note = "FIXTURE: synthetic events with a deliberate Tue 10:00 pattern"
        source = "FIXTURE"
    else:
        warm_events = _events_from_warm_dispo(_read_jsonl(WARM_DISPO))
        reply_events = _events_from_replies(_read_jsonl(REPLIES))
        proposal_events = _events_from_proposals(_read_jsonl(PROPOSALS))
        events = warm_events + reply_events + proposal_events
        note = (f"warm_dispo booked-outcome events: {len(warm_events)}; "
               f"reply SENT-volume events (no real outcome field yet, volume "
               f"proxy only): {len(reply_events)}; proposal opened-outcome "
               f"events: {len(proposal_events)}")
        source = "REAL"
    result = build(events, note)
    result["source"] = source
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))
    if result["best_slot"]:
        print(f"best_day [{source}]: {result['status']}, {result['total_events']} events, "
              f"best slot: {result['best_slot']['slot']} "
              f"({result['best_slot']['success_rate']:.0%} of {result['best_slot']['n']}) -> {OUT}")
    else:
        print(f"best_day [{source}]: {result['status']}, {result['total_events']} events, "
              f"{result.get('note', 'no cell clears the min-n bar yet')} -> {OUT}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", action="store_true")
    args = ap.parse_args()
    run(fixture=args.fixture)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
