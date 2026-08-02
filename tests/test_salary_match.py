#!/usr/bin/env python3
"""Salary-to-posting matcher (2026-07-07, Alex's "match my ask to the job, max
opportunities" request). Pins jobs.salary_target: ask near the bottom of the posted
band, never above their max, respect an optional floor, and stay flexible when no
pay is posted. Also pins that the pool floor loosened so low-paying jobs aren't dropped.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import jobs  # noqa: E402


class TestSalaryTarget:
    def test_asks_bottom_of_posted_band(self):
        n, note = jobs.salary_target({"comp_min": 90000, "comp_max": 130000}, floor=0)
        assert n == 90000                       # bottom of THEIR band, most competitive
        assert "90k" in note and "130k" in note

    def test_never_exceeds_their_max_even_with_high_floor(self):
        # his floor is above the whole band -> ask their ceiling, never over it
        n, _ = jobs.salary_target({"comp_min": 60000, "comp_max": 80000}, floor=150000)
        assert n == 80000

    def test_reversed_band_never_states_above_true_ceiling(self):
        # 2026-07-13 hunt: a scraped/malformed record with min > max used to make the operator
        # state the (larger) 150k. After the sort it asks the lower value, which is safe under
        # either interpretation of the malformed band (90k <= both posted numbers).
        n, note = jobs.salary_target({"comp_min": 150000, "comp_max": 90000}, floor=0)
        assert n == 90000                         # asks the lower of the two, never the 150k
        assert n <= 90000 and n <= 150000         # never above either posted number
        assert "state $90k" in note

    def test_comp_display_sorts_reversed_band(self):
        # _comp (ingestion-time) also normalizes so the stored comp_min <= comp_max
        disp, lo, hi, unit = jobs._comp({"yearly_min_compensation": 150000, "yearly_max_compensation": 90000})
        assert lo == 90000 and hi == 150000 and disp == "$90k-$150k" and unit == "year"

    def test_floor_lifts_within_band(self):
        n, _ = jobs.salary_target({"comp_min": 60000, "comp_max": 120000}, floor=90000)
        assert n == 90000                       # respect the floor since it fits under max

    def test_only_ceiling_posted_asks_just_under(self):
        n, _ = jobs.salary_target({"comp_max": 100000}, floor=0)
        assert n == 90000 and n <= 100000       # 90% of ceiling, never above it

    def test_nothing_posted_is_open_not_a_high_anchor(self):
        n, note = jobs.salary_target({}, floor=0)
        assert n == 0
        assert "open" in note.lower() or "negotiable" in note.lower()
        assert "135" not in note                # the old high anchor is gone

    def test_nothing_posted_with_floor_states_floor(self):
        n, note = jobs.salary_target({}, floor=70000)
        assert n == 70000 and "70k" in note


class TestHourlyCompUnitPreserved:
    """R2-12 (2026-07-13 hunt): hourly comp must never be silently annualized at 2080h and
    treated as if it were a salary -- that made a $50/hr part-time job read as $104k, pass
    the annual floor, and get told to the operator as a fake $104k number on an hourly field."""

    def test_comp_keeps_native_hourly_numbers_and_unit(self):
        disp, lo, hi, unit = jobs._comp({"hourly_min_compensation": 50, "hourly_max_compensation": 60})
        assert unit == "hour"
        assert lo == 50 and hi == 60          # NOT 104000/124800
        assert disp == "$50-60/hr"

    def test_salary_target_states_hourly_not_annualized(self):
        n, note = jobs.salary_target({"comp_min": 50, "comp_max": 60, "comp_unit": "hour"}, floor=0)
        assert n == 50
        assert "$50/hr" in note and "104" not in note and "124" not in note

    def test_annual_floor_does_not_inflate_an_hourly_ask(self):
        # an annual salary_floor config value must never get mixed into a raw hourly number
        n, note = jobs.salary_target({"comp_min": 50, "comp_max": 60, "comp_unit": "hour"}, floor=95000)
        assert n <= 60
        assert "95" not in note

    def test_hourly_job_never_dropped_by_the_annual_floor_filter(self):
        j = {"id": "hr1", "is_us": True, "title": "Marketing Manager", "apply_url": "u",
             "company": "HourlyCo", "posted": None, "comp_max": 60, "comp_unit": "hour"}
        # 60 (a raw hourly rate) must not be compared against a 40000 annual floor as if it
        # were an annual number
        assert jobs._passes_filters(j, 40000, set(), set(), set()) is True

    def test_missing_comp_unit_still_defaults_to_year_for_old_records(self):
        # pre-fix records on disk have no comp_unit key at all -- must behave exactly as
        # before (treated as a real annual number), not silently exempted from the floor
        j = {"id": "old1", "is_us": True, "title": "Marketing Manager", "apply_url": "u",
             "company": "OldCo", "posted": None, "comp_max": 30000}
        assert jobs._passes_filters(j, 40000, set(), set(), set()) is False

    def test_fit_skips_the_comp_bonus_entirely_for_hourly_unit(self):
        # a raw hourly rate must not be plugged into the annual comp-bonus formula (which
        # would read a low hourly number as a catastrophic "salary" and tank the score)
        hourly = jobs._fit({"title": "Marketing Manager", "comp_max": 60, "comp_unit": "hour",
                             "posted": None})
        neutral = jobs._fit({"title": "Marketing Manager", "posted": None})
        assert hourly == neutral


class TestPoolFloorLoosened:
    def test_default_pool_floor_dropped_from_95k(self):
        # the pool floor that was silently dropping sub-95k jobs is now low (Alex's ask)
        assert jobs._min_yearly() <= 40000

    def test_low_paying_job_with_posted_comp_is_not_auto_dropped_by_floor(self):
        # a $55k posting used to be culled by the 95k floor; now it clears the salary check
        floor = jobs._min_yearly()
        assert 55000 >= floor or floor == 0


class TestNoFixedAnchorLeaks:
    def test_profile_and_bank_no_longer_assert_fixed_salary(self):
        import json
        prof = json.loads((ROOT / "store" / "application_profile.json").read_text())
        assert "135" not in str(prof.get("salary_expectation", ""))
        bank = json.loads((ROOT / "store" / "answer_bank.json").read_text())
        qa = bank if isinstance(bank, list) else bank.get("qa", [])
        for item in qa:
            if "salar" in str(item.get("q", "")).lower():
                assert "135" not in str(item.get("a", "")), item
