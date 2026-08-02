#!/usr/bin/env python3
"""Dad's 4-part comment formula integration (2026-07-11). Pins that the li_quality gate
now ACCEPTS the formula's sincere-specific openers ("Thanks for sharing the story about
X") while still REJECTING the bare bot-filler forms ("Thanks for sharing."), that the
420-char cap fits the 4-part shape, and that a full formula-shaped comment passes the
whole validate_draft gate with its single closing question."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import li_quality  # noqa: E402

FORMULA = ('Really liked the line, "The problem is not the offer. It is the fulfillment." '
           "I've seen agencies close great clients and then get buried trying to deliver "
           "everything themselves. That one sentence explains a lot. At what point do you "
           "think most agency owners realize they need help?")


class TestSoftOpeners:
    def test_specific_thanks_allowed(self):
        assert li_quality.find_banned_phrase(
            "Thanks for sharing the story about the prospect asking how income shows up.") is None

    def test_bare_thanks_still_banned(self):
        assert li_quality.find_banned_phrase("Thanks for sharing.") == "thanks for sharing"
        assert li_quality.find_banned_phrase("Thanks for sharing!") == "thanks for sharing"

    def test_specific_great_post_allowed_bare_banned(self):
        assert li_quality.find_banned_phrase(
            "Great post. Breaking it down into scripts, videos, and editing shows the real work.") is None
        assert li_quality.find_banned_phrase("Great post.") == "great post"

    def test_hard_bans_unchanged(self):
        assert li_quality.find_banned_phrase("This is a game changer for synergy") is not None
        assert li_quality.find_banned_phrase("Totally agree with all of this") == "totally agree"


class TestFormulaPassesGate:
    def test_full_formula_comment_validates(self):
        v = li_quality.validate_draft(FORMULA, kind="comment", name="Jane Smith")
        assert v["ok"], v["reasons"]

    def test_cap_fits_four_parts(self):
        assert li_quality.MAX_CHARS >= 400
        assert len(FORMULA) <= li_quality.MAX_CHARS

    def test_one_question_max_still_enforced(self):
        two_q = FORMULA + " And another thing, what about pricing?"
        v = li_quality.validate_draft(two_q, kind="comment")
        assert not v["ok"]
