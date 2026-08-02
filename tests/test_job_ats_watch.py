#!/usr/bin/env python3
"""Unit tests for agents/job_ats_watch.py (D5 #2/#5 regressions).

Two gates that were missing:
1. easy derivation: jobs._norm hardcodes easy=True, so every ATS-watch row
   (including greenhouse, which is NOT in jobs.EASY_ATS) auto-approved into
   the apply pipeline when job_auto was on. job_ats_watch._norm now derives
   easy from jobs.EASY_ATS the same way jobs._extract does, and run() only
   auto-approves when easy is True (mirrors jobs.source_and_queue).
2. freshness: run() never applied jobs._MAX_AGE (21 days), and Lever's
   epoch-millisecond createdAt was unparseable by jobs._age_days anyway,
   silently disabling any age gate for Lever. Stale rows are now skipped
   and Lever timestamps are converted to ISO at fetch time.

No network calls: fetchers and the jobs store are monkeypatched.
Run: .venv/bin/python -m pytest tests/test_job_ats_watch.py -v
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import job_ats_watch  # noqa: E402
import jobs  # noqa: E402


def _iso_days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


def _row(ats: str, jid, title: str, posted) -> dict:
    """Build a row exactly the way the fetchers do."""
    return job_ats_watch._norm(ats, f"ats-{ats}", jid, title, "TestCo",
                               f"https://example.com/{jid}", None, "", posted)


class TestEasyDerivation:
    def test_greenhouse_is_not_easy(self):
        assert _row("greenhouse", 1, "Marketing Manager", None)["easy"] is False

    def test_lever_and_ashby_are_easy(self):
        assert _row("lever", 2, "Marketing Manager", None)["easy"] is True
        assert _row("ashby", 3, "Marketing Manager", None)["easy"] is True

    def test_tracks_jobs_easy_ats_exactly(self):
        # The derivation must stay in lockstep with jobs.EASY_ATS, never a copy.
        for ats in ("lever", "ashby", "greenhouse", "workday", "bamboohr"):
            assert _row(ats, 4, "t", None)["easy"] == (ats in jobs.EASY_ATS)

    def test_other_norm_fields_intact(self):
        rec = _row("greenhouse", 5, "SEO Manager", None)
        assert rec["id"] == "ats-greenhouse:5"
        assert rec["company"] == "TestCo"
        assert rec["source"] == "ats-greenhouse"


class TestIsoFromMs:
    def test_epoch_ms_becomes_age_gateable_iso(self):
        ms = int((datetime.now(timezone.utc) - timedelta(days=40)).timestamp() * 1000)
        age = jobs._age_days(job_ats_watch._iso_from_ms(ms))
        assert age is not None and 39 <= age <= 41

    def test_raw_epoch_ms_is_not_parseable_without_conversion(self):
        # Documents WHY the conversion exists: _age_days returns None on raw ms,
        # which would silently disable the freshness gate for every Lever row.
        ms = int((datetime.now(timezone.utc) - timedelta(days=40)).timestamp() * 1000)
        assert jobs._age_days(ms) is None

    def test_none_and_iso_passthrough(self):
        assert job_ats_watch._iso_from_ms(None) is None
        iso = _iso_days_ago(3)
        assert job_ats_watch._iso_from_ms(iso) == iso


class TestRunGates:
    def _run(self, monkeypatch, ats: str, rows: list[dict], auto: bool):
        saved = []
        monkeypatch.setattr(job_ats_watch, "_load_watch",
                            lambda: [{"co": "TestCo", "ats": ats, "slug": "s"}])
        monkeypatch.setitem(job_ats_watch.FETCH, ats, lambda slug, co: rows)
        monkeypatch.setattr(jobs, "load_jobs", lambda: [])
        monkeypatch.setattr(jobs, "auto_on", lambda: auto)
        monkeypatch.setattr(jobs, "_min_yearly", lambda: 95000)
        monkeypatch.setattr(jobs, "_save", saved.append)
        res = job_ats_watch.run()
        return res, saved

    def test_non_easy_ats_never_auto_approves(self, monkeypatch):
        # D5 #2 regression: greenhouse + job_auto ON must stage PENDING.
        rows = [_row("greenhouse", 10, "Growth Marketing Manager", _iso_days_ago(1))]
        res, saved = self._run(monkeypatch, "greenhouse", rows, auto=True)
        assert res["staged"] == 1
        assert saved[0]["status"] == "pending"

    def test_easy_ats_auto_approves_when_auto_on(self, monkeypatch):
        rows = [_row("lever", 11, "Growth Marketing Manager", _iso_days_ago(1))]
        res, saved = self._run(monkeypatch, "lever", rows, auto=True)
        assert res["staged"] == 1
        assert saved[0]["status"] == "approved"

    def test_easy_ats_stays_pending_when_auto_off(self, monkeypatch):
        rows = [_row("lever", 12, "Growth Marketing Manager", _iso_days_ago(1))]
        res, saved = self._run(monkeypatch, "lever", rows, auto=False)
        assert res["staged"] == 1
        assert saved[0]["status"] == "pending"

    def test_stale_rows_skipped(self, monkeypatch):
        # D5 #5 regression: rows past jobs._MAX_AGE (21d) never stage.
        rows = [_row("lever", 13, "SEO Manager", _iso_days_ago(40)),
                _row("lever", 14, "Growth Marketing Manager", _iso_days_ago(1))]
        res, saved = self._run(monkeypatch, "lever", rows, auto=False)
        assert res["staged"] == 1
        assert res["reasons"].get("stale") == 1
        assert [r["id"] for r in saved] == ["ats-lever:14"]

    def test_unknown_age_still_stages(self, monkeypatch):
        # Matches jobs.source_and_queue: no posted date means no age gate
        # (the gate drops KNOWN-stale rows, it does not require a date).
        rows = [_row("lever", 15, "Growth Marketing Manager", None)]
        res, saved = self._run(monkeypatch, "lever", rows, auto=False)
        assert res["staged"] == 1


class TestCompFromText:
    """D5 P2: '401k match' used to parse as $401,000 comp_max and poison _fit.
    Parser is now $-required, conservative: ambiguous -> None."""

    def test_401k_never_parses(self):
        assert job_ats_watch._comp_from_text("401k match and benefits") is None
        assert job_ats_watch._comp_from_text("401(k) with employer match") is None

    def test_dollar_k_range(self):
        assert job_ats_watch._comp_from_text("Salary: $80k-$100k DOE") == 100000

    def test_comma_form(self):
        assert job_ats_watch._comp_from_text("up to $120,000 per year") == 120000

    def test_hourly_converts_annual(self):
        assert job_ats_watch._comp_from_text("$50/hr contract") == 50 * 2080

    def test_bare_number_ignored(self):
        assert job_ats_watch._comp_from_text("ref id 128000 in system") is None
