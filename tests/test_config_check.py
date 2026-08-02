#!/usr/bin/env python3
"""Unit tests for tools/config_check.py's network-knob range validation (R3#10).

check() is pure given a config dict, so these build the minimal REQUIRED_KEYS
skeleton once and vary only the "network" sub-dict each test cares about.

Run: .venv/bin/python -m pytest tests/test_config_check.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "tools"):
    sys.path.insert(0, str(p))

import config_check  # noqa: E402


def _base_cfg(**overrides) -> dict:
    """Minimal config satisfying every REQUIRED_KEYS entry, so a test's only
    real signal is whatever it puts under "network" (or overrides directly)."""
    cfg = {
        "ntfy_topic": "x", "push_full": True, "auto_approve_min": 0,
        "job_scan_target": 1, "job_daily_apply_cap": 1, "job_apply_batch": 1,
        "job_apply_concurrency": 1, "job_apply_model": "claude-x", "job_auto": True,
        "job_min_yearly": 0, "salary_floor": 0, "job_evening_chain": 0,
        "evening_hour": 19, "content_daily_new": 6, "content_max_fresh": 30,
        "content_stale_days": 14, "money_session": 1, "pair_fit_min": 72,
        "pair_daily_cap": 5, "models": {"default": "claude-x"}, "network": {},
        "cold_daily_enroll": 0, "cold_domains": [], "job_morning_chain": 0,
        "webfix_daily_enroll": 0, "plan": {}, "payment_links": {},
    }
    cfg.update(overrides)
    return cfg


class TestNetworkDailyWeeklyNonNegative:
    def test_negative_daily_cap_is_an_error(self):
        errors = config_check.check(_base_cfg(network={"daily": {"connect": -1}}))
        assert any("daily" in e and "connect" in e and ">= 0" in e for e in errors)

    def test_negative_weekly_cap_is_an_error(self):
        errors = config_check.check(_base_cfg(network={"weekly": {"connect": -5}}))
        assert any("weekly" in e and "-5" in e and ">= 0" in e for e in errors)

    def test_zero_and_positive_caps_are_fine(self):
        errors = config_check.check(_base_cfg(
            network={"daily": {"connect": 0, "comment": 10}, "weekly": {"connect": 100}}))
        assert errors == []


class TestNetworkIntKeysNonNegative:
    def test_negative_daily_action_budget_is_an_error(self):
        errors = config_check.check(_base_cfg(network={"daily_action_budget": -10}))
        assert any("daily_action_budget" in e and ">= 0" in e for e in errors)

    def test_negative_queue_depth_floor_is_an_error(self):
        errors = config_check.check(_base_cfg(network={"queue_depth_floor": -1}))
        assert any("queue_depth_floor" in e and ">= 0" in e for e in errors)

    def test_negative_max_per_company_week_is_an_error(self):
        errors = config_check.check(_base_cfg(network={"max_per_company_week": -3}))
        assert any("max_per_company_week" in e and ">= 0" in e for e in errors)

    def test_positive_values_are_fine(self):
        errors = config_check.check(_base_cfg(network={
            "daily_action_budget": 40, "queue_depth_floor": 10,
            "max_per_company_week": 3, "max_per_niche_week": 10,
            "sourcing_runs_per_week": 2}))
        assert errors == []


class TestSourceMixCommenterPctRange:
    def test_negative_pct_is_an_error(self):
        errors = config_check.check(_base_cfg(network={"source_mix_commenter_pct": -1}))
        assert any("source_mix_commenter_pct" in e and "0..100" in e for e in errors)

    def test_over_100_pct_is_an_error(self):
        errors = config_check.check(_base_cfg(network={"source_mix_commenter_pct": 101}))
        assert any("source_mix_commenter_pct" in e and "0..100" in e for e in errors)

    def test_0_and_100_are_valid_boundaries(self):
        assert config_check.check(_base_cfg(network={"source_mix_commenter_pct": 0})) == []
        assert config_check.check(_base_cfg(network={"source_mix_commenter_pct": 100})) == []

    def test_40_is_valid(self):
        assert config_check.check(_base_cfg(network={"source_mix_commenter_pct": 40})) == []


class TestScoreFloorNonNegative:
    def test_negative_score_floor_is_an_error(self):
        errors = config_check.check(_base_cfg(network={"score_floor": -5}))
        assert any("score_floor" in e and ">= 0" in e for e in errors)

    def test_zero_and_positive_score_floor_fine(self):
        assert config_check.check(_base_cfg(network={"score_floor": 0})) == []
        assert config_check.check(_base_cfg(network={"score_floor": 35})) == []
        assert config_check.check(_base_cfg(network={"score_floor": 35.5})) == []


class TestHoursWindowRange:
    def test_start_above_23_is_an_error(self):
        errors = config_check.check(_base_cfg(network={"hours_window": {"start": 25, "end": 18}}))
        assert any("hours_window" in e and "start" in e and "0..23" in e for e in errors)

    def test_negative_end_is_an_error(self):
        errors = config_check.check(_base_cfg(network={"hours_window": {"start": 8, "end": -1}}))
        assert any("hours_window" in e and "end" in e and "0..23" in e for e in errors)

    def test_valid_boundaries_0_and_23(self):
        errors = config_check.check(_base_cfg(network={"hours_window": {"start": 0, "end": 23}}))
        assert errors == []

    def test_typical_window_is_fine(self):
        errors = config_check.check(_base_cfg(network={"hours_window": {"start": 8, "end": 18}}))
        assert errors == []


class TestExistingNetworkChecksStillWork:
    """Make sure the range-validation additions didn't disturb the pre-existing
    type checks (dict shape, weekend_pause bool, int-not-bool)."""

    def test_bool_still_rejected_as_int(self):
        errors = config_check.check(_base_cfg(network={"daily_action_budget": True}))
        assert any("daily_action_budget" in e and "expected int" in e for e in errors)

    def test_weekend_pause_type_check_unaffected(self):
        errors = config_check.check(_base_cfg(network={"weekend_pause": "yes"}))
        assert any("weekend_pause" in e and "expected bool" in e for e in errors)

    def test_daily_non_dict_still_rejected(self):
        errors = config_check.check(_base_cfg(network={"daily": "not-a-dict"}))
        assert any("network['daily']" in e for e in errors)
