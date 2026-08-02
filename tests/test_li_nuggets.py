#!/usr/bin/env python3
"""Unit tests for agents/li_nuggets.py (A34 niche-specific value-nugget bank).
Pure, no LLM, no network, no store access.

Run: .venv/bin/python -m pytest tests/test_li_nuggets.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import li_nuggets  # noqa: E402


class TestNuggetBankShape:
    def test_every_niche_has_exactly_ten(self):
        # A34's explicit spec: "10 per niche"
        for niche, pool in li_nuggets.NUGGETS.items():
            assert len(pool) == 10, f"{niche} has {len(pool)}, expected 10"

    def test_at_least_three_niches_defined(self):
        assert len(li_nuggets.NUGGETS) >= 3

    def test_agency_niche_present(self):
        # the primary ICP per icp-and-personas.md
        assert "agency" in li_nuggets.NUGGETS

    def test_no_empty_nugget_strings(self):
        for niche, pool in li_nuggets.NUGGETS.items():
            for n in pool:
                assert n.strip(), f"empty nugget in {niche}"

    def test_no_duplicate_nuggets_within_a_niche(self):
        for niche, pool in li_nuggets.NUGGETS.items():
            assert len(pool) == len(set(pool)), f"duplicate nugget in {niche}"

    def test_no_em_dashes_in_any_nugget(self):
        # matches VOICE-SPEC.md's hard rule 1, since these get dropped into
        # drafting prompts and eventually public-facing DMs
        for niche, pool in li_nuggets.NUGGETS.items():
            for n in pool:
                assert "—" not in n and "–" not in n, f"em/en dash in {niche}: {n!r}"


class TestNuggetsFor:
    def test_known_niche_returns_its_pool(self):
        assert li_nuggets.nuggets_for("agency") == li_nuggets.NUGGETS["agency"]

    def test_case_insensitive(self):
        assert li_nuggets.nuggets_for("AGENCY") == li_nuggets.NUGGETS["agency"]

    def test_unknown_niche_falls_back_to_default(self):
        result = li_nuggets.nuggets_for("totally-unknown-niche-xyz")
        assert result == li_nuggets.NUGGETS[li_nuggets.DEFAULT_NICHE]

    def test_empty_niche_falls_back_to_default(self):
        result = li_nuggets.nuggets_for("")
        assert result == li_nuggets.NUGGETS[li_nuggets.DEFAULT_NICHE]

    def test_returns_a_copy_not_the_original_list(self):
        # caller mutating the returned list must not corrupt the bank
        result = li_nuggets.nuggets_for("agency")
        result.append("SHOULD NOT PERSIST")
        assert "SHOULD NOT PERSIST" not in li_nuggets.NUGGETS["agency"]


class TestRandomNugget:
    def test_deterministic_for_same_seed(self):
        a = li_nuggets.random_nugget("agency", seed="https://linkedin.com/in/alice")
        b = li_nuggets.random_nugget("agency", seed="https://linkedin.com/in/alice")
        assert a == b

    def test_different_seeds_can_differ(self):
        seeds_results = {li_nuggets.random_nugget("agency", seed=f"seed{i}") for i in range(20)}
        assert len(seeds_results) > 1  # not literally always the same nugget

    def test_no_seed_returns_first(self):
        assert li_nuggets.random_nugget("agency", seed="") == li_nuggets.NUGGETS["agency"][0]

    def test_result_always_in_pool(self):
        for i in range(10):
            n = li_nuggets.random_nugget("agency", seed=f"x{i}")
            assert n in li_nuggets.NUGGETS["agency"]

    def test_unknown_niche_still_works(self):
        n = li_nuggets.random_nugget("unknown-niche", seed="x")
        assert n in li_nuggets.NUGGETS[li_nuggets.DEFAULT_NICHE]


class TestListNiches:
    def test_sorted(self):
        niches = li_nuggets.list_niches()
        assert niches == sorted(niches)

    def test_matches_bank_keys(self):
        assert set(li_nuggets.list_niches()) == set(li_nuggets.NUGGETS.keys())
