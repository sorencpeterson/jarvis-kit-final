#!/usr/bin/env python3
"""Unit tests for agents/li_whythem.py (A5 per-target why-them one-liner).
No real LLM calls in this suite — the LLM fallback path is exercised with a
mocked planner._cli, and allow_llm=False is tested directly (zero cost, fully
deterministic).

Run: .venv/bin/python -m pytest tests/test_li_whythem.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import planner  # noqa: E402
import li_scoring  # noqa: E402
import li_whythem  # noqa: E402


class TestDeterministicLine:
    def test_rich_target_produces_specific_line(self):
        target = {"headline": "Founder @ Acme Digital Agency | White-label web for agencies",
                  "location": "Austin, Texas Area", "mutuals_count": 8, "is_commenter": True}
        scored = li_scoring.score_target(target)
        line = li_whythem._deterministic_line(target, scored)
        assert line
        assert "mutual" in line.lower()

    def test_thin_target_returns_empty_signals_fallback_needed(self):
        target = {"headline": "", "location": "", "mutuals_count": 0}
        scored = {"components": {}, "tier": 0}
        line = li_whythem._deterministic_line(target, scored)
        assert line == ""

    def test_title_lexicon_hit_included(self):
        target = {"headline": "Founder @ Acme Agency", "location": ""}
        scored = li_scoring.score_target(target)
        line = li_whythem._deterministic_line(target, scored)
        assert "Founder" in line

    def test_low_mutuals_not_mentioned(self):
        target = {"headline": "Founder @ Acme Agency", "location": "", "mutuals_count": 1}
        scored = li_scoring.score_target(target)
        line = li_whythem._deterministic_line(target, scored)
        assert "mutual" not in line.lower()

    def test_commenter_flag_included(self):
        target = {"headline": "", "location": "", "is_commenter": True}
        scored = {"components": {"engagement_context": 10.0}, "tier": 0}
        line = li_whythem._deterministic_line(target, scored)
        assert "engaged" in line.lower()

    def test_never_raises_on_empty_dict(self):
        line = li_whythem._deterministic_line({}, {"components": {}, "tier": 0})
        assert line == ""


class TestWhyThem:
    def test_rich_target_uses_deterministic_no_llm_call(self, monkeypatch):
        called = {"n": 0}
        monkeypatch.setattr(planner, "_cli", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or "SHOULD NOT BE USED")
        target = {"headline": "Founder @ Acme Agency", "location": "Austin", "mutuals_count": 5}
        line = li_whythem.why_them(target)
        assert called["n"] == 0  # deterministic path taken, LLM never invoked
        assert "SHOULD NOT BE USED" not in line

    def test_thin_target_falls_back_to_llm(self, monkeypatch):
        monkeypatch.setattr(planner, "_cli", lambda *a, **k: "Specific reason from their post content.")
        target = {"headline": "", "location": "", "post_context": "their post text"}
        line = li_whythem.why_them(target, allow_llm=True)
        assert line == "Specific reason from their post content."

    def test_allow_llm_false_never_calls_llm(self, monkeypatch):
        called = {"n": 0}
        monkeypatch.setattr(planner, "_cli", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or "x")
        target = {"headline": "", "location": ""}
        line = li_whythem.why_them(target, allow_llm=False)
        assert called["n"] == 0
        assert line == "insufficient signal captured at sourcing time"

    def test_llm_empty_output_falls_back_to_default_line(self, monkeypatch):
        monkeypatch.setattr(planner, "_cli", lambda *a, **k: "")
        target = {"headline": "", "location": ""}
        line = li_whythem.why_them(target, allow_llm=True)
        assert line == "insufficient signal captured at sourcing time"

    def test_precomputed_scored_reused_not_recomputed(self, monkeypatch):
        # if scored is passed in, why_them should use IT directly rather than
        # calling score_target() again internally. Starve every field
        # _deterministic_line reads from (both target AND scored) so the only
        # way the LLM path fires is if the passed-in `scored` was honored
        # (if why_them silently recomputed scored from `target`, this test's
        # target has zero signal anyway, so either way should hit the LLM --
        # what this really guards is that why_them does NOT call
        # li_scoring.score_target() when scored is already provided).
        target = {"headline": "", "location": "", "mutuals_count": 0}
        fake_scored = {"components": {}, "tier": 0}
        monkeypatch.setattr(li_scoring, "score_target",
                             lambda *a, **k: (_ for _ in ()).throw(AssertionError("score_target should not be called")))
        monkeypatch.setattr(planner, "_cli", lambda *a, **k: "used the passed-in scored dict")
        line = li_whythem.why_them(target, scored=fake_scored, allow_llm=True)
        assert line == "used the passed-in scored dict"
