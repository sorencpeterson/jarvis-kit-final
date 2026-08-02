#!/usr/bin/env python3
"""Unit tests for agents/proposal_timers.py's C176/212/213 additions
(dormancy re-engage + review-ask + referral-ask lifecycle timers).

Original run()'s loop-close/resend behavior already has real coverage via the
mission's live end-to-end verification; this file focuses on the NEW
dormancy_and_lifecycle_drafts()/run_lifecycle() logic, which is independently
testable as a pure function over a states dict + a fired-log dict.

Run: .venv/bin/python -m pytest tests/test_proposal_timers.py -v
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
import convo_state  # noqa: E402
import proposal_factory  # noqa: E402
import planner  # noqa: E402
import proposal_timers  # noqa: E402


def _state(state, days, name="Braydon Lj"):
    return {"state": state, "name": name, "last_signal_days": days,
           "why": "", "last_signal_ts": None}


class TestDormancyAndLifecycleDraftsPure:
    def test_empty_states_no_drafts(self):
        assert proposal_timers.dormancy_and_lifecycle_drafts({}, {}) == []

    def test_negotiating_contact_no_dormancy_draft(self):
        states = {"c1": _state("negotiating", 5)}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, {})
        assert drafts == []

    def test_new_contact_no_dormancy_draft(self):
        states = {"c1": _state("new", 100)}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, {})
        assert drafts == []

    def test_dormant_under_30_days_no_draft(self):
        states = {"c1": _state("dormant", 25)}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, {})
        assert drafts == []

    def test_dormant_30_days_fires_dormant_30(self):
        states = {"c1": _state("dormant", 30)}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, {})
        assert len(drafts) == 1
        assert drafts[0]["kind"] == "dormant_30"
        assert drafts[0]["contact_id"] == "c1"

    def test_dormant_60_days_fires_dormant_60_not_30(self):
        # 60+ days means the HIGHEST qualifying rung fires, not the lowest
        states = {"c1": _state("dormant", 65)}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, {})
        assert len(drafts) == 1
        assert drafts[0]["kind"] == "dormant_60"

    def test_dormant_90_days_fires_dormant_90(self):
        states = {"c1": _state("dormant", 120)}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, {})
        assert drafts[0]["kind"] == "dormant_90"

    def test_already_fired_rung_never_refires(self):
        states = {"c1": _state("dormant", 35)}
        log = {"c1": {"fired": ["dormant_30"]}}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, log)
        assert drafts == []

    def test_higher_rung_fires_after_lower_already_fired(self):
        states = {"c1": _state("dormant", 65)}
        log = {"c1": {"fired": ["dormant_30"]}}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, log)
        assert len(drafts) == 1
        assert drafts[0]["kind"] == "dormant_60"

    def test_all_rungs_fired_no_more_drafts(self):
        states = {"c1": _state("dormant", 200)}
        log = {"c1": {"fired": ["dormant_30", "dormant_60", "dormant_90"]}}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, log)
        assert drafts == []

    def test_won_under_14_days_no_review_ask(self):
        states = {"c1": _state("won", 10)}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, {})
        assert not any(d["kind"] == "review_ask" for d in drafts)

    def test_won_14_days_fires_review_ask(self):
        states = {"c1": _state("won", 14)}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, {})
        kinds = [d["kind"] for d in drafts]
        assert "review_ask" in kinds

    def test_review_ask_already_fired_not_refired(self):
        states = {"c1": _state("won", 20)}
        log = {"c1": {"fired": ["review_ask"]}}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, log)
        assert not any(d["kind"] == "review_ask" for d in drafts)

    def test_won_under_30_days_no_referral_ask(self):
        states = {"c1": _state("won", 20)}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, {})
        assert not any(d["kind"] == "referral_ask" for d in drafts)

    def test_won_30_days_fires_both_review_and_referral(self):
        states = {"c1": _state("won", 30)}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, {})
        kinds = {d["kind"] for d in drafts}
        assert kinds == {"review_ask", "referral_ask"}

    def test_referral_ask_already_fired_not_refired(self):
        states = {"c1": _state("won", 35)}
        log = {"c1": {"fired": ["review_ask", "referral_ask"]}}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, log)
        assert drafts == []

    def test_multiple_contacts_independent(self):
        states = {"c1": _state("dormant", 30, name="Braydon"),
                  "c2": _state("won", 30, name="Sam")}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, {})
        by_contact = {d["contact_id"]: d["kind"] for d in drafts}
        assert by_contact["c1"] == "dormant_30"
        assert "c2" in [d["contact_id"] for d in drafts]  # sam gets review/referral

    def test_dormant_none_days_no_crash(self):
        states = {"c1": {"state": "dormant", "name": "X", "last_signal_days": None}}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, {})
        assert drafts == []

    def test_review_ask_draft_flags_placeholder_link(self):
        states = {"c1": _state("won", 14)}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, {})
        review = next(d for d in drafts if d["kind"] == "review_ask")
        assert "placeholder" in review["draft"].lower()

    def test_referral_ask_draft_has_no_placeholder_flag(self):
        states = {"c1": _state("won", 30)}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, {})
        referral = next(d for d in drafts if d["kind"] == "referral_ask")
        assert "placeholder" not in referral["draft"].lower()

    def test_no_name_defaults_gracefully(self):
        states = {"c1": {"state": "dormant", "name": "", "last_signal_days": 30}}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, {})
        assert len(drafts) == 1
        assert "there" in drafts[0]["draft"].lower() or "your business" in drafts[0]["draft"].lower()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(proposal_timers, "LIFECYCLE_LOG", tmp_path / "lifecycle_timers_log.json")
    monkeypatch.setattr(reply_watch, "REPLIES", tmp_path / "replies.jsonl")
    monkeypatch.setattr(reply_watch, "SUPPRESS", tmp_path / "suppress.jsonl")
    monkeypatch.setattr(convo_state, "OUT", tmp_path / "convo_states.json")
    return tmp_path


class TestRunLifecycleIntegration:
    def test_no_states_file_no_drafts(self, isolated):
        n = proposal_timers.run_lifecycle()
        assert n == 0
        assert not proposal_timers.LIFECYCLE_LOG.exists()

    def test_dormant_contact_stages_pending_draft(self, isolated):
        convo_state.OUT.write_text(json.dumps({"states": {
            "c1": {"state": "dormant", "name": "Braydon", "last_signal_days": 35}}}))
        n = proposal_timers.run_lifecycle()
        assert n == 1
        recs = reply_watch._load()
        assert len(recs) == 1
        assert recs[0]["status"] == "pending"
        assert recs[0]["src"] == "lifecycle_timer"
        assert recs[0]["contact_id"] == "c1"

    def test_second_run_does_not_redraft_same_rung(self, isolated):
        convo_state.OUT.write_text(json.dumps({"states": {
            "c1": {"state": "dormant", "name": "Braydon", "last_signal_days": 35}}}))
        first = proposal_timers.run_lifecycle()
        second = proposal_timers.run_lifecycle()
        assert first == 1
        assert second == 0
        assert len(reply_watch._load()) == 1

    def test_lifecycle_log_persisted_across_runs(self, isolated):
        convo_state.OUT.write_text(json.dumps({"states": {
            "c1": {"state": "dormant", "name": "Braydon", "last_signal_days": 35}}}))
        proposal_timers.run_lifecycle()
        assert proposal_timers.LIFECYCLE_LOG.exists()
        log = json.loads(proposal_timers.LIFECYCLE_LOG.read_text())
        assert "dormant_30" in log["c1"]["fired"]

    def test_won_contact_stages_two_drafts_at_30_days(self, isolated):
        convo_state.OUT.write_text(json.dumps({"states": {
            "c1": {"state": "won", "name": "Braydon", "last_signal_days": 30}}}))
        n = proposal_timers.run_lifecycle()
        assert n == 2
        recs = reply_watch._load()
        kinds = {r["their_msg"] for r in recs}
        assert any("review_ask" in k for k in kinds)
        assert any("referral_ask" in k for k in kinds)


# ---- C216: A/B openers on dormancy re-engage drafts ----
class TestAbVariantSelection:
    def test_deterministic_same_contact_always_same_variant(self):
        first = proposal_timers._ab_variant_for("c1")
        for _ in range(10):
            assert proposal_timers._ab_variant_for("c1") == first

    def test_returns_a_or_b_only(self):
        for cid in ("c1", "c2", "c3", "abc", "xyz", ""):
            assert proposal_timers._ab_variant_for(cid) in ("A", "B")

    def test_different_contacts_can_get_different_variants(self):
        # not a strict requirement every single pair differs, but across a
        # reasonable population both letters should appear (proves it's not
        # hardcoded to always return one value)
        variants = {proposal_timers._ab_variant_for(f"contact_{i}") for i in range(50)}
        assert variants == {"A", "B"}


class TestDormancyDraftCarriesAbVariant:
    def test_dormant_draft_has_ab_variant_field(self):
        states = {"c1": _state("dormant", 30)}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, {})
        assert drafts[0]["ab_variant"] in ("A", "B")

    def test_review_ask_has_no_ab_variant(self):
        states = {"c1": _state("won", 14)}
        drafts = proposal_timers.dormancy_and_lifecycle_drafts(states, {})
        review = next(d for d in drafts if d["kind"] == "review_ask")
        assert "ab_variant" not in review

    def test_variant_a_and_b_produce_different_text(self):
        text_a = proposal_timers.DORMANT_REENGAGE[30]["A"].format(first="X", biz="Y")
        text_b = proposal_timers.DORMANT_REENGAGE[30]["B"].format(first="X", biz="Y")
        assert text_a != text_b

    def test_all_rungs_have_both_variants(self):
        for rung in proposal_timers.DORMANCY_RUNGS:
            assert "A" in proposal_timers.DORMANT_REENGAGE[rung]
            assert "B" in proposal_timers.DORMANT_REENGAGE[rung]


class TestRunLifecycleCarriesAbVariantToRecord(object):
    def test_saved_record_has_ab_variant_for_dormancy(self, isolated):
        convo_state.OUT.write_text(json.dumps({"states": {
            "c1": {"state": "dormant", "name": "Braydon", "last_signal_days": 35}}}))
        proposal_timers.run_lifecycle()
        rec = reply_watch._load()[0]
        assert rec.get("ab_variant") in ("A", "B")

    def test_saved_record_has_no_ab_variant_for_review_ask(self, isolated):
        convo_state.OUT.write_text(json.dumps({"states": {
            "c1": {"state": "won", "name": "Braydon", "last_signal_days": 14}}}))
        proposal_timers.run_lifecycle()
        rec = reply_watch._load()[0]
        assert "ab_variant" not in rec


# ---- C205 opens name-drop + C211 payment-link nudge (both in the ORIGINAL run(),
# additively) ----
def _sent_proposal(**kw):
    base = {"id": "prop1", "status": "sent", "contact_id": "c1", "name": "Braydon",
           "company": "Legacy Plumbing", "tier": "standard", "link": "https://x/prop/1",
           "sent_at": "2020-01-01T00:00:00+00:00", "opens": 0}  # very old sent_at so age
    # thresholds are always satisfied in these tests regardless of when they run
    base.update(kw)
    return base


class TestPayLinkFor:
    def test_no_config_returns_empty(self, isolated, monkeypatch):
        monkeypatch.setattr(planner, "_config", lambda: {})
        assert proposal_timers._pay_link_for("standard") == ""

    def test_configured_tier_returns_link(self, isolated, monkeypatch):
        monkeypatch.setattr(planner, "_config",
                            lambda: {"payment_links": {"standard": "https://pay.example/standard"}})
        assert proposal_timers._pay_link_for("standard") == "https://pay.example/standard"

    def test_unconfigured_tier_returns_empty(self, isolated, monkeypatch):
        monkeypatch.setattr(planner, "_config",
                            lambda: {"payment_links": {"standard": "https://pay.example/standard"}})
        assert proposal_timers._pay_link_for("webfix") == ""


class TestRunOpensNameDropAndPaymentNudge:
    @pytest.fixture
    def isolated_run(self, isolated, monkeypatch):
        monkeypatch.setattr(convo_state, "OUT", isolated / "convo_states.json")
        return isolated

    def test_single_open_uses_original_loop_close_text(self, isolated_run, monkeypatch):
        monkeypatch.setattr(proposal_factory, "load_queue",
                            lambda: [_sent_proposal(opens=1)])
        saved = {}
        monkeypatch.setattr(proposal_factory, "save", lambda rec: saved.update(rec))
        monkeypatch.setattr(planner, "_config", lambda: {})
        proposal_timers.run()
        recs = reply_watch._load()
        assert len(recs) == 1
        assert "opened it" not in recs[0]["draft"]  # original text, no name-drop
        assert "No pitch, just closing the loop" in recs[0]["draft"]

    def test_multiple_opens_names_the_count(self, isolated_run, monkeypatch):
        monkeypatch.setattr(proposal_factory, "load_queue",
                            lambda: [_sent_proposal(opens=3)])
        monkeypatch.setattr(proposal_factory, "save", lambda rec: None)
        monkeypatch.setattr(planner, "_config", lambda: {})
        proposal_timers.run()
        recs = reply_watch._load()
        assert len(recs) == 1
        assert "3 times" in recs[0]["draft"]

    def test_won_contact_gets_payment_nudge_not_loop_close(self, isolated_run, monkeypatch):
        (isolated_run / "convo_states.json").write_text(json.dumps({"states": {
            "c1": {"state": "won", "name": "Braydon", "last_signal_days": 5}}}))
        monkeypatch.setattr(proposal_factory, "load_queue",
                            lambda: [_sent_proposal(opens=2)])
        monkeypatch.setattr(proposal_factory, "save", lambda rec: None)
        monkeypatch.setattr(
            planner, "_config",
            lambda: {"payment_links": {"standard": "https://pay.example/standard"}})
        proposal_timers.run()
        recs = reply_watch._load()
        assert len(recs) == 1
        assert "https://pay.example/standard" in recs[0]["draft"]
        assert "closing the loop" not in recs[0]["draft"]

    def test_won_contact_without_configured_pay_link_gets_honest_fallback_not_loop_close(self, isolated_run, monkeypatch):
        (isolated_run / "convo_states.json").write_text(json.dumps({"states": {
            "c1": {"state": "won", "name": "Braydon", "last_signal_days": 5}}}))
        monkeypatch.setattr(proposal_factory, "load_queue",
                            lambda: [_sent_proposal(opens=1)])
        monkeypatch.setattr(proposal_factory, "save", lambda rec: None)
        monkeypatch.setattr(planner, "_config", lambda: {})  # no payment_links configured
        proposal_timers.run()
        recs = reply_watch._load()
        assert len(recs) == 1
        # no pay link available -> the honest WON_NO_PAYLINK_FALLBACK, never the
        # original ambiguous "closing the loop, is it a no?" text (that would read
        # strangely to someone who already said yes) and never silently dropped
        assert "moving forward" in recs[0]["draft"]
        assert "closing the loop" not in recs[0]["draft"]

    def test_won_contact_permanently_exempt_from_loop_close_even_after_nudge_fires(self, isolated_run, monkeypatch):
        # regression guard for the exact bug this design went through: a won
        # contact must NEVER fall through to loop_drafted on a later run, whether
        # or not the one-time won nudge already fired
        prop = _sent_proposal(opens=1)
        (isolated_run / "convo_states.json").write_text(json.dumps({"states": {
            "c1": {"state": "won", "name": "Braydon", "last_signal_days": 5}}}))
        store = {"prop": prop}
        monkeypatch.setattr(proposal_factory, "load_queue", lambda: [store["prop"]])
        monkeypatch.setattr(proposal_factory, "save", lambda rec: store.__setitem__("prop", rec))
        monkeypatch.setattr(planner, "_config", lambda: {})  # no pay link -> fallback fires
        proposal_timers.run()  # first run: won_nudge_drafted fires (fallback variant)
        proposal_timers.run()  # second run: must NOT fall through to loop_drafted
        recs = reply_watch._load()
        assert len(recs) == 1  # still only the one nudge draft, nothing else ever staged
        assert "closing the loop" not in recs[0]["draft"]

    def test_won_nudge_fires_once(self, isolated_run, monkeypatch):
        prop = _sent_proposal(opens=1)
        (isolated_run / "convo_states.json").write_text(json.dumps({"states": {
            "c1": {"state": "won", "name": "Braydon", "last_signal_days": 5}}}))
        store = {"prop": prop}
        monkeypatch.setattr(proposal_factory, "load_queue", lambda: [store["prop"]])
        monkeypatch.setattr(proposal_factory, "save", lambda rec: store.__setitem__("prop", rec))
        monkeypatch.setattr(
            planner, "_config",
            lambda: {"payment_links": {"standard": "https://pay.example/standard"}})
        first = proposal_timers.run()
        second = proposal_timers.run()
        assert first == 1
        assert second == 0  # won_nudge_drafted flag now set, never refires

    def test_zero_opens_no_name_drop_text(self, isolated_run, monkeypatch):
        # 0 opens goes down the RESEND path entirely, never LOOP_CLOSE at all
        monkeypatch.setattr(proposal_factory, "load_queue",
                            lambda: [_sent_proposal(opens=0)])
        monkeypatch.setattr(proposal_factory, "save", lambda rec: None)
        monkeypatch.setattr(planner, "_config", lambda: {})
        proposal_timers.run()
        recs = reply_watch._load()
        assert len(recs) == 1
        assert "Resending this" in recs[0]["draft"]


# ---- C187 suppress-first audit: proposal_timers.py's TWO draft paths (run() and
# run_lifecycle()) both need the check, since a contact can be suppressed AFTER
# their proposal was sent or after convo_state.py already logged them as dormant/won ----
class TestSuppressFirstOnRun:
    def test_suppressed_contact_gets_no_loop_close_draft(self, isolated, monkeypatch):
        with (isolated / "suppress.jsonl").open("w") as f:
            f.write(json.dumps({"contact_id": "c1", "email": ""}) + "\n")
        monkeypatch.setattr(proposal_factory, "load_queue",
                            lambda: [_sent_proposal(opens=2)])
        save_called = {"n": 0}
        monkeypatch.setattr(proposal_factory, "save",
                            lambda rec: save_called.__setitem__("n", save_called["n"] + 1))
        monkeypatch.setattr(planner, "_config", lambda: {})
        fired = proposal_timers.run()
        assert fired == 0
        assert reply_watch._load() == []
        assert save_called["n"] == 0  # never even touched proposal_factory.save

    def test_suppressed_by_email_also_blocked(self, isolated, monkeypatch):
        with (isolated / "suppress.jsonl").open("w") as f:
            f.write(json.dumps({"contact_id": "", "email": "blocked@x.com"}) + "\n")
        monkeypatch.setattr(proposal_factory, "load_queue",
                            lambda: [_sent_proposal(opens=2, email="blocked@x.com")])
        monkeypatch.setattr(proposal_factory, "save", lambda rec: None)
        monkeypatch.setattr(planner, "_config", lambda: {})
        fired = proposal_timers.run()
        assert fired == 0

    def test_non_suppressed_contact_unaffected(self, isolated, monkeypatch):
        with (isolated / "suppress.jsonl").open("w") as f:
            f.write(json.dumps({"contact_id": "someone_else", "email": ""}) + "\n")
        monkeypatch.setattr(proposal_factory, "load_queue",
                            lambda: [_sent_proposal(opens=2)])
        monkeypatch.setattr(proposal_factory, "save", lambda rec: None)
        monkeypatch.setattr(planner, "_config", lambda: {})
        fired = proposal_timers.run()
        assert fired == 1  # c1 is not in the suppress list, unaffected


class TestSuppressFirstOnRunLifecycle:
    def test_suppressed_dormant_contact_gets_no_reengage_draft(self, isolated, monkeypatch):
        with (isolated / "suppress.jsonl").open("w") as f:
            f.write(json.dumps({"contact_id": "c1", "email": ""}) + "\n")
        (isolated / "convo_states.json").write_text(json.dumps({"states": {
            "c1": {"state": "dormant", "name": "Braydon", "last_signal_days": 35}}}))
        fired = proposal_timers.run_lifecycle()
        assert fired == 0
        assert reply_watch._load() == []

    def test_non_suppressed_dormant_contact_unaffected(self, isolated, monkeypatch):
        with (isolated / "suppress.jsonl").open("w") as f:
            f.write(json.dumps({"contact_id": "someone_else", "email": ""}) + "\n")
        (isolated / "convo_states.json").write_text(json.dumps({"states": {
            "c1": {"state": "dormant", "name": "Braydon", "last_signal_days": 35}}}))
        fired = proposal_timers.run_lifecycle()
        assert fired == 1
