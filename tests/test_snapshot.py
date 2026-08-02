#!/usr/bin/env python3
"""Pytest suite for the E336 pure diff/report helpers in agents/snapshot.py.
_diff_numbers and build_weekly_report's fallback logic are pure given a
snapshot dict; _oldest_snapshot/diff_with_n_days_ago touch store/snapshots/
so those get a temp-dir-backed fixture rather than real store I/O.

Run: .venv/bin/python -m pytest tests/test_snapshot.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "agents"):
    sys.path.insert(0, str(p))

import snapshot  # noqa: E402


class TestFlattenNumbers:
    def test_top_level_numbers(self):
        out = snapshot._flatten_numbers({"a": 1, "b": 2.5})
        assert out == {"a": 1, "b": 2.5}

    def test_skips_booleans(self):
        out = snapshot._flatten_numbers({"a": True, "b": 5})
        assert "a" not in out
        assert out["b"] == 5

    def test_one_level_nesting(self):
        out = snapshot._flatten_numbers({"a": {"b": 3}}, prefix="x.")
        assert out == {"x.a.b": 3}

    def test_none_input(self):
        assert snapshot._flatten_numbers(None) == {}

    def test_ignores_strings(self):
        out = snapshot._flatten_numbers({"a": "text", "b": 5})
        assert out == {"b": 5}


class TestAllEndpointsNull:
    def test_true_when_every_endpoint_is_none(self):
        snap = {"date": "2026-07-13", "state": None, "money": None, "jobs": None,
                "cold": None, "usage": None}
        assert snapshot._all_endpoints_null(snap) is True

    def test_false_when_any_endpoint_has_data(self):
        snap = {"date": "2026-07-13", "state": {"x": 1}, "money": None, "jobs": None,
                "cold": None, "usage": None}
        assert snapshot._all_endpoints_null(snap) is False

    def test_false_when_endpoint_is_falsy_but_not_none(self):
        # an empty dict/list is a real (if empty) response, not a failed fetch
        snap = {"date": "2026-07-13", "state": {}, "money": None, "jobs": None,
                "cold": None, "usage": None}
        assert snapshot._all_endpoints_null(snap) is False


class TestMainRefusesToOverwriteGoodSnapshotWithNullShell:
    def test_all_null_does_not_clobber_existing_good_snapshot(self, tmp_path, monkeypatch):
        """R2-51 regression: a rerun where every endpoint came back null (server
        down / bad token) must not overwrite a real snapshot already on disk for
        today with an empty shell."""
        monkeypatch.setattr(snapshot, "SNAP_DIR", tmp_path)
        monkeypatch.setattr(snapshot, "build_snapshot", lambda: {
            "date": "2026-07-13", "ts": "2026-07-13T09:00:00-07:00",
            "state": None, "money": None, "jobs": None, "cold": None, "usage": None})
        monkeypatch.setattr(snapshot, "track", lambda *a, **k: _NullCtx())
        good = tmp_path / "2026-07-13.json"
        good.write_text(json.dumps({"date": "2026-07-13", "state": {"x": 1}}))
        monkeypatch.setattr(sys, "argv", ["snapshot.py"])
        rc = snapshot.main()
        assert rc == 1
        assert json.loads(good.read_text())["state"] == {"x": 1}  # untouched
        assert not (tmp_path / "2026-07-13.json.tmp").exists()

    def test_all_null_still_writes_when_no_prior_snapshot(self, tmp_path, monkeypatch):
        monkeypatch.setattr(snapshot, "SNAP_DIR", tmp_path)
        monkeypatch.setattr(snapshot, "build_snapshot", lambda: {
            "date": "2026-07-13", "ts": "2026-07-13T09:00:00-07:00",
            "state": None, "money": None, "jobs": None, "cold": None, "usage": None})
        monkeypatch.setattr(snapshot, "track", lambda *a, **k: _NullCtx())
        monkeypatch.setattr(sys, "argv", ["snapshot.py"])
        rc = snapshot.main()
        assert rc == 0
        assert (tmp_path / "2026-07-13.json").exists()


class TestAnyEndpointNull:
    """R3#8 (2026-07-14): _all_endpoints_null only protects when EVERY endpoint
    fails; _any_endpoint_null is the tightened check the overwrite guard now
    uses so a single transient failure can't silently replace complete data."""

    def test_true_when_any_endpoint_is_none(self):
        snap = {"date": "2026-07-13", "state": {"x": 1}, "money": None,
                "jobs": {"y": 2}, "cold": {"z": 3}, "usage": {"w": 4}}
        assert snapshot._any_endpoint_null(snap) is True

    def test_true_when_every_endpoint_is_none_too(self):
        snap = {"date": "2026-07-13", "state": None, "money": None, "jobs": None,
                "cold": None, "usage": None}
        assert snapshot._any_endpoint_null(snap) is True

    def test_false_when_no_endpoint_is_none(self):
        snap = {"date": "2026-07-13", "state": {"x": 1}, "money": {"a": 1},
                "jobs": {"y": 2}, "cold": {"z": 3}, "usage": {"w": 4}}
        assert snapshot._any_endpoint_null(snap) is False

    def test_false_when_endpoint_is_falsy_but_not_none(self):
        snap = {"date": "2026-07-13", "state": {}, "money": {"a": 1},
                "jobs": {"y": 2}, "cold": {"z": 3}, "usage": {"w": 4}}
        assert snapshot._any_endpoint_null(snap) is False


class TestMainRefusesToOverwriteGoodSnapshotWithPartialShell:
    """R3#8 regression: the old guard only blocked when ALL endpoints were null,
    so a 4-good-1-null snapshot still silently overwrote a complete prior
    snapshot, permanently losing that one endpoint's data for the day (same-day
    reruns overwrite by design)."""

    def test_partial_null_does_not_clobber_existing_good_snapshot(self, tmp_path, monkeypatch):
        monkeypatch.setattr(snapshot, "SNAP_DIR", tmp_path)
        monkeypatch.setattr(snapshot, "build_snapshot", lambda: {
            "date": "2026-07-13", "ts": "2026-07-13T09:00:00-07:00",
            "state": {"x": 9}, "money": {"y": 9}, "jobs": {"z": 9}, "cold": {"w": 9},
            "usage": None})  # one transient failure, the other four succeeded
        monkeypatch.setattr(snapshot, "track", lambda *a, **k: _NullCtx())
        good = tmp_path / "2026-07-13.json"
        good.write_text(json.dumps({"date": "2026-07-13", "state": {"x": 1},
                                    "usage": {"calls": 5}}))
        monkeypatch.setattr(sys, "argv", ["snapshot.py"])
        rc = snapshot.main()
        assert rc == 1
        on_disk = json.loads(good.read_text())
        assert on_disk["state"] == {"x": 1}          # untouched, NOT clobbered with {"x": 9}
        assert on_disk["usage"] == {"calls": 5}       # the field that would've been lost
        assert not (tmp_path / "2026-07-13.json.tmp").exists()

    def test_partial_null_still_writes_when_no_prior_snapshot(self, tmp_path, monkeypatch):
        monkeypatch.setattr(snapshot, "SNAP_DIR", tmp_path)
        monkeypatch.setattr(snapshot, "build_snapshot", lambda: {
            "date": "2026-07-13", "ts": "2026-07-13T09:00:00-07:00",
            "state": {"x": 9}, "money": {"y": 9}, "jobs": {"z": 9}, "cold": {"w": 9},
            "usage": None})
        monkeypatch.setattr(snapshot, "track", lambda *a, **k: _NullCtx())
        monkeypatch.setattr(sys, "argv", ["snapshot.py"])
        rc = snapshot.main()
        assert rc == 0
        assert (tmp_path / "2026-07-13.json").exists()


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestDiffNumbers:
    def test_detects_changed_values(self):
        today = {"state": {"count": 10}}
        other = {"state": {"count": 5}}
        lines = snapshot._diff_numbers(today, other)
        assert any("count" in ln and "5 -> 10" in ln for ln in lines)

    def test_no_changes_empty_list(self):
        today = {"state": {"count": 10}}
        other = {"state": {"count": 10}}
        assert snapshot._diff_numbers(today, other) == []

    def test_respects_limit(self):
        today = {"state": {f"k{i}": i for i in range(10)}}
        other = {"state": {f"k{i}": -1 for i in range(10)}}
        lines = snapshot._diff_numbers(today, other, limit=3)
        assert len(lines) == 3

    def test_shows_positive_delta(self):
        today = {"state": {"n": 15}}
        other = {"state": {"n": 10}}
        lines = snapshot._diff_numbers(today, other)
        assert any("(+5)" in ln for ln in lines)

    def test_shows_negative_delta(self):
        today = {"state": {"n": 5}}
        other = {"state": {"n": 10}}
        lines = snapshot._diff_numbers(today, other)
        assert any("(-5)" in ln for ln in lines)


class TestDiffWithNDaysAgo:
    def test_no_snapshot_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(snapshot, "SNAP_DIR", tmp_path)
        result = snapshot.diff_with_n_days_ago({"date": "2026-07-03", "state": {"x": 1}}, 7)
        assert result["found"] is False
        assert result["actual_days_back"] is None
        assert result["lines"] == []

    def test_finds_real_n_days_ago_snapshot(self, tmp_path, monkeypatch):
        monkeypatch.setattr(snapshot, "SNAP_DIR", tmp_path)
        from datetime import datetime, timedelta
        seven_ago = (datetime.now().astimezone() - timedelta(days=7)).strftime("%Y-%m-%d")
        (tmp_path / f"{seven_ago}.json").write_text(json.dumps({"state": {"x": 1}}))
        result = snapshot.diff_with_n_days_ago({"date": "today", "state": {"x": 5}}, 7)
        assert result["found"] is True
        assert result["actual_days_back"] == 7
        assert any("x" in ln for ln in result["lines"])


class TestOldestSnapshot:
    def test_no_snapshots_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(snapshot, "SNAP_DIR", tmp_path)
        assert snapshot._oldest_snapshot() is None

    def test_returns_earliest_by_filename_sort(self, tmp_path, monkeypatch):
        monkeypatch.setattr(snapshot, "SNAP_DIR", tmp_path)
        (tmp_path / "2026-07-05.json").write_text(json.dumps({"state": {"x": 3}}))
        (tmp_path / "2026-07-01.json").write_text(json.dumps({"state": {"x": 1}}))
        (tmp_path / "2026-07-03.json").write_text(json.dumps({"state": {"x": 2}}))
        result = snapshot._oldest_snapshot()
        assert result is not None
        date, data = result
        assert date == "2026-07-01"
        assert data["state"]["x"] == 1


class TestBuildWeeklyReport:
    def test_no_history_at_all(self, tmp_path, monkeypatch):
        monkeypatch.setattr(snapshot, "SNAP_DIR", tmp_path)
        report = snapshot.build_weekly_report({"date": "2026-07-03", "state": {"x": 1}})
        assert "No prior snapshots exist yet" in report

    def test_real_seven_day_comparison(self, tmp_path, monkeypatch):
        monkeypatch.setattr(snapshot, "SNAP_DIR", tmp_path)
        from datetime import datetime, timedelta
        seven_ago = (datetime.now().astimezone() - timedelta(days=7)).strftime("%Y-%m-%d")
        (tmp_path / f"{seven_ago}.json").write_text(json.dumps({"state": {"x": 1}}))
        report = snapshot.build_weekly_report({"date": "2026-07-03", "state": {"x": 5}})
        assert "a real week-old snapshot" in report
        assert "x" in report

    def test_honest_fallback_when_no_true_week_snapshot(self, tmp_path, monkeypatch):
        monkeypatch.setattr(snapshot, "SNAP_DIR", tmp_path)
        (tmp_path / "2026-07-02.json").write_text(json.dumps({"state": {"x": 1}}))
        report = snapshot.build_weekly_report({"date": "2026-07-03", "state": {"x": 5}})
        assert "No snapshot from exactly 7 days ago yet" in report
        assert "2026-07-02" in report

    def test_never_lies_about_having_a_week_of_data(self, tmp_path, monkeypatch):
        # regression guard: the report must NEVER claim "comparing against 7
        # days ago" when it actually fell back to a closer snapshot
        monkeypatch.setattr(snapshot, "SNAP_DIR", tmp_path)
        (tmp_path / "2026-07-02.json").write_text(json.dumps({"state": {"x": 1}}))
        report = snapshot.build_weekly_report({"date": "2026-07-03", "state": {"x": 5}})
        assert "Comparing against 7 day(s) ago" not in report
