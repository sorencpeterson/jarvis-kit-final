#!/usr/bin/env python3
"""Pytest suite for capture/pull_reminders.py's pure fuzzy-dedup helpers
(E330). No AppleScript, no store I/O — _normalize_for_match and _fuzzy_dupe
only. Loaded via importlib since capture/ isn't on the default module path
(mirrors how the file itself expects to be run: `from the second-brain folder`).

Run: .venv/bin/python -m pytest tests/test_pull_reminders.py -v
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))

_spec = importlib.util.spec_from_file_location("pull_reminders", ROOT / "capture" / "pull_reminders.py")
pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr)


class TestNormalizeForMatch:
    def test_lowercases(self):
        assert pr._normalize_for_match("Call The Bank") == pr._normalize_for_match("call the bank")

    def test_strips_punctuation(self):
        assert pr._normalize_for_match("Call the bank!") == pr._normalize_for_match("Call the bank")

    def test_collapses_whitespace(self):
        assert pr._normalize_for_match("call   the  bank") == "call the bank"

    def test_empty(self):
        assert pr._normalize_for_match("") == ""

    def test_none_safe(self):
        assert pr._normalize_for_match(None) == ""


class TestFuzzyDupe:
    def test_exact_match_different_case(self):
        existing = [pr._normalize_for_match("call the bank")]
        assert pr._fuzzy_dupe("Call the Bank", existing) is True

    def test_punctuation_only_difference(self):
        existing = [pr._normalize_for_match("call the bank")]
        assert pr._fuzzy_dupe("call the bank!", existing) is True

    def test_unrelated_text_not_dupe(self):
        existing = [pr._normalize_for_match("call the bank")]
        assert pr._fuzzy_dupe("buy groceries", existing) is False

    def test_empty_candidate_never_dupe(self):
        existing = [pr._normalize_for_match("call the bank")]
        assert pr._fuzzy_dupe("", existing) is False

    def test_empty_existing_list_never_dupe(self):
        assert pr._fuzzy_dupe("call the bank", []) is False

    def test_real_world_template_collision_not_flagged(self):
        # Regression guard for the exact false-positive found while calibrating
        # FUZZY_THRESHOLD against the real store: two DIFFERENT companies
        # sharing an auto-generated template sentence must NOT dedupe against
        # each other (ratio 0.877 in the real data — must stay below threshold).
        existing = [pr._normalize_for_match(
            "Revive stale deal: Cedarview Landscaping (draft ready in dashboard)")]
        candidate = "Revive stale deal: Imperial Lawn & Landscape (draft ready in dashboard)"
        assert pr._fuzzy_dupe(candidate, existing) is False

    def test_threshold_is_calibrated_above_real_template_ceiling(self):
        # Documents WHY the threshold is 0.95: the worst real same-template/
        # different-entity collision found was 0.877. This asserts the
        # constant stays safely above that ceiling even if someone tweaks it
        # later without re-checking against real data.
        assert pr.FUZZY_THRESHOLD > 0.90

    def test_matches_multiple_candidates_any_hit(self):
        existing = [pr._normalize_for_match("buy groceries"),
                    pr._normalize_for_match("call the bank")]
        assert pr._fuzzy_dupe("Call The Bank", existing) is True
