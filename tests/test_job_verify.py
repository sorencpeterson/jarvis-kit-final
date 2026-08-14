"""Verifying applications against employer confirmation mail.

The one error this tool must never make is marking an UNSENT application as sent, so
most of these tests are about what it refuses to accept as a confirmation. Job-board
digests are the dangerous false positive: they routinely contain "your application"
verbatim while confirming nothing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import job_verify as jv  # noqa: E402
import jobs  # noqa: E402


class TestIsConfirmation:
    @pytest.mark.parametrize("subject", [
        "Thank you for applying to Acme",
        "Thanks for applying!",
        "Application received",
        "Your application has been received",
        "We have received your application",
        "We've received your application for Marketing Manager",
        "Application Confirmation - Acme Corp",
        "Thank you for your interest in Acme",
    ])
    def test_real_confirmations_are_recognised(self, subject):
        assert jv.is_confirmation(subject)

    @pytest.mark.parametrize("subject", [
        "Job alert: 12 new Marketing Manager roles",
        "Jobs for you this week",
        "Recommended jobs based on your application",
        "Weekly digest: your application activity",
        "New jobs matching your search",
    ])
    def test_job_board_noise_is_rejected(self, subject):
        # these contain application-ish words and confirm nothing
        assert not jv.is_confirmation(subject)

    def test_generic_subject_counts_only_from_an_ats_sender(self):
        assert not jv.is_confirmation("Your application", "friend@gmail.com")
        assert jv.is_confirmation("Your application",
                                  "no-reply@us.greenhouse.io")

    def test_ats_sender_still_needs_an_application_word(self):
        assert not jv.is_confirmation("Happy holidays", "no-reply@greenhouse.io")

    def test_empty_input_is_never_a_confirmation(self):
        assert not jv.is_confirmation("", "")
        assert not jv.is_confirmation(None, None)

    def test_rejections_are_left_to_job_replies(self):
        # receipt is implied, but this module must not fight job_replies for them
        assert not jv.is_confirmation("Update on your application to Acme")
        assert not jv.is_confirmation("Regarding your application")
        assert not jv.is_confirmation("Your application status has changed")

    def test_receipt_language_still_matches_across_a_company_name(self):
        assert jv.is_confirmation("Your application to Acme Corp has been received")
        assert jv.is_confirmation("Your application for Marketing Manager was received")


class TestMatchCompany:
    def test_matches_the_company_in_the_text(self):
        assert jv.match_company("Thank you for applying to Acme Corp",
                                ["Acme Corp", "Globex"]) == "Acme Corp"

    def test_prefers_the_longer_more_specific_name(self):
        got = jv.match_company("Thanks for applying to Acme Health Systems",
                               ["Acme", "Acme Health Systems"])
        assert got == "Acme Health Systems"

    def test_no_match_returns_empty(self):
        assert jv.match_company("Thank you for applying to Initech", ["Acme"]) == ""

    def test_very_short_company_keys_cannot_match_loosely(self):
        # a 2-3 char key would match inside almost any subject
        assert jv.match_company("Thanks for applying to Something", ["AB"]) == ""

    def test_legal_suffix_differences_still_match(self):
        # mirrors jobs._conorm so a match here means what a dedupe there means
        assert jv.match_company("Thank you for applying to Acme, Inc.",
                                ["Acme"]) == "Acme"


def _queue(monkeypatch, tmp_path, recs):
    q = tmp_path / "jobs.jsonl"
    q.write_text("".join(json.dumps(r) + "\n" for r in recs))
    monkeypatch.setattr(jobs, "QUEUE", q)
    return q


class TestRun:
    def test_confirmation_upgrades_an_unconfirmed_application(self, monkeypatch, tmp_path):
        _queue(monkeypatch, tmp_path, [
            {"id": "a", "status": "applied", "company": "Acme Corp",
             "reason": "unconfirmed (operator quoted no submission confirmation)"}])
        monkeypatch.setattr(jv, "_fetch", lambda days: [
            {"subject": "Thank you for applying to Acme Corp",
             "sender": "no-reply@greenhouse.io", "snippet": ""}])
        jv.run()
        rec = next(x for x in jobs.load_jobs() if x["id"] == "a")
        assert rec["status"] == "applied"
        assert rec["reason"].startswith("confirm:")

    def test_confirmation_recovers_a_lost_submission(self, monkeypatch, tmp_path):
        # the valuable case: the operator died, the application had already landed
        _queue(monkeypatch, tmp_path, [
            {"id": "a", "status": "skipped", "company": "Globex",
             "reason": "inflight_timeout (operator ended without callback)"}])
        monkeypatch.setattr(jv, "_fetch", lambda days: [
            {"subject": "Application received", "sender": "jobs@globex.com",
             "snippet": "your application to Globex"}])
        jv.run()
        rec = next(x for x in jobs.load_jobs() if x["id"] == "a")
        assert rec["status"] == "applied", "a real submission must be recovered"

    def test_absence_of_mail_never_marks_anything(self, monkeypatch, tmp_path):
        _queue(monkeypatch, tmp_path, [
            {"id": "a", "status": "skipped", "company": "Acme",
             "reason": "inflight_timeout (x)"}])
        monkeypatch.setattr(jv, "_fetch", lambda days: [])
        jv.run()
        rec = next(x for x in jobs.load_jobs() if x["id"] == "a")
        assert rec["status"] == "skipped", "no evidence must change nothing"

    def test_a_human_status_outranks_the_mailbox(self, monkeypatch, tmp_path):
        # interview/rejected/replied came from a person and must survive
        _queue(monkeypatch, tmp_path, [
            {"id": "a", "status": "interview", "company": "Acme",
             "reason": "gmail:interview"}])
        monkeypatch.setattr(jv, "_fetch", lambda days: [
            {"subject": "Thank you for applying to Acme", "sender": "x@acme.com",
             "snippet": ""}])
        jv.run()
        rec = next(x for x in jobs.load_jobs() if x["id"] == "a")
        assert rec["status"] == "interview"

    def test_unrelated_confirmation_does_not_touch_another_company(self, monkeypatch, tmp_path):
        _queue(monkeypatch, tmp_path, [
            {"id": "a", "status": "applied", "company": "Acme",
             "reason": "unconfirmed (x)"},
            {"id": "b", "status": "applied", "company": "Globex",
             "reason": "unconfirmed (x)"}])
        monkeypatch.setattr(jv, "_fetch", lambda days: [
            {"subject": "Thank you for applying to Globex", "sender": "x@globex.com",
             "snippet": ""}])
        jv.run()
        recs = {x["id"]: x for x in jobs.load_jobs()}
        assert recs["b"]["reason"].startswith("confirm:")
        assert recs["a"]["reason"].startswith("unconfirmed")

    def test_report_mode_needs_no_mailbox(self, monkeypatch, tmp_path, capsys):
        _queue(monkeypatch, tmp_path, [
            {"id": "a", "status": "skipped", "company": "Acme",
             "reason": "attempt_cap (2 tries)", "apply_url": "https://x.example/a"}])
        def _boom(days):
            raise AssertionError("--report must not touch the mailbox")
        monkeypatch.setattr(jv, "_fetch", _boom)
        jv.run(report_only=True)
        assert "Acme" in capsys.readouterr().out

    def test_no_mailbox_degrades_to_a_report_rather_than_failing(self, monkeypatch, tmp_path, capsys):
        _queue(monkeypatch, tmp_path, [
            {"id": "a", "status": "skipped", "company": "Acme",
             "reason": "inflight_timeout (x)"}])
        monkeypatch.setattr(jv, "_fetch", lambda days: [])
        assert jv.run() == 0
        assert "nothing was changed" in capsys.readouterr().out
