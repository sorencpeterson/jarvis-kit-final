#!/usr/bin/env python3
"""B0 regressions (2026-07-07): per-line parse on /api/objections + /api/futures
(one corrupt line must not blank the endpoint, D3 #5/#6) and the flocked
config.json RMW routes still round-trip (D3 #25).

Run: .venv/bin/python -m pytest tests/test_server_perline.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import server  # noqa: E402


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    (tmp_path / "store").mkdir()
    monkeypatch.setattr(server, "ROOT", tmp_path)
    return tmp_path


class TestPerLineParse:
    def test_objections_survive_corrupt_line(self, sandbox):
        p = sandbox / "store" / "objections.jsonl"
        p.write_text(json.dumps({"objection": "too pricey", "counter": "value holds"}) + "\n"
                     + "{corrupt not json\n"
                     + json.dumps({"objection": "later", "counter": "book now"}) + "\n")
        rows = server.api_objections()["rows"]
        assert len(rows) == 2  # both good rows survive the bad middle line

    def test_futures_survive_corrupt_line(self, sandbox):
        p = sandbox / "store" / "futures.jsonl"
        p.write_text(json.dumps({"id": "f1", "when_reply_from": "amy", "text": "t",
                                 "status": "waiting"}) + "\n"
                     + "\x00garbage\n"
                     + json.dumps({"id": "f2", "when_reply_from": "bo", "text": "t",
                                   "status": "waiting"}) + "\n")
        rows = server.api_futures()["rows"]
        assert {r["id"] for r in rows} == {"f1", "f2"}

    def test_futures_skip_missing_id_line(self, sandbox):
        p = sandbox / "store" / "futures.jsonl"
        p.write_text(json.dumps({"status": "waiting"}) + "\n"
                     + json.dumps({"id": "ok", "status": "waiting"}) + "\n")
        assert [r["id"] for r in server.api_futures()["rows"]] == ["ok"]


class _FakeReq:
    def __init__(self, headers=None, body=b"{}"):
        self.headers = headers or {}
        self._body = body

    async def body(self):
        return self._body


def _call(coro):
    import asyncio
    return asyncio.run(coro)


class TestGhlWebhook:
    def test_fail_closed_without_secret(self, sandbox, monkeypatch):
        import store_lib
        monkeypatch.setattr(store_lib, "secret", lambda n, d="": "")
        r = _call(server.api_ghl_webhook(_FakeReq()))
        assert r.status_code == 503

    def test_rejects_wrong_secret(self, sandbox, monkeypatch):
        import store_lib
        monkeypatch.setattr(store_lib, "secret", lambda n, d="": "right")
        r = _call(server.api_ghl_webhook(_FakeReq({"X-GHL-Webhook-Secret": "wrong"})))
        assert r.status_code == 403

    def test_accepts_and_appends(self, sandbox, monkeypatch):
        import store_lib
        monkeypatch.setattr(store_lib, "secret", lambda n, d="": "s3cret")
        body = json.dumps({"event": "bounce", "email": "x@y.com"}).encode()
        r = _call(server.api_ghl_webhook(_FakeReq({"X-GHL-Webhook-Secret": "s3cret"}, body)))
        assert r == {"ok": True}
        lines = (sandbox / "store" / "ghl_events.jsonl").read_text().splitlines()
        rec = json.loads(lines[0])
        assert rec["event"] == "bounce" and rec["received"]

    def test_rejects_oversize(self, sandbox, monkeypatch):
        import store_lib
        monkeypatch.setattr(store_lib, "secret", lambda n, d="": "s")
        r = _call(server.api_ghl_webhook(_FakeReq({"X-GHL-Webhook-Secret": "s"}, b"x" * 70000)))
        assert r.status_code == 413


class TestNeedsMoneySort:
    def test_interview_and_usd(self, sandbox, monkeypatch):
        import reply_watch, proposal_factory, jobs, networking, content_gen  # noqa: E401
        monkeypatch.setattr(reply_watch, "_load", lambda: [{"status": "pending"}])
        monkeypatch.setattr(proposal_factory, "load_queue",
                            lambda: [{"status": "staged", "price": 3500},
                                     {"status": "staged", "price": "1200"}])
        monkeypatch.setattr(jobs, "load_jobs", lambda: [{"id": "j1", "status": "interview"}])
        monkeypatch.setattr(jobs, "needs_manual", lambda: [])
        monkeypatch.setattr(jobs, "stalled", lambda: [])
        monkeypatch.setattr(networking, "load_queue", lambda: [])
        monkeypatch.setattr(content_gen, "load_posts", lambda: [])
        monkeypatch.setattr(server, "_retro_proposal", lambda: None)
        monkeypatch.setattr(server._outbox, "items", lambda: [])
        out = server.api_needs()
        keys = [i["key"] for i in out["items"]]
        assert keys[0] == "interviews"  # deal-mover sorts above routine counts
        prop = next(i for i in out["items"] if i["key"] == "proposals")
        assert prop["usd"] == 4700 and "$4,700" in prop["label"]


class TestFunnels:
    def test_proposal_funnel_stages(self, sandbox, monkeypatch):
        import proposal_factory
        monkeypatch.setattr(proposal_factory, "load_queue", lambda: [
            {"status": "staged", "price": 3500},
            {"status": "sent", "price": 2500, "sent_at": "2026-07-01", "opens": 2},
            {"status": "accepted", "price": 1200, "sent_at": "2026-07-01", "opens": 5},
            {"status": "skipped", "price": 800},
        ])
        st = server.api_proposals_funnel()["stages"]
        assert st["staged"] == {"n": 1, "usd": 3500}
        assert st["sent"]["n"] == 2 and st["sent"]["usd"] == 3700
        assert st["opened"]["n"] == 2
        assert st["accepted"] == {"n": 1, "usd": 1200}

    def test_jobs_funnel_merges_sources(self, sandbox, monkeypatch):
        (sandbox / "store" / "job_funnel.json").write_text(
            json.dumps({"total_records": 3, "by_status": {"applied": 2}}))
        import jobs
        monkeypatch.setattr(jobs, "load_jobs",
                            lambda: [{"status": "applied"}, {"status": "skipped"}])
        out = server.api_jobs_funnel()
        assert out["funnel"]["total_records"] == 3
        assert out["live_status"] == {"applied": 1, "skipped": 1}


class TestPubDeadman:
    def test_healthy_when_morning_stamped_today(self, sandbox, monkeypatch):
        import datetime as _dt
        today = _dt.date.today().isoformat()
        (sandbox / "store" / f".morning-done-{today}").touch()
        r = server.pub_deadman()
        assert r["ok"] is True and r["healthy"] is True and r["morning_stale"] is False

    def test_unhealthy_when_no_stamp(self, sandbox):
        # no morning-done stamp at all -> brain looks dead to the off-Mac canary
        r = server.pub_deadman()
        assert r["ok"] is True and r["healthy"] is False and r["morning_stale"] is True

    def test_leaks_nothing_sensitive(self, sandbox):
        # public route: only booleans + a date, never names/$/PII
        r = server.pub_deadman()
        assert set(r) <= {"ok", "healthy", "morning_last", "morning_stale"}


class TestConfigRoutesStillWork:
    def test_content_config_roundtrip(self, sandbox):
        cfg = sandbox / "store" / "config.json"
        cfg.write_text(json.dumps({"auto_approve_min": 0, "keep_me": True}))
        r = server.api_content_config_set(server.ContentCfg(auto_approve_min=7))
        assert r["ok"] and r["auto_approve_min"] == 7
        saved = json.loads(cfg.read_text())
        assert saved["auto_approve_min"] == 7 and saved["keep_me"] is True

    def test_jobs_config_roundtrip(self, sandbox):
        cfg = sandbox / "store" / "config.json"
        cfg.write_text(json.dumps({"job_auto": False}))
        r = server.api_jobs_config_set(server.JobsCfg(job_auto=True))
        assert r["ok"] and r["job_auto"] is True
        assert json.loads(cfg.read_text())["job_auto"] is True
