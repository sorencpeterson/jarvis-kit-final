#!/usr/bin/env python3
"""E402: daily plan generator — turns the warm block, today's calendar events,
and the attention router's top items into one ordered, time-boxed, HONEST plan
for the day. "Honest" means: if the calendar is unreachable or a queue is
empty, the plan says so in plain language rather than inventing structure.

WHAT: reads store/warm_block.json (today's 10-call warm sprint, built by
      warm_block.py which is NOT this file's job to rebuild), /api/gcal (same
      endpoint + auth pattern as agents/meeting_prep.py) for today's events,
      store/attention.json (built by agents/attention.py; run that first or
      this will just report it as missing rather than guessing), and
      store/energy.json (peak-productivity hours, if present) to bias where
      in the day the top attention item gets slotted.
WHEN: run once each morning (after warm_block.py + attention.py, so both
      inputs are fresh), or ad hoc any time [OWNER] wants a re-plan.
RAILS: read-only against every store/endpoint it touches. Only write is
      store/day_plan.md (full overwrite each run, no history kept, it's a
      today document). No LLM call needed — pure assembly + scheduling math.

WORKDAY WINDOW: 08:00-18:00 local time (WORKDAY_START_HOUR/WORKDAY_END_HOUR
below). Calendar-free-time assumption: any hour in that window with no
calendar event within CALENDAR_BUSY_PAD_MIN minutes is assumed open. This is
intentionally simple (point-events + a pad, not a true free/busy diff) because
/api/gcal returns point-in-time markers, not start/end ranges (see
agents/meeting_prep.py's own docstring, same finding).

Run:  .venv/bin/python agents/day_plan.py
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import LOCAL_TZ, now_iso, secret  # noqa: E402
import planner  # noqa: E402

WARM_BLOCK = ROOT / "store" / "warm_block.json"
ATTENTION = ROOT / "store" / "attention.json"
ENERGY = ROOT / "store" / "energy.json"
OUT = ROOT / "store" / "day_plan.md"

WORKDAY_START_HOUR = 8
WORKDAY_END_HOUR = 18
CALENDAR_BUSY_PAD_MIN = 45  # each event blocks this many minutes around itself
WARM_BLOCK_DURATION_MIN = 90  # the 10-call warm sprint gets a fixed slot
TOP_ATTENTION_SLOT_MIN = 45
SLOT_GRANULARITY_MIN = 30  # free-time search granularity


def _get(path: str) -> dict:
    req = urllib.request.Request("http://127.0.0.1:8765" + path,
                                 headers={"X-Brain-Token": secret("brain_token")})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _today_events() -> tuple[list[dict], str | None]:
    """Returns (today's timed events sorted by time, error_message_or_None)."""
    try:
        events = _get("/api/gcal").get("events", [])
    except Exception as e:  # noqa: BLE001
        return [], f"calendar unreachable ({e})"
    today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    out = []
    for e in events:
        when = e.get("when") or ""
        # D7: /api/gcal sends all-day events as a bare date ("2026-07-07", no time
        # part). fromisoformat happily parses that as MIDNIGHT, so they used to show
        # up as fake 12:00am appointments. Keep them, flagged all_day, so they're
        # listed as all-day entries but never busy-block a clock time.
        all_day = "T" not in when and len(when) <= 10
        try:
            dt = datetime.fromisoformat(when)
        except ValueError:
            continue
        if not dt.tzinfo:
            dt = dt.astimezone(LOCAL_TZ)
        if dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d") == today:
            out.append({"text": e.get("text", "(untitled)"), "dt": dt, "all_day": all_day})
    out.sort(key=lambda x: x["dt"])
    return out, None


def _free_windows(events: list[dict]) -> list[tuple[datetime, datetime]]:
    """Workday window minus a pad around each event = the open slots."""
    today = datetime.now(LOCAL_TZ).replace(hour=WORKDAY_START_HOUR, minute=0, second=0, microsecond=0)
    end_of_day = today.replace(hour=WORKDAY_END_HOUR)
    busy: list[tuple[datetime, datetime]] = []
    pad = timedelta(minutes=CALENDAR_BUSY_PAD_MIN)
    for e in events:
        if e.get("all_day"):
            continue  # no clock time to pad around; listed in the doc, not blocking
        busy.append((e["dt"] - pad, e["dt"] + pad))
    busy.sort(key=lambda b: b[0])

    # merge overlapping busy windows
    merged: list[tuple[datetime, datetime]] = []
    for b in busy:
        if merged and b[0] <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b[1]))
        else:
            merged.append(b)

    free: list[tuple[datetime, datetime]] = []
    cursor = today
    for b_start, b_end in merged:
        if b_start > cursor:
            free.append((cursor, min(b_start, end_of_day)))
        cursor = max(cursor, b_end)
        if cursor >= end_of_day:
            break
    if cursor < end_of_day:
        free.append((cursor, end_of_day))
    return [(s, e) for s, e in free if e > s]


def _take_slot(free: list[tuple[datetime, datetime]], minutes: int) -> tuple[datetime, datetime] | None:
    """Pop the first free window with room for `minutes`, shrinking it in place.
    Mutates `free`. Returns None if nothing fits (an honest signal, not a crash)."""
    need = timedelta(minutes=minutes)
    for i, (s, e) in enumerate(free):
        if e - s >= need:
            slot = (s, s + need)
            free[i] = (s + need, e)
            return slot
    return None


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%-I:%M%p").lower()


def build() -> dict:
    """Returns {'lines': [...], 'warnings': [...]} — lines is the ordered plan
    body, warnings is what's honestly missing/stale so the caller can decide
    whether to still write the file (it always does, warnings go IN the doc)."""
    warnings: list[str] = []
    lines: list[str] = []

    events, cal_err = _today_events()
    if cal_err:
        warnings.append(f"Calendar: {cal_err} — planning as if the day is fully open.")
    free = _free_windows(events)

    attention = _load_json(ATTENTION)
    if attention is None:
        warnings.append("store/attention.json not found — run agents/attention.py first "
                         "for a real top item; no attention items will appear in today's plan.")
        top_items = []
    else:
        top_items = attention.get("ranked", [])[:3]

    warm = _load_json(WARM_BLOCK)
    warm_picks = (warm or {}).get("picks", [])
    warm_date = (warm or {}).get("date")
    today_str = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    if warm is None:
        warnings.append("store/warm_block.json not found — no warm sprint slotted today.")
    elif warm_date != today_str:
        warnings.append(f"warm_block.json is dated {warm_date}, not today ({today_str}) — "
                         "run agents/warm_block.py to refresh before trusting this slot.")

    energy = _load_json(ENERGY)
    peak_hours = (energy or {}).get("peak_hours") or []

    entries: list[tuple[datetime, datetime, str]] = []  # (start, end, label)

    # 1. Top attention item first (the single most important thing today),
    #    slotted into a peak hour if one's known and open, else the first free slot.
    if top_items:
        top = top_items[0]
        slot = None
        if peak_hours:
            for ph in peak_hours:
                try:
                    hour = int(ph)
                except (TypeError, ValueError):
                    continue
                for i, (s, e) in enumerate(free):
                    if s.hour <= hour < e.hour or (s.hour == hour):
                        want_start = max(s, s.replace(hour=hour, minute=0))
                        if e - want_start >= timedelta(minutes=TOP_ATTENTION_SLOT_MIN):
                            slot = (want_start, want_start + timedelta(minutes=TOP_ATTENTION_SLOT_MIN))
                            free[i] = (slot[1], e) if slot[1] < e else (e, e)
                            remainder_before = (s, want_start)
                            if remainder_before[1] > remainder_before[0]:
                                free.insert(i, remainder_before)
                            break
                if slot:
                    break
        if not slot:
            slot = _take_slot(free, TOP_ATTENTION_SLOT_MIN)
        if slot:
            entries.append((slot[0], slot[1], f"[TOP] {top['label']} (score {top['score']})"))
        else:
            warnings.append(f"No open slot fit the top attention item: {top['label']}")
    else:
        warnings.append("Attention router has nothing ranked — queues are clear, or it hasn't run.")

    # 2. Warm sprint block (fixed duration), next available slot.
    if warm_picks:
        slot = _take_slot(free, WARM_BLOCK_DURATION_MIN)
        if slot:
            names = ", ".join(p["name"].split()[0] for p in warm_picks[:4])
            more = "..." if len(warm_picks) > 4 else ""
            entries.append((slot[0], slot[1], f"Warm sprint: {len(warm_picks)} calls ({names}{more})"))
        else:
            warnings.append(f"No open {WARM_BLOCK_DURATION_MIN}-min slot fit the warm sprint "
                             f"({len(warm_picks)} calls) — day's calendar is packed.")

    # 3. Remaining attention items (#2, #3), each a lighter slot, best-effort.
    for item in top_items[1:3]:
        slot = _take_slot(free, TOP_ATTENTION_SLOT_MIN // 2)
        if slot:
            entries.append((slot[0], slot[1], f"{item['label']} (score {item['score']})"))
        else:
            warnings.append(f"No open slot left for: {item['label']}")

    entries.sort(key=lambda x: x[0])

    lines.append(f"# Day plan — {today_str}")
    lines.append("")
    if not entries:
        lines.append("Nothing to time-box today: no calendar events, no attention items, "
                      "and no warm sprint loaded. Either everything's genuinely clear, or "
                      "the inputs haven't run yet (see warnings below).")
    else:
        for s, e, label in entries:
            lines.append(f"- **{_fmt_time(s)}-{_fmt_time(e)}** {label}")
    if events:
        lines.append("")
        lines.append("## Calendar today")
        for e in events:
            when = "all day" if e.get("all_day") else _fmt_time(e["dt"])
            lines.append(f"- {when} {e['text']}")
    if warnings:
        lines.append("")
        lines.append("## Honest gaps")
        for w in warnings:
            lines.append(f"- {w}")
    lines.append("")
    lines.append(f"_generated {now_iso()}_")

    return {"lines": lines, "warnings": warnings, "n_entries": len(entries)}


def main() -> int:
    from runlog import track
    with track("day_plan"):
        result = build()
        OUT.write_text("\n".join(result["lines"]) + "\n")

    print(f"day_plan: {result['n_entries']} slot(s) planned, {len(result['warnings'])} gap(s) -> {OUT}")
    for w in result["warnings"]:
        print(f"  gap: {w}")
    try:
        planner.feed_add("agent", f"Day plan ready: {result['n_entries']} slot(s)")
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
