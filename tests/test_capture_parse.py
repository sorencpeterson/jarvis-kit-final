#!/usr/bin/env python3
"""Pytest suite for agents/capture_parse.py's parse_capture() (E329).
Pure function, no store I/O, no network — every test pins `now` explicitly
so results never depend on the wall-clock date this suite happens to run on.

Run: .venv/bin/python -m pytest tests/test_capture_parse.py -v
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "agents"):
    sys.path.insert(0, str(p))

import capture_parse  # noqa: E402
from store_lib import LOCAL_TZ  # noqa: E402

# Fixed reference "now": Wednesday 2026-07-01, 10:17am local.
NOW = datetime(2026, 7, 1, 10, 17, 0, tzinfo=LOCAL_TZ)


class TestMissionExample:
    def test_call_bob_tue_p1_warm(self):
        out = capture_parse.parse_capture("call Bob tue p1 @warm", now=NOW)
        assert out["text"] == "call Bob"
        assert out["priority"] == 1
        assert out["project"] == "warm"
        assert out["scheduled_time"] is not None
        assert out["scheduled_time"].startswith("2026-07-07")  # next Tuesday from Wed 7/1


class TestProjectTag:
    def test_extracts_project(self):
        out = capture_parse.parse_capture("email the client @outreach", now=NOW)
        assert out["project"] == "outreach"
        assert "@outreach" not in out["text"]

    def test_lowercases_project(self):
        out = capture_parse.parse_capture("thing @WARM", now=NOW)
        assert out["project"] == "warm"

    def test_no_project_tag_is_none(self):
        out = capture_parse.parse_capture("no tag here", now=NOW)
        assert out["project"] is None


class TestPriority:
    def test_p1(self):
        assert capture_parse.parse_capture("thing p1", now=NOW)["priority"] == 1

    def test_p2(self):
        assert capture_parse.parse_capture("thing p2", now=NOW)["priority"] == 2

    def test_p3(self):
        assert capture_parse.parse_capture("thing p3", now=NOW)["priority"] == 3

    def test_case_insensitive(self):
        assert capture_parse.parse_capture("thing P1", now=NOW)["priority"] == 1

    def test_p4_not_matched(self):
        # p4 isn't a valid priority; should NOT match and should NOT be stripped
        out = capture_parse.parse_capture("thing p4", now=NOW)
        assert out["priority"] is None
        assert "p4" in out["text"]

    def test_no_priority_is_none(self):
        assert capture_parse.parse_capture("thing with no marker", now=NOW)["priority"] is None

    def test_strips_marker_from_text(self):
        out = capture_parse.parse_capture("call Bob p1", now=NOW)
        assert out["text"] == "call Bob"


class TestWeekday:
    def test_bare_weekday_full_name(self):
        out = capture_parse.parse_capture("review deck friday", now=NOW)
        assert out["scheduled_time"].startswith("2026-07-03")  # first Friday on/after Wed 7/1
        assert out["text"] == "review deck"

    def test_bare_weekday_abbreviation(self):
        out = capture_parse.parse_capture("call Bob tue", now=NOW)
        assert out["scheduled_time"].startswith("2026-07-07")

    def test_weekday_said_on_itself_means_next_week(self):
        # NOW is a Wednesday; "wed" said on Wednesday should mean NEXT Wednesday
        out = capture_parse.parse_capture("thing wed", now=NOW)
        assert out["scheduled_time"].startswith("2026-07-08")

    def test_sets_default_hour(self):
        out = capture_parse.parse_capture("thing friday", now=NOW)
        assert "T09:00:00" in out["scheduled_time"]

    def test_sets_default_duration(self):
        out = capture_parse.parse_capture("thing friday", now=NOW)
        assert out["duration_min"] == 30

    def test_abbreviation_does_not_eat_longer_word(self):
        # "tues" should match as a whole weekday, not leave a stray "s" behind
        out = capture_parse.parse_capture("call Bob tues", now=NOW)
        assert out["text"] == "call Bob"


class TestTomorrow:
    def test_tomorrow_resolves(self):
        out = capture_parse.parse_capture("follow up tomorrow", now=NOW)
        assert out["scheduled_time"].startswith("2026-07-02")
        assert out["text"] == "follow up"

    def test_tomorrow_wins_over_weekday_match(self):
        # "tomorrow" itself contains no weekday substring, but confirm the
        # date-marker branch order doesn't double-process when both patterns
        # could theoretically be present in adjacent words
        out = capture_parse.parse_capture("tomorrow morning call", now=NOW)
        assert out["scheduled_time"].startswith("2026-07-02")


class TestToday:
    def test_today_before_default_hour_uses_default_hour(self):
        # NOW is 10:17am, DEFAULT_HOUR is 9am (already passed) -> +1h from now
        out = capture_parse.parse_capture("wrap this up today", now=NOW)
        assert out["scheduled_time"].startswith("2026-07-01T11:17")

    def test_today_early_morning_uses_default_hour(self):
        early = NOW.replace(hour=6, minute=0)
        out = capture_parse.parse_capture("thing today", now=early)
        assert out["scheduled_time"].startswith("2026-07-01T09:00")


class TestPlainText:
    def test_no_markers_passes_through_unchanged(self):
        out = capture_parse.parse_capture("just a plain thought with no markers", now=NOW)
        assert out["text"] == "just a plain thought with no markers"
        assert out["project"] is None
        assert out["priority"] is None
        assert out["scheduled_time"] is None
        assert out["duration_min"] is None

    def test_empty_string(self):
        out = capture_parse.parse_capture("", now=NOW)
        assert out["text"] == ""

    def test_double_space_cleanup_after_stripping(self):
        out = capture_parse.parse_capture("call Bob  tue  p1  @warm", now=NOW)
        assert "  " not in out["text"]
        assert out["text"] == "call Bob"


class TestCombinedMarkers:
    def test_all_three_markers_together(self):
        out = capture_parse.parse_capture("p2 review the deck friday @systems", now=NOW)
        assert out["text"] == "review the deck"
        assert out["priority"] == 2
        assert out["project"] == "systems"
        assert out["scheduled_time"].startswith("2026-07-03")

    def test_markers_in_different_order(self):
        out = capture_parse.parse_capture("@systems friday p2 review the deck", now=NOW)
        assert out["text"] == "review the deck"
        assert out["priority"] == 2
        assert out["project"] == "systems"
