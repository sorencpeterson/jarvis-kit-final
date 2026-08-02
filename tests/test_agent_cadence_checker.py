#!/usr/bin/env python3
"""Pytest suite for agents/agent_cadence_checker.py's pure/near-pure logic.
No LLM calls, no network. Uses monkeypatch to redirect RUNS/ROOT-relative
paths to tmp_path fixtures rather than touching the real store.

Run: .venv/bin/python -m pytest tests/test_agent_cadence_checker.py -v
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "agents"):
    sys.path.insert(0, str(p))

import agent_cadence_checker as acc  # noqa: E402


class TestLastRunEnd:
    def test_finds_matching_agent_last_line_wins(self, tmp_path, monkeypatch):
        runs = tmp_path / "runs.jsonl"
        runs.write_text(
            json.dumps({"agent": "foo", "end": "2026-07-01T10:00:00-07:00"}) + "\n" +
            json.dumps({"agent": "foo", "end": "2026-07-02T10:00:00-07:00"}) + "\n" +
            json.dumps({"agent": "bar", "end": "2026-07-03T10:00:00-07:00"}) + "\n"
        )
        monkeypatch.setattr(acc, "RUNS", runs)
        assert acc._last_run_end("foo") == "2026-07-02T10:00:00-07:00"

    def test_no_matching_agent_returns_none(self, tmp_path, monkeypatch):
        runs = tmp_path / "runs.jsonl"
        runs.write_text(json.dumps({"agent": "bar", "end": "2026-07-01T10:00:00-07:00"}) + "\n")
        monkeypatch.setattr(acc, "RUNS", runs)
        assert acc._last_run_end("foo") is None

    def test_missing_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(acc, "RUNS", tmp_path / "nonexistent.jsonl")
        assert acc._last_run_end("foo") is None


class TestOutputFreshnessHours:
    def test_missing_path_returns_none(self):
        assert acc._output_freshness_hours("store/definitely_not_a_real_file_xyz.json") is None

    def test_real_file_returns_small_positive_number(self, tmp_path, monkeypatch):
        monkeypatch.setattr(acc, "ROOT", tmp_path)
        f = tmp_path / "store"
        f.mkdir()
        (f / "test.json").write_text("{}")
        hours = acc._output_freshness_hours("store/test.json")
        assert hours is not None
        assert 0 <= hours < 0.01  # just created, should be near-instant

    def test_directory_uses_newest_child(self, tmp_path, monkeypatch):
        monkeypatch.setattr(acc, "ROOT", tmp_path)
        d = tmp_path / "store" / "snapshots"
        d.mkdir(parents=True)
        (d / "old.json").write_text("{}")
        import time
        time.sleep(0.05)
        (d / "new.json").write_text("{}")
        hours = acc._output_freshness_hours("store/snapshots")
        assert hours is not None
        assert hours < 0.01

    def test_empty_directory_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(acc, "ROOT", tmp_path)
        d = tmp_path / "store" / "empty"
        d.mkdir(parents=True)
        assert acc._output_freshness_hours("store/empty") is None


class TestHoursSinceIso:
    def test_recent_timestamp(self):
        recent = (datetime.now().astimezone() - timedelta(hours=2)).isoformat()
        h = acc._hours_since_iso(recent)
        assert h is not None
        assert 1.9 < h < 2.1

    def test_bad_timestamp_returns_none(self):
        assert acc._hours_since_iso("not a timestamp") is None

    def test_empty_string_returns_none(self):
        assert acc._hours_since_iso("") is None


class TestCheckOne:
    def test_no_signals_is_unwatchable(self):
        entry = {"agent": "foo", "expected_hours": 24, "runlog_name": None, "output_file": None}
        result = acc.check_one(entry)
        assert result["status"] == "unwatchable"
        assert result["signal_used"] is None

    def test_ok_when_within_expected_window(self, tmp_path, monkeypatch):
        monkeypatch.setattr(acc, "ROOT", tmp_path)
        (tmp_path / "store").mkdir()
        (tmp_path / "store" / "out.json").write_text("{}")
        entry = {"agent": "foo", "expected_hours": 24, "runlog_name": None, "output_file": "store/out.json"}
        result = acc.check_one(entry)
        assert result["status"] == "ok"
        assert result["signal_used"] == "output_file"

    def test_miss_when_expected_window_too_tight(self, tmp_path, monkeypatch):
        monkeypatch.setattr(acc, "ROOT", tmp_path)
        (tmp_path / "store").mkdir()
        (tmp_path / "store" / "out.json").write_text("{}")
        entry = {"agent": "foo", "expected_hours": 0.0, "runlog_name": None, "output_file": "store/out.json"}
        result = acc.check_one(entry)
        assert result["status"] == "miss"

    def test_prefers_freshest_signal_when_both_available(self, tmp_path, monkeypatch):
        monkeypatch.setattr(acc, "ROOT", tmp_path)
        monkeypatch.setattr(acc, "RUNS", tmp_path / "runs.jsonl")
        # runlog says STALE (25h ago), output_file says FRESH (just now) -> should use fresh
        stale_ts = (datetime.now().astimezone() - timedelta(hours=25)).isoformat()
        (tmp_path / "runs.jsonl").write_text(json.dumps({"agent": "foo", "end": stale_ts}) + "\n")
        (tmp_path / "store").mkdir()
        (tmp_path / "store" / "out.json").write_text("{}")
        entry = {"agent": "foo", "expected_hours": 24, "runlog_name": "foo", "output_file": "store/out.json"}
        result = acc.check_one(entry)
        assert result["status"] == "ok"  # would be MISS if it used the stale runlog signal
        assert result["signal_used"] == "output_file"


class TestRun:
    def test_fixture_mode_has_expected_shape(self):
        # the fixture's miss/unwatchable split depends on real store/ files existing;
        # on a bare fresh-install restore (no store/runs.jsonl) the counts shift, so
        # skip rather than fail there (DR drill / survivability). Real checkout: runs.
        if not acc.RUNS.exists():
            import pytest
            pytest.skip("no store/runs.jsonl (fresh install)")
        data = acc.run(fixture=True)
        assert data["ok"] is True
        assert data["fixture"] is True
        assert len(data["results"]) == 2
        assert data["miss_count"] == 1
        assert data["unwatchable_count"] == 1

    def test_missing_table_reports_failure(self, monkeypatch, tmp_path):
        monkeypatch.setattr(acc, "CADENCE_TABLE", tmp_path / "nonexistent.json")
        data = acc.run(fixture=False)
        assert data["ok"] is False
        assert "error" in data
