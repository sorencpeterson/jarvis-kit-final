#!/usr/bin/env python3
"""Pytest suite for agents/standup.py's non-LLM logic: the fixture lines are
deterministic, and the humanize() voice-filter application is verified by
mocking planner._cli (no real network/LLM call in this suite, keeping it
fast and free — the real end-to-end LLM path was verified manually).

Run: .venv/bin/python -m pytest tests/test_standup.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import standup  # noqa: E402
import planner  # noqa: E402


class TestFixtureLines:
    def test_returns_nonempty_deterministic_lines(self):
        lines = standup._fixture_lines()
        assert len(lines) > 0
        assert lines == standup._fixture_lines()  # deterministic, same every call


class TestBuildNoLlmPaths:
    def test_empty_lines_skips_llm_entirely(self):
        with mock.patch.object(standup.snapshot, "diff_with_yesterday", return_value=[]):
            with mock.patch.object(planner, "_cli") as mock_cli:
                result = standup.build(fixture=False)
                mock_cli.assert_not_called()
                assert result["text"] == "Nothing meaningfully moved since yesterday."
                assert result["lines"] == []


class TestBuildHumanizeApplication:
    def test_strips_em_dash_from_llm_output(self):
        with mock.patch.object(planner, "_cli", return_value="Result—with an em dash—here."):
            result = standup.build(fixture=True)
            assert "—" not in result["text"]

    def test_strips_en_dash_from_llm_output(self):
        with mock.patch.object(planner, "_cli", return_value="Open 9am–5pm today."):
            result = standup.build(fixture=True)
            assert "–" not in result["text"]

    def test_clean_output_passes_through(self):
        with mock.patch.object(planner, "_cli", return_value="A perfectly clean sentence."):
            result = standup.build(fixture=True)
            assert result["text"] == "A perfectly clean sentence."

    def test_llm_failure_falls_back_to_raw_lines(self):
        with mock.patch.object(planner, "_cli", return_value=None):
            result = standup.build(fixture=True)
            assert "Raw deltas" in result["text"]
            # fallback text should still contain the actual delta content
            for line in standup._fixture_lines():
                assert line in result["text"]

    def test_fixture_flag_propagates(self):
        with mock.patch.object(planner, "_cli", return_value="text"):
            result = standup.build(fixture=True)
            assert result["fixture"] is True
