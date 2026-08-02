#!/usr/bin/env python3
"""Outbox rails (EMAIL-INFRA-SPEC Phase 1, built 2026-07-06).

Every rail in app/outbox.py's docstring is asserted here: one send per call, an item
sends once, daily cap, humanize on the send path, no retry on delivery failure.

Run: .venv/bin/python -m pytest tests/test_outbox.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import outbox  # noqa: E402


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """Point the module at throwaway store files so tests never touch real state."""
    monkeypatch.setattr(outbox, "OUTBOX", tmp_path / "outbox.json")
    monkeypatch.setattr(outbox, "SENT_LOG", tmp_path / "sent_log.jsonl")
    monkeypatch.setattr(outbox, "MAIL_DRAFTS", tmp_path / "mail_drafts.jsonl")
    monkeypatch.setattr(outbox, "_me_cache", {"t": 9e12, "addr": "alex@test.local"})
    return tmp_path


def _ok_deliver(raw, thread_id=""):
    return {"id": "msg123", "threadId": thread_id or "th123"}


class TestStage:
    def test_stage_and_list(self, sandbox):
        r = outbox.stage("a@b.com", "hi", "Short and human.")
        assert r["ok"] and r["item"]["status"] == "draft"
        assert outbox.items()[0]["to"] == "a@b.com"

    def test_stage_rejects_garbage(self, sandbox):
        assert not outbox.stage("not-an-address", "s", "b")["ok"]
        assert not outbox.stage("a@b.com", "s", "")["ok"]

    def test_stage_humanizes_em_dashes(self, sandbox):
        r = outbox.stage("a@b.com", "s", "warm beats cold — every time")
        assert "—" not in r["item"]["body"]


class TestSend:
    def test_sends_once_and_only_once(self, sandbox):
        oid = outbox.stage("a@b.com", "s", "body")["item"]["id"]
        r1 = outbox.send_one(oid, _deliver_fn=_ok_deliver)
        assert r1["ok"] and r1["item"]["status"] == "sent"
        r2 = outbox.send_one(oid, _deliver_fn=_ok_deliver)
        assert not r2["ok"] and "already sent" in r2["error"]

    def test_send_humanizes_override_body(self, sandbox):
        oid = outbox.stage("a@b.com", "s", "clean")["item"]["id"]
        r = outbox.send_one(oid, body_override="edited — with a dash", _deliver_fn=_ok_deliver)
        assert r["ok"] and "—" not in r["item"]["body"]

    def test_daily_cap_blocks(self, sandbox, monkeypatch):
        monkeypatch.setattr(outbox, "DAILY_CAP", 2)
        ids = [outbox.stage(f"a{i}@b.com", "s", "b")["item"]["id"] for i in range(3)]
        assert outbox.send_one(ids[0], _deliver_fn=_ok_deliver)["ok"]
        assert outbox.send_one(ids[1], _deliver_fn=_ok_deliver)["ok"]
        r = outbox.send_one(ids[2], _deliver_fn=_ok_deliver)
        assert not r["ok"] and "cap" in r["error"]

    def test_delivery_failure_stays_draft_no_retry(self, sandbox):
        oid = outbox.stage("a@b.com", "s", "b")["item"]["id"]
        calls = {"n": 0}

        def boom(raw, thread_id=""):
            calls["n"] += 1
            raise RuntimeError("504")

        r = outbox.send_one(oid, _deliver_fn=boom)
        assert not r["ok"] and calls["n"] == 1  # exactly ONE attempt, never auto-retried
        assert outbox.items()[0]["status"] == "draft"  # still his to retry deliberately

    def test_dismissed_never_sends(self, sandbox):
        oid = outbox.stage("a@b.com", "s", "b")["item"]["id"]
        outbox.dismiss(oid)
        assert not outbox.send_one(oid, _deliver_fn=_ok_deliver)["ok"]

    def test_claim_blocks_double_send(self, sandbox):
        # concurrent-tap: first claim flips draft->sending, second gets None (D6 audit)
        oid = outbox.stage("a@b.com", "s", "b")["item"]["id"]
        assert outbox._claim(oid) is not None
        assert outbox._claim(oid) is None
        # and reap_stuck recovers it to draft
        assert outbox.reap_stuck() == 1
        assert outbox.items()[0]["status"] == "draft"

    def test_send_failure_reverts_to_draft(self, sandbox):
        oid = outbox.stage("a@b.com", "s", "b")["item"]["id"]

        def boom(raw, thread_id=""):
            raise RuntimeError("504")
        r = outbox.send_one(oid, _deliver_fn=boom)
        assert not r["ok"]
        # claim was released back to draft so Alex can retry (not stuck at 'sending')
        assert outbox.items()[0]["status"] == "draft"


class TestImport:
    def test_imports_pending_and_dedups(self, sandbox):
        d = {"id": "m1", "to": "x@y.com", "subject": "Re: q", "draft": "reply text",
             "thread_id": "t9", "status": "pending"}
        outbox.MAIL_DRAFTS.write_text(json.dumps(d) + "\n")
        assert outbox.import_mail_drafts()["imported"] == 1
        assert outbox.import_mail_drafts()["imported"] == 0  # src_id dedup
        it = outbox.items()[0]
        assert it["thread_id"] == "t9" and it["source"] == "mail_drafts"

    def test_send_writes_back_to_mail_drafts(self, sandbox):
        d = {"id": "m2", "to": "x@y.com", "subject": "Re: q", "draft": "t",
             "thread_id": "t1", "status": "pending"}
        outbox.MAIL_DRAFTS.write_text(json.dumps(d) + "\n")
        outbox.import_mail_drafts()
        oid = outbox.items()[0]["id"]
        assert outbox.send_one(oid, _deliver_fn=_ok_deliver)["ok"]
        lines = [json.loads(x) for x in outbox.MAIL_DRAFTS.read_text().splitlines()]
        assert lines[-1] == {**lines[-1], "id": "m2", "status": "sent"}
