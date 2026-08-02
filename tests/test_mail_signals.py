#!/usr/bin/env python3
"""Unit tests for agents/mail_signals.py (B88/B89/B90/B97). Pure-function tests for
the regex/matching logic, same convention as tests/test_li_*.py and
tests/test_mail_patterns.py (standalone file, not wired into test_pure.py).

Includes regression tests for two real false positives found during live testing
against Alex's actual mailbox:
  1. detect_attachments matched body-text keywords with NO check that the message
     actually had an attachment at all, flagging plain marketing newsletters (Lyft
     promo, a "Your Spouse is not the problem" spam email) as "invoice/contract-
     flavored attachments" because generic body text ("terms & conditions" footer
     boilerplate) happened to match.
  2. The bare substring "nda" matched inside "sta-nda-rd" (word-boundary bug) on a
     real Lyft email body containing "the standard save 5% on-demand ride benefit".

Run: .venv/bin/python -m pytest tests/test_mail_signals.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "agents"):
    sys.path.insert(0, str(p))

import mail_signals  # noqa: E402


class TestAmountExtraction:
    def test_dollar_sign_amount(self):
        assert mail_signals._extract_amount("Payment received - $500.00") == 500.0

    def test_dollar_sign_no_cents(self):
        assert mail_signals._extract_amount("You received $1,250") == 1250.0

    def test_no_amount_present(self):
        assert mail_signals._extract_amount("Money added to your account") is None

    def test_empty_string(self):
        assert mail_signals._extract_amount("") is None


class TestInvoiceContractRegex:
    def test_real_invoice_word_boundary(self):
        assert mail_signals._INVOICE_RE.search("your invoice for this month")

    def test_regression_nda_word_boundary_false_positive(self):
        """Real bug: bare 'nda' substring matched inside 'standard' on a live Lyft
        promo email ('the standard save 5% on-demand ride benefit'). Must NOT match
        with word boundaries in place."""
        text = "this offer will supersede the standard save 5% on-demand ride benefit"
        assert not mail_signals._CONTRACT_RE.search(text)

    def test_genuine_nda_still_matches(self):
        text = "please sign the attached NDA before we proceed"
        assert mail_signals._CONTRACT_RE.search(text)

    def test_regression_terms_conditions_footer_not_a_contract_signal(self):
        """'terms' was dropped from CONTRACT_HINTS entirely (real bug: matched
        generic 'terms & conditions' marketing-footer boilerplate present on nearly
        every commercial email, not a real contract signal)."""
        text = "save 10% off on-demand rides. terms & conditions apply."
        assert not mail_signals._CONTRACT_RE.search(text)
        assert not mail_signals._INVOICE_RE.search(text)


class TestDetectAttachmentsGating:
    """detect_attachments must gate on REAL attachment presence, never on body-text
    keywords alone. Real bug: an earlier version had no attachment-presence check at
    all and flagged plain newsletters as attachment suggestions."""

    def test_no_attachments_no_suggestion(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mail_signals, "ATTACH_OUT", tmp_path / "out.jsonl")
        rows = [{"id": "x1", "subject": "Bigger ride savings end soon",
                  "from": "promo@lyftmail.com",
                  "_attachments": [],  # NO real attachment
                  "_body": "terms & conditions apply, save on rides", "lane": "newsletter"}]
        monkeypatch.setattr(mail_signals, "_read_jsonl",
                             lambda p: rows if p == mail_signals.TRIAGE else [])
        n = mail_signals.detect_attachments(fixture=False)
        assert n == 0

    def test_real_attachment_with_invoice_language_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mail_signals, "ATTACH_OUT", tmp_path / "out.jsonl")
        rows = [{"id": "x2", "subject": "Your invoice #123", "from": "billing@vendor.com",
                  "_attachments": [{"filename": "invoice_123.pdf"}],
                  "_body": "please find your invoice attached", "lane": "receipts"}]
        monkeypatch.setattr(mail_signals, "_read_jsonl",
                             lambda p: rows if p == mail_signals.TRIAGE else [])
        n = mail_signals.detect_attachments(fixture=False)
        assert n == 1

    def test_csv_extension_detected_from_filename_alone(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mail_signals, "ATTACH_OUT", tmp_path / "out.jsonl")
        rows = [{"id": "x3", "subject": "Data export", "from": "reports@vendor.com",
                  "_attachments": [{"filename": "contacts_export.csv"}],
                  "_body": "attached is the export you requested", "lane": "business"}]
        monkeypatch.setattr(mail_signals, "_read_jsonl",
                             lambda p: rows if p == mail_signals.TRIAGE else [])
        n = mail_signals.detect_attachments(fixture=False)
        assert n == 1
        out_lines = (tmp_path / "out.jsonl").read_text().splitlines()
        import json
        assert json.loads(out_lines[0])["kind"] == "csv"


class TestMeetingHints:
    def test_schedule_a_call_detected(self):
        assert any(h in "let's schedule a call this week" for h in mail_signals.MEETING_HINTS)

    def test_calendly_link_detected(self):
        assert any(h in "book here: calendly.com/me/30min" for h in mail_signals.MEETING_HINTS)

    def test_no_meeting_language(self):
        text = "thanks for the update, talk soon"
        assert not any(h in text for h in mail_signals.MEETING_HINTS)
