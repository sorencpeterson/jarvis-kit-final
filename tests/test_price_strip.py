#!/usr/bin/env python3
"""The cover-email bait-and-switch guard (red-team F1 #3): the proposal PAGE owns price;
an email stating a different/spelled-out number reads as bait-and-switch. Pin that every
price form is excised and the booking CTA + benign copy survive."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import proposal_factory as pf  # noqa: E402


def _has_price(s):
    import re
    return bool(re.search(r"\$\s?\d|\d[\d,]*\s*(?:dollars?|usd|bucks|k)\b|\b(?:usd|us\$)\s*\d",
                          s, re.I))


def test_strips_every_price_form():
    for line in ("$3,500 flat", "It is 1,200 dollars total.", "USD 2500 flat.",
                 "Price is 3500 USD.", "About three thousand dollars.", "$3.5k",
                 "just 800 bucks"):
        assert not _has_price(pf._strip_price_timeline(line)), line


def test_strips_timelines():
    assert "days" not in pf._strip_price_timeline("We deliver in 14 days").lower()


def test_preserves_booking_and_benign():
    assert pf._strip_price_timeline("Book at example.com/book") == "Book at example.com/book"
    assert pf._strip_price_timeline("Your new site, delivered.") == "Your new site, delivered."
    # a naked number that is not a price (e.g. a year or a stat) should survive
    assert "2020" in pf._strip_price_timeline("You have run this since 2020.")
