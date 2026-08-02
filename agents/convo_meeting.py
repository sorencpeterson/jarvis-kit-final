#!/usr/bin/env python3
"""C206 meeting-proposal drafts include 2 concrete slots (calendar-aware).

When a draft is offering a call (booking language, playbook's "grab 15 minutes"
close), naming two actual open times beats "whenever works for you" -- it removes a
round-trip and reads as organized, which is on-brand (VOICE-SPEC: "competence over
cute"). suggest_slots() reads real calendar events (/api/gcal, the same endpoint
agents/meeting_prep.py already calls, same defensive pattern: unreachable server or
empty calendar degrades to generic slot language rather than erroring) and proposes
the next 2 weekday business-hours 30-minute-clear windows.

Business hours default 9am-5pm his local timezone (store_lib.LOCAL_TZ, the machine's
actual zone, same convention every other agent in this codebase already follows so
"today" and hour boundaries track wherever [OWNER] actually is -- see store_lib.py's
own comment on why a hardcoded UTC offset was wrong).

Rails: read-only against /api/gcal. Never writes to the calendar, never books
anything, never sends. Pure suggestion text for reply_watch.py to append to a draft;
the actual reply still needs [OWNER]'s approve click like everything else.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timedelta, time as dtime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import LOCAL_TZ, secret  # noqa: E402

BUSINESS_START = 9
BUSINESS_END = 17
SLOT_MINUTES = 30
LOOKAHEAD_DAYS = 10
SERVER_BASE = "http://127.0.0.1:8765"


def _fetch_events() -> list[dict]:
    """Same /api/gcal call agents/meeting_prep.py already makes (identical auth
    header, identical base URL, identical timeout). Returns [] on ANY failure
    (server down, unauthorized, malformed response) -- suggest_slots() degrades
    gracefully rather than raising, matching meeting_prep.py's own documented
    'empty/unreachable is a valid state, not an error' stance."""
    try:
        req = urllib.request.Request(SERVER_BASE + "/api/gcal",
                                     headers={"X-Brain-Token": secret("brain_token")})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        return data.get("events", []) if isinstance(data, dict) else []
    except Exception:  # noqa: BLE001 — network/auth/parse failures all degrade the same way
        return []


def _busy_windows(events: list[dict]) -> list[tuple[datetime, datetime]]:
    """Parse events' 'when' timestamps into (start, start+1h) busy windows -- events
    here don't carry an explicit duration, so 1 hour is a conservative default
    (better to skip a slot that was actually free than suggest one that collides).
    All-day-style events (when == just a date, no time component, e.g. "2026-06-11")
    are IGNORED for slot-blocking purposes (they'd otherwise get treated as a busy
    window starting at midnight, which is harmless for slot-blocking purposes since
    midnight never overlaps 9am-5pm business hours anyway, BUT is still the wrong
    semantic: an all-day note like "Stay at Hangar Inn" isn't a same-day scheduling
    conflict for a 30-minute call and shouldn't be read as a timed event at all).
    datetime.fromisoformat() does NOT raise on a bare date string (confirmed: it
    parses "2026-07-06" as midnight that day without error) -- checking for the
    literal 'T' separator explicitly, rather than relying on a parse failure that
    doesn't actually happen, is what correctly excludes these (a real bug here
    originally: the docstring assumed fromisoformat would raise on date-only input,
    a fixture test caught that it silently doesn't)."""
    out = []
    for e in events:
        when = e.get("when") or ""
        if "T" not in when:
            continue  # date-only string, e.g. an all-day event -- not a timed conflict
        try:
            dt = datetime.fromisoformat(when)
        except ValueError:
            continue
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        out.append((dt, dt + timedelta(hours=1)))
    return out


def _overlaps(slot_start: datetime, slot_end: datetime, busy: list[tuple[datetime, datetime]]) -> bool:
    return any(slot_start < b_end and slot_end > b_start for b_start, b_end in busy)


def _candidate_slots(now: datetime, days_ahead: int = LOOKAHEAD_DAYS) -> list[datetime]:
    """Every business-hours slot-start, weekdays only, for the next `days_ahead`
    calendar days starting tomorrow (never suggests a same-day slot -- not enough
    lead time to be a real offer)."""
    out = []
    d = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(days_ahead):
        if d.weekday() < 5:  # Mon-Fri
            hour = BUSINESS_START
            minute = 0
            while (hour, minute) < (BUSINESS_END, 0):
                out.append(d.replace(hour=hour, minute=minute))
                minute += SLOT_MINUTES
                if minute >= 60:
                    minute -= 60
                    hour += 1
        d += timedelta(days=1)
    return out


def suggest_slots(n: int = 2, now: datetime | None = None, events: list[dict] | None = None) -> list[dict]:
    """Returns up to `n` {"when": datetime, "label": human string} slots that don't
    collide with any fetched calendar event. events param is for testability (pass a
    fixture list to skip the real /api/gcal call); real callers omit it and let this
    fetch live. Empty calendar/unreachable server just means no collisions to check
    against, so slots are still proposed (never silently returns nothing just because
    the calendar couldn't be read)."""
    now = now or datetime.now(LOCAL_TZ)
    events = _fetch_events() if events is None else events
    busy = _busy_windows(events)
    out = []
    for slot_start in _candidate_slots(now):
        slot_end = slot_start + timedelta(minutes=SLOT_MINUTES)
        if _overlaps(slot_start, slot_end, busy):
            continue
        out.append({"when": slot_start, "label": _label(slot_start)})
        if len(out) >= n:
            break
    return out


def _label(dt: datetime) -> str:
    """Human phrasing matching [OWNER]'s voice register (short, no fluff): 'Thursday
    at 2pm' not 'Thursday, July 9th at 2:00 PM'."""
    day = dt.strftime("%A")
    hour = dt.hour % 12 or 12
    ampm = "am" if dt.hour < 12 else "pm"
    minute_part = f":{dt.minute:02d}" if dt.minute else ""
    return f"{day} at {hour}{minute_part}{ampm}"


def slots_line(n: int = 2, now: datetime | None = None, events: list[dict] | None = None) -> str:
    """Convenience for reply_watch.py: one ready-to-append sentence, or '' if no
    slots could be proposed (e.g. every business-hours window in the lookahead is
    somehow busy -- degrades to '' so the caller falls back to generic booking
    language rather than appending a broken/empty sentence)."""
    slots = suggest_slots(n, now, events)
    if not slots:
        return ""
    labels = [s["label"] for s in slots]
    if len(labels) == 1:
        return f"I've got {labels[0]} open if that works."
    return f"I've got {labels[0]} or {labels[1]} open, whichever works better."
