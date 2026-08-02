#!/usr/bin/env python3
"""Unit tests for agents/convo_dedupe.py (C174 same-company dedupe, C180 multi-party
detection). All pure functions, all fixtures.

Run: .venv/bin/python -m pytest tests/test_convo_dedupe.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import convo_dedupe  # noqa: E402


class TestNormalizeCompany:
    def test_strips_llc_suffix(self):
        assert convo_dedupe.normalize_company("Legacy Plumbing, LLC.") == "legacy plumbing"

    def test_strips_inc_suffix(self):
        assert convo_dedupe.normalize_company("Acme Inc") == "acme"

    def test_no_suffix_unchanged_lowercase(self):
        assert convo_dedupe.normalize_company("Legacy Plumbing") == "legacy plumbing"

    def test_collapses_whitespace(self):
        assert convo_dedupe.normalize_company("  Legacy   Plumbing   Inc  ") == "legacy plumbing"

    def test_empty_string_returns_empty(self):
        assert convo_dedupe.normalize_company("") == ""

    def test_none_returns_empty(self):
        assert convo_dedupe.normalize_company(None) == ""

    def test_different_companies_stay_different(self):
        assert convo_dedupe.normalize_company("Nimbus") != convo_dedupe.normalize_company("Best Co")

    def test_punctuation_stripped(self):
        assert convo_dedupe.normalize_company("Braydon's Plumbing & Sons, LLC.") == \
               convo_dedupe.normalize_company("Braydons Plumbing Sons")


class TestGroupByCompany:
    def test_two_rows_same_company_grouped(self):
        rows = [{"company": "Legacy Plumbing LLC"}, {"company": "Legacy Plumbing"}]
        groups = convo_dedupe.group_by_company(rows)
        assert len(groups) == 1
        assert len(list(groups.values())[0]) == 2

    def test_rows_with_no_company_never_grouped_together(self):
        rows = [{"company": ""}, {"company": None}, {"company": "  "}]
        groups = convo_dedupe.group_by_company(rows)
        assert groups == {}

    def test_different_companies_separate_groups(self):
        rows = [{"company": "Acme"}, {"company": "Best Co"}]
        groups = convo_dedupe.group_by_company(rows)
        assert len(groups) == 2


class TestSameCompanyFlags:
    def test_two_contacts_same_company_both_flagged(self):
        rows = [
            {"contact_id": "a", "name": "Braydon", "company": "Legacy Plumbing LLC"},
            {"contact_id": "b", "name": "Sam", "company": "Legacy Plumbing"},
        ]
        flags = convo_dedupe.same_company_flags(rows)
        assert "a" in flags and "b" in flags
        assert "Sam" in flags["a"]
        assert "Braydon" in flags["b"]

    def test_solo_company_not_flagged(self):
        rows = [
            {"contact_id": "a", "name": "Braydon", "company": "Legacy Plumbing"},
            {"contact_id": "c", "name": "Unrelated", "company": "Other Co"},
        ]
        flags = convo_dedupe.same_company_flags(rows)
        assert flags == {}

    def test_three_contacts_same_company_all_flagged(self):
        rows = [
            {"contact_id": "a", "name": "A", "company": "Acme"},
            {"contact_id": "b", "name": "B", "company": "Acme"},
            {"contact_id": "c", "name": "C", "company": "Acme"},
        ]
        flags = convo_dedupe.same_company_flags(rows)
        assert set(flags) == {"a", "b", "c"}
        assert "B" in flags["a"] and "C" in flags["a"]

    def test_empty_candidates_no_flags(self):
        assert convo_dedupe.same_company_flags([]) == {}

    def test_missing_contact_id_falls_back_defensively(self):
        rows = [{"name": "A", "company": "Acme"}, {"name": "B", "company": "Acme"}]
        flags = convo_dedupe.same_company_flags(rows)
        assert len(flags) == 2  # doesn't crash, uses a fallback key per row


class TestDetectMultiParty:
    def test_no_signal_returns_false(self):
        ok, detail = convo_dedupe.detect_multi_party("sounds good, thanks")
        assert not ok
        assert detail == ""

    def test_empty_message_returns_false(self):
        ok, _ = convo_dedupe.detect_multi_party("")
        assert not ok

    def test_partner_mention_detected(self):
        ok, detail = convo_dedupe.detect_multi_party("my partner wants to see the proposal too")
        assert ok
        assert detail

    def test_wife_mention_detected(self):
        ok, _ = convo_dedupe.detect_multi_party("let me ask my wife about the budget")
        assert ok

    def test_cc_mention_detected(self):
        ok, _ = convo_dedupe.detect_multi_party("cc jamie@acme.com on replies please")
        assert ok

    def test_looping_in_detected(self):
        ok, _ = convo_dedupe.detect_multi_party("looping in my co-founder here")
        assert ok

    def test_we_both_language_detected(self):
        ok, _ = convo_dedupe.detect_multi_party("we both think this looks great")
        assert ok

    def test_second_email_address_detected(self):
        ok, detail = convo_dedupe.detect_multi_party(
            "reach out to jamie@acme.com about this", known_email="alex@riveradigital.com")
        assert ok
        assert "jamie@acme.com" in detail

    def test_same_known_email_not_flagged(self):
        ok, _ = convo_dedupe.detect_multi_party(
            "reach out to jamie@acme.com if needed", known_email="jamie@acme.com")
        assert not ok

    def test_word_boundary_avoids_cc_substring_false_positive(self):
        ok, _ = convo_dedupe.detect_multi_party("accessories are on sale this week")
        assert not ok

    def test_another_cc_substring_false_positive_guard(self):
        ok, _ = convo_dedupe.detect_multi_party("that was a successful launch")
        assert not ok

    def test_unrelated_manager_mention_not_over_triggered_falsely(self):
        # 'my manager' IS a real multi-party signal (a decision-maker other than the
        # contact) -- confirm it's intentionally caught, not a false positive to avoid
        ok, _ = convo_dedupe.detect_multi_party("my manager approved the budget")
        assert ok
