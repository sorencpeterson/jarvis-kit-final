#!/usr/bin/env python3
"""Unit tests for agents/li_quality.py — validate_draft() against 10 good/bad
fixture drafts (mission requirement: include an emoji one, a two-question one,
a 400-char one). Pure, no LLM, no network, no store mutation.

Run: .venv/bin/python -m pytest tests/test_li_quality.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import li_quality  # noqa: E402


# ---- 10 good/bad fixture drafts (mission-required minimum) ----

GOOD_FIXTURES = [
    "Ran into this exact issue scaling a client site last year, ended up sharding the queue and it held.",
    "Curious how you handled the migration downtime, did you stage it or go all at once?",
    "That's the part most agencies miss until the third rebuild. Learned it the hard way too.",
    "Same pattern on our end. Fulfillment was the bottleneck, not sales.",
    "Appreciate you laying out the numbers, not many people show the actual margin.",
]

BAD_FIXTURES = [
    ("Great post! Love your energy 🔥 So inspiring!", "emoji"),  # emoji + banned phrase
    ("What stack are you on? And did you consider the alternative?", "too_many_questions"),  # 2 questions
    ("A" * 500, "too_long"),  # over the 420 cap (raised from 300 for the 4-part formula, 2026-07-11)
    ("Huge fan of your work, would love to connect and pick your brain sometime!", "banned_phrase"),
    ("", "empty"),
]


class TestGoodDraftsPass:
    def test_all_good_fixtures_pass(self):
        for text in GOOD_FIXTURES:
            v = li_quality.validate_draft(text)
            assert v["ok"], f"expected pass, got {v['reasons']} for: {text!r}"


class TestBadDraftsFail:
    def test_all_bad_fixtures_fail(self):
        for text, expect_substr in BAD_FIXTURES:
            v = li_quality.validate_draft(text)
            assert not v["ok"], f"expected fail for: {text!r}"
            assert any(expect_substr in r for r in v["reasons"]), (
                f"expected a reason containing {expect_substr!r}, got {v['reasons']}"
            )


class TestTenFixturesExactly:
    def test_exactly_five_good_five_bad_defined(self):
        # mission requirement: 10 good/bad fixtures total
        assert len(GOOD_FIXTURES) == 5
        assert len(BAD_FIXTURES) == 5


# ---- targeted rule tests ----

class TestEmoji:
    def test_detects_emoji(self):
        assert li_quality.has_emoji("nice work 🔥")

    def test_detects_emoji_variation_selector(self):
        assert li_quality.has_emoji("100% ✅")  # check mark

    def test_no_false_positive_on_plain_text(self):
        assert not li_quality.has_emoji("This is a normal sentence with punctuation!")

    def test_no_false_positive_on_currency_and_math(self):
        assert not li_quality.has_emoji("$1,200 at 20% margin, not bad.")


class TestCharCap:
    def test_exactly_420_chars_passes(self):
        text = "x" * 420
        v = li_quality.validate_draft(text)
        assert not any("too_long" in r for r in v["reasons"])

    def test_421_chars_fails(self):
        text = "x" * 421
        v = li_quality.validate_draft(text)
        assert any("too_long" in r for r in v["reasons"])

    def test_500_char_fixture_fails_with_length_reason(self):
        v = li_quality.validate_draft("A" * 500)
        assert not v["ok"]
        assert any(r.startswith("too_long:500>420") for r in v["reasons"])


class TestQuestionRule:
    def test_zero_questions_ok(self):
        v = li_quality.validate_draft("Ran into this exact issue last month.")
        assert not any("too_many_questions" in r for r in v["reasons"])

    def test_one_question_ok(self):
        v = li_quality.validate_draft("What stack did you land on?")
        assert not any("too_many_questions" in r for r in v["reasons"])

    def test_two_questions_fails(self):
        v = li_quality.validate_draft("What stack? And did it scale?")
        assert any("too_many_questions:2" in r for r in v["reasons"])


class TestLinkHygiene:
    def test_link_allowed_when_not_first_touch(self):
        v = li_quality.validate_draft("Sure, here's the case study: https://example.com/case", first_touch=False)
        assert not any("link_in_first_touch" in r for r in v["reasons"])

    def test_link_banned_on_first_touch(self):
        v = li_quality.validate_draft("Check out my site https://example.com", first_touch=True)
        assert any("link_in_first_touch" in r for r in v["reasons"])

    def test_www_style_link_also_caught(self):
        v = li_quality.validate_draft("See www.example.com for details", first_touch=True)
        assert any("link_in_first_touch" in r for r in v["reasons"])


class TestBannedPhrases:
    def test_case_insensitive_match(self):
        v = li_quality.validate_draft("LOVE YOUR ENERGY, this is great")
        assert any("banned_phrase" in r for r in v["reasons"])

    def test_clean_text_no_banned_phrase(self):
        assert li_quality.find_banned_phrase("Ran into this exact bottleneck too") is None


class TestNameCorrectness:
    def test_generic_name_flagged(self):
        v = li_quality.validate_draft("Good to connect with you.", name="there")
        assert any("generic_name" in r for r in v["reasons"])

    def test_real_name_not_flagged(self):
        v = li_quality.validate_draft("Good to connect with you.", name="Jordan Ross")
        assert not any("generic_name" in r for r in v["reasons"])

    def test_email_derived_placeholder_flagged(self):
        assert li_quality.name_looks_generic("jsmith2019")

    def test_normal_two_word_name_not_generic(self):
        assert not li_quality.name_looks_generic("Darius Gordon")


class TestNeverEngage:
    def test_mlm_pattern_flagged(self):
        reason = li_quality.is_never_engage("Join my team, be your own boss, unlimited earning potential!")
        assert reason == "mlm-pattern"

    def test_normal_business_post_not_flagged(self):
        reason = li_quality.is_never_engage("We closed our Q2 numbers, revenue up 20% YoY.")
        assert reason is None


class TestTargetContentScreen:
    def test_profanity_flagged(self):
        v = li_quality.screen_target_content("This industry is fucking broken honestly")
        assert not v["ok"]
        assert "profanity" in v["reason"]

    def test_clean_content_passes(self):
        v = li_quality.screen_target_content("Revenue is up and margins are healthy this quarter.")
        assert v["ok"]


class TestLengthVariance:
    def test_uniform_lengths_flagged_not_ok(self):
        drafts = ["a" * 50, "b" * 51, "c" * 49, "d" * 50, "e" * 50]
        assert not li_quality.length_variance_ok(drafts)

    def test_varied_lengths_ok(self):
        drafts = ["short one", "a" * 40, "b" * 200, "c" * 90]
        assert li_quality.length_variance_ok(drafts)

    def test_small_batch_always_passes(self):
        # under 4 items, not enough signal to judge
        assert li_quality.length_variance_ok(["a" * 50, "b" * 50])


class TestValidateBatch:
    def test_batch_returns_one_verdict_per_item(self):
        items = [{"draft": "Ran into this same issue last quarter.", "author": "Jordan Ross"},
                 {"draft": "Great post! Love your energy!", "author": "there"}]
        out = li_quality.validate_batch(items)
        assert len(out) == 2
        assert out[0]["ok"]
        assert not out[1]["ok"]

    def test_never_raises_on_missing_keys(self):
        out = li_quality.validate_batch([{}])
        assert len(out) == 1
        assert not out[0]["ok"]


class TestNeverRaises:
    def test_none_input(self):
        v = li_quality.validate_draft(None)
        assert v["ok"] is False

    def test_non_string_name(self):
        v = li_quality.validate_draft("hello there friend", name=None)
        assert isinstance(v, dict)
