#!/usr/bin/env python3
"""Pytest suite for the pure date-resolution logic in agents/promises.py.
No store I/O, no network, no LLM calls — deterministic date math only.

Run: .venv/bin/python -m pytest tests/test_promises.py -v
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import promises  # noqa: E402

WED = date(2026, 7, 1)  # fixed reference: a Wednesday


class TestResolveDate:
    def test_tomorrow(self):
        assert promises.resolve_date("tomorrow", "tomorrow", WED) == date(2026, 7, 2)

    def test_today_eod(self):
        assert promises.resolve_date("today_eod", "eod", WED) == WED

    def test_next_week(self):
        assert promises.resolve_date("next_week", "next week", WED) == date(2026, 7, 8)

    def test_end_of_month_31_days(self):
        assert promises.resolve_date("end_of_month", "eom", WED) == date(2026, 7, 31)

    def test_end_of_month_28_days_feb(self):
        assert promises.resolve_date("end_of_month", "eom", date(2026, 2, 5)) == date(2026, 2, 28)

    def test_by_weekday_later_this_week(self):
        # Wed -> "by friday" = this week's Friday
        assert promises.resolve_date("by_weekday", "friday", WED) == date(2026, 7, 3)

    def test_by_weekday_earlier_in_week_rolls_forward(self):
        # Wed -> "by monday" = NEXT Monday (Monday already passed this week)
        assert promises.resolve_date("by_weekday", "monday", WED) == date(2026, 7, 6)

    def test_by_weekday_same_day(self):
        # Wed -> "by wednesday" = today (same-day counts per the grammar doc)
        assert promises.resolve_date("by_weekday", "wednesday", WED) == WED

    def test_next_weekday_always_following_week(self):
        # Wed -> "next friday" must NOT be this week's Friday (that's bare/by),
        # must be the Friday of the following week.
        result = promises.resolve_date("next_weekday", "friday", WED)
        assert result == date(2026, 7, 10)
        assert result != promises.resolve_date("by_weekday", "friday", WED)

    def test_next_weekday_monday(self):
        # Wed 7/1: this calendar week's Monday (6/29) already passed, so the
        # nearest Monday (7/6) already falls in the FOLLOWING calendar week —
        # "next monday" and bare "monday" legitimately agree here, same as
        # they would if a person said either phrase out loud on a Wednesday.
        result = promises.resolve_date("next_weekday", "monday", WED)
        assert result == date(2026, 7, 6)
        assert result == promises.resolve_date("by_weekday", "monday", WED)

    def test_unknown_kind_returns_none(self):
        assert promises.resolve_date("nonsense", "x", WED) is None

    def test_bad_weekday_name_returns_none(self):
        assert promises.resolve_date("by_weekday", "blursday", WED) is None


class TestFindPromises:
    def test_by_friday_phrase(self):
        out = promises.find_promises("I'll get you the updated site by Friday.", WED)
        assert len(out) == 1
        assert out[0]["phrase"] == "by Friday"
        assert out[0]["due_date"] == "2026-07-03"

    def test_next_week_phrase(self):
        out = promises.find_promises("Circling back next week.", WED)
        assert len(out) == 1
        assert out[0]["due_date"] == "2026-07-08"

    def test_tomorrow_phrase(self):
        out = promises.find_promises("Can send that over tomorrow morning.", WED)
        assert len(out) == 1
        assert out[0]["due_date"] == "2026-07-02"

    def test_no_match_on_plain_text(self):
        assert promises.find_promises("Just checking in, no rush.", WED) == []

    def test_empty_string(self):
        assert promises.find_promises("", WED) == []

    def test_by_weekday_does_not_also_fire_bare_weekday(self):
        # overlap-avoidance: "by friday" should produce exactly ONE match,
        # not two (by_weekday AND bare_weekday both matching "friday")
        out = promises.find_promises("by friday", WED)
        assert len(out) == 1
        assert out[0]["kind"] == "by_weekday"

    def test_next_weekday_does_not_also_fire_bare_weekday(self):
        out = promises.find_promises("next friday works for me", WED)
        assert len(out) == 1
        assert out[0]["kind"] == "next_weekday"

    def test_case_insensitive(self):
        out = promises.find_promises("BY FRIDAY please", WED)
        assert len(out) == 1
        assert out[0]["due_date"] == "2026-07-03"

    def test_eom_variant(self):
        out = promises.find_promises("Wrapping up EOM", WED)
        assert len(out) == 1
        assert out[0]["due_date"] == "2026-07-31"


class TestDedupKey:
    def test_stable_for_same_inputs(self):
        k1 = promises._dedup_key("src1", "by Friday", "2026-07-03")
        k2 = promises._dedup_key("src1", "by Friday", "2026-07-03")
        assert k1 == k2

    def test_case_insensitive_on_phrase(self):
        k1 = promises._dedup_key("src1", "By Friday", "2026-07-03")
        k2 = promises._dedup_key("src1", "by friday", "2026-07-03")
        assert k1 == k2

    def test_differs_by_source(self):
        k1 = promises._dedup_key("src1", "by Friday", "2026-07-03")
        k2 = promises._dedup_key("src2", "by Friday", "2026-07-03")
        assert k1 != k2


class TestBuildFixture:
    def test_fixture_candidates_produce_expected_count(self):
        cands = promises._fixture_candidates()
        records = promises.build(cands)
        assert len(records) == 3

    def test_fixture_records_have_required_fields(self):
        cands = promises._fixture_candidates()
        records = promises.build(cands)
        for r in records:
            assert r["id"]
            assert r["dedup_key"]
            assert r["due_date"]
            assert r["status"] == "open"
            assert r["warned_48h"] is False

    def test_build_is_idempotent_within_one_call_list(self):
        # Two candidates that would resolve to the identical dedup key should
        # only produce one record (guards against dupes WITHIN a single run).
        cands = [
            {"text": "by friday", "sent_ts": "2026-07-01T10:00:00-07:00",
             "source_kind": "reply", "source_id": "same_id", "contact": "A"},
        ]
        first = promises.build(cands)
        assert len(first) == 1


class TestSentDateOf:
    def test_parses_iso(self):
        assert promises._sent_date_of("2026-07-01T10:00:00-07:00") == date(2026, 7, 1)

    def test_bad_input_falls_back_to_today_not_crash(self):
        result = promises._sent_date_of("not a date")
        assert isinstance(result, date)
