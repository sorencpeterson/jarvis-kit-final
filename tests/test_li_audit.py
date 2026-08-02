#!/usr/bin/env python3
"""Unit tests for agents/li_audit.py (A20 operator-run transcript reader).
Isolated to tmp_path, never the real store.

Run: .venv/bin/python -m pytest tests/test_li_audit.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import li_audit  # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(li_audit, "RUNS", tmp_path / "li_operator_runs.jsonl")
    return tmp_path


class TestLoadRuns:
    def test_no_file_empty_list(self, isolated):
        assert li_audit.load_runs() == []

    def test_reads_appended_rows(self, isolated):
        with li_audit.RUNS.open("a") as f:
            f.write(json.dumps({"ts": "x", "done": 5, "skipped": 1}) + "\n")
        runs = li_audit.load_runs()
        assert len(runs) == 1
        assert runs[0]["done"] == 5

    def test_malformed_line_skipped(self, isolated):
        li_audit.RUNS.parent.mkdir(parents=True, exist_ok=True)
        with li_audit.RUNS.open("a") as f:
            f.write("not json\n")
            f.write(json.dumps({"ts": "x", "done": 1}) + "\n")
        assert len(li_audit.load_runs()) == 1


class TestSummary:
    def test_empty_state_honest_zeros(self, isolated):
        s = li_audit.summary()
        assert s["total_runs"] == 0
        assert s["totals"]["done"] == 0
        assert s["recent"] == []

    def test_totals_sum_across_runs(self, isolated):
        with li_audit.RUNS.open("a") as f:
            f.write(json.dumps({"ts": "1", "done": 5, "skipped": 1, "accepted_captured": 2}) + "\n")
            f.write(json.dumps({"ts": "2", "done": 3, "skipped": 0, "accepted_captured": 1}) + "\n")
        s = li_audit.summary()
        assert s["totals"]["done"] == 8
        assert s["totals"]["skipped"] == 1
        assert s["totals"]["accepted_captured"] == 3

    def test_recent_limited_to_n(self, isolated):
        with li_audit.RUNS.open("a") as f:
            for i in range(15):
                f.write(json.dumps({"ts": str(i), "done": 1}) + "\n")
        s = li_audit.summary(n=5)
        assert len(s["recent"]) == 5

    def test_flagged_notes_collected(self, isolated):
        with li_audit.RUNS.open("a") as f:
            f.write(json.dumps({"ts": "1", "done": 1, "notes": "selector drift on connect button"}) + "\n")
            f.write(json.dumps({"ts": "2", "done": 1, "notes": ""}) + "\n")
        s = li_audit.summary()
        assert s["flagged_notes"] == ["selector drift on connect button"]

    def test_missing_numeric_fields_treated_as_zero(self, isolated):
        with li_audit.RUNS.open("a") as f:
            f.write(json.dumps({"ts": "1"}) + "\n")  # no done/skipped/accepted_captured at all
        s = li_audit.summary()
        assert s["totals"]["done"] == 0  # never crashes on missing keys
