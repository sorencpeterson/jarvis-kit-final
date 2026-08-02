#!/usr/bin/env python3
"""jobs.set_status compare-and-swap (2026-07-13 hunt): a replayed/late operator callback
(/applied or /skipped) must not clobber a more-authoritative terminal status already written
from a real employer email (interview|rejected|confirmed). Legit forward transitions still work.

Run: .venv/bin/python -m pytest tests/test_status_cas.py -v
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import jobs  # noqa: E402


def _queue(tmp_path, monkeypatch, rows):
    q = tmp_path / "jobs.jsonl"
    q.write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr(jobs, "QUEUE", q)
    return q


def _status(job_id):
    return next((j["status"] for j in jobs.load_jobs() if j["id"] == job_id), None)


class TestCallbackCompareAndSwap:
    def test_replayed_applied_cannot_clobber_rejected(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "rejected", "reason": "position_filled"}])
        jobs.set_status("j1", "applied")          # a late/duplicate operator callback
        assert _status("j1") == "rejected"         # real employer verdict preserved

    def test_replayed_skipped_cannot_clobber_interview(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "interview"}])
        jobs.set_status("j1", "skipped", "inflight_timeout")
        assert _status("j1") == "interview"

    def test_replayed_applied_cannot_clobber_confirmed(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "confirmed"}])
        jobs.set_status("j1", "applied")
        assert _status("j1") == "confirmed"

    def test_legit_applying_to_applied_still_works(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "applying"}])
        jobs.set_status("j1", "applied")
        assert _status("j1") == "applied"

    def test_authoritative_email_still_overwrites_applied(self, tmp_path, monkeypatch):
        # the guard is one-directional: a real employer email (interview/rejected) still wins
        # over a prior 'applied' — only the reverse (callback over terminal) is blocked
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "applied"}])
        jobs.set_status("j1", "rejected", "competitive_no_reason")
        assert _status("j1") == "rejected"
        jobs.set_status("j1", "interview")
        assert _status("j1") == "interview"

    def test_missing_job_returns_none(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch, [])
        assert jobs.set_status("nope", "applied") is None

    def test_replayed_skipped_cannot_clobber_replied(self, tmp_path, monkeypatch):
        # CX-G1 (2026-07-13 codex pass): 'replied' (a real human reply) was missing from the
        # terminal set a callback can't clobber -- a replayed /skipped must not erase it.
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "replied"}])
        jobs.set_status("j1", "skipped", "inflight_timeout")
        assert _status("j1") == "replied"

    def test_replayed_applied_cannot_clobber_replied(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "replied"}])
        jobs.set_status("j1", "applied")
        assert _status("j1") == "replied"


class TestReapproveCannotReopenASubmittedJob:
    """R2-10 (2026-07-13 hunt): a re-approve must not put an already-submitted job back in
    the apply pool -- that's a double-application waiting to happen."""

    def test_each_submitted_terminal_status_blocks_a_reapprove(self, tmp_path, monkeypatch):
        for terminal in ("applied", "confirmed", "interview", "rejected", "replied"):
            _queue(tmp_path, monkeypatch, [{"id": "j1", "status": terminal}])
            jobs.set_status("j1", "approved")
            assert _status("j1") == terminal, terminal

    def test_pending_can_still_be_approved_normally(self, tmp_path, monkeypatch):
        # sanity: the new guard only blocks ALREADY-SUBMITTED statuses, not the ordinary
        # pending -> approved promotion every job goes through first.
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "pending"}])
        jobs.set_status("j1", "approved")
        assert _status("j1") == "approved"

    def test_a_skipped_never_submitted_job_can_still_be_reapproved(self, tmp_path, monkeypatch):
        # a job skipped for a reason that never involved a real submission (dup_company,
        # stale_at_selection, attempt_cap, ...) is NOT "already submitted" -- Alex must
        # still be able to re-approve it if he chooses to.
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "skipped", "reason": "attempt_cap"}])
        jobs.set_status("j1", "approved")
        assert _status("j1") == "approved"


class TestMarkApplyingRequiresStillApproved:
    """R2-40 (2026-07-13 hunt): mark_applying (via set_status's CAS) must not flip a job to
    'applying' unless it is still 'approved' -- otherwise a stale/late mark_applying call
    stomps a concurrently-written terminal/skip status back to 'applying'."""

    def test_mark_applying_skips_a_job_no_longer_approved(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "skipped", "reason": "attempt_cap"}])
        jobs.mark_applying(["j1"])
        assert _status("j1") == "skipped"

    def test_mark_applying_flips_a_still_approved_job(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "approved"}])
        jobs.mark_applying(["j1"])
        assert _status("j1") == "applying"

    def test_direct_set_status_applying_also_guarded(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "applied", "applied_at": "x"}])
        jobs.set_status("j1", "applying")
        assert _status("j1") == "applied"

    def test_mark_applying_passes_expect_approved_explicitly(self, tmp_path, monkeypatch):
        # regression (post-17bf56c): mark_applying must call set_status with expect="approved"
        # (the real CAS), not rely implicitly on set_status's hardcoded default.
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "approved"}])
        calls = []
        real = jobs.set_status

        def _spy(jid, status, reason=None, expect=None):
            calls.append((jid, status, expect))
            return real(jid, status, reason, expect=expect)
        monkeypatch.setattr(jobs, "set_status", _spy)
        jobs.mark_applying(["j1"])
        assert calls == [("j1", "applying", "approved")]


class TestApplyingAlsoBlocksReapprove:
    """Regression (post-17bf56c): 'applying' (an operator actively mid-submit) was missing
    from the re-approve blocklist -- a re-approve tap racing the in-flight window could put a
    job an operator is actively working back into the approved pool."""

    def test_applying_job_cannot_be_reapproved(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "applying", "applying_at": "x"}])
        jobs.set_status("j1", "approved")
        assert _status("j1") == "applying"


class TestExpectedStatusCAS:
    """The `expect` param (regression fix, post-17bf56c): jobs.set_status used to be a
    hardcoded blocklist of specific (status, cur) pairs, not a real compare-and-swap. `expect`
    (a status string, or a set of acceptable current statuses) makes the check-and-write
    atomic under the SAME lock, for any caller that needs it -- not just the cases someone
    thought to special-case."""

    def test_expect_str_matches_writes(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "sent"}])
        rec = jobs.set_status("j1", "accepted", expect="sent")
        assert rec["status"] == "accepted"
        assert _status("j1") == "accepted"

    def test_expect_str_mismatch_blocks_write(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "skipped"}])
        rec = jobs.set_status("j1", "applying", expect="approved")
        assert rec["status"] == "skipped"          # unchanged record returned
        assert _status("j1") == "skipped"           # nothing written

    def test_expect_set_matches_writes(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "interview"}])
        jobs.set_status("j1", "skipped", expect={"applying", "interview"})
        assert _status("j1") == "skipped"

    def test_expect_set_mismatch_blocks_write(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "confirmed"}])
        jobs.set_status("j1", "skipped", expect={"applying", "interview"})
        assert _status("j1") == "confirmed"

    def test_expect_overrides_default_blocklist_when_caller_explicitly_allows_it(self, tmp_path, monkeypatch):
        # a caller that explicitly names its own expected current status gets a REAL CAS
        # against exactly that -- it is not additionally filtered through the hardcoded
        # applied/skipped-vs-terminal defaults (those only apply when expect is omitted).
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "interview"}])
        rec = jobs.set_status("j1", "applied", expect="interview")
        assert rec["status"] == "applied"
        assert _status("j1") == "applied"

    def test_missing_job_returns_none_even_with_expect(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch, [])
        assert jobs.set_status("nope", "applied", expect="approved") is None


class TestAppliedReplayIsIdempotent:
    """R1#10 (regression, post-17bf56c): an "applied"->"applied" replay (duplicated callback,
    network retry) must not refresh applied_at -- that would move the real submission
    timestamp forward and miscount against applied_today()'s daily-cap check on a later
    stale replay landing on some other day."""

    def test_replayed_applied_does_not_refresh_applied_at(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch,
              [{"id": "j1", "status": "applied", "applied_at": "2020-01-01T00:00:00+00:00"}])
        jobs.set_status("j1", "applied")
        rec = next(j for j in jobs.load_jobs() if j["id"] == "j1")
        assert rec["applied_at"] == "2020-01-01T00:00:00+00:00"

    def test_fresh_transition_to_applied_does_set_applied_at(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "applying"}])
        jobs.set_status("j1", "applied")
        rec = next(j for j in jobs.load_jobs() if j["id"] == "j1")
        assert rec["applied_at"][:10] == jobs.now_iso()[:10]  # stamped fresh, today
