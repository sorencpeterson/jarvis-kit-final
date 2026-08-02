#!/usr/bin/env python3
"""JARVIS inbox-triage routing (2026-07-08). 'triage my inbox' used to fall through to the
chat brain, which reached for the Gmail MCP and looped forever on a permission prompt it
could never get approved non-interactively. Now a mail READ intent is answered straight from
the morning mail digest (rung-1, no live crawl), compose/reply asks are NOT hijacked, and the
chat spawn carries --strict-mcp-config so it can't reach Gmail MCP at all.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import commander  # noqa: E402


class TestMailIntentRouting:
    def _digest(self, monkeypatch, tmp_path):
        d = {"date": "2026-07-08", "generated": "2026-07-08T10:03:30+02:00",
             "top_line": "lisa needs a reply about the number port",
             "sections": {"vip": [], "response_needed": [
                 {"from": "lisa@x.com", "subject": "Fwd: Updates", "why": "direct question",
                  "draft_ready": True}], "business": [{"from": "a@b.com", "subject": "x"}]}}
        f = tmp_path / "mail_digest.json"
        f.write_text(json.dumps(d))
        monkeypatch.setattr(commander, "ROOT", tmp_path)  # _mail_digest_reply reads ROOT/store...
        (tmp_path / "store").mkdir(exist_ok=True)
        (tmp_path / "store" / "mail_digest.json").write_text(json.dumps(d))

    def test_triage_reads_digest_no_cli(self, monkeypatch, tmp_path):
        self._digest(monkeypatch, tmp_path)
        # if the fast-path fires, _cli is never called; make _cli explode to prove it
        monkeypatch.setattr(commander.planner, "_cli",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("hit the CLI")))
        for msg in ("triage my inbox", "check my email", "what's in my inbox", "go through my inbox"):
            r = commander.interpret(msg)
            assert r["reply"].startswith("Inbox triage"), msg
            assert r["steps"] == []
            assert "lisa" in r["reply"]

    def test_compose_is_not_hijacked(self, monkeypatch, tmp_path):
        self._digest(monkeypatch, tmp_path)
        # a compose/reply ask must NOT return the digest; it should reach the normal path (_cli)
        called = {"n": 0}
        monkeypatch.setattr(commander.planner, "_cli",
                            lambda *a, **k: called.__setitem__("n", called["n"] + 1) or '{"steps":[],"reply":"ok"}')
        r = commander.interpret("draft a reply to lisa")
        assert not r["reply"].startswith("Inbox triage")

    def test_refresh_triggers_bg_rescan(self, monkeypatch, tmp_path):
        spawned = {}
        monkeypatch.setattr(commander, "_mail_refresh_bg", lambda: "Re-scanning your inbox now.")
        r = commander.interpret("refresh my inbox")
        assert "Re-scanning" in r["reply"] and r["steps"] == []


class TestChatSpawnHasNoMcp:
    def test_cli_and_stream_pass_strict_mcp(self):
        import inspect
        import planner
        # the text-gen + streaming chat spawns must strip MCP servers so they can't hang on a
        # Gmail permission prompt (the whole point of the 2026-07-08 fix)
        assert "--strict-mcp-config" in inspect.getsource(planner._cli)
        assert "--strict-mcp-config" in inspect.getsource(commander._stream_converse)
