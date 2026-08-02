#!/usr/bin/env python3
"""Unit tests for agents/li_pipeline.py — the composed sourcing pipeline
(li_history dedupe/cooldown -> li_quality never-engage hard filter -> li_scoring
rank+floor -> li_budget diversity -> li_whythem why-line). This IS the mission's
explicit VERIFY requirement ("RUN networking sourcing in dry/fixture mode"),
committed as a regression suite so the end-to-end wiring stays correct.

fixture=True (the default and what these tests use throughout) makes every run
100% deterministic: zero LLM calls (li_whythem falls back to a fixed string
instead of calling planner._cli), zero network, zero store mutation. All tests
isolate networking.QUEUE to tmp_path.

Run: .venv/bin/python -m pytest tests/test_li_pipeline.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import networking  # noqa: E402
import li_pipeline  # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(networking, "QUEUE", tmp_path / "network.jsonl")
    return tmp_path


class TestFixtureCandidatesShape:
    def test_fixture_candidates_clearly_marked(self):
        # mission requirement: "NO invented LinkedIn data ever: fixtures clearly marked"
        for c in li_pipeline.FIXTURE_CANDIDATES:
            assert "FIXTURE" in c.get("name", "") or "FIXTURE" in c.get("headline", "")

    def test_at_least_five_fixture_candidates(self):
        assert len(li_pipeline.FIXTURE_CANDIDATES) >= 5


class TestRunPipelineEndToEnd:
    def test_full_fixture_run_zero_llm_zero_mutation(self, isolated, monkeypatch):
        called = {"n": 0}
        import planner
        monkeypatch.setattr(planner, "_cli", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or "SHOULD NOT BE CALLED")
        monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {"draft": "SHOULD NOT BE CALLED"})

        result = li_pipeline.run_pipeline(li_pipeline.FIXTURE_CANDIDATES, fixture=True)

        assert called["n"] == 0  # fixture mode: zero LLM calls anywhere in the pipeline
        assert result["input_n"] == len(li_pipeline.FIXTURE_CANDIDATES)
        assert result["fixture_mode"] is True
        assert not (isolated / "network.jsonl").exists()  # never writes to the queue

    def test_strong_target_survives_to_the_end(self, isolated):
        result = li_pipeline.run_pipeline(li_pipeline.FIXTURE_CANDIDATES, fixture=True)
        names = [t["name"] for t in result["queued_ready"]]
        assert "FIXTURE Founder Agency Owner" in names

    def test_weak_target_dropped_by_score_floor(self, isolated):
        result = li_pipeline.run_pipeline(li_pipeline.FIXTURE_CANDIDATES, fixture=True)
        names = [t["name"] for t in result["queued_ready"]]
        assert "FIXTURE Weak Target" not in names

    def test_mlm_target_dropped_by_never_engage(self, isolated):
        result = li_pipeline.run_pipeline(li_pipeline.FIXTURE_CANDIDATES, fixture=True)
        names = [t["name"] for t in result["queued_ready"]]
        assert "FIXTURE MLM Person" not in names

    def test_disguised_high_scoring_mlm_still_caught(self, isolated):
        # this is the load-bearing test: a candidate with enough ICP keywords
        # + mutuals + recency to CLEAR the score floor, but that is still an
        # MLM pattern -- must be caught by the explicit li_quality.is_never_engage
        # hard filter, not accidentally rely on scoring low
        result = li_pipeline.run_pipeline(li_pipeline.FIXTURE_CANDIDATES, fixture=True)
        names = [t["name"] for t in result["queued_ready"]]
        assert "FIXTURE Disguised MLM High Scorer" not in names
        # sanity: prove it WOULD have cleared the score floor on its own
        import li_scoring
        disguised = next(c for c in li_pipeline.FIXTURE_CANDIDATES if c["name"] == "FIXTURE Disguised MLM High Scorer")
        v = li_scoring.score_target(disguised)
        assert v["score"] >= 50  # high enough that score-floor alone would NOT have dropped it

    def test_funnel_counts_monotonically_non_increasing(self, isolated):
        result = li_pipeline.run_pipeline(li_pipeline.FIXTURE_CANDIDATES, fixture=True)
        counts = [result["input_n"], result["after_dedupe"], result["after_cooldown"],
                  result["after_never_engage"], result["after_score_floor"], result["after_diversity"]]
        for i in range(len(counts) - 1):
            assert counts[i] >= counts[i + 1], f"funnel count increased at step {i}"

    def test_every_survivor_has_why_line(self, isolated):
        result = li_pipeline.run_pipeline(li_pipeline.FIXTURE_CANDIDATES, fixture=True)
        for t in result["queued_ready"]:
            assert t.get("_why")

    def test_every_survivor_has_score(self, isolated):
        result = li_pipeline.run_pipeline(li_pipeline.FIXTURE_CANDIDATES, fixture=True)
        for t in result["queued_ready"]:
            assert "_score" in t
            assert t["_score"] >= 0

    def test_previously_touched_target_excluded(self, isolated, tmp_path):
        # write a fixture into the queue matching one of the pipeline candidates' URL
        target_url = li_pipeline.FIXTURE_CANDIDATES[0]["url"]
        with (tmp_path / "network.jsonl").open("a") as f:
            f.write(json.dumps({"id": "prior1", "kind": "connect", "author": "X", "target": "",
                                 "url": target_url, "draft": "", "status": "done",
                                 "created": "2026-01-01T08:00:00-07:00"}) + "\n")
        result = li_pipeline.run_pipeline(li_pipeline.FIXTURE_CANDIDATES, fixture=True)
        urls = [t["url"] for t in result["queued_ready"]]
        assert target_url not in urls

    def test_empty_candidates_returns_empty_ready_list(self, isolated):
        result = li_pipeline.run_pipeline([], fixture=True)
        assert result["input_n"] == 0
        assert result["queued_ready"] == []

    def test_custom_score_floor_overrides_config(self, isolated):
        # setting a floor ABOVE the max possible score (100, see li_scoring's own
        # cap) must drop everything, even a perfect-scoring target
        result = li_pipeline.run_pipeline(li_pipeline.FIXTURE_CANDIDATES, fixture=True, score_floor=100.1)
        assert result["queued_ready"] == []

    def test_zero_score_floor_keeps_more_targets(self, isolated):
        result_default = li_pipeline.run_pipeline(li_pipeline.FIXTURE_CANDIDATES, fixture=True)
        result_zero_floor = li_pipeline.run_pipeline(li_pipeline.FIXTURE_CANDIDATES, fixture=True, score_floor=0.0)
        assert len(result_zero_floor["queued_ready"]) >= len(result_default["queued_ready"])


class TestNeverRaisesOnMalformedCandidates:
    def test_candidate_missing_fields_never_crashes(self, isolated):
        result = li_pipeline.run_pipeline([{}], fixture=True)
        assert isinstance(result, dict)  # never raises, just scores it low and likely drops it

    def test_candidate_with_no_url_handled(self, isolated):
        result = li_pipeline.run_pipeline([{"name": "No URL Person", "headline": "Founder"}], fixture=True)
        assert isinstance(result, dict)
