#!/usr/bin/env python3
"""Unit tests for agents/job_mail_patterns.py (D224/D225 regex fast-path).

Regression coverage for the confirmation-before-rejection ordering bug (D5 #1):
rejection emails routinely end with a boilerplate "thank you for applying"
footer, and because classify() checked confirmation FIRST (and job_replies.py
lets the regex result override the LLM's classification), those rejections --
and interview invites with the same footer -- were silently recorded as plain
confirmations. Precedence is now interview > rejection > confirmation, and
when interview AND rejection both match, classify() returns None so the LLM
decides instead of the regex overriding it with a coin flip.

Pure-function, no LLM/network calls.
Run: .venv/bin/python -m pytest tests/test_job_mail_patterns.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "agents"):
    sys.path.insert(0, str(p))

import job_mail_patterns  # noqa: E402

classify = job_mail_patterns.classify

FOOTER = "Thank you for applying to Acme."


class TestRejectionBeatsConfirmationFooter:
    """D5 #1 regression: rejection body + confirmation boilerplate footer."""

    def test_ats_rejection_with_thanks_footer(self):
        # Greenhouse sender: rejection language, then the classic footer.
        assert classify(
            "no-reply@greenhouse.io",
            "Update on your application to Acme",
            "We have decided to move forward with other candidates. " + FOOTER,
        ) == "rejection"

    def test_ats_unfortunately_with_thanks_footer(self):
        assert classify(
            "no-reply@greenhouse.io",
            "Your application to Acme",
            "Unfortunately we will not be proceeding with your candidacy. " + FOOTER,
        ) == "rejection"

    def test_generic_rejection_with_thanks_footer(self):
        # No known ATS domain: generic patterns, same precedence.
        assert classify(
            "talent@somecompany.com",
            "Application update",
            "We have decided to move forward with other applicants. " + FOOTER,
        ) == "rejection"


class TestInterviewBeatsConfirmationFooter:
    """D5 #1 regression: interview invite + confirmation boilerplate footer."""

    def test_ats_interview_with_thanks_footer(self):
        assert classify(
            "no-reply@greenhouse.io",
            "Acme - let's talk",
            "We would like to schedule a call to discuss your background. " + FOOTER,
        ) == "interview"

    def test_lever_interview_with_thanks_footer(self):
        assert classify(
            "no-reply@hire.lever.co",
            "Next step with Acme",
            "We'd love to set up a phone screen this week. " + FOOTER,
        ) == "interview"

    def test_generic_interview_with_thanks_footer(self):
        assert classify(
            "recruiting@somecompany.com",
            "Acme interview",
            "Can we schedule a chat for Thursday? " + FOOTER,
        ) == "interview"


class TestPureConfirmation:
    """Plain auto-acks still resolve as confirmation (the fast path's bread
    and butter) -- the reorder must not break the common case."""

    def test_ats_confirmation(self):
        assert classify(
            "no-reply@greenhouse.io",
            "Thank you for applying to Acme",
            "We have received your application and will be in touch.",
        ) == "confirmation"

    def test_ashby_confirmation(self):
        assert classify(
            "no-reply@ashbyhq.com",
            "Application received",
            "Your application was received. We appreciate your interest.",
        ) == "confirmation"

    def test_generic_confirmation(self):
        assert classify(
            "jobs@somecompany.com",
            "We received your application",
            "Thanks for your application to Acme.",
        ) == "confirmation"


class TestGenericSignalBeatsKnownAtsConfirmation:
    """R2-21 follow-up: checking one candidate pattern set at a time (a known ATS's own
    set, then _GENERIC) re-creates the same bug one level up -- the ATS's own (narrower)
    confirmation regex could match and return before the broader _GENERIC interview/
    rejection patterns (e.g. the soft "grab some time to chat" invite) ever get a look.
    Both passes must scan ALL candidate sets together before confirmation can win."""

    def test_known_ats_soft_chat_invite_beats_its_own_confirmation_regex(self):
        # greenhouse's OWN interview regex doesn't cover "grab some time to chat" (that
        # phrasing only lives in _GENERIC), but its confirmation regex DOES match the
        # "thank you for applying" footer -- must still resolve as interview, not confirmation.
        assert classify(
            "no-reply@greenhouse.io",
            "Acme",
            "Thank you for applying to Acme. We'd love to grab some time to chat about the role this week.",
        ) == "interview"


class TestAmbiguousDefersToLLM:
    """Interview AND rejection language in one mail: the regex is not
    confident, and its answer would override the LLM downstream -- so it
    must return None and let the LLM decide."""

    def test_rejection_phrased_around_next_round(self):
        # Real-world rejection shape that also trips the interview family.
        assert classify(
            "no-reply@greenhouse.io",
            "Your application to Acme",
            "Unfortunately we are unable to move you to the next round at this time.",
        ) is None


class TestNoMatch:
    def test_unrelated_mail_returns_none(self):
        assert classify(
            "newsletter@somecompany.com",
            "Our July product update",
            "Here is what shipped this month.",
        ) is None

    def test_empty_inputs_do_not_crash(self):
        assert classify("", "", "") is None
        assert classify(None, None, None) is None


class TestAtsForSender:
    def test_known(self):
        assert job_mail_patterns.ats_for_sender("no-reply@greenhouse.io") == "greenhouse"
        assert job_mail_patterns.ats_for_sender("no-reply@hire.lever.co") == "lever"

    def test_unknown_and_empty(self):
        assert job_mail_patterns.ats_for_sender("someone@gmail.com") is None
        assert job_mail_patterns.ats_for_sender("") is None
        assert job_mail_patterns.ats_for_sender(None) is None
