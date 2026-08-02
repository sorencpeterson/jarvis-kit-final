#!/usr/bin/env python3
"""Email-OTP patch for the apply operator (2026-07-07). Pins the privacy rails:
the operator gets AT MOST a code + sender domain from a fresh verification-shaped
email in Alex's own inbox — never a body, never anything stale, never someone
else's code, and only with that job's own cb HMAC.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import apply_otp  # noqa: E402


class _Req:
    def __init__(self, params):
        self.query_params = params


def _msg(mid, subject, body, age_s=30, sender="no-reply@ashbyhq.com"):
    return {"id": mid, "from": sender, "subject": subject, "body": body,
            "internalDate": str(int((time.time() - age_s) * 1000))}


class _GmailStub:
    HistoryStale = Exception

    def __init__(self, messages):
        self._m = {m["id"]: m for m in messages}

    def search(self, q, n):
        return [{"id": k} for k in self._m]

    def get_message(self, mid, fmt="full"):
        return self._m[mid]


class _JobsStub:
    """Deterministic stand-in for agents/jobs.py (CX14): fetch_code() now looks up the
    job's company on record when no hint is given, and tests must not depend on whatever
    happens to be in the REAL store/jobs.jsonl. Defaults to no records (i.e. no derivable
    company -- matches old, hint-only behavior for every test that doesn't care about CX14)."""

    def __init__(self, records=None):
        self._records = records or []

    def load_jobs(self):
        return self._records


class TestCodeExtraction:
    def test_labeled_code_wins_over_other_digits(self):
        assert apply_otp._extract_code("Order 8812 shipped. Your code is 493021.") == "493021"

    def test_bare_six_digit_default(self):
        assert apply_otp._extract_code("123456 is all this says") == "123456"

    def test_years_are_damped(self):
        # (c) 2026 boilerplate digits must not become "the code"
        assert apply_otp._extract_code("Copyright 2026. Your code: 7714") == "7714"

    def test_no_digits_no_code(self):
        assert apply_otp._extract_code("please verify your email") == ""


class TestFetchRails:
    def _wire(self, monkeypatch, tmp_path, messages, job_records=None):
        monkeypatch.setattr(apply_otp, "gmail_api", _GmailStub(messages))
        monkeypatch.setattr(apply_otp, "CONSUMED", tmp_path / "otp_consumed.json")
        monkeypatch.setattr(apply_otp, "jobs", _JobsStub(job_records))

    def test_returns_code_and_never_the_body(self, monkeypatch, tmp_path):
        self._wire(monkeypatch, tmp_path,
                   [_msg("m1", "Your verification code", "Your code is 493021. SECRET BODY TEXT.")])
        r = apply_otp.fetch_code("job1", "ashby")
        assert r["ok"] and r["code"] == "493021"
        assert "SECRET" not in str(r)          # body never crosses the firewall
        assert set(r) <= {"ok", "code", "from_domain", "subject", "age_s"}

    def test_stale_message_is_invisible(self, monkeypatch, tmp_path):
        self._wire(monkeypatch, tmp_path,
                   [_msg("m1", "Your verification code", "code 493021", age_s=3600)])
        assert not apply_otp.fetch_code("job1")["ok"]

    def test_non_verification_email_never_leaks_digits(self, monkeypatch, tmp_path):
        # a fresh invoice with digits must NOT be treated as an OTP source
        self._wire(monkeypatch, tmp_path,
                   [_msg("m1", "Invoice from Acme", "Amount due 482913 cents")])
        assert not apply_otp.fetch_code("job1")["ok"]

    def test_consumed_once_across_jobs(self, monkeypatch, tmp_path):
        self._wire(monkeypatch, tmp_path,
                   [_msg("m1", "Your verification code", "code 493021")])
        assert apply_otp.fetch_code("jobA")["ok"]
        assert not apply_otp.fetch_code("jobB")["ok"]   # jobB can't steal jobA's code
        assert apply_otp.fetch_code("jobA")["ok"]       # jobA itself may re-read

    def test_hint_prefers_matching_sender(self, monkeypatch, tmp_path):
        self._wire(monkeypatch, tmp_path, [
            _msg("m1", "Your verification code", "code 111111", age_s=10, sender="no-reply@lever.co"),
            _msg("m2", "Greenhouse verification code", "code 222222", age_s=40,
                 sender="no-reply@greenhouse.io"),
        ])
        r = apply_otp.fetch_code("job1", hint="greenhouse")
        assert r["code"] == "222222"            # hinted match beats the fresher one

    def test_hint_match_is_required_not_just_preferred(self, monkeypatch, tmp_path):
        # CX14: an unrelated fresher verification email must not win just because nothing
        # else in the inbox happens to match the hint -- fail closed instead.
        self._wire(monkeypatch, tmp_path,
                   [_msg("m1", "Your verification code", "code 999999", sender="no-reply@lever.co")])
        r = apply_otp.fetch_code("job1", hint="totallydifferentcompany")
        assert not r["ok"]

    def test_hint_for_different_company_does_not_override_job_record_anchor(self, monkeypatch, tmp_path):
        # regression (R1#1, post-17bf56c): job1's OWN record says its employer is Ashby -- a
        # caller-supplied hint for some OTHER company (the caller is a browser operator,
        # possibly steered by a hostile third-party page) must CORROBORATE that anchor, never
        # REPLACE it, and must not surface a different application's OTP under job1's id.
        self._wire(monkeypatch, tmp_path, [
            _msg("m1", "Your verification code", "code 111111", age_s=5, sender="no-reply@ashbyhq.com"),
            _msg("m2", "Greenhouse verification code", "code 222222", age_s=10,
                 sender="no-reply@greenhouse.io"),
        ], job_records=[{"id": "job1", "company": "Ashby"}])
        r = apply_otp.fetch_code("job1", hint="greenhouse")
        assert r["ok"] and r["code"] == "111111"   # the job's own anchor (Ashby) wins, not the
        #                                            mismatched hint (which would have leaked
        #                                            greenhouse's code, "222222", under job1)

    def test_derives_company_from_job_record_when_no_hint(self, monkeypatch, tmp_path):
        # CX14: the caller (apply operator) passed no hint, but OUR OWN job record names the
        # employer -- that must still authenticate the match, not "freshest wins".
        self._wire(monkeypatch, tmp_path, [
            _msg("m1", "Your verification code", "code 111111", age_s=5, sender="no-reply@lever.co"),
            _msg("m2", "Greenhouse verification code", "code 222222", age_s=40,
                 sender="no-reply@greenhouse.io"),
        ], job_records=[{"id": "job1", "company": "Greenhouse"}])
        r = apply_otp.fetch_code("job1")
        assert r["ok"] and r["code"] == "222222"

    def test_lock_revalidates_against_a_concurrent_claim(self, monkeypatch, tmp_path):
        # CX16: the claim must be re-checked under the lock against a FRESH read, not trusted
        # from the pre-lock scan -- simulate a rival process claiming "m1" for a different job
        # in the window between this call's outer scan and its locked commit.
        self._wire(monkeypatch, tmp_path,
                   [_msg("m1", "Your verification code", "code 493021")])
        calls = {"n": 0}

        def _racy_load():
            calls["n"] += 1
            return {} if calls["n"] == 1 else {"m1": "someone-else"}

        monkeypatch.setattr(apply_otp, "_load_consumed", _racy_load)
        r = apply_otp.fetch_code("jobA")
        assert not r["ok"]                      # must not hand out a code claimed mid-flight

    def test_gmail_down_degrades_clean(self, monkeypatch, tmp_path):
        class _Dead:
            def search(self, q, n):
                raise OSError("no net")
        monkeypatch.setattr(apply_otp, "gmail_api", _Dead())
        monkeypatch.setattr(apply_otp, "CONSUMED", tmp_path / "c.json")
        r = apply_otp.fetch_code("job1")
        assert r["ok"] is False and "code" not in r


class TestOtpRouteAuth:
    def test_per_job_cb_authorizes_only_its_own_jid(self):
        import server
        cb = server._apply_cb("jobA")
        assert server._apply_cb_ok("/api/apply/otp", _Req({"cb": cb, "jid": "jobA"}))
        assert not server._apply_cb_ok("/api/apply/otp", _Req({"cb": cb, "jid": "jobB"}))
        assert not server._apply_cb_ok("/api/apply/otp", _Req({"cb": "deadbeef", "jid": "jobA"}))
        assert not server._apply_cb_ok("/api/apply/otp", _Req({"jid": "jobA"}))
        # and the original applied/skipped containment still holds
        assert server._apply_cb_ok("/api/jobs/jobA/applied", _Req({"cb": cb}))
        assert not server._apply_cb_ok("/api/state", _Req({"cb": cb}))


class TestOperatorTooling:
    def test_browser_tabs_allowed_for_otp_flow(self):
        # the email-OTP flow REQUIRES a second tab; without browser_tabs the operator
        # navigates away, SPA state resets, ADP invalidates the code = unbreakable loop
        # (2026-07-11: ShipBob/Hubbard/NDG/Woodhouse all lost to this)
        import server
        assert "mcp__playwright__browser_tabs" in server._PW_TOOLS
        # the danger tools stay OFF (operators browse hostile third-party pages)
        assert not any("evaluate" in t or "run_code" in t for t in server._PW_TOOLS)

    def test_alnum_labeled_code_extracts(self):
        import apply_otp
        assert apply_otp._extract_code("Your verification code: 7K2M9QX1") == "7K2M9QX1"
        assert apply_otp._extract_code("code: CONFIRM your email") == ""
