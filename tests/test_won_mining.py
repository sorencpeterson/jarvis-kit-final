#!/usr/bin/env python3
"""Unit tests for agents/won_mining.py (C177 won-conversation mining).

mine_contact() is tested directly with fixture rows. run() gets isolated-store
integration tests covering the honest-empty-state path, idempotency (mine once
per contact_id), and the actual won_patterns.jsonl write shape.

Run: .venv/bin/python -m pytest tests/test_won_mining.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import convo_state  # noqa: E402
import convo_context  # noqa: E402
import won_mining  # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(won_mining, "WON_PATTERNS", tmp_path / "won_patterns.jsonl")
    monkeypatch.setattr(won_mining, "STATE", tmp_path / "won_mining_state.json")
    monkeypatch.setattr(won_mining, "REPLIES", tmp_path / "replies.jsonl")
    monkeypatch.setattr(won_mining, "PROPOSALS", tmp_path / "proposals.jsonl")
    monkeypatch.setattr(won_mining, "ROOT", tmp_path)
    (tmp_path / "store").mkdir()
    monkeypatch.setattr(convo_state, "OUT", tmp_path / "convo_states.json")
    monkeypatch.setattr(convo_state, "REPLIES", tmp_path / "replies.jsonl")
    monkeypatch.setattr(convo_state, "PROPOSALS", tmp_path / "proposals.jsonl")
    monkeypatch.setattr(convo_state, "WARM_DISPO", tmp_path / "warm_dispo.jsonl")
    monkeypatch.setattr(convo_state, "LEDGER", tmp_path / "ledger.jsonl")
    return tmp_path


class TestMineContact:
    def test_basic_shape(self, isolated, monkeypatch):
        with (isolated / "replies.jsonl").open("w") as f:
            f.write(json.dumps({"id": "r1", "contact_id": "c1", "convo": "convo1",
                                "intent": "interested", "created": "2026-06-01T00:00:00+00:00"}) + "\n")
        with (isolated / "proposals.jsonl").open("w") as f:
            f.write(json.dumps({"id": "p1", "contact_id": "c1", "status": "sent",
                                "tier": "standard", "price": 1200,
                                "created": "2026-06-01T00:00:00+00:00"}) + "\n")
        monkeypatch.setattr(convo_context, "fetch_context", lambda *a, **k: [])
        state_rec = {"name": "Braydon", "why": "ledger entry references this contact",
                    "last_signal_days": 3.5}
        rec = won_mining.mine_contact("c1", state_rec)
        assert rec["contact_id"] == "c1"
        assert rec["name"] == "Braydon"
        assert rec["winning_tier"] == "standard"
        assert rec["winning_price"] == 1200
        assert rec["touches"] == 2
        assert rec["days_to_close"] == 3.5

    def test_no_winning_proposal_fields_are_none(self, isolated, monkeypatch):
        monkeypatch.setattr(convo_context, "fetch_context", lambda *a, **k: [])
        rec = won_mining.mine_contact("c1", {"name": "X", "why": "", "last_signal_days": None})
        assert rec["winning_tier"] is None
        assert rec["winning_price"] is None
        assert rec["touches"] == 0

    def test_objections_pulled_by_contact_id(self, isolated, monkeypatch):
        with (isolated / "store" / "objections.jsonl").open("w") as f:
            f.write(json.dumps({"objection": "too expensive", "contact_id": "c1"}) + "\n")
            f.write(json.dumps({"objection": "not my objection", "contact_id": "c2"}) + "\n")
        monkeypatch.setattr(convo_context, "fetch_context", lambda *a, **k: [])
        rec = won_mining.mine_contact("c1", {"name": "X", "why": "", "last_signal_days": None})
        assert rec["objections_raised"] == ["too expensive"]

    def test_context_tail_formatted_when_convo_present(self, isolated, monkeypatch):
        with (isolated / "replies.jsonl").open("w") as f:
            f.write(json.dumps({"id": "r1", "contact_id": "c1", "convo": "convo1",
                                "created": "2026-06-01T00:00:00+00:00"}) + "\n")
        monkeypatch.setattr(
            convo_context, "fetch_context",
            lambda *a, **k: [{"dir": "inbound", "body": "deposit is in", "ts": ""}])
        rec = won_mining.mine_contact("c1", {"name": "X", "why": "", "last_signal_days": None})
        assert "THEM: deposit is in" in rec["context_tail"]

    def test_no_convo_id_context_tail_empty(self, isolated, monkeypatch):
        called = {"n": 0}
        monkeypatch.setattr(convo_context, "fetch_context",
                            lambda *a, **k: called.__setitem__("n", called["n"] + 1) or [])
        rec = won_mining.mine_contact("c1", {"name": "X", "why": "", "last_signal_days": None})
        assert rec["context_tail"] == ""
        assert called["n"] == 0  # never fetches when there's no convo id to fetch


class TestRun:
    def test_empty_states_triggers_convo_state_run(self, isolated, monkeypatch):
        # no convo_states.json exists yet -- run() should call convo_state.run() to
        # produce fresh data rather than silently mining nothing
        called = {"n": 0}
        orig_run = convo_state.run

        def _tracking_run(*a, **k):
            called["n"] += 1
            return orig_run(*a, **k)
        monkeypatch.setattr(convo_state, "run", _tracking_run)
        result = won_mining.run(dry=True)
        assert called["n"] == 1
        assert result["won_count"] == 0

    def test_no_won_contacts_honest_empty_state(self, isolated, monkeypatch):
        with (isolated / "replies.jsonl").open("w") as f:
            f.write(json.dumps({"id": "r1", "contact_id": "c1", "name": "X",
                                "intent": "question",
                                "created": "2026-07-01T00:00:00+00:00"}) + "\n")
        result = won_mining.run(dry=True)
        assert result["won_count"] == 0
        assert not won_mining.WON_PATTERNS.exists()

    def test_won_contact_gets_mined_and_written(self, isolated, monkeypatch):
        with (isolated / "ledger.jsonl").open("w") as f:
            f.write(json.dumps({"ts": "2026-07-01T00:00:00+00:00", "kind": "deal",
                                "amount": 1200, "note": "c1 closed the deal"}) + "\n")
        with (isolated / "replies.jsonl").open("w") as f:
            f.write(json.dumps({"id": "r1", "contact_id": "c1", "name": "Braydon",
                                "intent": "interested",
                                "created": "2026-07-01T00:00:00+00:00"}) + "\n")
        monkeypatch.setattr(convo_context, "fetch_context", lambda *a, **k: [])
        result = won_mining.run(dry=False)
        assert result["won_count"] == 1
        assert result["newly_mined"] == 1
        assert won_mining.WON_PATTERNS.exists()
        rows = [json.loads(l) for l in won_mining.WON_PATTERNS.read_text().splitlines()]
        assert len(rows) == 1
        assert rows[0]["contact_id"] == "c1"

    def test_second_run_does_not_remine_same_contact(self, isolated, monkeypatch):
        with (isolated / "ledger.jsonl").open("w") as f:
            f.write(json.dumps({"ts": "2026-07-01T00:00:00+00:00", "kind": "deal",
                                "amount": 1200, "note": "c1 closed"}) + "\n")
        with (isolated / "replies.jsonl").open("w") as f:
            f.write(json.dumps({"id": "r1", "contact_id": "c1", "name": "Braydon",
                                "intent": "interested",
                                "created": "2026-07-01T00:00:00+00:00"}) + "\n")
        monkeypatch.setattr(convo_context, "fetch_context", lambda *a, **k: [])
        first = won_mining.run(dry=False)
        assert first["newly_mined"] == 1
        second = won_mining.run(dry=False)
        assert second["newly_mined"] == 0
        assert second["won_count"] == 1  # still won, just not re-mined
        rows = [json.loads(l) for l in won_mining.WON_PATTERNS.read_text().splitlines()]
        assert len(rows) == 1  # not duplicated

    def test_dry_run_writes_nothing(self, isolated, monkeypatch):
        with (isolated / "ledger.jsonl").open("w") as f:
            f.write(json.dumps({"ts": "2026-07-01T00:00:00+00:00", "kind": "deal",
                                "amount": 1200, "note": "c1 closed"}) + "\n")
        with (isolated / "replies.jsonl").open("w") as f:
            f.write(json.dumps({"id": "r1", "contact_id": "c1", "name": "Braydon",
                                "intent": "interested",
                                "created": "2026-07-01T00:00:00+00:00"}) + "\n")
        monkeypatch.setattr(convo_context, "fetch_context", lambda *a, **k: [])
        won_mining.run(dry=True)
        assert not won_mining.WON_PATTERNS.exists()
