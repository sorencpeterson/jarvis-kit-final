#!/usr/bin/env python3
"""Unit tests for agents/convo_state.py (C161 conversation-state machine).

classify() is a pure function over already-loaded rows, so every case here is a
fixture, no file I/O, no LLM calls. run()/build_all() get one integration-style
smoke test against isolated tmp_path stores.

Run: .venv/bin/python -m pytest tests/test_convo_state.py -v
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import convo_state  # noqa: E402

NOW = datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc)


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def _reply(intent="interested", days_ago=1.0, **kw):
    base = {"contact_id": "c1", "intent": intent, "created": _iso(days_ago),
            "their_msg": "", "draft": ""}
    base.update(kw)
    return base


def _proposal(status="staged", days_ago=1.0, **kw):
    base = {"contact_id": "c1", "status": status, "created": _iso(days_ago)}
    base.update(kw)
    return base


class TestNewState:
    def test_no_rows_is_new(self):
        r = convo_state.classify("c1", now=NOW)
        assert r["state"] == "new"
        assert r["last_signal_ts"] is None
        assert "no signal" in r["why"]

    def test_skipped_proposal_only_is_new_but_reason_is_honest(self):
        r = convo_state.classify("c1", proposals=[_proposal(status="skipped")], now=NOW)
        assert r["state"] == "new"
        assert r["last_signal_ts"] is not None  # regression guard: had a real bug where
        # this stayed None-ish "no signal" text despite a timestamped proposal existing.
        assert "skipped" in r["why"]


class TestEngagedState:
    def test_real_reply_no_proposal_is_engaged(self):
        r = convo_state.classify("c1", replies=[_reply(intent="question")], now=NOW)
        assert r["state"] == "engaged"

    def test_not_now_intent_is_engaged_not_negotiating(self):
        r = convo_state.classify("c1", replies=[_reply(intent="not_now")], now=NOW)
        assert r["state"] == "engaged"

    def test_remove_intent_alone_does_not_count_as_engaged(self):
        # reply_watch already suppresses+drops "remove" intent before it ever reaches
        # replies.jsonl, but classify() should be defensive about it anyway.
        r = convo_state.classify("c1", replies=[_reply(intent="remove")], now=NOW)
        assert r["state"] == "new"


class TestNegotiatingState:
    def test_staged_proposal_is_negotiating(self):
        r = convo_state.classify("c1", proposals=[_proposal(status="staged")], now=NOW)
        assert r["state"] == "negotiating"

    def test_sent_proposal_is_negotiating(self):
        r = convo_state.classify("c1", proposals=[_proposal(status="sent")], now=NOW)
        assert r["state"] == "negotiating"

    def test_interested_reply_plus_staged_proposal_is_negotiating(self):
        r = convo_state.classify(
            "c1", replies=[_reply(intent="interested")],
            proposals=[_proposal(status="staged")], now=NOW)
        assert r["state"] == "negotiating"

    def test_objection_intent_reply_after_proposal_stays_negotiating(self):
        r = convo_state.classify(
            "c1", replies=[_reply(intent="objection", days_ago=0.5)],
            proposals=[_proposal(status="sent", days_ago=2)], now=NOW)
        assert r["state"] == "negotiating"


class TestWonState:
    def test_ledger_entry_by_contact_id_is_won(self):
        r = convo_state.classify(
            "c1", ledger=[{"ts": _iso(1), "kind": "deal", "amount": 1200, "note": "c1 closed"}],
            now=NOW)
        assert r["state"] == "won"

    def test_ledger_entry_by_name_fallback_is_won(self):
        r = convo_state.classify(
            "c1", name="Acme Plumbing",
            ledger=[{"ts": _iso(1), "kind": "deal", "amount": 800, "note": "Acme Plumbing paid deposit"}],
            now=NOW)
        assert r["state"] == "won"

    def test_zero_amount_ledger_entry_does_not_win(self):
        r = convo_state.classify(
            "c1", ledger=[{"ts": _iso(1), "kind": "booked_call", "amount": 0, "note": "c1"}],
            now=NOW)
        assert r["state"] != "won"

    def test_proposal_marked_won_is_won(self):
        r = convo_state.classify("c1", proposals=[_proposal(status="won")], now=NOW)
        assert r["state"] == "won"

    def test_won_language_reply_after_sent_proposal_is_won(self):
        r = convo_state.classify(
            "c1",
            proposals=[_proposal(status="sent", days_ago=3)],
            replies=[_reply(intent="interested", days_ago=1, their_msg="deposit is in, let's do it")],
            now=NOW)
        assert r["state"] == "won"

    def test_won_language_without_any_proposal_does_not_win(self):
        # "let's do it" with no proposal in flight is weak signal (could be about
        # anything); requiring a sent proposal first avoids false positives.
        r = convo_state.classify(
            "c1", replies=[_reply(intent="interested", their_msg="let's do it")], now=NOW)
        assert r["state"] != "won"

    def test_won_beats_dormancy(self):
        r = convo_state.classify(
            "c1", ledger=[{"ts": _iso(40), "kind": "deal", "amount": 1200, "note": "c1"}], now=NOW)
        assert r["state"] == "won"


class TestDormantState:
    def test_stale_negotiating_becomes_dormant(self):
        r = convo_state.classify(
            "c1", proposals=[_proposal(status="sent", days_ago=30)], now=NOW)
        assert r["state"] == "dormant"
        assert r["last_signal_days"] > convo_state.DORMANT_DAYS

    def test_stale_engaged_becomes_dormant(self):
        r = convo_state.classify(
            "c1", replies=[_reply(intent="question", days_ago=25)], now=NOW)
        assert r["state"] == "dormant"

    def test_recent_negotiating_not_dormant(self):
        r = convo_state.classify(
            "c1", proposals=[_proposal(status="sent", days_ago=5)], now=NOW)
        assert r["state"] == "negotiating"

    def test_stale_new_contact_stays_new_not_dormant(self):
        # dormancy only applies to contacts that WERE engaged/negotiating; a contact
        # with only a skipped proposal from 40 days ago never became active, so it's
        # still "new", not "dormant" (dormant implies "went quiet", not "never started").
        r = convo_state.classify(
            "c1", proposals=[_proposal(status="skipped", days_ago=40)], now=NOW)
        assert r["state"] == "new"

    def test_exactly_at_dormancy_floor_is_not_dormant(self):
        r = convo_state.classify(
            "c1", replies=[_reply(intent="question", days_ago=convo_state.DORMANT_DAYS)], now=NOW)
        assert r["state"] == "engaged"  # strictly greater-than, boundary stays engaged


class TestThreadHealth:
    """C220 conversation NPS heuristic (thread_health()). Pure function, every case
    is a fixture."""

    def test_won_scores_highest_base(self):
        r = convo_state.thread_health("won", [], None)
        assert r["score"] == 90
        assert r["label"] == "healthy"

    def test_dormant_scores_low(self):
        r = convo_state.thread_health("dormant", [], None)
        assert r["score"] == 20
        assert r["label"] == "at_risk"

    def test_new_and_engaged_share_default_base(self):
        assert convo_state.thread_health("new", [], None)["score"] == 50
        assert convo_state.thread_health("engaged", [], None)["score"] == 50

    def test_single_interested_reply_gets_meaningful_positive_adjustment(self):
        # regression guard for a real bug: weight ordering was originally backwards,
        # so a SINGLE 'interested' reply got the SMALLEST possible weight (0.2)
        # instead of the full weight (0.5) a lone signal should carry.
        r = convo_state.thread_health(
            "negotiating", [{"intent": "interested", "created": "2026-01-01"}], 2)
        # base 65 + (25 * 0.5) = 77.5 -> 78; must be meaningfully above the bare base
        assert r["score"] == 78
        assert r["score"] > 65 + 5  # sanity: not just a token adjustment

    def test_single_not_now_reply_scores_below_bare_base(self):
        r = convo_state.thread_health(
            "negotiating", [{"intent": "not_now", "created": "2026-01-01"}], 2)
        assert r["score"] < 65

    def test_most_recent_intent_weighted_highest(self):
        # objection (old) -> interested (newest) should score noticeably higher than
        # interested (old) -> objection (newest), because the MOST RECENT intent
        # carries the most weight in both cases
        improving = convo_state.thread_health("negotiating", [
            {"intent": "objection", "created": "2026-01-01"},
            {"intent": "interested", "created": "2026-01-02"},
        ], 1)
        worsening = convo_state.thread_health("negotiating", [
            {"intent": "interested", "created": "2026-01-01"},
            {"intent": "objection", "created": "2026-01-02"},
        ], 1)
        assert improving["score"] > worsening["score"]

    def test_three_replies_all_positive_trends_healthy(self):
        r = convo_state.thread_health("negotiating", [
            {"intent": "objection", "created": "2026-01-01"},
            {"intent": "question", "created": "2026-01-02"},
            {"intent": "interested", "created": "2026-01-03"},
        ], 1)
        assert r["label"] == "healthy"

    def test_more_than_three_replies_only_last_three_considered(self):
        # a 4th, very old, very negative reply shouldn't drag down a thread that's
        # since turned around
        many = [{"intent": "wrong_person", "created": "2020-01-01"}] + [
            {"intent": "interested", "created": f"2026-01-0{i}"} for i in range(1, 4)]
        r = convo_state.thread_health("negotiating", many, 1)
        assert r["score"] > 65  # the ancient wrong_person entry is excluded, not averaged in

    def test_staleness_penalty_kicks_in_before_hard_dormancy_floor(self):
        fresh = convo_state.thread_health("negotiating", [], 5)
        aging = convo_state.thread_health("negotiating", [], 15)
        assert aging["score"] < fresh["score"]

    def test_staleness_penalty_capped(self):
        very_stale = convo_state.thread_health("negotiating", [], 200)
        somewhat_stale = convo_state.thread_health("negotiating", [], 30)
        # the penalty caps at 20 points, so beyond a point staleness alone doesn't
        # keep dragging the score further down
        assert very_stale["score"] == somewhat_stale["score"]

    def test_won_state_exempt_from_staleness_penalty(self):
        # a won deal doesn't get penalized for the CONTACT going quiet afterward --
        # winning it is what matters, not whether they text back after
        fresh_won = convo_state.thread_health("won", [], 2)
        stale_won = convo_state.thread_health("won", [], 200)
        assert fresh_won["score"] == stale_won["score"] == 90

    def test_score_never_exceeds_100(self):
        r = convo_state.thread_health("won", [
            {"intent": "interested", "created": "2026-01-01"},
            {"intent": "interested", "created": "2026-01-02"},
            {"intent": "interested", "created": "2026-01-03"},
        ], 0)
        assert r["score"] <= 100

    def test_score_never_negative(self):
        r = convo_state.thread_health("dormant", [
            {"intent": "wrong_person", "created": "2026-01-01"},
            {"intent": "wrong_person", "created": "2026-01-02"},
            {"intent": "wrong_person", "created": "2026-01-03"},
        ], 500)
        assert r["score"] >= 0

    def test_labels_match_score_bands(self):
        assert convo_state.thread_health("won", [], None)["label"] == "healthy"  # 90
        assert convo_state.thread_health("dormant", [], None)["label"] == "at_risk"  # 20
        mid = convo_state.thread_health("engaged", [], None)  # 50
        assert mid["label"] == "watch"

    def test_classify_output_includes_health_fields(self):
        r = convo_state.classify(
            "c1", replies=[_reply(intent="interested", days_ago=1)], now=NOW)
        assert "health_score" in r
        assert "health_label" in r
        assert isinstance(r["health_score"], int)
        assert r["health_label"] in ("healthy", "watch", "at_risk")


class TestBuildAllIntegration:
    @pytest.fixture
    def isolated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(convo_state, "REPLIES", tmp_path / "replies.jsonl")
        monkeypatch.setattr(convo_state, "PROPOSALS", tmp_path / "proposals.jsonl")
        monkeypatch.setattr(convo_state, "WARM_DISPO", tmp_path / "warm_dispo.jsonl")
        monkeypatch.setattr(convo_state, "LEDGER", tmp_path / "ledger.jsonl")
        monkeypatch.setattr(convo_state, "OUT", tmp_path / "convo_states.json")
        return tmp_path

    def test_empty_stores_produce_empty_result(self, isolated):
        result = convo_state.build_all()
        assert result == {}

    def test_two_contacts_classified_independently(self, isolated):
        with (isolated / "replies.jsonl").open("w") as f:
            f.write(json.dumps({"id": "rw_1", "contact_id": "c1", "name": "Alice", "intent": "interested",
                                "created": datetime.now(timezone.utc).isoformat()}) + "\n")
            f.write(json.dumps({"id": "rw_2", "contact_id": "c2", "name": "Bob", "intent": "not_now",
                                "created": datetime.now(timezone.utc).isoformat()}) + "\n")
        result = convo_state.build_all()
        assert set(result) == {"c1", "c2"}
        assert result["c1"]["state"] == "engaged"
        assert result["c2"]["state"] == "engaged"

    def test_run_writes_store_file(self, isolated):
        with (isolated / "proposals.jsonl").open("w") as f:
            f.write(json.dumps({"id": "prop_1", "contact_id": "c1", "status": "staged",
                                "created": datetime.now(timezone.utc).isoformat()}) + "\n")
        payload = convo_state.run(dry=False)
        assert convo_state.OUT.exists()
        on_disk = json.loads(convo_state.OUT.read_text())
        assert on_disk["count"] == payload["count"] == 1
        assert on_disk["states"]["c1"]["state"] == "negotiating"

    def test_dry_run_writes_nothing(self, isolated):
        with (isolated / "proposals.jsonl").open("w") as f:
            f.write(json.dumps({"contact_id": "c1", "status": "staged",
                                "created": datetime.now(timezone.utc).isoformat()}) + "\n")
        convo_state.run(dry=True)
        assert not convo_state.OUT.exists()

    def test_state_for_unknown_contact_defaults_new(self, isolated):
        assert convo_state.state_for("nonexistent") == "new"
        assert convo_state.state_for("") == "new"

    def test_state_for_reads_last_run(self, isolated):
        with (isolated / "replies.jsonl").open("w") as f:
            f.write(json.dumps({"id": "rw_1", "contact_id": "c1", "name": "Alice", "intent": "question",
                                "created": datetime.now(timezone.utc).isoformat()}) + "\n")
        convo_state.run(dry=False)
        assert convo_state.state_for("c1") == "engaged"
