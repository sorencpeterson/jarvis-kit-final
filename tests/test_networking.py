#!/usr/bin/env python3
"""Unit tests for agents/networking.py's claim/release path.

R2-29: stale-claim recovery must not re-approve an action that may already have
completed, and must not double-insert the record into the approval list (both
shapes of the same bug: an unattended run could post the same LinkedIn comment
twice).
R2-32: the weekly connect count must include 'running' claims (like the daily
count already does), so two overlapping approved_to_run() pulls can't both
reserve the same weekly slots and overshoot the cap.
R2-33: the hours/weekend/daily-budget guard (li_budget) must actually gate
approved_to_run(), not rely on every caller remembering to compose it.

All tests monkeypatch networking.QUEUE to an isolated tmp_path file — never the
real store — and default the li_budget gate to OPEN + planner config to empty
so only the thing each test cares about varies.

Run: .venv/bin/python -m pytest tests/test_networking.py -v
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import networking  # noqa: E402
import li_budget  # noqa: E402
import planner  # noqa: E402
from store_lib import now_iso  # noqa: E402


@pytest.fixture
def fake_queue(tmp_path, monkeypatch):
    q = tmp_path / "network.jsonl"
    monkeypatch.setattr(networking, "QUEUE", q)
    return q


@pytest.fixture(autouse=True)
def _isolated_defaults(monkeypatch):
    """Baseline for every test: empty network config (so _net_caps() falls back
    to its documented defaults instead of whatever the real store/config.json
    happens to have) and an OPEN li_budget gate (in-hours, non-weekend,
    unlimited daily budget). Tests that care about a specific value override it
    themselves."""
    monkeypatch.setattr(planner, "_config", lambda: {})
    monkeypatch.setattr(li_budget, "weekend_paused", lambda now=None: False)
    monkeypatch.setattr(li_budget, "in_hours_window", lambda now=None: True)
    monkeypatch.setattr(li_budget, "budget_remaining_today", lambda: 10 ** 6)


def _write(path: Path, records: list[dict]):
    with path.open("a") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _rec(**kw) -> dict:
    base = {"id": "id1", "kind": "connect", "author": "Test Person",
            "target": "headline", "url": "https://linkedin.com/in/test",
            "draft": "", "status": "pending", "created": "2026-07-01T08:00:00-07:00"}
    base.update(kw)
    return base


def _stale_claimed_at() -> str:
    return (datetime.now().astimezone() - timedelta(hours=3)).isoformat()


class TestStaleClaimRecovery:
    def test_stale_running_item_reverts_to_pending_not_approved(self, fake_queue):
        _write(fake_queue, [_rec(id="a1", status="running", claimed_at=_stale_claimed_at())])
        out = networking.approved_to_run()
        assert out == []  # not fed back into this round -- needs a fresh human tap
        latest = next(x for x in networking.load_queue() if x["id"] == "a1")
        assert latest["status"] == "pending"

    def test_stale_revert_is_not_returned_twice(self, fake_queue):
        # the concrete double-post shape: before the fix, one stale item could appear
        # TWICE in a single approved_to_run() call -- once via the q-filter (the
        # in-place mutation to "approved" made it match the filter) and once via the
        # literal "+ stale" -- so a caller iterating the result would act on it twice.
        _write(fake_queue, [_rec(id="a1", kind="like", status="running", claimed_at=_stale_claimed_at())])
        out = networking.approved_to_run()
        assert [x["id"] for x in out].count("a1") == 0
        assert out == []

    def test_stale_revert_is_still_written_even_if_nothing_else_runs(self, fake_queue):
        # preserves the 2026-07-11 fix this sits on top of: the revert must be WRITTEN
        # regardless of whether the item is picked this round, or it stays 'running'
        # forever and never reappears anywhere for review.
        _write(fake_queue, [_rec(id="a1", status="running", claimed_at=_stale_claimed_at())])
        networking.approved_to_run()
        lines = fake_queue.read_text().splitlines()
        last = json.loads(lines[-1])
        assert last["id"] == "a1" and last["status"] == "pending"

    def test_fresh_running_claim_left_untouched(self, fake_queue):
        _write(fake_queue, [_rec(id="a1", status="running", claimed_at=now_iso())])
        out = networking.approved_to_run()
        assert out == []
        latest = next(x for x in networking.load_queue() if x["id"] == "a1")
        assert latest["status"] == "running"  # not stale yet (< 2h), left alone

    def test_genuinely_approved_items_still_claimed_and_run(self, fake_queue):
        _write(fake_queue, [_rec(id="a1", status="approved", kind="like")])
        out = networking.approved_to_run()
        assert [x["id"] for x in out] == ["a1"]
        assert out[0]["status"] == "running"

    def test_stale_and_approved_together_only_approved_runs(self, fake_queue):
        _write(fake_queue, [
            _rec(id="a1", kind="like", status="running", claimed_at=_stale_claimed_at()),
            _rec(id="a2", kind="like", status="approved"),
        ])
        out = networking.approved_to_run()
        assert [x["id"] for x in out] == ["a2"]


class TestWeeklyConnectCapIncludesRunning:
    def test_running_connect_claims_count_toward_weekly_cap(self, fake_queue, monkeypatch):
        monkeypatch.setattr(planner, "_config", lambda: {"network": {"weekly": {"connect": 5}}})
        today = now_iso()
        _write(fake_queue, [_rec(id=f"c{i}", kind="connect", status="running", claimed_at=today)
                             for i in range(5)])
        assert networking.allowance()["connect"] == 0  # all 5 weekly slots already claimed

    def test_done_and_running_both_count_no_double_count(self, fake_queue):
        today = now_iso()
        _write(fake_queue, [
            _rec(id="c1", kind="connect", status="done", acted_at=today),
            _rec(id="c2", kind="connect", status="running", claimed_at=today),
        ])
        assert networking._connects_last_7d() == 2

    def test_running_claim_outside_7d_window_not_counted(self, fake_queue):
        import datetime as dt
        old = (dt.date.today() - dt.timedelta(days=10)).isoformat() + "T08:00:00-07:00"
        _write(fake_queue, [_rec(id="c1", kind="connect", status="running", claimed_at=old)])
        assert networking._connects_last_7d() == 0

    def test_overlapping_pulls_cannot_overshoot_weekly_cap(self, fake_queue, monkeypatch):
        # the exact scenario from the audit note: two approved_to_run() calls landing
        # close together must not BOTH see room and both claim connects past the cap.
        monkeypatch.setattr(planner, "_config", lambda: {"network": {"weekly": {"connect": 2}}})
        _write(fake_queue, [_rec(id="c1", kind="connect", status="approved"),
                            _rec(id="c2", kind="connect", status="approved"),
                            _rec(id="c3", kind="connect", status="approved")])
        first = networking.approved_to_run()
        assert len(first) == 2  # fills the weekly cap, claims them 'running'
        second = networking.approved_to_run()
        assert second == []  # the 3rd approved connect must NOT also be released


class TestBudgetGateComposition:
    """R2-33: approved_to_run() itself must honor li_budget's guard, since the one
    real caller (the operator brief) invokes it directly, not via li_budget.gate()."""

    def test_weekend_pause_blocks_everything(self, fake_queue, monkeypatch):
        _write(fake_queue, [_rec(id="a1", status="approved", kind="like")])
        monkeypatch.setattr(li_budget, "weekend_paused", lambda now=None: True)
        out = networking.approved_to_run()
        assert out == []
        latest = next(x for x in networking.load_queue() if x["id"] == "a1")
        assert latest["status"] == "approved"  # left alone, not falsely claimed 'running'

    def test_outside_hours_blocks_everything(self, fake_queue, monkeypatch):
        _write(fake_queue, [_rec(id="a1", status="approved", kind="like")])
        monkeypatch.setattr(li_budget, "in_hours_window", lambda now=None: False)
        out = networking.approved_to_run()
        assert out == []

    def test_daily_action_budget_trims_the_release(self, fake_queue, monkeypatch):
        _write(fake_queue, [_rec(id=f"a{i}", status="approved", kind="like") for i in range(5)])
        monkeypatch.setattr(li_budget, "budget_remaining_today", lambda: 2)
        out = networking.approved_to_run()
        assert len(out) == 2

    def test_open_gate_does_not_change_existing_behavior(self, fake_queue):
        _write(fake_queue, [_rec(id="a1", status="approved", kind="like"),
                            _rec(id="a2", status="approved", kind="comment")])
        out = networking.approved_to_run()
        assert {x["id"] for x in out} == {"a1", "a2"}


class TestBudgetGateFailsClosed:
    """R3#7 (2026-07-14): the li_budget.gate() call is wrapped in try/except --
    an error used to `pass`, leaving `out` as whatever allowance-only trimming
    had already produced, i.e. an unevaluatable safety gate silently released
    the FULL allowance outside hours/weekends/over budget. Must fail CLOSED:
    an error blocks the release for that call, same as a real weekend_paused()."""

    def test_gate_raising_blocks_everything(self, fake_queue, monkeypatch):
        _write(fake_queue, [_rec(id="a1", status="approved", kind="like")])

        def _boom(candidates):
            raise RuntimeError("li_budget blew up")
        monkeypatch.setattr(li_budget, "gate", _boom)
        out = networking.approved_to_run()
        assert out == []
        latest = next(x for x in networking.load_queue() if x["id"] == "a1")
        assert latest["status"] == "approved"  # left alone, not falsely claimed 'running'

    def test_gate_import_failure_blocks_everything(self, fake_queue, monkeypatch):
        # the actual exception shape the try/except guards against: li_budget
        # itself failing to import (not just gate() raising once imported)
        _write(fake_queue, [_rec(id="a1", status="approved", kind="like")])
        monkeypatch.setitem(sys.modules, "li_budget", None)  # import li_budget -> ImportError
        out = networking.approved_to_run()
        assert out == []
