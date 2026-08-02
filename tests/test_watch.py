#!/usr/bin/env python3
"""/pub/watch wrist-feed tests (JARVIS Apple Watch, 2026-07-11).

Each test pins a containment or hygiene rule:
- 401 without / with a wrong sig; BRAIN_TOKEN and the guest token are NOT valid
  sigs (the watch credential authorizes one tiny read, nothing else)
- the payload never carries an email or URL and labels are hard-capped
  (wrist = glance data; guest-token lesson: read-only is not low-sensitivity)
- structure is stable for the watchOS client (ok/money/attention/one_thing/dial)

Run: .venv/bin/python -m pytest tests/test_watch.py -v
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import server  # noqa: E402


def _fresh():
    """Payload is TTL-cached; reset so each test exercises the real build."""
    server._WATCH_CACHE.update(t=0.0, data=None)


class TestWatchSigContainment:
    def test_no_sig_is_401(self):
        with pytest.raises(HTTPException) as e:
            server.pub_watch(sig="")
        assert e.value.status_code == 401

    def test_wrong_sig_is_401(self):
        with pytest.raises(HTTPException) as e:
            server.pub_watch(sig="a" * 24)
        assert e.value.status_code == 401

    def test_brain_token_is_not_a_sig(self):
        tok = server.secret("brain_token") or "not-a-real-token"
        with pytest.raises(HTTPException):
            server.pub_watch(sig=tok)

    def test_guest_token_is_not_a_sig(self):
        tok = server.secret("guest_token") or "not-a-real-guest"
        with pytest.raises(HTTPException):
            server.pub_watch(sig=tok)

    def test_good_sig_returns_payload(self):
        _fresh()
        out = server.pub_watch(sig=server._watch_sig())
        assert out.get("ok") is True
        for key in ("money", "attention", "one_thing", "dial", "ts"):
            assert key in out

    def test_sig_is_versioned_and_short(self):
        # 24 hex chars (96-bit), derived from the version string: bumping
        # _WATCH_SIG_MSG must rotate every issued credential.
        s = server._watch_sig()
        assert re.fullmatch(r"[0-9a-f]{24}", s)
        old = server._WATCH_SIG_MSG
        try:
            server._WATCH_SIG_MSG = b"watch:v2"
            assert server._watch_sig() != s
        finally:
            server._WATCH_SIG_MSG = old


class TestWristScrub:
    def test_strips_email_and_url_tokens(self):
        s = server._wrist_scrub("mail lesley@medspa.com then https://x.co today")
        assert "@" not in s and "://" not in s
        assert "mail" in s and "today" in s

    def test_caps_length(self):
        assert len(server._wrist_scrub("word " * 200)) <= 90

    def test_none_safe(self):
        assert server._wrist_scrub(None) == ""


class TestWatchPayloadHygiene:
    def test_no_email_or_url_anywhere(self):
        _fresh()
        blob = json.dumps(server.pub_watch(sig=server._watch_sig()))
        assert "://" not in blob
        assert not re.search(r"[\w.+-]+@[\w-]+\.\w", blob)

    def test_tokens_never_in_payload(self):
        _fresh()
        blob = json.dumps(server.pub_watch(sig=server._watch_sig()))
        for name in ("brain_token", "guest_token"):
            tok = server.secret(name)
            if tok:
                assert tok not in blob

    def test_attention_capped_at_three_scrubbed_labels(self):
        _fresh()
        out = server.pub_watch(sig=server._watch_sig())
        att = out["attention"]
        assert len(att) <= 3
        for a in att:
            assert set(a) == {"kind", "label"}
            assert len(a["label"]) <= 90

    def test_dial_counts_are_nonnegative_ints(self):
        _fresh()
        d = server.pub_watch(sig=server._watch_sig())["dial"]
        assert d["queued"] >= 0 and d["today"] >= 0
        assert isinstance(d["queued"], int) and isinstance(d["today"], int)
