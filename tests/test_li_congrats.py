#!/usr/bin/env python3
"""Unit tests for agents/li_congrats.py (A63 title-change congrats machinery,
[E] pending operator snapshot-diff data). Isolated to tmp_path, LLM calls mocked.

Run: .venv/bin/python -m pytest tests/test_li_congrats.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import planner  # noqa: E402
import li_congrats  # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(li_congrats, "TITLE_CHANGES", tmp_path / "li_title_changes.jsonl")
    monkeypatch.setattr(li_congrats, "CONGRATS_DRAFTED", tmp_path / "li_congrats_drafted.jsonl")
    return tmp_path


def _fixture_change(**kw) -> dict:
    base = {"url": "https://linkedin.com/in/fixture-person", "name": "FIXTURE Person",
            "old_headline": "Marketing Manager @ FIXTURE Co", "new_headline": "Founder @ FIXTURE Co",
            "detected_at": "2026-07-01T08:00:00-07:00"}
    base.update(kw)
    return base


class TestLooksLikeRoleChange:
    def test_promotion_detected(self):
        assert li_congrats.looks_like_role_change("Marketing Manager @ Acme", "Founder @ Acme")

    def test_cosmetic_tagline_add_not_flagged(self):
        assert not li_congrats.looks_like_role_change(
            "Founder @ Acme", "Founder @ Acme | Building cool stuff")

    def test_company_change_flagged(self):
        assert li_congrats.looks_like_role_change("Founder @ Acme", "Founder @ Bigger Co")

    def test_identical_headlines_not_flagged(self):
        assert not li_congrats.looks_like_role_change("Same headline", "Same headline")

    def test_empty_old_headline_not_flagged(self):
        assert not li_congrats.looks_like_role_change("", "Founder @ Acme")

    def test_role_escalation_detected(self):
        assert li_congrats.looks_like_role_change("Director of Ops @ Acme", "COO @ Acme")


class TestRunEmptyState:
    def test_no_file_reports_e_gap_note(self, isolated):
        result = li_congrats.run(dry=False)
        assert result["triggers"] == 0
        assert "note" in result

    def test_empty_file_reports_e_gap_note(self, isolated):
        li_congrats.TITLE_CHANGES.parent.mkdir(parents=True, exist_ok=True)
        li_congrats.TITLE_CHANGES.write_text("")
        result = li_congrats.run(dry=False)
        assert "note" in result


class TestRunDryMode:
    def test_identifies_trigger_calls_no_llm(self, isolated, monkeypatch):
        with li_congrats.TITLE_CHANGES.open("a") as f:
            f.write(json.dumps(_fixture_change()) + "\n")
        called = {"n": 0}
        monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {"draft": "x"})
        result = li_congrats.run(dry=True)
        assert result["triggers"] == 1
        assert called["n"] == 0

    def test_non_role_change_not_a_trigger(self, isolated):
        with li_congrats.TITLE_CHANGES.open("a") as f:
            f.write(json.dumps(_fixture_change(old_headline="Founder @ Acme",
                                                 new_headline="Founder @ Acme | now hiring")) + "\n")
        result = li_congrats.run(dry=True)
        assert result["triggers"] == 0


class TestRunRealMode:
    def test_good_draft_recorded(self, isolated, monkeypatch):
        with li_congrats.TITLE_CHANGES.open("a") as f:
            f.write(json.dumps(_fixture_change()) + "\n")
        monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: {"draft": "Congrats on the new role."})
        result = li_congrats.run(dry=False)
        assert len(result["drafted"]) == 1
        assert result["drafted"][0]["draft"] == "Congrats on the new role."

    def test_bad_draft_never_recorded(self, isolated, monkeypatch):
        with li_congrats.TITLE_CHANGES.open("a") as f:
            f.write(json.dumps(_fixture_change()) + "\n")
        monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: {"draft": "Love your energy! 🔥"})
        result = li_congrats.run(dry=False)
        assert result["drafted"] == []

    def test_idempotent_no_redraft_on_second_run(self, isolated, monkeypatch):
        with li_congrats.TITLE_CHANGES.open("a") as f:
            f.write(json.dumps(_fixture_change()) + "\n")
        monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: {"draft": "Congrats on the new role."})
        first = li_congrats.run(dry=False)
        assert len(first["drafted"]) == 1

        monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: {"draft": "SHOULD NOT APPEAR"})
        second = li_congrats.run(dry=False)
        assert second["drafted"] == []  # already drafted, skipped
