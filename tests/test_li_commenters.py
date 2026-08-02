#!/usr/bin/env python3
"""Unit tests for agents/li_commenters.py (A2, star item: engaged-commenters
mining with post context attached). Pure/deterministic by default (allow_llm
defaults to False everywhere in this suite).

Run: .venv/bin/python -m pytest tests/test_li_commenters.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import li_commenters  # noqa: E402


def _row(**kw) -> dict:
    base = {"commenter_name": "FIXTURE Person", "commenter_headline": "Founder @ FIXTURE Agency",
            "commenter_url": "https://linkedin.com/in/fixture-person",
            "post_author": "FIXTURE Poster", "post_url": "https://linkedin.com/posts/fixture",
            "post_context": "Struggling with fulfillment.", "comment_text": "Same here."}
    base.update(kw)
    return base


class TestCommenterToCandidate:
    def test_reshapes_into_scoring_candidate_shape(self):
        c = li_commenters.commenter_to_candidate(_row())
        assert c["name"] == "FIXTURE Person"
        assert c["headline"] == "Founder @ FIXTURE Agency"
        assert c["url"] == "https://linkedin.com/in/fixture-person"

    def test_is_commenter_always_true(self):
        c = li_commenters.commenter_to_candidate(_row())
        assert c["is_commenter"] is True

    def test_post_context_includes_both_post_and_comment_text(self):
        c = li_commenters.commenter_to_candidate(_row())
        assert "Struggling with fulfillment" in c["post_context"]
        assert "Same here" in c["post_context"]
        assert "FIXTURE Poster" in c["post_context"]

    def test_post_url_and_author_preserved(self):
        c = li_commenters.commenter_to_candidate(_row())
        assert c["post_url"] == "https://linkedin.com/posts/fixture"
        assert c["post_author"] == "FIXTURE Poster"

    def test_missing_fields_never_raise(self):
        c = li_commenters.commenter_to_candidate({})
        assert c["name"] == ""
        assert c["is_commenter"] is True


class TestScoreCommenters:
    def test_empty_list_returns_empty(self):
        assert li_commenters.score_commenters([]) == []

    def test_scored_and_sorted(self):
        rows = [
            _row(commenter_name="Weak", commenter_headline="Student", commenter_url="https://linkedin.com/in/weak"),
            _row(commenter_name="Strong", commenter_headline="Founder @ Digital Agency",
                 commenter_url="https://linkedin.com/in/strong"),
        ]
        result = li_commenters.score_commenters(rows)
        assert result[0]["name"] == "Strong"  # higher score sorts first

    def test_why_line_uses_post_context_not_llm(self, monkeypatch):
        import planner
        called = {"n": 0}
        monkeypatch.setattr(planner, "_cli", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or "x")
        result = li_commenters.score_commenters([_row()], allow_llm=False)
        assert called["n"] == 0  # post_context is always truthy here, so why_them's LLM path never fires
        assert "Struggling with fulfillment" in result[0]["_why"]

    def test_every_result_has_score_and_why(self):
        result = li_commenters.score_commenters([_row()])
        assert "_score" in result[0]
        assert "_why" in result[0]
        assert result[0]["_why"]

    def test_zero_score_never_crashes_pipeline(self):
        # a commenter row with literally nothing but a name should still score
        # (very low) rather than raise
        result = li_commenters.score_commenters([{"commenter_name": "Bare"}])
        assert len(result) == 1
        assert result[0]["_score"] >= 0
