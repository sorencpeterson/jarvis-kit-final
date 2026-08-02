#!/usr/bin/env python3
"""Pytest suite for agents/interruption_log.py's pure gating logic
(check_one_todo) and the weekly-pattern report assembly. No store I/O for
check_one_todo tests (all args passed in-memory); build_weekly_pattern is
exercised via monkeypatched INTERRUPTIONS path.

Run: .venv/bin/python -m pytest tests/test_interruption_log.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import interruption_log as il  # noqa: E402

TODAY = "2026-07-03"
PLAN_IDS = {"tdo_planned_1", "tdo_planned_2"}


def _todo(**overrides) -> dict:
    base = {"id": "tdo_new", "source": "manual",
            "created": "2026-07-03T14:00:00+02:00", "text": "something"}
    base.update(overrides)
    return base



def _at_local_hour(hour: int, day: str = "2026-07-03") -> str:
    """An ISO timestamp at `hour` LOCAL time.

    These tests assert on work-hour windows, which the agent evaluates after
    converting to LOCAL_TZ. Hard-coding a fixed UTC offset made them pass only
    on a machine in that zone (they broke the moment the clock moved from
    +02:00 to -04:00). Build the instant in the local zone instead.
    """
    from datetime import datetime
    from store_lib import LOCAL_TZ
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, hour, 0, 0, tzinfo=LOCAL_TZ).isoformat()


class TestCheckOneTodo:
    def test_manual_workhour_not_in_plan_is_interruption(self):
        assert il.check_one_todo(_todo(), PLAN_IDS, today=TODAY) is True

    def test_siri_source_also_counts(self):
        assert il.check_one_todo(_todo(source="siri"), PLAN_IDS, today=TODAY) is True

    def test_agent_source_is_not_interruption(self):
        assert il.check_one_todo(_todo(source="agent"), PLAN_IDS, today=TODAY) is False

    def test_in_plan_is_not_interruption(self):
        assert il.check_one_todo(_todo(id="tdo_planned_1"), PLAN_IDS, today=TODAY) is False

    def test_early_morning_is_not_interruption(self):
        assert il.check_one_todo(_todo(created=_at_local_hour(2, "2026-07-03")), PLAN_IDS, today=TODAY) is False

    def test_late_night_is_not_interruption(self):
        assert il.check_one_todo(_todo(created=_at_local_hour(23, "2026-07-03")), PLAN_IDS, today=TODAY) is False

    def test_exactly_work_start_hour_counts(self):
        assert il.check_one_todo(_todo(created=_at_local_hour(8, "2026-07-03")), PLAN_IDS, today=TODAY) is True

    def test_exactly_work_end_hour_excluded(self):
        # WORK_HOUR_END is exclusive (19:00 itself is outside the window)
        assert il.check_one_todo(_todo(created=_at_local_hour(19, "2026-07-03")), PLAN_IDS, today=TODAY) is False

    def test_yesterday_is_not_interruption(self):
        assert il.check_one_todo(_todo(created=_at_local_hour(14, "2026-07-02")), PLAN_IDS, today=TODAY) is False

    def test_tomorrow_is_not_interruption(self):
        assert il.check_one_todo(_todo(created=_at_local_hour(14, "2026-07-04")), PLAN_IDS, today=TODAY) is False

    def test_bad_timestamp_is_not_interruption(self):
        assert il.check_one_todo(_todo(created="not a timestamp"), PLAN_IDS, today=TODAY) is False

    def test_empty_plan_ids_still_gates_correctly(self):
        assert il.check_one_todo(_todo(), set(), today=TODAY) is True


class TestTodayPlanIds:
    def test_returns_ids_when_plan_is_todays(self, tmp_path, monkeypatch):
        # _today_plan_ids() compares the plan's "date" field against the REAL
        # current date (it has no injectable "today" parameter, unlike
        # check_one_todo), so this test writes a plan dated with today's
        # actual date rather than the fixed TODAY constant used elsewhere.
        import store_lib
        from datetime import datetime
        real_today = datetime.now(store_lib.LOCAL_TZ).strftime("%Y-%m-%d")
        p = tmp_path / "plan.json"
        p.write_text(json.dumps({"date": real_today, "actions": [{"id": "a1"}, {"id": "a2"}]}))
        monkeypatch.setattr(il, "PLAN", p)
        assert il._today_plan_ids() == {"a1", "a2"}

    def test_stale_plan_date_returns_empty(self, tmp_path, monkeypatch):
        p = tmp_path / "plan.json"
        p.write_text(json.dumps({"date": "2020-01-01", "actions": [{"id": "a1"}]}))
        monkeypatch.setattr(il, "PLAN", p)
        assert il._today_plan_ids() == set()

    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(il, "PLAN", tmp_path / "nonexistent.json")
        assert il._today_plan_ids() == set()

    def test_malformed_json_returns_empty(self, tmp_path, monkeypatch):
        p = tmp_path / "plan.json"
        p.write_text("not json{{{")
        monkeypatch.setattr(il, "PLAN", p)
        assert il._today_plan_ids() == set()


class TestBuildWeeklyPattern:
    def test_no_interruptions_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr(il, "INTERRUPTIONS", tmp_path / "nonexistent.jsonl")
        report = il.build_weekly_pattern()
        assert "No interruptions logged" in report

    def test_counts_by_day(self, tmp_path, monkeypatch):
        from datetime import datetime
        p = tmp_path / "interruptions.jsonl"
        now = datetime.now().astimezone()
        recent = now.strftime("%Y-%m-%dT12:00:00%z")
        p.write_text(
            json.dumps({"todo_id": "t1", "text": "a", "source": "manual", "created": recent}) + "\n" +
            json.dumps({"todo_id": "t2", "text": "b", "source": "manual", "created": recent}) + "\n"
        )
        monkeypatch.setattr(il, "INTERRUPTIONS", p)
        report = il.build_weekly_pattern()
        assert "2 interruption(s)" in report

    def test_excludes_records_older_than_7_days(self, tmp_path, monkeypatch):
        p = tmp_path / "interruptions.jsonl"
        p.write_text(json.dumps({"todo_id": "old", "text": "a", "source": "manual",
                                 "created": "2020-01-01T12:00:00+00:00"}) + "\n")
        monkeypatch.setattr(il, "INTERRUPTIONS", p)
        report = il.build_weekly_pattern()
        assert "No interruptions logged" in report
