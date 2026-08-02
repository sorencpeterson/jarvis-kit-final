#!/usr/bin/env python3
"""Unit tests for agents/warm_followup.py's C187 suppress-check addition.

The original conveyor's template rendering / GHL-lookup / proposal-factory-fire
behavior is exercised live by the mission's own verification pass (real CLI
invocations against real store files); this file focuses on the NEW suppress gate,
both as a direct _is_suppressed() unit test and as a full run() integration test
proving a suppressed contact gets zero draft and zero side effects.

Run: .venv/bin/python -m pytest tests/test_warm_followup.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import reply_watch  # noqa: E402
import proposal_factory  # noqa: E402
import warm_followup  # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(warm_followup, "SUPPRESS", tmp_path / "suppress.jsonl")
    monkeypatch.setattr(reply_watch, "REPLIES", tmp_path / "replies.jsonl")
    return tmp_path


class TestIsSuppressed:
    def test_no_file_not_suppressed(self, isolated):
        assert not warm_followup._is_suppressed("a@b.com", "Braydon")

    def test_matches_by_email(self, isolated):
        with warm_followup.SUPPRESS.open("w") as f:
            f.write(json.dumps({"contact_id": "", "email": "a@b.com"}) + "\n")
        assert warm_followup._is_suppressed("a@b.com", "")

    def test_matches_by_name(self, isolated):
        with warm_followup.SUPPRESS.open("w") as f:
            f.write(json.dumps({"contact_id": "", "email": "", "name": "Braydon Lj"}) + "\n")
        assert warm_followup._is_suppressed("", "Braydon Lj")

    def test_email_match_case_insensitive(self, isolated):
        with warm_followup.SUPPRESS.open("w") as f:
            f.write(json.dumps({"email": "A@B.COM"}) + "\n")
        assert warm_followup._is_suppressed("a@b.com", "")

    def test_name_match_case_insensitive(self, isolated):
        with warm_followup.SUPPRESS.open("w") as f:
            f.write(json.dumps({"name": "BRAYDON LJ"}) + "\n")
        assert warm_followup._is_suppressed("", "braydon lj")

    def test_no_match_not_suppressed(self, isolated):
        with warm_followup.SUPPRESS.open("w") as f:
            f.write(json.dumps({"email": "other@x.com", "name": "Someone Else"}) + "\n")
        assert not warm_followup._is_suppressed("a@b.com", "Braydon")

    def test_malformed_line_skipped_not_raised(self, isolated):
        with warm_followup.SUPPRESS.open("w") as f:
            f.write("not json\n")
            f.write(json.dumps({"email": "a@b.com"}) + "\n")
        assert warm_followup._is_suppressed("a@b.com", "")


class TestRunSuppressFirst:
    def test_suppressed_contact_produces_no_draft(self, isolated, monkeypatch):
        with warm_followup.SUPPRESS.open("w") as f:
            f.write(json.dumps({"email": "blocked@x.com"}) + "\n")
        find_contact_called = {"n": 0}
        monkeypatch.setattr(proposal_factory, "find_contact",
                            lambda **k: find_contact_called.__setitem__("n", find_contact_called["n"] + 1) or {})

        warm_followup.run("w1", "dead", "Blocked Guy", "", "blocked@x.com", "local service")

        assert reply_watch._load() == []
        assert find_contact_called["n"] == 0  # never even resolved the GHL contact

    def test_non_suppressed_contact_still_gets_draft(self, isolated, monkeypatch):
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {})
        warm_followup.run("w1", "dead", "Real Guy", "", "real@x.com", "local service")
        recs = reply_watch._load()
        assert len(recs) == 1
        assert recs[0]["name"] == "Real Guy"

    def test_suppressed_by_name_only_also_blocked(self, isolated, monkeypatch):
        with warm_followup.SUPPRESS.open("w") as f:
            f.write(json.dumps({"name": "Blocked By Name"}) + "\n")
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {})
        warm_followup.run("w1", "noans", "Blocked By Name", "+15550001111", "", "local service")
        assert reply_watch._load() == []

    def test_unknown_dispo_returns_before_suppress_check(self, isolated, monkeypatch):
        # a dispo with no matching template returns immediately -- confirm this
        # still works and doesn't error even with the new check added above it
        called = {"n": 0}
        monkeypatch.setattr(warm_followup, "_is_suppressed",
                            lambda *a, **k: called.__setitem__("n", called["n"] + 1) or False)
        warm_followup.run("w1", "not_a_real_dispo", "Someone", "", "", "local service")
        assert reply_watch._load() == []
        # the unknown-dispo early return happens BEFORE the suppress check even runs
        # (matches the original code's structure: template lookup first)
        assert called["n"] == 0
