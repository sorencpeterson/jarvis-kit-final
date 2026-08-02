#!/usr/bin/env python3
"""agree_accept regressions (post-17bf56c, gpt-5.6-sol review):

R2#1: a proposal Alex sends BY HAND never flips out of 'staged', so a sent-only claim
      404'd a legit acceptance of a manually-sent proposal. Accept must work from staged
      OR sent.
R2#2: the agreement record + evidence snapshot must be written BEFORE the final flip to
      'accepted', via an intermediate 'accepting' status, so a crash can't leave a proposal
      durably "accepted" with the evidence lost (returning already:true forever). A stuck
      'accepting' self-heals at server start (_reap_stuck_accepting).

Run: .venv/bin/python -m pytest tests/test_agree_accept.py -q
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import server  # noqa: E402
import proposal_factory  # noqa: E402
import planner  # noqa: E402

PID = "prop_20260714_abc123"


class _FakeReq:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def _wire(tmp_path, monkeypatch, status):
    """Point both server.ROOT (agreements + evidence) and proposal_factory.QUEUE at tmp,
    seed one proposal at `status`, create its .agree.html so the evidence snapshot can be
    written, and neutralize the outward notify/feed."""
    (tmp_path / "store" / "proposals").mkdir(parents=True)
    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(proposal_factory, "QUEUE", tmp_path / "store" / "proposals.jsonl")
    proposal_factory.save({"id": PID, "status": status, "company": "Acme HVAC", "price": 2500})
    (tmp_path / "store" / "proposals" / f"{PID}.agree.html").write_text("<h1>Agreement body</h1>")
    monkeypatch.setattr(planner, "notify", lambda *a, **k: True)
    monkeypatch.setattr(planner, "feed_add", lambda *a, **k: None)
    return server.agree_view and PID


def _accept(sig=None, name="Jordan Blake"):
    if sig is None:
        sig = proposal_factory.sig_for(PID)
    return asyncio.run(server.agree_accept(PID, _FakeReq({"name": name}), sig=sig))


def _status():
    return {x["id"]: x for x in proposal_factory.load_queue()}[PID]["status"]


class TestAcceptFromStagedOrSent:
    def test_accept_from_sent_still_works(self, tmp_path, monkeypatch):
        _wire(tmp_path, monkeypatch, "sent")
        r = _accept()
        assert r == {"ok": True}
        assert _status() == "accepted"

    def test_accept_from_staged_manually_sent_proposal(self, tmp_path, monkeypatch):
        # R2#1: a manually-sent proposal is still 'staged' -- accepting it must NOT 404.
        _wire(tmp_path, monkeypatch, "staged")
        r = _accept()
        assert r == {"ok": True}
        assert _status() == "accepted"

    def test_accept_from_skipped_is_rejected(self, tmp_path, monkeypatch):
        # a proposal that was never really live (skipped/suppressed) must not be acceptable.
        _wire(tmp_path, monkeypatch, "skipped")
        r = _accept()
        assert getattr(r, "status_code", None) == 404

    def test_bad_sig_404s(self, tmp_path, monkeypatch):
        _wire(tmp_path, monkeypatch, "sent")
        r = _accept(sig="deadbeef")
        assert getattr(r, "status_code", None) == 404
        assert _status() == "sent"   # untouched

    def test_short_name_rejected(self, tmp_path, monkeypatch):
        _wire(tmp_path, monkeypatch, "sent")
        r = _accept(name="Al")
        assert r == {"ok": False, "error": "name required"}
        assert _status() == "sent"


class TestEvidenceBeforeAccept:
    def test_agreement_and_evidence_written_on_accept(self, tmp_path, monkeypatch):
        _wire(tmp_path, monkeypatch, "sent")
        _accept()
        # the agreement record landed...
        ag = (tmp_path / "store" / "agreements.jsonl").read_text().splitlines()
        assert len(ag) == 1 and json.loads(ag[0])["pid"] == PID
        # ...and the frozen evidence snapshot with a content hash exists.
        snap = (tmp_path / "store" / "agreements" / f"{PID}-accepted.html")
        assert snap.exists() and "sha256(content)=" in snap.read_text()
        assert _status() == "accepted"

    def test_double_tap_is_idempotent(self, tmp_path, monkeypatch):
        _wire(tmp_path, monkeypatch, "sent")
        assert _accept() == {"ok": True}
        second = _accept()
        assert second == {"ok": True, "already": True}
        # exactly one agreement record -- the second tap did not double-log.
        assert len((tmp_path / "store" / "agreements.jsonl").read_text().splitlines()) == 1


class TestReapStuckAccepting:
    """A crash between the evidence write and the final 'accepted' flip leaves the proposal
    at the intermediate 'accepting'. _reap_stuck_accepting (server start) self-heals it:
    finalize if the evidence (agreement record) already landed, else roll back to 'sent'."""

    def test_finalizes_when_agreement_record_exists(self, tmp_path, monkeypatch):
        (tmp_path / "store").mkdir(parents=True)
        monkeypatch.setattr(server, "ROOT", tmp_path)
        monkeypatch.setattr(proposal_factory, "QUEUE", tmp_path / "store" / "proposals.jsonl")
        proposal_factory.save({"id": PID, "status": "accepting", "company": "Acme", "price": 2500})
        (tmp_path / "store" / "agreements.jsonl").write_text(
            json.dumps({"pid": PID, "signed_name": "Jordan", "company": "Acme"}) + "\n")
        server._reap_stuck_accepting()
        assert _status() == "accepted"   # evidence is durable -> finish the transaction

    def test_rolls_back_when_no_agreement_record(self, tmp_path, monkeypatch):
        (tmp_path / "store").mkdir(parents=True)
        monkeypatch.setattr(server, "ROOT", tmp_path)
        monkeypatch.setattr(proposal_factory, "QUEUE", tmp_path / "store" / "proposals.jsonl")
        proposal_factory.save({"id": PID, "status": "accepting", "company": "Acme", "price": 2500})
        # no agreements.jsonl at all -> the crash was before the evidence write
        server._reap_stuck_accepting()
        assert _status() == "sent"       # clean rollback so a retried accept writes evidence


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
