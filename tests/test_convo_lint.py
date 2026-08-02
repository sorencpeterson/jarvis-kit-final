#!/usr/bin/env python3
"""Unit tests for agents/convo_lint.py (C167-169, C178-179, C202-204, C217, C219
drafting-time quality gates). Every function under test is pure, so every case is
a plain fixture, no mocking, no file I/O.

Run: .venv/bin/python -m pytest tests/test_convo_lint.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import convo_lint  # noqa: E402


class TestNoDoubleQuestion:
    def test_zero_questions_ok(self):
        ok, _ = convo_lint.check_no_double_question("Sounds good, I'll send it over.")
        assert ok

    def test_one_question_ok(self):
        ok, _ = convo_lint.check_no_double_question("Want to grab 15 minutes this week?")
        assert ok

    def test_two_questions_fails(self):
        ok, detail = convo_lint.check_no_double_question("Free tuesday? Does 2pm work?")
        assert not ok
        assert "2" in detail

    def test_three_questions_fails(self):
        ok, _ = convo_lint.check_no_double_question("Free? Morning or afternoon? This week?")
        assert not ok


class TestPriceConsistency:
    def test_no_dollar_figure_ok(self):
        ok, _ = convo_lint.check_price_consistency("I'll send the plan over today.")
        assert ok

    def test_valid_standard_price_ok(self):
        ok, _ = convo_lint.check_price_consistency("Standard site is $1,200, half now half on delivery.")
        assert ok

    def test_valid_landing_price_ok(self):
        ok, _ = convo_lint.check_price_consistency("The landing page option is $800.")
        assert ok

    def test_valid_deposit_figure_ok(self):
        # 50% deposit on the $1,200 standard build is a real, commonly-quoted figure
        ok, _ = convo_lint.check_price_consistency("Deposit is $600 to hold the slot.")
        assert ok

    def test_valid_care_growth_monthly_ok(self):
        ok, _ = convo_lint.check_price_consistency("Care Growth runs $150 a month after.")
        assert ok

    def test_invented_price_fails(self):
        ok, detail = convo_lint.check_price_consistency("I can do it for $999 flat.")
        assert not ok
        assert "999" in detail

    def test_shorthand_k_price_matches_real_sku(self):
        # "$1K" is the white-glove/agency-first shorthand used in VOICE-SPEC copy
        ok, _ = convo_lint.check_price_consistency("Agency first build is $1K flat.")
        assert ok

    def test_expected_tier_mismatch_fails(self):
        ok, detail = convo_lint.check_price_consistency(
            "Standard is $1,200.", expected_tier="webfix")
        assert not ok
        assert "webfix" in detail

    def test_expected_tier_match_ok(self):
        ok, _ = convo_lint.check_price_consistency(
            "The webfix bundle is $450.", expected_tier="webfix")
        assert ok

    def test_expected_tier_deposit_ok(self):
        ok, _ = convo_lint.check_price_consistency(
            "Deposit to start the webfix is $225.", expected_tier="webfix")
        assert ok

    def test_small_dollar_aside_ignored(self):
        # a small incidental figure (e.g. referencing hosting cost) isn't a quote
        ok, _ = convo_lint.check_price_consistency("Hosting itself only runs $10 a month.")
        assert ok


class TestNameGuard:
    def test_no_exact_name_ok(self):
        ok, _ = convo_lint.check_name_guard("Hey there, thanks for reaching out.", "")
        assert ok

    def test_generic_placeholder_name_ok(self):
        ok, _ = convo_lint.check_name_guard("Hey there, following up.", "there")
        assert ok

    def test_matching_name_ok(self):
        ok, _ = convo_lint.check_name_guard("Hey Braydon, got your message.", "Braydon Lj", "Braydon")
        assert ok

    def test_no_guess_supplied_ok(self):
        ok, _ = convo_lint.check_name_guard("Hey Braydon, got your message.", "Braydon Lj")
        assert ok

    def test_wrong_parsed_guess_used_in_draft_fails(self):
        ok, detail = convo_lint.check_name_guard(
            "Hey Legacy, got your message.", "Braydon Lj", "Legacy")
        assert not ok
        assert "Legacy" in detail and "Braydon" in detail

    def test_guess_wrong_but_draft_uses_correct_name_ok(self):
        # the guess existed but the draft happened to use the right name anyway
        ok, _ = convo_lint.check_name_guard(
            "Hey Braydon, got your message.", "Braydon Lj", "Legacy")
        assert ok


class TestLinkHygiene:
    def test_no_links_ok(self):
        ok, _ = convo_lint.check_link_hygiene("Sounds good, I'll follow up soon.")
        assert ok

    def test_one_link_ok(self):
        ok, _ = convo_lint.check_link_hygiene("Grab a time here: example.com/book")
        assert ok

    def test_two_links_fails(self):
        ok, detail = convo_lint.check_link_hygiene(
            "Book here: example.com/book or see the plan: https://x.com/prop/abc")
        assert not ok
        assert "2 links" in detail


class TestStripExtraLinks:
    def test_single_link_unchanged(self):
        d = "Book here: example.com/book"
        assert convo_lint.strip_extra_links(d) == d

    def test_two_links_keeps_last_by_default(self):
        d = "Book: example.com/book and plan: https://x.com/prop/abc"
        out = convo_lint.strip_extra_links(d)
        assert "https://x.com/prop/abc" in out
        assert "example.com/book" not in out

    def test_two_links_keeps_requested_one(self):
        d = "Book: example.com/book and plan: https://x.com/prop/abc"
        out = convo_lint.strip_extra_links(d, keep="example.com/book")
        assert "example.com/book" in out
        assert "https://x.com/prop/abc" not in out

    def test_result_passes_the_check_afterward(self):
        d = "Book: example.com/book and plan: https://x.com/prop/abc"
        out = convo_lint.strip_extra_links(d)
        ok, _ = convo_lint.check_link_hygiene(out)
        assert ok


class TestFormalityDetection:
    def test_casual_slang_detected(self):
        assert convo_lint.detect_formality("yeah lol sounds good") == "casual"

    def test_formal_greeting_detected(self):
        assert convo_lint.detect_formality(
            "Dear Mr. Rivera, I would like to inquire about your services.") == "formal"

    def test_contractions_read_casual(self):
        assert convo_lint.detect_formality("I don't think that's gonna work for us") == "casual"

    def test_empty_message_defaults_casual(self):
        assert convo_lint.detect_formality("") == "casual"

    def test_ambiguous_short_message_defaults_casual(self):
        assert convo_lint.detect_formality("ok") == "casual"


class TestLengthMatch:
    def test_matched_lengths_ok(self):
        ok, detail = convo_lint.check_length_match("Sounds good, I'll send it over.", "ok great")
        assert ok
        assert detail == ""

    def test_short_message_long_reply_flagged_as_warning_not_failure(self):
        ok, detail = convo_lint.check_length_match(
            "Absolutely, that makes total sense and I completely understand where you're "
            "coming from on this, let me walk you through exactly how we'd approach it "
            "step by step so you have full visibility into the whole process end to end.",
            "k")
        assert ok  # soft check, never fails
        assert detail != ""

    def test_email_channel_more_tolerant_than_sms(self):
        their = "sounds good"
        draft = " ".join(["word"] * 20)
        sms_ok, sms_detail = convo_lint.check_length_match(draft, their, channel="SMS")
        email_ok, email_detail = convo_lint.check_length_match(draft, their, channel="Email")
        assert sms_ok and email_ok
        # SMS should flag at a shorter draft length than email requires
        assert sms_detail != "" or email_detail == ""

    def test_empty_their_message_ok(self):
        ok, detail = convo_lint.check_length_match("Some reply text.", "")
        assert ok
        assert detail == ""


class TestHarassmentDetection:
    def test_clean_message_not_flagged(self):
        assert not convo_lint.detect_harassment("this is way too expensive honestly")
    def test_clean_objection_not_flagged(self):
        assert not convo_lint.detect_harassment("not interested, please stop texting me")

    def test_hostile_language_flagged(self):
        assert convo_lint.detect_harassment("fuck you, scammer, leave me alone")

    def test_threat_language_flagged(self):
        assert convo_lint.detect_harassment("I know where you live, watch yourself")

    def test_sexual_harassment_flagged(self):
        assert convo_lint.detect_harassment("hey sexy, what are you wearing")

    def test_empty_message_not_flagged(self):
        assert not convo_lint.detect_harassment("")


class TestLanguageDetection:
    def test_english_default(self):
        assert convo_lint.detect_language("Hey how much does this cost") == "en"

    def test_spanish_marker_words_detected(self):
        assert convo_lint.detect_language("Hola, cuanto cuesta el sitio web?") == "es"

    def test_spanish_accented_chars_detected(self):
        assert convo_lint.detect_language("Necesito más información, gracias") == "es"

    def test_single_stray_accent_not_enough(self):
        # one accented char alone (could be a typo/copy-paste artifact) isn't enough signal
        assert convo_lint.detect_language("cafe on the corner looks nice") == "en"

    def test_empty_message_defaults_english(self):
        assert convo_lint.detect_language("") == "en"


class TestRunAllGates:
    def test_clean_draft_passes(self):
        result = convo_lint.run_all_gates(
            "Standard is $1,200, half now half on delivery. Want me to hold the slot?",
            "how much is it", "Braydon")
        assert result["ok"]
        assert result["failures"] == []

    def test_multiple_failures_all_reported(self):
        result = convo_lint.run_all_gates(
            "Hey Legacy, it's $999. Free tuesday? Does 2pm work? Link: example.com/book "
            "also https://x.com/prop/abc",
            "how much is it", "Braydon", parsed_guess="Legacy")
        assert not result["ok"]
        gates_failed = {f["gate"] for f in result["failures"]}
        assert "double_question" in gates_failed
        assert "price_consistency" in gates_failed
        assert "name_guard" in gates_failed
        assert "link_hygiene" in gates_failed

    def test_warnings_never_flip_ok_false(self):
        result = convo_lint.run_all_gates(
            "Absolutely, that makes complete sense and here is a very long explanation "
            "of exactly how we would go about doing that for you from start to finish.",
            "k", "Braydon")
        assert result["ok"]
        assert result["warnings"] != []


class TestSuggestRefusal:
    def test_seo_guarantee_request_matched(self):
        r = convo_lint.suggest_refusal("can you guarantee we rank number 1 on google")
        assert r["matched"]
        assert "Nobody honest guarantees Google" in r["refusal"]
        assert r["source"] == "objections.md #50"

    def test_rev_share_request_matched(self):
        r = convo_lint.suggest_refusal("could we do a rev share deal instead")
        assert r["matched"]
        assert "rev-share" in r["refusal"]

    def test_equity_request_matched(self):
        r = convo_lint.suggest_refusal("what about equity in the business instead of cash")
        assert r["matched"]

    def test_pay_when_it_makes_money_matched(self):
        r = convo_lint.suggest_refusal("can we do pay when it makes me money")
        assert r["matched"]

    def test_same_day_support_matched(self):
        r = convo_lint.suggest_refusal("do you offer same-day support")
        assert r["matched"]
        assert "Care Growth" in r["refusal"]

    def test_own_hosting_matched(self):
        r = convo_lint.suggest_refusal("can I use my own hosting")
        assert r["matched"]

    def test_ordinary_price_question_not_matched(self):
        r = convo_lint.suggest_refusal("how much does the standard site cost")
        assert not r["matched"]
        assert r["refusal"] == ""

    def test_empty_message_not_matched(self):
        r = convo_lint.suggest_refusal("")
        assert not r["matched"]

    def test_every_template_has_a_source_citation(self):
        for _pattern, _refusal, source in convo_lint._REFUSAL_TEMPLATES:
            assert source  # every refusal is traceable to a real playbook line

    def test_refusal_text_has_no_em_dash(self):
        # VOICE-SPEC hard rule #1 -- refusal templates are drafted text too
        for _pattern, refusal, _source in convo_lint._REFUSAL_TEMPLATES:
            assert "—" not in refusal and "–" not in refusal
