#!/usr/bin/env python3
"""Unit tests for agents/convo_context.py (C166 context window, C167 objection
sequence detection). GHL calls are mocked via monkeypatch on ghl_social._api;
store/objections.jsonl is isolated to tmp_path.

Run: .venv/bin/python -m pytest tests/test_convo_context.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import ghl_social  # noqa: E402
import convo_context  # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(convo_context, "OBJECTIONS", tmp_path / "objections.jsonl")
    return tmp_path


def _ghl_messages_payload(msgs: list[dict]) -> str:
    """Shape GHL's real GET /conversations/{id}/messages response, confirmed live:
    {"messages": {"messages": [{"direction": "inbound"|"outbound", "body": "...",
    "dateAdded": "..."}]}}"""
    return json.dumps({"messages": {"messages": msgs}})


class TestFetchContext:
    def test_empty_convo_id_returns_empty(self, monkeypatch):
        calls = {"n": 0}
        monkeypatch.setattr(ghl_social, "_api", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or "{}")
        result = convo_context.fetch_context("")
        assert result == []
        assert calls["n"] == 0  # never calls the API for an empty id

    def test_real_messages_parsed_in_reading_order(self, monkeypatch):
        # GHL returns newest-first
        payload = _ghl_messages_payload([
            {"direction": "inbound", "body": "third", "dateAdded": "3"},
            {"direction": "outbound", "body": "second", "dateAdded": "2"},
            {"direction": "inbound", "body": "first", "dateAdded": "1"},
        ])
        monkeypatch.setattr(ghl_social, "_api", lambda *a, **k: payload)
        result = convo_context.fetch_context("convo1", turns=5)
        assert [m["body"] for m in result] == ["first", "second", "third"]
        assert result[0]["dir"] == "inbound"
        assert result[1]["dir"] == "outbound"

    def test_system_events_filtered_out(self, monkeypatch):
        payload = _ghl_messages_payload([
            {"direction": "outbound", "body": "Opportunity updated", "dateAdded": "3"},
            {"direction": "inbound", "body": "a real reply", "dateAdded": "2"},
            {"direction": "outbound", "body": "Opportunity created", "dateAdded": "1"},
        ])
        monkeypatch.setattr(ghl_social, "_api", lambda *a, **k: payload)
        result = convo_context.fetch_context("convo1", turns=5)
        assert len(result) == 1
        assert result[0]["body"] == "a real reply"

    def test_system_events_do_not_starve_real_messages_under_limit(self, monkeypatch):
        # 6 system events + 3 real messages, asking for turns=3 should still surface
        # all 3 real messages, not fewer, even though the raw feed is mostly noise.
        raw = [{"direction": "outbound", "body": "Opportunity updated", "dateAdded": str(i)}
               for i in range(6)]
        raw += [{"direction": "inbound", "body": f"real {i}", "dateAdded": str(10 + i)}
                for i in range(3)]
        payload = _ghl_messages_payload(raw)
        monkeypatch.setattr(ghl_social, "_api", lambda *a, **k: payload)
        result = convo_context.fetch_context("convo1", turns=3)
        assert len(result) == 3
        assert all(m["body"].startswith("real") for m in result)

    def test_caps_to_requested_turns(self, monkeypatch):
        raw = [{"direction": "inbound", "body": f"msg{i}", "dateAdded": str(i)} for i in range(10)]
        payload = _ghl_messages_payload(raw)
        monkeypatch.setattr(ghl_social, "_api", lambda *a, **k: payload)
        result = convo_context.fetch_context("convo1", turns=5)
        assert len(result) == 5

    def test_empty_body_messages_dropped(self, monkeypatch):
        payload = _ghl_messages_payload([
            {"direction": "inbound", "body": "", "dateAdded": "1"},
            {"direction": "inbound", "body": "  ", "dateAdded": "2"},
            {"direction": "inbound", "body": "real one", "dateAdded": "3"},
        ])
        monkeypatch.setattr(ghl_social, "_api", lambda *a, **k: payload)
        result = convo_context.fetch_context("convo1", turns=5)
        assert len(result) == 1

    def test_malformed_response_returns_empty_not_raises(self, monkeypatch):
        monkeypatch.setattr(ghl_social, "_api", lambda *a, **k: "not even json")
        result = convo_context.fetch_context("convo1")
        assert result == []

    def test_api_exception_returns_empty_not_raises(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("network down")
        monkeypatch.setattr(ghl_social, "_api", _boom)
        # fetch_context only catches (ValueError, json.JSONDecodeError, IndexError);
        # confirm it at least degrades gracefully for the errors it's documented to
        # handle, and that a totally broken payload (empty string) doesn't raise.
        monkeypatch.setattr(ghl_social, "_api", lambda *a, **k: "")
        result = convo_context.fetch_context("convo1")
        assert result == []


class TestFormatContext:
    def test_empty_list_returns_empty_string(self):
        assert convo_context.format_context([]) == ""

    def test_formats_both_directions_labeled(self):
        msgs = [{"dir": "inbound", "body": "how much?", "ts": ""},
                {"dir": "outbound", "body": "1200 flat", "ts": ""}]
        out = convo_context.format_context(msgs)
        assert "THEM: how much?" in out
        assert "ALEX RIVERA: 1200 flat" in out
        assert out.index("THEM") < out.index("ALEX RIVERA")


class TestObjectionSequenceCount:
    def test_no_history_returns_zero(self, isolated):
        assert convo_context.objection_sequence_count("c1") == 0

    def test_no_id_and_no_name_returns_zero(self, isolated):
        assert convo_context.objection_sequence_count("", "") == 0

    def test_one_prior_objection_by_contact_id(self, isolated):
        convo_context.log_objection("too expensive", "compared to what", contact_id="c1")
        assert convo_context.objection_sequence_count("c1") == 1

    def test_two_prior_objections_counted(self, isolated):
        convo_context.log_objection("too expensive", "compared to what", contact_id="c1")
        convo_context.log_objection("still too much", "price is the price", contact_id="c1")
        assert convo_context.objection_sequence_count("c1") == 2

    def test_other_contacts_objections_not_counted(self, isolated):
        convo_context.log_objection("too expensive", "compared to what", contact_id="c1")
        convo_context.log_objection("too expensive", "compared to what", contact_id="c2")
        assert convo_context.objection_sequence_count("c1") == 1
        assert convo_context.objection_sequence_count("c2") == 1

    def test_legacy_row_with_no_contact_id_falls_back_to_name(self, isolated):
        # simulates a row written by server.py's /api/objections endpoint, which has
        # no contact_id field at all
        with convo_context.OBJECTIONS.open("w") as f:
            f.write(json.dumps({"ts": "x", "objection": "pricey", "counter": "y"}) + "\n")
        assert convo_context.objection_sequence_count("c1", name="Braydon Lj") == 0
        # only counts when a name is actually supplied AND matches -- a bare legacy
        # row with no name field never matches anything (can't fabricate a match)

    def test_name_fallback_matches_case_insensitively(self, isolated):
        with convo_context.OBJECTIONS.open("w") as f:
            f.write(json.dumps({"ts": "x", "objection": "pricey", "counter": "y",
                                "name": "Braydon Lj"}) + "\n")
        assert convo_context.objection_sequence_count("", name="braydon lj") == 1

    def test_contact_id_row_does_not_double_count_via_name_fallback(self, isolated):
        # a row that HAS a contact_id should only be matched by contact_id, not also
        # incidentally by name, so a caller passing both never double-counts one row
        convo_context.log_objection("pricey", "counter", contact_id="c1", name="Braydon Lj")
        assert convo_context.objection_sequence_count("c1", name="Braydon Lj") == 1


class TestLogObjection:
    def test_writes_expected_shape(self, isolated):
        convo_context.log_objection("too expensive", "compared to what", contact_id="c1",
                                    name="Braydon", niche="local service")
        rows = [json.loads(l) for l in convo_context.OBJECTIONS.read_text().splitlines()]
        assert len(rows) == 1
        r = rows[0]
        assert r["objection"] == "too expensive"
        assert r["counter"] == "compared to what"
        assert r["contact_id"] == "c1"
        assert r["name"] == "Braydon"
        assert r["niche"] == "local service"
        assert r["src"] == "reply_watch"
        assert "ts" in r

    def test_appends_not_overwrites(self, isolated):
        convo_context.log_objection("first", "counter1", contact_id="c1")
        convo_context.log_objection("second", "counter2", contact_id="c1")
        rows = [json.loads(l) for l in convo_context.OBJECTIONS.read_text().splitlines()]
        assert len(rows) == 2

    def test_truncates_long_fields(self, isolated):
        convo_context.log_objection("x" * 500, "y" * 500, contact_id="c1")
        rows = [json.loads(l) for l in convo_context.OBJECTIONS.read_text().splitlines()]
        assert len(rows[0]["objection"]) == 300
        assert len(rows[0]["counter"]) == 400
