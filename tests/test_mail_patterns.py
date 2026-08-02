#!/usr/bin/env python3
"""Unit tests for agents/mail_patterns.py (B141-150: per-ATS regex pattern library).
Pure-function, no LLM/network calls — same convention as tests/test_li_*.py (each
build fleet ships its own standalone pytest file rather than editing test_pure.py).

Fixtures below are drawn from REAL ATS mail pulled from Alex's mailbox during
development (Ashby/Breezy/Rippling senders, 2026-07 window) plus one regression
fixture for a real false positive found and fixed during that same real-data testing
(the "IP House" case: conditional "...or wish to schedule an interview with you"
boilerplate inside a plain confirmation email, which an earlier pattern version
mis-fired on).

Run: .venv/bin/python -m pytest tests/test_mail_patterns.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "agents"):
    sys.path.insert(0, str(p))

import mail_patterns  # noqa: E402


class TestIsAtsSender:
    def test_known_domains(self):
        assert mail_patterns.is_ats_sender("no-reply@ashbyhq.com")
        assert mail_patterns.is_ats_sender("no-reply@ats.rippling.com")
        assert mail_patterns.is_ats_sender("hiring@epic-cleantec.breezy-mail.com")

    def test_unknown_domain(self):
        assert not mail_patterns.is_ats_sender("someone@gmail.com")

    def test_empty_sender(self):
        assert not mail_patterns.is_ats_sender("")
        assert not mail_patterns.is_ats_sender(None)


class TestRejection:
    def test_real_mytomorrows_rejection(self):
        r = mail_patterns.classify(
            "Your application for Product Marketing Manager | myTomorrows",
            "After carefully reviewing your experience and motivation, we regret to "
            "inform you that we will not be moving forward.",
            "no-reply@ashbyhq.com")
        assert r["type"] == "rejection"
        assert r["confidence"] == "high"

    def test_real_bullseye_rejection(self):
        r = mail_patterns.classify(
            "Re: Bullseye Strategy opportunity",
            "We appreciate your interest in Bullseye Strategy and the time you've "
            "invested. At this time we will not be moving forward with your application.",
            "no-reply@bullseye-strategy.breezy-mail.com")
        assert r["type"] == "rejection"

    def test_real_829studios_rejection(self):
        r = mail_patterns.classify(
            "SEO Account Director at 829 Studios",
            "After careful review, we won't be moving forward with your application "
            "for this position. We had a strong pool of candidates.",
            "no-reply@ashbyhq.com")
        assert r["type"] == "rejection"

    def test_position_filled(self):
        r = mail_patterns.classify("Update", "Unfortunately the position has been filled.", "")
        assert r["type"] == "rejection"


class TestConfirmation:
    def test_real_xcures_confirmation(self):
        r = mail_patterns.classify(
            "Thank you for applying to xCures, Inc.",
            "Thank you for applying to xCures, Inc.! We have received your application "
            "and will review it promptly.",
            "no-reply@ats.rippling.com")
        assert r["type"] == "confirmation"
        assert r["confidence"] == "high"

    def test_real_shippo_confirmation_metaphor_template(self):
        """Confirmation templates can use playful language (Shippo's shipping-metaphor
        template) and still correctly match — this isn't really about shipping."""
        r = mail_patterns.classify(
            "Your package has been delivered",
            "Thank you for applying for Sr Product Marketing Manager role at Shippo. "
            "We have successfully received your package, in this case your resume.",
            "no-reply@ats.rippling.com")
        assert r["type"] == "confirmation"


class TestInterview:
    def test_genuine_invite_please_schedule(self):
        r = mail_patterns.classify(
            "Next steps",
            "Great news, we would like to move forward. Please schedule a call with "
            "us using the link below.",
            "noreply@ashbyhq.com")
        assert r["type"] == "interview"

    def test_genuine_invite_lets_schedule(self):
        r = mail_patterns.classify(
            "Interview",
            "Let's schedule an interview with you this week, what times work?",
            "noreply@ashbyhq.com")
        assert r["type"] == "interview"

    def test_regression_conditional_boilerplate_not_interview(self):
        """Real false positive found during dev: IP House's confirmation email says
        'you will be contacted if... or wish to schedule an interview with you' as
        CONDITIONAL boilerplate inside an otherwise plain confirmation. Must NOT
        classify as interview just because the phrase appears somewhere in the body."""
        r = mail_patterns.classify(
            "Thank you for applying to IP House",
            "Thank you for submitting your resume for the position of Digital "
            "Marketing Manager. We are currently reviewing resumes and will be "
            "scheduling interviews soon. You will be contacted if we need additional "
            "information or wish to schedule an interview with you. If you are not "
            "selected for an interview, we encourage you to view our job postings.",
            "no-reply@ats.rippling.com")
        assert r["type"] != "interview"
        assert r["type"] == "confirmation"


class TestAssessment:
    def test_take_home(self):
        r = mail_patterns.classify(
            "Next step", "Please complete the take-home assignment attached.", "")
        assert r["type"] == "assessment"

    def test_assessment_platform(self):
        r = mail_patterns.classify(
            "Skills check", "We use HackerRank for our technical screen.", "")
        assert r["type"] == "assessment"


class TestFallback:
    def test_unknown_ats_sender_no_pattern_match(self):
        r = mail_patterns.classify("Hi", "Just checking in on things generally.",
                                    "no-reply@ashbyhq.com")
        assert r["type"] == "other"
        assert r["confidence"] == "low"

    def test_non_ats_no_match(self):
        r = mail_patterns.classify("Lunch?", "Want to grab lunch Tuesday?", "friend@gmail.com")
        assert r["type"] == "other"
        assert r["confidence"] == "low"

    def test_specific_type_wins_over_generic_confirmation_language(self):
        """A rejection or interview email that ALSO contains confirmation-flavored
        boilerplate ('thank you for applying') must classify by the more specific,
        more actionable signal, not the generic one."""
        r = mail_patterns.classify(
            "Update on your application",
            "Thank you for applying and for your interest in our company. "
            "Unfortunately, we have decided to pursue other candidates.",
            "")
        assert r["type"] == "rejection"
