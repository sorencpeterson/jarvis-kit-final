#!/usr/bin/env python3
"""Unit tests for agents/convo_meeting.py (C206 meeting-proposal drafts with 2
concrete calendar-aware slots).

All tests pass `events=` explicitly to suggest_slots()/slots_line() so no test ever
makes a real network call to /api/gcal -- the live-network path is covered by the
mission's own real end-to-end verification against the actual running server.

Run: .venv/bin/python -m pytest tests/test_convo_meeting.py -v
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

from store_lib import LOCAL_TZ  # noqa: E402
import convo_meeting  # noqa: E402

FRIDAY = datetime(2026, 7, 3, 12, 0, 0, tzinfo=LOCAL_TZ)  # a known Friday


class TestCandidateSlots:
    def test_only_weekdays(self):
        slots = convo_meeting._candidate_slots(FRIDAY, days_ahead=10)
        assert all(s.weekday() < 5 for s in slots)

    def test_never_same_day(self):
        slots = convo_meeting._candidate_slots(FRIDAY, days_ahead=10)
        assert all(s.date() > FRIDAY.date() for s in slots)

    def test_within_business_hours(self):
        slots = convo_meeting._candidate_slots(FRIDAY, days_ahead=10)
        assert all(convo_meeting.BUSINESS_START <= s.hour < convo_meeting.BUSINESS_END for s in slots)

    def test_thirty_minute_increments(self):
        slots = convo_meeting._candidate_slots(FRIDAY, days_ahead=3)
        assert all(s.minute in (0, 30) for s in slots)


class TestBusyWindows:
    def test_timed_event_parsed(self):
        events = [{"when": datetime(2026, 7, 6, 9, 0, tzinfo=LOCAL_TZ).isoformat()}]
        busy = convo_meeting._busy_windows(events)
        assert len(busy) == 1

    def test_all_day_event_ignored(self):
        events = [{"when": "2026-07-06"}]  # date-only, no time
        busy = convo_meeting._busy_windows(events)
        assert busy == []

    def test_empty_when_ignored(self):
        events = [{"when": ""}]
        busy = convo_meeting._busy_windows(events)
        assert busy == []

    def test_missing_when_key_ignored(self):
        events = [{}]
        busy = convo_meeting._busy_windows(events)
        assert busy == []

    def test_naive_timestamp_gets_local_tz(self):
        events = [{"when": "2026-07-06T09:00:00"}]
        busy = convo_meeting._busy_windows(events)
        assert len(busy) == 1
        assert busy[0][0].tzinfo is not None


class TestOverlaps:
    def test_exact_overlap_detected(self):
        busy = [(datetime(2026, 7, 6, 9, 0, tzinfo=LOCAL_TZ),
                 datetime(2026, 7, 6, 10, 0, tzinfo=LOCAL_TZ))]
        assert convo_meeting._overlaps(
            datetime(2026, 7, 6, 9, 30, tzinfo=LOCAL_TZ),
            datetime(2026, 7, 6, 10, 0, tzinfo=LOCAL_TZ), busy)

    def test_adjacent_not_overlapping(self):
        busy = [(datetime(2026, 7, 6, 9, 0, tzinfo=LOCAL_TZ),
                 datetime(2026, 7, 6, 10, 0, tzinfo=LOCAL_TZ))]
        assert not convo_meeting._overlaps(
            datetime(2026, 7, 6, 10, 0, tzinfo=LOCAL_TZ),
            datetime(2026, 7, 6, 10, 30, tzinfo=LOCAL_TZ), busy)

    def test_no_busy_windows_no_overlap(self):
        assert not convo_meeting._overlaps(
            datetime(2026, 7, 6, 9, 0, tzinfo=LOCAL_TZ),
            datetime(2026, 7, 6, 9, 30, tzinfo=LOCAL_TZ), [])


class TestSuggestSlots:
    def test_empty_calendar_still_proposes_slots(self):
        slots = convo_meeting.suggest_slots(2, now=FRIDAY, events=[])
        assert len(slots) == 2

    def test_returns_requested_count(self):
        slots = convo_meeting.suggest_slots(3, now=FRIDAY, events=[])
        assert len(slots) == 3

    def test_busy_slot_skipped(self):
        # block Monday 9am-10am -- the first slot that would otherwise be proposed
        events = [{"when": datetime(2026, 7, 6, 9, 0, tzinfo=LOCAL_TZ).isoformat()}]
        slots = convo_meeting.suggest_slots(2, now=FRIDAY, events=events)
        assert all(s["when"].hour >= 10 or s["when"].date() > datetime(2026, 7, 6).date()
                  for s in slots)

    def test_all_day_event_does_not_block_slots(self):
        events = [{"when": "2026-07-06"}]  # all-day, should NOT block the whole day
        slots = convo_meeting.suggest_slots(2, now=FRIDAY, events=events)
        assert len(slots) == 2
        assert slots[0]["when"].date() == datetime(2026, 7, 6).date()  # Monday still available

    def test_slots_are_chronologically_ordered(self):
        slots = convo_meeting.suggest_slots(4, now=FRIDAY, events=[])
        whens = [s["when"] for s in slots]
        assert whens == sorted(whens)

    def test_first_slot_is_next_monday_from_friday(self):
        slots = convo_meeting.suggest_slots(1, now=FRIDAY, events=[])
        assert slots[0]["when"].weekday() == 0  # Monday
        assert slots[0]["when"].hour == convo_meeting.BUSINESS_START


class TestLabel:
    def test_on_the_hour_no_minutes_shown(self):
        dt = datetime(2026, 7, 6, 9, 0, tzinfo=LOCAL_TZ)
        assert convo_meeting._label(dt) == "Monday at 9am"

    def test_half_hour_shown(self):
        dt = datetime(2026, 7, 6, 14, 30, tzinfo=LOCAL_TZ)
        assert convo_meeting._label(dt) == "Monday at 2:30pm"

    def test_noon_is_12pm_not_0pm(self):
        dt = datetime(2026, 7, 6, 12, 0, tzinfo=LOCAL_TZ)
        assert convo_meeting._label(dt) == "Monday at 12pm"

    def test_morning_hour_correct(self):
        dt = datetime(2026, 7, 6, 9, 0, tzinfo=LOCAL_TZ)
        assert "9am" in convo_meeting._label(dt)


class TestSlotsLine:
    def test_two_slots_both_named(self):
        line = convo_meeting.slots_line(2, now=FRIDAY, events=[])
        assert "or" in line
        assert "Monday" in line

    def test_one_slot_singular_phrasing(self):
        line = convo_meeting.slots_line(1, now=FRIDAY, events=[])
        assert "I've got" in line
        assert " or " not in line

    def test_no_slots_returns_empty_string(self):
        # block every single business-hours slot in the whole lookahead window
        events = []
        d = datetime(2026, 7, 6, 0, 0, tzinfo=LOCAL_TZ)
        from datetime import timedelta
        for i in range(convo_meeting.LOOKAHEAD_DAYS + 2):
            day = d + timedelta(days=i)
            if day.weekday() < 5:
                for h in range(convo_meeting.BUSINESS_START, convo_meeting.BUSINESS_END):
                    events.append({"when": day.replace(hour=h).isoformat()})
        line = convo_meeting.slots_line(2, now=FRIDAY, events=events)
        assert line == ""

    def test_voice_register_no_em_dash_no_fluff(self):
        line = convo_meeting.slots_line(2, now=FRIDAY, events=[])
        assert "—" not in line and "–" not in line
