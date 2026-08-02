#!/usr/bin/env python3
"""E329: capture v2 — quick-add parses date/priority/project inline, so
"call Bob tue p1 @warm" becomes {text: "call Bob", scheduled_time: <next
Tuesday>, priority: 1, project: "warm"} instead of landing as one opaque
inbox string that still needs manual triage later.

WHAT: a pure parse_capture(text) -> dict function, no I/O, no store writes,
      no network. Strips inline markers (weekday, "p1"/"p2"/"p3", "@project")
      out of the free text and resolves them to the SAME field shapes
      POST /api/todo already accepts (see CONTRACT below) — this file does
      NOT call that endpoint or write to todos.jsonl itself; it's the parsing
      half only, ready for a caller (capture/quick-add.sh, a future UI, or
      app/server.py, all outside this lane's exclusive files) to wire up.
WHEN: import parse_capture() from anywhere; run this file directly for a
      quick manual smoke-test of a few example strings.
RAILS: 100% pure function, zero side effects. This file itself never writes
      to any store — seeing it listed under "capture" is about what it
      PARSES FOR, not what it touches on disk.

GRAMMAR (order of extraction, markers can appear anywhere in the text):
  @<word>          -> project = "<word>" (lowercased, first one wins)
  p1 / p2 / p3      -> priority = 1/2/3 (case-insensitive, first one wins)
  a weekday name
    (mon/monday...) -> scheduled_time = next occurrence of that weekday,
                        09:00 local time by default (see DEFAULT_HOUR)
  "tomorrow"        -> scheduled_time = tomorrow, 09:00 local
  "today"           -> scheduled_time = today, current local time + 1h
                        (adding a same-day item shouldn't schedule it in
                        the past)
  All matched markers are stripped from the returned 'text' (with cleanup
  of resulting double-spaces), so "call Bob tue p1 @warm" -> text: "call Bob".
  Unmatched free text (no markers) parses to a plain todo: everything null
  except text itself, exactly matching what typing into quick-add today does.

CONTRACT for a future caller (e.g. app/server.py's POST /api/todo, which is
outside this lane's exclusive files so this module does not call it itself):
  parsed = parse_capture(raw_text)
  # parsed = {"text": str, "project": str|None, "priority": int|None,
  #           "scheduled_time": iso8601-str|None, "duration_min": int|None}
  # This is a direct superset-compatible match for AddTodo's fields
  # (text, project?, priority?, at?, dur?) — 'scheduled_time' here is what
  # AddTodo calls 'at' (both ISO8601), 'duration_min' is what it calls 'dur'.
  # A caller can do: AddTodo(text=parsed["text"], project=parsed["project"],
  #                           priority=parsed["priority"], at=parsed["scheduled_time"],
  #                           dur=parsed["duration_min"])

Run standalone: .venv/bin/python agents/capture_parse.py
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT,):
    sys.path.insert(0, str(p))
from store_lib import LOCAL_TZ  # noqa: E402

DEFAULT_HOUR = 9  # 09:00 local for a bare weekday/tomorrow with no explicit time
DEFAULT_DURATION_MIN = 30  # matches capture/pull_reminders.py's own default for scheduled items

WEEKDAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
            "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thurs": 3,
            "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
_WD_ALT = "|".join(sorted(WEEKDAYS.keys(), key=len, reverse=True))  # longest-first avoids "tue" eating "tues"

PROJECT_RE = re.compile(r"@(\w+)")
PRIORITY_RE = re.compile(r"\bp([123])\b", re.I)
WEEKDAY_RE = re.compile(rf"\b({_WD_ALT})\b", re.I)
TOMORROW_RE = re.compile(r"\btomorrow\b", re.I)
TODAY_RE = re.compile(r"\btoday\b", re.I)


def _next_weekday(wd_name: str, now: datetime) -> datetime:
    target = WEEKDAYS[wd_name.lower()]
    delta = (target - now.weekday()) % 7
    delta = delta or 7  # "tue" said ON a Tuesday means NEXT Tuesday, not right now
    d = (now + timedelta(days=delta)).replace(hour=DEFAULT_HOUR, minute=0, second=0, microsecond=0)
    return d


def _strip_span(text: str, span: tuple[int, int]) -> str:
    return text[:span[0]] + text[span[1]:]


def parse_capture(text: str, *, now: datetime | None = None) -> dict:
    """Parse one quick-add string into todo fields. Pure function: same input
    always gives the same output for a given `now` (defaults to real now)."""
    now = now or datetime.now(LOCAL_TZ)
    working = text
    project = None
    priority = None
    scheduled_time = None
    duration_min = None

    m = PROJECT_RE.search(working)
    if m:
        project = m.group(1).lower()
        working = _strip_span(working, m.span())

    m = PRIORITY_RE.search(working)
    if m:
        priority = int(m.group(1))
        working = _strip_span(working, m.span())

    # date markers: tomorrow / today / a bare weekday name (checked in that
    # priority order so "tomorrow" doesn't also get eaten by a coincidental
    # weekday-substring match; each pattern is anchored to \b so this is
    # already safe, order here is about which WINS if a string absurdly
    # contained more than one, which is a should-never-happen edge [OWNER]'s
    # own typed text is unlikely to hit, but resolving it predictably beats
    # silently picking whichever regex ran last).
    m = TOMORROW_RE.search(working)
    if m:
        scheduled_time = (now + timedelta(days=1)).replace(
            hour=DEFAULT_HOUR, minute=0, second=0, microsecond=0)
        working = _strip_span(working, m.span())
    else:
        m = TODAY_RE.search(working)
        if m:
            candidate = now.replace(second=0, microsecond=0)
            if candidate.hour >= DEFAULT_HOUR:
                candidate = candidate + timedelta(hours=1)  # don't schedule in the past
            else:
                candidate = candidate.replace(hour=DEFAULT_HOUR, minute=0)
            scheduled_time = candidate
            working = _strip_span(working, m.span())
        else:
            m = WEEKDAY_RE.search(working)
            if m:
                scheduled_time = _next_weekday(m.group(1), now)
                working = _strip_span(working, m.span())

    if scheduled_time is not None:
        duration_min = DEFAULT_DURATION_MIN

    clean_text = re.sub(r"\s{2,}", " ", working).strip()
    return {
        "text": clean_text,
        "project": project,
        "priority": priority,
        "scheduled_time": scheduled_time.isoformat(timespec="seconds") if scheduled_time else None,
        "duration_min": duration_min,
    }


if __name__ == "__main__":
    import json
    examples = [
        "call Bob tue p1 @warm",
        "follow up with the roofer tomorrow @outreach",
        "just a plain thought with no markers",
        "p2 review the deck friday",
        "today at some point @systems",
    ]
    for ex in examples:
        result = parse_capture(ex)
        print(f"{ex!r}\n  -> {json.dumps(result, ensure_ascii=False)}\n")
