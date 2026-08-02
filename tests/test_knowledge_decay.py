#!/usr/bin/env python3
"""Pytest suite for agents/knowledge_decay.py's pure parse/compare logic.
No network. get_pricing_factory() does a real import of proposal_factory
(cheap, local, no side effects) to prove it reads the REAL PRICING dict, not
a mock — the actual end-to-end real-vs-doc comparison was verified manually
against the live pricing-tree.md.

Run: .venv/bin/python -m pytest tests/test_knowledge_decay.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import knowledge_decay as kd  # noqa: E402


SAMPLE_MD = """# Pricing Decision Tree

## The ladder
| SKU | Price | When |
|---|---|---|
| Landing page | $800 | Single-page |
| Standard site | $1,200 | 3-6 pages |
| Care Basic | $75/mo | Hosting watch |

## Routing rules
Some other content that should NOT be parsed as pricing.
| Not a real | $999 | row from another section |
"""


class TestParsePricingTree:
    def test_parses_the_ladder_table(self):
        result = kd.parse_pricing_tree(SAMPLE_MD)
        assert result["Landing page"] == 800
        assert result["Standard site"] == 1200

    def test_handles_comma_thousands(self):
        result = kd.parse_pricing_tree(SAMPLE_MD)
        assert result["Standard site"] == 1200

    def test_strips_per_month_suffix(self):
        result = kd.parse_pricing_tree(SAMPLE_MD)
        assert result["Care Basic"] == 75

    def test_ignores_rows_outside_the_ladder_section(self):
        result = kd.parse_pricing_tree(SAMPLE_MD)
        assert "Not a real" not in result

    def test_empty_text(self):
        assert kd.parse_pricing_tree("") == {}

    def test_no_ladder_section_at_all(self):
        text = "# Doc\n\n## Other section\n| SKU | Price | When |\n|---|---|---|\n| X | $100 | Y |\n"
        assert kd.parse_pricing_tree(text) == {}


class TestGetPricingFactory:
    def test_reads_real_pricing_dict(self):
        # this is a REAL import, proving the checker reads the actual code,
        # not a re-parsed guess at it
        result = kd.get_pricing_factory()
        assert "Landing page" in result
        assert result["Landing page"] == 800  # matches PRICING["landing"]["price"] as of this writing

    def test_includes_care_tiers(self):
        result = kd.get_pricing_factory()
        assert "Care Basic" in result
        assert "Care Growth" in result


class TestCompare:
    def test_matched_when_prices_agree(self):
        result = kd.compare({"Landing page": 800}, {"Landing page": 800})
        assert len(result["matched"]) == 1
        assert result["mismatched"] == []

    def test_mismatch_when_prices_differ(self):
        result = kd.compare({"Landing page": 800}, {"Landing page": 900})
        assert result["mismatched"] == [
            {"doc_label": "Landing page", "code_label": "Landing page", "doc_price": 800, "code_price": 900}
        ]

    def test_doc_only_when_no_code_match(self):
        result = kd.compare({"Something New": 500}, {})
        assert result["doc_only"] == [{"label": "Something New", "price": 500}]

    def test_code_only_when_no_doc_match(self):
        result = kd.compare({}, {"Legacy SKU": 300})
        assert result["code_only"] == [{"label": "Legacy SKU", "price": 300}]

    def test_loose_substring_match_either_direction(self):
        # "Webfix" (doc) should match "Webfix bundle" (code) via substring containment
        result = kd.compare({"Webfix": 450}, {"Webfix bundle": 450})
        assert len(result["matched"]) == 1
        assert result["doc_only"] == []
        assert result["code_only"] == []

    def test_likely_same_by_price_suggestion(self):
        # regression guard for the exact real-world case found while
        # verifying: "Webfix bundle" (doc) vs "Site fix bundle" (code),
        # same $450, no substring overlap -> should surface as a suggestion
        result = kd.compare({"Webfix bundle": 450}, {"Site fix bundle": 450})
        assert len(result["doc_only"]) == 1
        assert len(result["code_only"]) == 1
        assert len(result["likely_same_by_price"]) == 1
        assert result["likely_same_by_price"][0]["price"] == 450

    def test_no_suggestion_when_prices_differ(self):
        result = kd.compare({"Doc SKU": 450}, {"Code SKU": 500})
        assert result["likely_same_by_price"] == []

    def test_each_code_entry_matched_at_most_once(self):
        # two doc entries with the same normalized label shouldn't both
        # claim the same code entry
        result = kd.compare({"Landing": 800, "Landing Page": 800}, {"Landing page": 800})
        total_matched_or_docmonly = len(result["matched"]) + len(result["doc_only"])
        assert total_matched_or_docmonly == 2
        assert len(result["matched"]) <= 1


class TestNorm:
    def test_strips_non_alphanumeric(self):
        assert kd._norm("E-com / Booking") == "ecombooking"

    def test_lowercases(self):
        assert kd._norm("WEBFIX") == "webfix"
