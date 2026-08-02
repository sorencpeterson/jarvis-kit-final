#!/usr/bin/env python3
"""Outbound email lint (2026-07-11). Pins the hard-fail set (internal/pre-flip links,
http links, off-brand domains, em-dashes, empty subject) and that clean on-voice drafts
pass. Link liveness is exercised with check_links=False here (network-free tests); the
live check ran against all 15 staged drafts at build time (0 findings).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import email_lint  # noqa: E402

CLEAN = ("Lesley,\n\nLooked at your site. You won Best Medical Spa 2026 and launched a new "
         "program this month, but neither one is doing conversion work on your homepage. "
         "Put together a quick breakdown of what is costing you bookings and what I would "
         "build instead.\n\n"
         "Proposal: https://proposals.example.com/prop/x?sig=abc\n\n"
         "If it makes sense, book 15 minutes: https://example.com/book\n\nAlex")


class TestHardFails:
    def test_clean_draft_passes(self):
        r = email_lint.lint("your site, what I'd fix", CLEAN, check_links=False)
        assert r["ok"] and not r["errors"] and not r["warns"]

    def test_tailnet_leak_blocks(self):
        r = email_lint.lint("s", "see https://macbook-pro.yourmachine.ts.net/prop/x?sig=y", check_links=False)
        assert not r["ok"] and any("pre-flip" in e or "internal" in e for e in r["errors"])

    def test_http_and_offbrand_block(self):
        r = email_lint.lint("s", "http://bit.ly/xyz", check_links=False)
        assert not r["ok"]
        assert any("insecure" in e for e in r["errors"])
        assert any("off-brand" in e for e in r["errors"])

    def test_emdash_blocks(self):
        r = email_lint.lint("s", "This is great — trust me", check_links=False)
        assert not r["ok"]

    def test_empty_subject_blocks_new_but_not_reply(self):
        assert not email_lint.lint("", "hi", check_links=False)["ok"]
        assert email_lint.lint("", "hi there friend, quick thought on the rebuild plan "
                                   "you mentioned last week.", is_reply=True, check_links=False)["ok"]


class TestWarns:
    def test_spam_phrases_warn_not_block(self):
        r = email_lint.lint("s", "This is risk-free and act now " + CLEAN, check_links=False)
        assert r["ok"] and any("spam" in w for w in r["warns"])

    def test_generic_greeting_warns(self):
        r = email_lint.lint("s", "Hi there,\n" + CLEAN, check_links=False)
        assert r["ok"] and any("there" in w for w in r["warns"])

    def test_long_subject_and_bang_warn(self):
        r = email_lint.lint("x" * 70 + "!", CLEAN, check_links=False)
        assert r["ok"] and len(r["warns"]) >= 2
