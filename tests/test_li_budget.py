#!/usr/bin/env python3
"""Unit tests for agents/li_budget.py (A7 score floor via config, A8 diversity
guard, A44 queue-depth alert, A54 daily action budget, A55 hours window,
A56 weekend pause). All tests monkeypatch networking.QUEUE and planner._config
to isolated fixtures — never the real store.

Run: .venv/bin/python -m pytest tests/test_li_budget.py -v
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import networking  # noqa: E402
import planner  # noqa: E402
import li_budget  # noqa: E402


@pytest.fixture
def fake_queue(tmp_path, monkeypatch):
    q = tmp_path / "network.jsonl"
    monkeypatch.setattr(networking, "QUEUE", q)
    return q


def _write(path: Path, records: list[dict]):
    with path.open("a") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _rec(**kw) -> dict:
    base = {"id": "id1", "kind": "connect", "author": "Test Person",
            "target": "headline", "url": "https://linkedin.com/in/test",
            "draft": "", "status": "pending", "created": "2026-06-01T08:00:00-07:00"}
    base.update(kw)
    return base


@pytest.fixture
def net_config(monkeypatch):
    """Monkeypatch planner._config() to return a controlled network config,
    so tests don't depend on the real store/config.json's current values."""
    def _make(overrides: dict | None = None):
        cfg = {"network": dict(li_budget.DEFAULTS)}
        if overrides:
            cfg["network"].update(overrides)
        monkeypatch.setattr(planner, "_config", lambda: cfg)
        return cfg
    return _make


class TestHoursWindow:
    def test_inside_window(self, net_config):
        net_config({"hours_window": {"start": 8, "end": 18}})
        noon = datetime(2026, 7, 6, 12, 0)  # a Monday, noon
        assert li_budget.in_hours_window(noon)

    def test_before_window(self, net_config):
        net_config({"hours_window": {"start": 8, "end": 18}})
        early = datetime(2026, 7, 6, 6, 0)
        assert not li_budget.in_hours_window(early)

    def test_after_window(self, net_config):
        net_config({"hours_window": {"start": 8, "end": 18}})
        late = datetime(2026, 7, 6, 20, 0)
        assert not li_budget.in_hours_window(late)

    def test_exact_start_hour_inside(self, net_config):
        net_config({"hours_window": {"start": 8, "end": 18}})
        exact = datetime(2026, 7, 6, 8, 0)
        assert li_budget.in_hours_window(exact)

    def test_exact_end_hour_outside(self, net_config):
        net_config({"hours_window": {"start": 8, "end": 18}})
        exact = datetime(2026, 7, 6, 18, 0)
        assert not li_budget.in_hours_window(exact)


class TestWeekendPause:
    def test_saturday_is_weekend(self):
        sat = datetime(2026, 7, 4, 12, 0)  # a Saturday
        assert li_budget.is_weekend(sat)

    def test_sunday_is_weekend(self):
        sun = datetime(2026, 7, 5, 12, 0)  # a Sunday
        assert li_budget.is_weekend(sun)

    def test_monday_not_weekend(self):
        mon = datetime(2026, 7, 6, 12, 0)  # a Monday
        assert not li_budget.is_weekend(mon)

    def test_weekend_paused_when_enabled(self, net_config):
        net_config({"weekend_pause": True})
        sat = datetime(2026, 7, 4, 12, 0)
        assert li_budget.weekend_paused(sat)

    def test_weekend_not_paused_when_disabled(self, net_config):
        net_config({"weekend_pause": False})
        sat = datetime(2026, 7, 4, 12, 0)
        assert not li_budget.weekend_paused(sat)

    def test_weekday_never_paused(self, net_config):
        net_config({"weekend_pause": True})
        mon = datetime(2026, 7, 6, 12, 0)
        assert not li_budget.weekend_paused(mon)


class TestDailyActionBudget:
    def test_no_actions_full_budget(self, fake_queue, net_config):
        net_config({"daily_action_budget": 40})
        assert li_budget.budget_remaining_today() == 40

    def test_some_actions_taken_reduces_budget(self, fake_queue, net_config):
        net_config({"daily_action_budget": 40})
        from store_lib import now_iso
        today = now_iso()
        _write(fake_queue, [
            _rec(id="a1", kind="connect", status="done", acted_at=today),
            _rec(id="a2", kind="comment", status="done", acted_at=today),
        ])
        assert li_budget.budget_remaining_today() == 38

    def test_zero_budget_means_unlimited(self, fake_queue, net_config):
        net_config({"daily_action_budget": 0})
        assert li_budget.budget_remaining_today() >= 999999

    def test_budget_never_negative(self, fake_queue, net_config):
        net_config({"daily_action_budget": 2})
        from store_lib import now_iso
        today = now_iso()
        _write(fake_queue, [_rec(id=f"a{i}", kind="like", status="done", acted_at=today)
                             for i in range(5)])
        assert li_budget.budget_remaining_today() == 0


class TestQueueDepthAlert:
    def test_healthy_depth_no_alert(self, fake_queue, net_config):
        net_config({"queue_depth_floor": 5})
        _write(fake_queue, [_rec(id=f"a{i}", status="pending") for i in range(10)])
        result = li_budget.check_queue_depth_alert(notify=False)
        assert not result["low"]
        assert result["depth"] == 10

    def test_low_depth_flags_alert(self, fake_queue, net_config):
        net_config({"queue_depth_floor": 20})
        _write(fake_queue, [_rec(id=f"a{i}", status="pending") for i in range(3)])
        result = li_budget.check_queue_depth_alert(notify=False)
        assert result["low"]
        assert result["depth"] == 3

    def test_notify_false_never_pushes(self, fake_queue, net_config, monkeypatch):
        net_config({"queue_depth_floor": 20})
        pushed = {"called": False}
        monkeypatch.setattr(planner, "notify", lambda *a, **k: pushed.update(called=True))
        li_budget.check_queue_depth_alert(notify=False)
        assert pushed["called"] is False

    def test_zero_floor_disables_alert(self, fake_queue, net_config):
        net_config({"queue_depth_floor": 0})
        result = li_budget.check_queue_depth_alert(notify=False)
        assert not result["low"]


class TestDiversityGuard:
    def test_unknown_company_always_ok(self, net_config):
        net_config({"max_per_company_week": 3})
        assert li_budget.diversity_ok_for_company("")

    def test_under_limit_ok(self, net_config):
        net_config({"max_per_company_week": 3})
        from collections import Counter
        counts = Counter({"nimbusrp": 1})
        assert li_budget.diversity_ok_for_company("Nimbusrp", counts)

    def test_at_limit_blocked(self, net_config):
        net_config({"max_per_company_week": 3})
        from collections import Counter
        counts = Counter({"nimbusrp": 3})
        assert not li_budget.diversity_ok_for_company("Nimbusrp", counts)

    def test_zero_limit_disables_guard(self, net_config):
        net_config({"max_per_company_week": 0})
        from collections import Counter
        counts = Counter({"nimbusrp": 999})
        assert li_budget.diversity_ok_for_company("Nimbusrp", counts)


class TestFilterDiversity:
    def test_drops_targets_past_company_cap(self, fake_queue, net_config):
        net_config({"max_per_company_week": 2})
        targets = [
            {"headline": "Founder @ Nimbusrp", "target": ""},
            {"headline": "Owner @ Nimbusrp", "target": ""},
            {"headline": "CEO @ Nimbusrp", "target": ""},  # 3rd, over cap
            {"headline": "Founder @ Other Inc", "target": ""},
        ]
        out = li_budget.filter_diversity(targets)
        nimbusunt = sum(1 for t in out if "Nimbusrp" in t["headline"])
        assert nimbusunt == 2
        assert any("Other Inc" in t["headline"] for t in out)

    def test_no_limit_keeps_everything(self, fake_queue, net_config):
        net_config({"max_per_company_week": 0})
        targets = [{"headline": "Founder @ Nimbusrp", "target": ""} for _ in range(10)]
        out = li_budget.filter_diversity(targets)
        assert len(out) == 10


class TestReleaseGate:
    def test_weekend_blocks_everything(self, fake_queue, net_config, monkeypatch):
        net_config({"weekend_pause": True})
        monkeypatch.setattr(li_budget, "weekend_paused", lambda now=None: True)
        result = li_budget.gate([{"id": "a1"}, {"id": "a2"}])
        assert result == []

    def test_outside_hours_blocks_everything(self, fake_queue, net_config, monkeypatch):
        monkeypatch.setattr(li_budget, "weekend_paused", lambda now=None: False)
        monkeypatch.setattr(li_budget, "in_hours_window", lambda now=None: False)
        result = li_budget.gate([{"id": "a1"}])
        assert result == []

    def test_budget_trims_candidates(self, fake_queue, net_config, monkeypatch):
        monkeypatch.setattr(li_budget, "weekend_paused", lambda now=None: False)
        monkeypatch.setattr(li_budget, "in_hours_window", lambda now=None: True)
        monkeypatch.setattr(li_budget, "budget_remaining_today", lambda: 2)
        candidates = [{"id": f"a{i}"} for i in range(5)]
        result = li_budget.gate(candidates)
        assert len(result) == 2
        assert result == candidates[:2]  # order-preserving

    def test_empty_candidates_returns_empty(self, fake_queue, net_config, monkeypatch):
        monkeypatch.setattr(li_budget, "weekend_paused", lambda now=None: False)
        monkeypatch.setattr(li_budget, "in_hours_window", lambda now=None: True)
        assert li_budget.gate([]) == []


class TestWeekStart:
    def test_monday_returns_itself(self):
        mon = datetime(2026, 7, 6, 15, 30)  # a Monday
        assert li_budget._week_start(mon) == "2026-07-06"

    def test_sunday_returns_prior_monday(self):
        sun = datetime(2026, 7, 12, 15, 30)  # a Sunday
        assert li_budget._week_start(sun) == "2026-07-06"

    def test_friday_returns_that_weeks_monday(self):
        fri = datetime(2026, 7, 10, 9, 0)  # a Friday
        assert li_budget._week_start(fri) == "2026-07-06"
