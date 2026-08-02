#!/usr/bin/env python3
"""Unit tests for agents/li_scoring.py (A1 relevance scoring v2, A12 mutuals,
A13 title lexicon, A14 geo tiering, A57 freshness bias). Pure, no LLM, no network.

Run: .venv/bin/python -m pytest tests/test_li_scoring.py -v
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import li_scoring  # noqa: E402


class TestTitleLexicon:
    def test_founder_hits(self):
        assert li_scoring.title_lexicon_hit("Founder at Acme Agency")

    def test_director_of_ops_hits(self):
        assert li_scoring.title_lexicon_hit("Director of Operations, Acme")

    def test_fractional_coo_hits(self):
        assert li_scoring.title_lexicon_hit("Fractional COO for growing agencies")

    def test_intern_does_not_hit(self):
        assert not li_scoring.title_lexicon_hit("Marketing Intern at Acme")

    def test_empty_headline_no_hit(self):
        assert not li_scoring.title_lexicon_hit("")


class TestIcpKeywords:
    def test_agency_keyword_counted(self):
        assert li_scoring.icp_keyword_hits("Runs a digital agency") >= 1

    def test_multiple_keywords_counted(self):
        n = li_scoring.icp_keyword_hits("White-label web design agency, client delivery focused")
        assert n >= 3

    def test_unrelated_headline_zero_hits(self):
        assert li_scoring.icp_keyword_hits("Professional dog walker and pet sitter") == 0


class TestGeoTier:
    def test_tier1_metro_matched(self):
        tier, tz = li_scoring.geo_tier("Austin, Texas Area")
        assert tier == 1
        assert tz == "America/Chicago"

    def test_unknown_us_location_tier2(self):
        tier, tz = li_scoring.geo_tier("Boise, Idaho")
        assert tier == 2

    def test_non_us_location_tier3(self):
        tier, tz = li_scoring.geo_tier("London, United Kingdom")
        assert tier == 3

    def test_empty_location_is_unknown_tier_not_default(self):
        # blank location must NOT silently become "known generic US" (tier 2) —
        # that would invent a positive geo_score for data that was never captured.
        tier, tz = li_scoring.geo_tier("")
        assert tier == li_scoring.UNKNOWN_GEO_TIER
        assert li_scoring.geo_score("") == 0.0

    def test_known_generic_us_location_scores_partial(self):
        # contrast case: a REAL non-metro US location (not blank) still gets tier 2
        assert li_scoring.geo_score("Boise, Idaho") == 7.0


class TestRecencyScore:
    def test_today_full_score(self):
        assert li_scoring.recency_score(date.today().isoformat()) == 25.0

    def test_two_days_ago_full_score(self):
        d = (date.today() - timedelta(days=2)).isoformat()
        assert li_scoring.recency_score(d) == 25.0

    def test_thirty_days_ago_zero(self):
        d = (date.today() - timedelta(days=30)).isoformat()
        assert li_scoring.recency_score(d) == 0.0

    def test_missing_date_never_guessed_zero(self):
        assert li_scoring.recency_score("") == 0.0

    def test_mid_range_decays_between_bounds(self):
        d = (date.today() - timedelta(days=15)).isoformat()
        score = li_scoring.recency_score(d)
        assert 0.0 < score < 25.0

    def test_malformed_date_returns_zero_not_crash(self):
        assert li_scoring.recency_score("not-a-date") == 0.0


class TestMutualSignal:
    def test_zero_mutuals_zero_score(self):
        assert li_scoring.mutual_signal_score(0) == 0.0

    def test_high_mutuals_capped(self):
        assert li_scoring.mutual_signal_score(500) <= 15.0

    def test_group_membership_adds_bonus(self):
        no_group = li_scoring.mutual_signal_score(5, in_target_group=False)
        with_group = li_scoring.mutual_signal_score(5, in_target_group=True)
        assert with_group > no_group

    def test_negative_mutuals_clamped_not_negative_score(self):
        assert li_scoring.mutual_signal_score(-5) == 0.0


class TestScoreTarget:
    def test_strong_target_scores_high(self):
        t = {"headline": "Founder @ Acme Digital Agency | White-label web for agencies",
             "location": "Austin, Texas Area", "last_active": date.today().isoformat(),
             "mutuals_count": 10, "is_commenter": True}
        v = li_scoring.score_target(t)
        assert v["score"] >= 80

    def test_weak_target_scores_low(self):
        t = {"headline": "Student", "location": "Unknown", "last_active": ""}
        v = li_scoring.score_target(t)
        assert v["score"] < 20

    def test_empty_dict_never_raises(self):
        v = li_scoring.score_target({})
        assert v["score"] == 0.0

    def test_score_capped_at_100(self):
        t = {"headline": "Founder Owner CEO Agency Agencies White-label Fulfillment",
             "location": "Austin, Texas Area", "last_active": date.today().isoformat(),
             "mutuals_count": 999, "in_target_group": True, "is_commenter": True}
        v = li_scoring.score_target(t)
        assert v["score"] <= 100.0

    def test_components_sum_to_score(self):
        t = {"headline": "Founder Agency", "location": "Chicago", "mutuals_count": 3}
        v = li_scoring.score_target(t)
        assert round(sum(v["components"].values()), 1) == v["score"]

    def test_missing_fields_never_invents_data(self):
        # a target with NO headline/location/activity should score entirely on
        # what's absent = 0, never a guessed/default positive value
        v = li_scoring.score_target({"headline": "", "location": "", "last_active": ""})
        assert v["score"] == 0.0
        assert all(c == 0.0 for c in v["components"].values())


class TestRankTargets:
    def test_sorted_best_first(self):
        targets = [
            {"headline": "Student", "location": ""},
            {"headline": "Founder @ Agency", "location": "Austin", "mutuals_count": 10,
             "last_active": date.today().isoformat()},
        ]
        ranked = li_scoring.rank_targets(targets)
        assert ranked[0]["_score"] >= ranked[1]["_score"]

    def test_floor_drops_low_scorers(self):
        targets = [{"headline": "Student", "location": ""}]
        ranked = li_scoring.rank_targets(targets, floor=50.0)
        assert ranked == []

    def test_floor_keeps_high_scorers(self):
        targets = [{"headline": "Founder @ Agency", "location": "Austin", "mutuals_count": 10,
                    "last_active": date.today().isoformat()}]
        ranked = li_scoring.rank_targets(targets, floor=50.0)
        assert len(ranked) == 1

    def test_original_fields_preserved(self):
        targets = [{"headline": "Founder", "location": "Austin", "url": "https://x.com/y"}]
        ranked = li_scoring.rank_targets(targets)
        assert ranked[0]["url"] == "https://x.com/y"

    def test_empty_list_returns_empty(self):
        assert li_scoring.rank_targets([]) == []
