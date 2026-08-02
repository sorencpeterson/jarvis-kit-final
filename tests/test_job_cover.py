#!/usr/bin/env python3
"""Unit tests for agents/job_cover.py: the backfill() compare-and-swap append (R2-47,
2026-07-13 hunt).

R2-47: job_cover.backfill() used to jobs._save() a job dict CAPTURED from a load_jobs()
snapshot taken once before its loop even started. jobs._save() only locks the append itself,
not a read-modify-write -- so if a concurrent apply-operator advanced that SAME job
approved -> applying -> applied WHILE backfill() was still iterating, the stale-status
append (still carrying status="approved" from backfill's old snapshot) landed as a LATER
line in the append-only store than the real transition -- silently REVERTING an in-flight/
submitted job back to "approved" and opening it to a duplicate re-application.

Fixed with a load-under-lock + compare-and-swap append (_save_cover_override), mirroring
jobs.set_status()'s own pattern.

Run: .venv/bin/python -m pytest tests/test_job_cover.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import job_cover  # noqa: E402
import jobs  # noqa: E402


def _queue(tmp_path, monkeypatch, rows):
    q = tmp_path / "jobs.jsonl"
    q.write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr(jobs, "QUEUE", q)
    return q


def _status(job_id):
    return next((j["status"] for j in jobs.load_jobs() if j["id"] == job_id), None)


class TestSaveCoverOverrideCAS:
    def test_writes_when_still_approved(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "approved"}])
        assert job_cover._save_cover_override("j1", "a cover") is True
        rec = next(j for j in jobs.load_jobs() if j["id"] == "j1")
        assert rec["cover_override"] == "a cover" and rec["status"] == "approved"

    def test_writes_when_still_pending(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "pending"}])
        assert job_cover._save_cover_override("j1", "a cover") is True

    def test_refuses_once_job_moved_past_approved_to_applying(self, tmp_path, monkeypatch):
        # simulates the race: by the time the CAS write is attempted, an apply-operator
        # already advanced the job to 'applying'. The stale enrichment must NOT be
        # appended -- it would revert the real status back to 'approved'.
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "applying"}])
        assert job_cover._save_cover_override("j1", "a cover") is False
        assert _status("j1") == "applying"       # untouched, no revert

    def test_refuses_for_an_already_applied_job(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch, [{"id": "j1", "status": "applied", "applied_at": "x"}])
        assert job_cover._save_cover_override("j1", "a cover") is False
        assert _status("j1") == "applied"

    def test_refuses_when_job_is_missing(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch, [])
        assert job_cover._save_cover_override("nope", "a cover") is False

    def test_does_not_overwrite_an_existing_cover_override(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch,
               [{"id": "j1", "status": "approved", "cover_override": "existing"}])
        assert job_cover._save_cover_override("j1", "new cover") is False
        rec = next(j for j in jobs.load_jobs() if j["id"] == "j1")
        assert rec["cover_override"] == "existing"


class TestBackfillUsesCAS:
    def test_backfill_skips_a_job_that_raced_to_applying(self, tmp_path, monkeypatch):
        # the classic R2-47 scenario end-to-end through backfill(): load_jobs()'s snapshot
        # (taken at the top of backfill's loop) says 'approved', but by the time the CAS
        # write is attempted the REAL file already shows 'applying' (a concurrent operator
        # got there first). backfill() must not count or persist a stale revert.
        q = _queue(tmp_path, monkeypatch,
                   [{"id": "j1", "status": "approved", "title": "Marketing Manager", "company": "Acme"}])
        monkeypatch.setattr(jobs, "load_profile", lambda: {"default_cover": "Hi there."})
        monkeypatch.setattr(job_cover, "_FACT_CACHE", tmp_path / "company_facts.json")
        real_save = job_cover._save_cover_override

        def _racy_save(job_id, cov):
            q.write_text(json.dumps({"id": "j1", "status": "applying"}) + "\n")
            return real_save(job_id, cov)

        monkeypatch.setattr(job_cover, "_save_cover_override", _racy_save)
        n = job_cover.backfill()
        assert n == 0
        assert _status("j1") == "applying"     # never reverted to approved

    def test_backfill_writes_for_a_genuinely_untouched_job(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch,
               [{"id": "j1", "status": "approved", "title": "Marketing Manager", "company": "Acme"}])
        monkeypatch.setattr(jobs, "load_profile", lambda: {"default_cover": "Hi there."})
        monkeypatch.setattr(job_cover, "_FACT_CACHE", tmp_path / "company_facts.json")
        n = job_cover.backfill()
        assert n == 1
        rec = next(j for j in jobs.load_jobs() if j["id"] == "j1")
        assert rec.get("cover_override") and rec["status"] == "approved"

    def test_backfill_idempotent_on_rerun(self, tmp_path, monkeypatch):
        _queue(tmp_path, monkeypatch,
               [{"id": "j1", "status": "approved", "title": "Marketing Manager", "company": "Acme"}])
        monkeypatch.setattr(jobs, "load_profile", lambda: {"default_cover": "Hi there."})
        monkeypatch.setattr(job_cover, "_FACT_CACHE", tmp_path / "company_facts.json")
        assert job_cover.backfill() == 1
        assert job_cover.backfill() == 0   # already covered -- no new write


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
