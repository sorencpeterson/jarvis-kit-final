#!/usr/bin/env python3
"""Pytest suite for agents/self_report.py's pure summarize/render logic.
No LLM calls, no network — all synthetic runs.jsonl/usage.jsonl data via
monkeypatched paths.

Run: .venv/bin/python -m pytest tests/test_self_report.py -v
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import self_report as sr  # noqa: E402


def _iso_days_ago(n: int) -> str:
    return (datetime.now().astimezone() - timedelta(days=n)).isoformat(timespec="seconds")


class TestSummarizeRuns:
    def test_counts_ok_and_fail(self, tmp_path, monkeypatch):
        p = tmp_path / "runs.jsonl"
        p.write_text(
            json.dumps({"agent": "foo", "start": _iso_days_ago(1), "ok": True, "err": None}) + "\n" +
            json.dumps({"agent": "foo", "start": _iso_days_ago(1), "ok": False, "err": "boom"}) + "\n"
        )
        monkeypatch.setattr(sr, "RUNS", p)
        result = sr.summarize_runs(7)
        assert result["total_runs"] == 2
        assert result["by_agent"]["foo"]["ok"] == 1
        assert result["by_agent"]["foo"]["fail"] == 1
        assert result["by_agent"]["foo"]["errors"] == ["boom"]

    def test_excludes_records_outside_window(self, tmp_path, monkeypatch):
        p = tmp_path / "runs.jsonl"
        p.write_text(json.dumps({"agent": "foo", "start": _iso_days_ago(30), "ok": True}) + "\n")
        monkeypatch.setattr(sr, "RUNS", p)
        result = sr.summarize_runs(7)
        assert result["total_runs"] == 0

    def test_separate_agents_tracked_independently(self, tmp_path, monkeypatch):
        p = tmp_path / "runs.jsonl"
        p.write_text(
            json.dumps({"agent": "foo", "start": _iso_days_ago(1), "ok": True}) + "\n" +
            json.dumps({"agent": "bar", "start": _iso_days_ago(1), "ok": True}) + "\n"
        )
        monkeypatch.setattr(sr, "RUNS", p)
        result = sr.summarize_runs(7)
        assert set(result["by_agent"].keys()) == {"foo", "bar"}

    def test_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sr, "RUNS", tmp_path / "nonexistent.jsonl")
        result = sr.summarize_runs(7)
        assert result["total_runs"] == 0
        assert result["by_agent"] == {}


class TestSummarizeUsage:
    def test_counts_tokens_by_feature(self, tmp_path, monkeypatch):
        p = tmp_path / "usage.jsonl"
        p.write_text(json.dumps({"ts": _iso_days_ago(1), "feature": "plan",
                                 "model": "claude-haiku-4-5-20251001", "in": 100, "out": 200}) + "\n")
        monkeypatch.setattr(sr, "USAGE", p)
        result = sr.summarize_usage(7)
        assert result["by_feature"]["plan"]["in"] == 100
        assert result["by_feature"]["plan"]["out"] == 200

    def test_estimates_cost_for_priced_model(self, tmp_path, monkeypatch):
        p = tmp_path / "usage.jsonl"
        p.write_text(json.dumps({"ts": _iso_days_ago(1), "feature": "plan",
                                 "model": "claude-haiku-4-5-20251001",
                                 "in": 1_000_000, "out": 1_000_000}) + "\n")
        monkeypatch.setattr(sr, "USAGE", p)
        result = sr.summarize_usage(7)
        # 1M in @ $1.00/Mtok + 1M out @ $5.00/Mtok = $6.00
        assert result["estimated_cost_usd"] == 6.0
        assert result["unpriced_tokens"] == 0

    def test_unpriced_model_excluded_from_cost_but_counted(self, tmp_path, monkeypatch):
        p = tmp_path / "usage.jsonl"
        p.write_text(json.dumps({"ts": _iso_days_ago(1), "feature": "plan",
                                 "model": "some-future-unpriced-model",
                                 "in": 500, "out": 500}) + "\n")
        monkeypatch.setattr(sr, "USAGE", p)
        result = sr.summarize_usage(7)
        assert result["estimated_cost_usd"] == 0.0
        assert result["unpriced_tokens"] == 1000

    def test_excludes_records_outside_window(self, tmp_path, monkeypatch):
        p = tmp_path / "usage.jsonl"
        p.write_text(json.dumps({"ts": _iso_days_ago(30), "feature": "plan",
                                 "model": "claude-haiku-4-5-20251001", "in": 100, "out": 100}) + "\n")
        monkeypatch.setattr(sr, "USAGE", p)
        result = sr.summarize_usage(7)
        assert result["total_calls"] == 0

    def test_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sr, "USAGE", tmp_path / "nonexistent.jsonl")
        result = sr.summarize_usage(7)
        assert result["total_calls"] == 0


class TestRenderMarkdown:
    def test_includes_agent_table(self):
        runs = {"total_runs": 1, "by_agent": {"foo": {"runs": 1, "ok": 1, "fail": 0, "errors": []}}}
        usage = {"total_calls": 0, "by_feature": {}, "by_model": {},
                "estimated_cost_usd": 0.0, "unpriced_tokens": 0}
        md = sr.render_markdown(runs, usage, 7)
        assert "| foo | 1 | 1 | 0 | 0% |" in md

    def test_shows_error_rate(self):
        runs = {"total_runs": 2, "by_agent": {"foo": {"runs": 2, "ok": 1, "fail": 1, "errors": ["x"]}}}
        usage = {"total_calls": 0, "by_feature": {}, "by_model": {},
                "estimated_cost_usd": 0.0, "unpriced_tokens": 0}
        md = sr.render_markdown(runs, usage, 7)
        assert "50%" in md
        assert "x" in md  # the actual error message surfaces

    def test_empty_runs_gives_honest_message(self):
        runs = {"total_runs": 0, "by_agent": {}}
        usage = {"total_calls": 0, "by_feature": {}, "by_model": {},
                "estimated_cost_usd": 0.0, "unpriced_tokens": 0}
        md = sr.render_markdown(runs, usage, 7)
        assert "UNDERCOUNTS" in md

    def test_shows_unpriced_tokens_note(self):
        runs = {"total_runs": 0, "by_agent": {}}
        usage = {"total_calls": 1, "by_feature": {}, "by_model": {},
                "estimated_cost_usd": 0.0, "unpriced_tokens": 500}
        md = sr.render_markdown(runs, usage, 7)
        assert "500" in md
        assert "unpriced" in md.lower()
