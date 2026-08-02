#!/usr/bin/env python3
"""Unit tests for agents/reply_watch.py's C161-220 additions.

Covers: SLA aging (_age_hours/_escalation_for/refresh_sla_fields), the suppress-first
audit (C187 -- proven both as a direct _is_suppressed() unit test AND as a full run()
integration test with a mocked GHL/LLM stack showing a suppressed candidate never
reaches any drafting step), webhook-priority signal reading, and spam/original-behavior
regression guards (every pre-existing filter/marker still works byte-identically).

Does NOT re-test convo_lint/convo_context/convo_dedupe/convo_state's own internals --
those have their own dedicated test files. This file tests reply_watch.py's OWN new
code and its wiring of those modules together.

Run: .venv/bin/python -m pytest tests/test_reply_watch.py -v
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

import planner  # noqa: E402
import ghl_social  # noqa: E402
import proposal_factory  # noqa: E402
import convo_state  # noqa: E402
import convo_context  # noqa: E402
import convo_meeting  # noqa: E402
import reply_watch  # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(reply_watch, "REPLIES", tmp_path / "replies.jsonl")
    monkeypatch.setattr(reply_watch, "SUPPRESS", tmp_path / "suppress.jsonl")
    monkeypatch.setattr(reply_watch, "WEBHOOK_SEEN", tmp_path / "webhook_replies_seen.jsonl")
    return tmp_path


# ---- C163 SLA aging ----
class TestAgeHours:
    def test_five_hours_ago(self):
        ts = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        age = reply_watch._age_hours(ts)
        assert 4.9 < age < 5.1

    def test_zero_for_now(self):
        ts = datetime.now(timezone.utc).isoformat()
        age = reply_watch._age_hours(ts)
        assert 0 <= age < 0.01

    def test_naive_timestamp_handled(self):
        # a timestamp with no tzinfo shouldn't raise
        age = reply_watch._age_hours("2026-01-01T00:00:00")
        assert age > 0

    def test_invalid_timestamp_returns_zero(self):
        assert reply_watch._age_hours("not a timestamp") == 0.0

    def test_empty_string_returns_zero(self):
        assert reply_watch._age_hours("") == 0.0

    def test_never_negative(self):
        # a future timestamp (clock skew edge case) should clamp to 0, not go negative
        ts = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        assert reply_watch._age_hours(ts) == 0.0


class TestEscalationFor:
    def test_fresh_no_escalation(self):
        assert reply_watch._escalation_for(0.5) == ""

    def test_just_under_first_threshold(self):
        assert reply_watch._escalation_for(3.9) == ""

    def test_at_first_threshold_is_watch(self):
        assert reply_watch._escalation_for(4.0) == "watch"

    def test_between_thresholds_is_watch(self):
        assert reply_watch._escalation_for(12.0) == "watch"

    def test_at_second_threshold_is_urgent(self):
        assert reply_watch._escalation_for(24.0) == "urgent"

    def test_well_past_second_threshold_is_urgent(self):
        assert reply_watch._escalation_for(72.0) == "urgent"


class TestRefreshSlaFields:
    def test_no_pending_records_no_updates(self, isolated):
        assert reply_watch.refresh_sla_fields() == 0

    def test_non_pending_records_never_touched(self, isolated):
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
        reply_watch._save({"id": "r1", "status": "sent", "created": old_ts})
        n = reply_watch.refresh_sla_fields()
        assert n == 0
        rec = {r["id"]: r for r in reply_watch._load()}["r1"]
        assert "age_hours" not in rec

    def test_pending_record_gets_aged(self, isolated):
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
        reply_watch._save({"id": "r1", "status": "pending", "created": old_ts})
        n = reply_watch.refresh_sla_fields()
        assert n == 1
        rec = {r["id"]: r for r in reply_watch._load()}["r1"]
        assert 9.9 < rec["age_hours"] < 10.1
        assert rec["escalation"] == "watch"

    def test_unchanged_age_not_rewritten_twice_in_same_second(self, isolated):
        # calling refresh twice in immediate succession on a record whose rounded
        # age_hours hasn't changed should not produce a second update
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
        reply_watch._save({"id": "r1", "status": "pending", "created": old_ts})
        reply_watch.refresh_sla_fields()
        n2 = reply_watch.refresh_sla_fields()
        assert n2 == 0

    def test_escalation_urgent_written_correctly(self, isolated):
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        reply_watch._save({"id": "r1", "status": "pending", "created": old_ts})
        reply_watch.refresh_sla_fields()
        rec = {r["id"]: r for r in reply_watch._load()}["r1"]
        assert rec["escalation"] == "urgent"


# ---- C181 webhook-first signal ----
class TestWebhookPriorityContactIds:
    def test_no_file_returns_empty_set(self, isolated):
        assert reply_watch._webhook_priority_contact_ids() == set()

    def test_reads_contact_ids(self, isolated):
        with reply_watch.WEBHOOK_SEEN.open("w") as f:
            f.write(json.dumps({"ts": "x", "contact_id": "c1", "email": "", "kind": "reply"}) + "\n")
            f.write(json.dumps({"ts": "x", "contact_id": "c2", "email": "", "kind": "reply"}) + "\n")
        assert reply_watch._webhook_priority_contact_ids() == {"c1", "c2"}

    def test_rows_without_contact_id_skipped(self, isolated):
        with reply_watch.WEBHOOK_SEEN.open("w") as f:
            f.write(json.dumps({"ts": "x", "contact_id": "", "email": "a@b.com"}) + "\n")
        assert reply_watch._webhook_priority_contact_ids() == set()

    def test_malformed_lines_skipped_not_raised(self, isolated):
        with reply_watch.WEBHOOK_SEEN.open("w") as f:
            f.write("not json\n")
            f.write(json.dumps({"contact_id": "c1"}) + "\n")
        assert reply_watch._webhook_priority_contact_ids() == {"c1"}


# ---- C187 suppress-first audit ----
class TestIsSuppressed:
    def test_no_file_not_suppressed(self, isolated):
        assert not reply_watch._is_suppressed("c1", "a@b.com")

    def test_matches_by_contact_id(self, isolated):
        with reply_watch.SUPPRESS.open("w") as f:
            f.write(json.dumps({"contact_id": "c1", "email": ""}) + "\n")
        assert reply_watch._is_suppressed("c1", "")

    def test_matches_by_email(self, isolated):
        with reply_watch.SUPPRESS.open("w") as f:
            f.write(json.dumps({"contact_id": "", "email": "a@b.com"}) + "\n")
        assert reply_watch._is_suppressed("", "a@b.com")

    def test_email_match_case_insensitive(self, isolated):
        with reply_watch.SUPPRESS.open("w") as f:
            f.write(json.dumps({"contact_id": "", "email": "A@B.COM"}) + "\n")
        assert reply_watch._is_suppressed("", "a@b.com")

    def test_no_match_not_suppressed(self, isolated):
        with reply_watch.SUPPRESS.open("w") as f:
            f.write(json.dumps({"contact_id": "other", "email": "other@x.com"}) + "\n")
        assert not reply_watch._is_suppressed("c1", "a@b.com")


class TestSuppressFirstIntegration:
    """The audit requirement: a suppressed candidate must never reach ANY drafting
    step (no LLM output used for it, no proposal build, no context fetch, no lint
    gate, nothing saved for it at all) -- run() is exercised end to end with every
    external call mocked, proving the suppress check happens before all of that."""

    def test_suppressed_contact_produces_no_record_and_no_side_effects(self, isolated, monkeypatch):
        monkeypatch.setattr(reply_watch, "SUPPRESS", isolated / "suppress.jsonl")
        with reply_watch.SUPPRESS.open("w") as f:
            f.write(json.dumps({"contact_id": "suppressed_c1", "email": ""}) + "\n")

        monkeypatch.setattr(reply_watch, "_loc", lambda: "loc1")
        monkeypatch.setattr(
            ghl_social, "_api",
            lambda *a, **k: json.dumps({"conversations": [
                {"id": "convo1", "contactId": "suppressed_c1", "contactName": "Suppressed Guy",
                 "lastMessageDirection": "inbound", "lastMessageBody": "how much is it",
                 "lastMessageType": "TYPE_SMS", "email": ""},
            ]}))

        proposal_build_called = {"n": 0}
        monkeypatch.setattr(proposal_factory, "build",
                            lambda **k: proposal_build_called.__setitem__("n", proposal_build_called["n"] + 1) or {})
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {})

        context_fetch_called = {"n": 0}
        monkeypatch.setattr(
            convo_context, "fetch_context",
            lambda *a, **k: context_fetch_called.__setitem__("n", context_fetch_called["n"] + 1) or [])

        llm_called = {"n": 0}

        def _fake_cli(*a, **k):
            llm_called["n"] += 1
            return json.dumps([{"real": True, "intent": "interested", "reply": "sure, 1200 flat"}])
        monkeypatch.setattr(planner, "_cli", _fake_cli)
        monkeypatch.setattr(planner, "notify", lambda *a, **k: True)
        monkeypatch.setattr(planner, "_config", lambda: {})

        added = reply_watch.run()

        assert added == []
        assert reply_watch._load() == []  # nothing saved for the suppressed candidate
        assert proposal_build_called["n"] == 0  # never built a proposal for them
        assert context_fetch_called["n"] == 0  # never spent a context-window fetch on them
        # the LLM classify call itself still happens (it's a batch call over ALL
        # candidates before the per-candidate suppress filter, matching the ORIGINAL
        # reply_watch.py's own batching architecture) -- the audit guarantee is that
        # the suppressed candidate's OWN classification result is never ACTED on, which
        # the assertions above (no save, no proposal, no context fetch) directly prove.

    def test_non_suppressed_contact_in_same_batch_still_processed(self, isolated, monkeypatch):
        """Companion to the test above: proves suppression is per-candidate, not an
        all-or-nothing batch kill -- a real candidate alongside a suppressed one in
        the SAME poll still gets drafted normally."""
        monkeypatch.setattr(reply_watch, "SUPPRESS", isolated / "suppress.jsonl")
        with reply_watch.SUPPRESS.open("w") as f:
            f.write(json.dumps({"contact_id": "suppressed_c1", "email": ""}) + "\n")

        monkeypatch.setattr(reply_watch, "_loc", lambda: "loc1")
        monkeypatch.setattr(
            ghl_social, "_api",
            lambda *a, **k: json.dumps({"conversations": [
                {"id": "convo1", "contactId": "suppressed_c1", "contactName": "Suppressed Guy",
                 "lastMessageDirection": "inbound", "lastMessageBody": "how much is it",
                 "lastMessageType": "TYPE_SMS", "email": ""},
                {"id": "convo2", "contactId": "real_c2", "contactName": "Real Guy",
                 "lastMessageDirection": "inbound", "lastMessageBody": "sounds interesting",
                 "lastMessageType": "TYPE_SMS", "email": ""},
            ]}))
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {})
        monkeypatch.setattr(proposal_factory, "build", lambda **k: {"link": ""})
        monkeypatch.setattr(convo_context, "fetch_context", lambda *a, **k: [])
        monkeypatch.setattr(
            planner, "_cli",
            lambda *a, **k: json.dumps([
                {"real": True, "intent": "interested", "reply": "sure, want to grab a time?"},
                {"real": True, "intent": "question", "reply": "happy to answer that"},
            ]))
        monkeypatch.setattr(planner, "notify", lambda *a, **k: True)
        monkeypatch.setattr(planner, "_config", lambda: {})

        added = reply_watch.run()

        ids_by_contact = {r["contact_id"] for r in added}
        assert "suppressed_c1" not in ids_by_contact
        assert "real_c2" in ids_by_contact


# ---- Original-behavior regression guards (nothing weakened) ----
class TestOriginalBehaviorPreserved:
    def test_spam_markers_no_longer_include_optout_phrases(self):
        """B: opt-out phrases used to sit inside SPAM_MARKERS, which meant a real
        'unsubscribe'/'opt out'/'reply stop' reply got dropped as spam before
        _suppress() ever saw it. They've moved to OPT_OUT_MARKERS (checked and
        suppressed BEFORE the spam filter runs -- see TestOptOutHandling below);
        SPAM_MARKERS keeps only the promo/automated markers."""
        original = ("text stop", "to stop", "offers.",
                   "% off", "buy 3", "free!", "limited time", "flash sale", "deal ends",
                   "click here", "act now", "sale ends", "www.", "http")
        assert reply_watch.SPAM_MARKERS == original
        assert reply_watch.OPT_OUT_MARKERS == ("unsubscribe", "opt out", "reply stop",
                                               "take me off", "remove me")

    def test_looks_spam_still_works(self):
        assert reply_watch._looks_spam("Huge flash sale, deal ends tonight, click here")
        assert reply_watch._looks_spam("50% off this week only!")
        assert not reply_watch._looks_spam("how much does the standard site cost")

    def test_looks_optout_detects_optout_phrases(self):
        assert reply_watch._looks_optout("please unsubscribe me")
        assert reply_watch._looks_optout("opt out please")
        assert reply_watch._looks_optout("reply stop")
        assert reply_watch._looks_optout("take me off your list")
        assert reply_watch._looks_optout("remove me from this")
        assert not reply_watch._looks_optout("how much does the standard site cost")

    def test_looks_optout_ignores_negated_mentions(self):
        """R2#10: a plain substring match suppressed NEGATED mentions -- "I don't
        want to unsubscribe" contains "unsubscribe" as a literal substring and
        used to read as a real opt-out, silently suppressing a still-interested
        prospect. A negated / "please don't" / "no need to" mention must NOT
        suppress; a real, non-negated opt-out still must."""
        assert not reply_watch._looks_optout(
            "I don't want to unsubscribe, just curious about pricing")
        assert not reply_watch._looks_optout("please don't remove me from your list")
        assert not reply_watch._looks_optout("no need to opt out, I'm still interested")
        assert not reply_watch._looks_optout("don't take me off, I like these updates")
        assert not reply_watch._looks_optout("not asking you to unsubscribe me, just a question")
        # still catches the real thing
        assert reply_watch._looks_optout("please unsubscribe me")

    def test_looks_optout_word_boundary_rejects_unrelated_compound(self):
        # R2#10: word-boundary matching -- "opt out" must not fire inside an
        # unrelated compound like "adopt out" (a rescue-pet / hand-me-down context)
        assert not reply_watch._looks_optout("we're looking to adopt out our old couch")

    def test_looks_optout_still_catches_common_inflections(self):
        # word-boundary is anchored on the LEADING edge only, so an ordinary
        # inflection glued onto the back of "unsubscribe" still counts -- a
        # strict \bphrase\b would otherwise silently miss "unsubscribed"
        assert reply_watch._looks_optout(
            "I already unsubscribed last week, why am I still getting texts")

    def test_niche_for_agency(self):
        assert reply_watch._niche_for({"tags": ["wl-webdev-partner"]}) == "agency"

    def test_niche_for_webfix(self):
        assert reply_watch._niche_for({"tags": ["webfix-lead"]}) == "webfix"

    def test_niche_for_default_local_service(self):
        assert reply_watch._niche_for({"tags": []}) == "local service"

    def test_playbook_digest_returns_string(self):
        # real file read against the actual business-library playbook
        digest = reply_watch._playbook_digest()
        assert isinstance(digest, str)

    def test_suppress_writes_expected_shape(self, isolated):
        reply_watch._suppress("c1", "a@b.com", "asked to be removed")
        rows = [json.loads(l) for l in reply_watch.SUPPRESS.read_text().splitlines()]
        assert rows[0]["contact_id"] == "c1"
        assert rows[0]["email"] == "a@b.com"
        assert rows[0]["why"] == "asked to be removed"
        assert "ts" in rows[0]

    def test_load_save_roundtrip_last_write_wins(self, isolated):
        reply_watch._save({"id": "r1", "status": "pending", "draft": "first"})
        reply_watch._save({"id": "r1", "status": "pending", "draft": "edited"})
        rows = reply_watch._load()
        assert len(rows) == 1
        assert rows[0]["draft"] == "edited"

    def test_classify_prompt_still_has_original_hard_rules(self):
        # every original instruction line must still be present verbatim (additions
        # are fine, but nothing from the original CLASSIFY prompt was removed)
        assert "no em-dashes or en-dashes" in reply_watch.CLASSIFY
        assert "toward a quick call at [OWNER_SITE]/book" in reply_watch.CLASSIFY
        assert "adapt that exact counter, do not invent one" in reply_watch.CLASSIFY
        assert "intent is remove, reply must be" in reply_watch.CLASSIFY

    def test_classify_prompt_has_new_c168_c169_c178_c202_c204_rules(self):
        assert "ANSWER THEIR ACTUAL QUESTION FIRST" in reply_watch.CLASSIFY
        assert "FORMALITY" in reply_watch.CLASSIFY
        assert "LENGTH" in reply_watch.CLASSIFY
        assert "ONE question per reply" in reply_watch.CLASSIFY
        assert "AT MOST one link" in reply_watch.CLASSIFY


# ---- B/CX1/CX2: opt-out replies must suppress, never get silently eaten ----
class TestOptOutHandling:
    def test_optout_message_suppressed_not_dropped_as_spam(self, isolated, monkeypatch):
        """B: a genuine opt-out ('unsubscribe'/'opt out'/'reply stop'/'take me off'/
        'remove me') must reach _suppress(), not be silently dropped by the
        promo-spam filter (which used to contain these exact phrases) -- and it
        must never even reach the classify call."""
        monkeypatch.setattr(reply_watch, "_loc", lambda: "loc1")
        monkeypatch.setattr(
            ghl_social, "_api",
            lambda *a, **k: json.dumps({"conversations": [
                {"id": "convo1", "contactId": "c1", "contactName": "Wants Out",
                 "lastMessageDirection": "inbound", "lastMessageBody": "please unsubscribe me",
                 "lastMessageType": "TYPE_SMS", "email": "out@x.com"},
            ]}))
        llm_called = {"n": 0}
        monkeypatch.setattr(planner, "_cli",
                            lambda *a, **k: llm_called.__setitem__("n", llm_called["n"] + 1) or "[]")
        monkeypatch.setattr(planner, "_config", lambda: {})
        monkeypatch.setattr(planner, "notify", lambda *a, **k: True)

        added = reply_watch.run()
        assert added == []
        assert reply_watch._is_suppressed("c1", "out@x.com")
        assert llm_called["n"] == 0  # handled before the classify call even fires

    def test_optout_suppresses_even_on_already_sent_convo(self, isolated, monkeypatch):
        """CX1: seen_final (an already-'sent' record for this convo) must not block
        a LATER opt-out on the same convo from suppressing."""
        reply_watch._save({"id": "old1", "convo": "convo1", "status": "sent",
                          "contact_id": "c1", "created": "2026-01-01T00:00:00+00:00"})
        monkeypatch.setattr(reply_watch, "_loc", lambda: "loc1")
        monkeypatch.setattr(
            ghl_social, "_api",
            lambda *a, **k: json.dumps({"conversations": [
                {"id": "convo1", "contactId": "c1", "contactName": "Wants Out",
                 "lastMessageDirection": "inbound", "lastMessageBody": "take me off your list",
                 "lastMessageType": "TYPE_SMS", "email": ""},
            ]}))
        monkeypatch.setattr(planner, "_cli", lambda *a, **k: "[]")
        monkeypatch.setattr(planner, "_config", lambda: {})
        monkeypatch.setattr(planner, "notify", lambda *a, **k: True)

        reply_watch.run()
        assert reply_watch._is_suppressed("c1", "")

    def test_classifier_real_false_remove_intent_still_suppresses(self, isolated, monkeypatch):
        """CX2: the CLASSIFY prompt's own instructions list opt-outs under
        real=false, so a polite opt-out phrased outside the 5 literal
        OPT_OUT_MARKERS (so it reaches the classifier, not the B/CX1 pre-filter)
        can legitimately come back real=false + intent=remove. That combination
        must still suppress, not be discarded by the real-gate first."""
        monkeypatch.setattr(reply_watch, "_loc", lambda: "loc1")
        monkeypatch.setattr(
            ghl_social, "_api",
            lambda *a, **k: json.dumps({"conversations": [
                {"id": "convo1", "contactId": "c1", "contactName": "Polite No",
                 "lastMessageDirection": "inbound",
                 "lastMessageBody": "please don't contact me anymore",
                 "lastMessageType": "TYPE_SMS", "email": "c1@x.com"},
            ]}))
        monkeypatch.setattr(
            planner, "_cli",
            lambda *a, **k: json.dumps([{"real": False, "intent": "remove", "reply": ""}]))
        monkeypatch.setattr(planner, "_config", lambda: {})
        monkeypatch.setattr(planner, "notify", lambda *a, **k: True)

        added = reply_watch.run()
        assert added == []
        assert reply_watch._is_suppressed("c1", "c1@x.com")


# ---- C173 hot-lead fast path ----
class TestHotLeadFastPath:
    def test_interested_plus_booking_language_triggers_hot_notify(self, isolated, monkeypatch):
        monkeypatch.setattr(reply_watch, "_loc", lambda: "loc1")
        monkeypatch.setattr(
            ghl_social, "_api",
            lambda *a, **k: json.dumps({"conversations": [
                {"id": "convo1", "contactId": "c1", "contactName": "Hot Lead",
                 "lastMessageDirection": "inbound",
                 "lastMessageBody": "yes lets book a call this week",
                 "lastMessageType": "TYPE_SMS", "email": ""},
            ]}))
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {})
        monkeypatch.setattr(proposal_factory, "build", lambda **k: {"link": ""})
        monkeypatch.setattr(convo_context, "fetch_context", lambda *a, **k: [])
        monkeypatch.setattr(
            planner, "_cli",
            lambda *a, **k: json.dumps([
                {"real": True, "intent": "interested", "reply": "great, when works?"}]))
        monkeypatch.setattr(planner, "_config", lambda: {})
        notify_calls = []
        monkeypatch.setattr(planner, "notify",
                            lambda title, body, **k: notify_calls.append(title) or True)

        reply_watch.run()
        assert any("HOT LEAD" in t for t in notify_calls)

    def test_interested_without_booking_language_no_hot_notify(self, isolated, monkeypatch):
        monkeypatch.setattr(reply_watch, "_loc", lambda: "loc1")
        monkeypatch.setattr(
            ghl_social, "_api",
            lambda *a, **k: json.dumps({"conversations": [
                {"id": "convo1", "contactId": "c1", "contactName": "Interested Guy",
                 "lastMessageDirection": "inbound",
                 "lastMessageBody": "this sounds interesting, tell me more",
                 "lastMessageType": "TYPE_SMS", "email": ""},
            ]}))
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {})
        monkeypatch.setattr(proposal_factory, "build", lambda **k: {"link": ""})
        monkeypatch.setattr(convo_context, "fetch_context", lambda *a, **k: [])
        monkeypatch.setattr(
            planner, "_cli",
            lambda *a, **k: json.dumps([
                {"real": True, "intent": "interested", "reply": "happy to explain more"}]))
        monkeypatch.setattr(planner, "_config", lambda: {})
        notify_calls = []
        monkeypatch.setattr(planner, "notify",
                            lambda title, body, **k: notify_calls.append(title) or True)

        reply_watch.run()
        assert not any("HOT LEAD" in t for t in notify_calls)


# ---- C165 re-classification ----
class TestReclassification:
    def test_pending_convo_still_reconsidered(self, isolated, monkeypatch):
        # a convo with an existing PENDING record should still show up as a candidate
        # (re-classified fresh) rather than being permanently excluded
        reply_watch._save({"id": "old1", "convo": "convo1", "status": "pending",
                          "contact_id": "c1", "created": "2026-01-01T00:00:00+00:00"})
        monkeypatch.setattr(reply_watch, "_loc", lambda: "loc1")
        monkeypatch.setattr(
            ghl_social, "_api",
            lambda *a, **k: json.dumps({"conversations": [
                {"id": "convo1", "contactId": "c1", "contactName": "Braydon",
                 "lastMessageDirection": "inbound", "lastMessageBody": "actually not now",
                 "lastMessageType": "TYPE_SMS", "email": ""},
            ]}))
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {})
        monkeypatch.setattr(proposal_factory, "build", lambda **k: {"link": ""})
        monkeypatch.setattr(convo_context, "fetch_context", lambda *a, **k: [])
        monkeypatch.setattr(
            planner, "_cli",
            lambda *a, **k: json.dumps([{"real": True, "intent": "not_now", "reply": "no worries"}]))
        monkeypatch.setattr(planner, "_config", lambda: {})
        monkeypatch.setattr(planner, "notify", lambda *a, **k: True)

        added = reply_watch.run()
        assert len(added) == 1
        assert added[0]["intent"] == "not_now"  # flipped from whatever it was before

    def test_sent_convo_excluded_from_reclassification(self, isolated, monkeypatch):
        # a convo whose existing record is ALREADY "sent" must NOT be reconsidered
        # (Alex already acted on it) -- this is the original exclusion behavior,
        # unweakened
        reply_watch._save({"id": "old1", "convo": "convo1", "status": "sent",
                          "contact_id": "c1", "created": "2026-01-01T00:00:00+00:00"})
        monkeypatch.setattr(reply_watch, "_loc", lambda: "loc1")
        monkeypatch.setattr(
            ghl_social, "_api",
            lambda *a, **k: json.dumps({"conversations": [
                {"id": "convo1", "contactId": "c1", "contactName": "Braydon",
                 "lastMessageDirection": "inbound", "lastMessageBody": "thanks",
                 "lastMessageType": "TYPE_SMS", "email": ""},
            ]}))
        llm_called = {"n": 0}
        monkeypatch.setattr(planner, "_cli",
                            lambda *a, **k: llm_called.__setitem__("n", llm_called["n"] + 1) or "[]")
        monkeypatch.setattr(planner, "_config", lambda: {})

        added = reply_watch.run()
        assert added == []
        assert llm_called["n"] == 0  # never even reached the classify call -- filtered pre-batch


# ---- C218 wrong-person graceful close ----
class TestWrongPersonHandling:
    def test_wrong_person_gets_draft_and_is_suppressed(self, isolated, monkeypatch):
        monkeypatch.setattr(reply_watch, "_loc", lambda: "loc1")
        monkeypatch.setattr(
            ghl_social, "_api",
            lambda *a, **k: json.dumps({"conversations": [
                {"id": "convo1", "contactId": "c1", "contactName": "Not Braydon",
                 "lastMessageDirection": "inbound",
                 "lastMessageBody": "wrong number, I never talked to anyone about a website",
                 "lastMessageType": "TYPE_SMS", "email": ""},
            ]}))
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {})
        monkeypatch.setattr(convo_context, "fetch_context", lambda *a, **k: [])
        monkeypatch.setattr(
            planner, "_cli",
            lambda *a, **k: json.dumps([
                {"real": True, "intent": "wrong_person",
                 "reply": "My mistake, sorry for the bother. Have a good one."}]))
        monkeypatch.setattr(planner, "_config", lambda: {})
        monkeypatch.setattr(planner, "notify", lambda *a, **k: True)

        added = reply_watch.run()

        assert len(added) == 1
        assert added[0]["intent"] == "wrong_person"
        assert added[0]["draft"] == "My mistake, sorry for the bother. Have a good one."
        # AND suppressed, so nothing auto-drafts at this contact again
        assert reply_watch._is_suppressed("c1", "")

    def test_wrong_person_never_triggers_proposal_build(self, isolated, monkeypatch):
        monkeypatch.setattr(reply_watch, "_loc", lambda: "loc1")
        monkeypatch.setattr(
            ghl_social, "_api",
            lambda *a, **k: json.dumps({"conversations": [
                {"id": "convo1", "contactId": "c1", "contactName": "Not Braydon",
                 "lastMessageDirection": "inbound", "lastMessageBody": "wrong number",
                 "lastMessageType": "TYPE_SMS", "email": ""},
            ]}))
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {})
        build_called = {"n": 0}
        monkeypatch.setattr(proposal_factory, "build",
                            lambda **k: build_called.__setitem__("n", build_called["n"] + 1) or {})
        monkeypatch.setattr(convo_context, "fetch_context", lambda *a, **k: [])
        monkeypatch.setattr(
            planner, "_cli",
            lambda *a, **k: json.dumps([
                {"real": True, "intent": "wrong_person", "reply": "My mistake, sorry."}]))
        monkeypatch.setattr(planner, "_config", lambda: {})
        monkeypatch.setattr(planner, "notify", lambda *a, **k: True)

        reply_watch.run()
        assert build_called["n"] == 0

    def test_classify_prompt_includes_wrong_person_instruction(self):
        assert "wrong_person" in reply_watch.CLASSIFY
        assert "gracious close" in reply_watch.CLASSIFY


# ---- C206 meeting-proposal drafts with 2 concrete calendar-aware slots ----
class TestMeetingSlotsWiring:
    def test_booking_language_appends_slots(self, isolated, monkeypatch):
        monkeypatch.setattr(reply_watch, "_loc", lambda: "loc1")
        monkeypatch.setattr(
            ghl_social, "_api",
            lambda *a, **k: json.dumps({"conversations": [
                {"id": "convo1", "contactId": "c1", "contactName": "Braydon",
                 "lastMessageDirection": "inbound",
                 "lastMessageBody": "yes lets book a call this week",
                 "lastMessageType": "TYPE_SMS", "email": ""},
            ]}))
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {})
        monkeypatch.setattr(proposal_factory, "build", lambda **k: {"link": ""})
        monkeypatch.setattr(convo_context, "fetch_context", lambda *a, **k: [])
        monkeypatch.setattr(convo_meeting, "slots_line",
                            lambda n=2: "I've got Monday at 9am or Tuesday at 2pm open, whichever works better.")
        monkeypatch.setattr(
            planner, "_cli",
            lambda *a, **k: json.dumps([
                {"real": True, "intent": "interested", "reply": "great, when works?"}]))
        monkeypatch.setattr(planner, "_config", lambda: {})
        monkeypatch.setattr(planner, "notify", lambda *a, **k: True)

        added = reply_watch.run()
        assert len(added) == 1
        assert "Monday at 9am" in added[0]["draft"]

    def test_no_booking_language_no_slots_appended(self, isolated, monkeypatch):
        monkeypatch.setattr(reply_watch, "_loc", lambda: "loc1")
        monkeypatch.setattr(
            ghl_social, "_api",
            lambda *a, **k: json.dumps({"conversations": [
                {"id": "convo1", "contactId": "c1", "contactName": "Braydon",
                 "lastMessageDirection": "inbound", "lastMessageBody": "sounds interesting",
                 "lastMessageType": "TYPE_SMS", "email": ""},
            ]}))
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {})
        monkeypatch.setattr(proposal_factory, "build", lambda **k: {"link": ""})
        monkeypatch.setattr(convo_context, "fetch_context", lambda *a, **k: [])
        called = {"n": 0}
        monkeypatch.setattr(convo_meeting, "slots_line",
                            lambda n=2: called.__setitem__("n", called["n"] + 1) or "slots text")
        monkeypatch.setattr(
            planner, "_cli",
            lambda *a, **k: json.dumps([
                {"real": True, "intent": "interested", "reply": "happy to explain more"}]))
        monkeypatch.setattr(planner, "_config", lambda: {})
        monkeypatch.setattr(planner, "notify", lambda *a, **k: True)

        reply_watch.run()
        assert called["n"] == 0  # never even called slots_line

    def test_draft_already_naming_a_day_skips_slots_append(self, isolated, monkeypatch):
        monkeypatch.setattr(reply_watch, "_loc", lambda: "loc1")
        monkeypatch.setattr(
            ghl_social, "_api",
            lambda *a, **k: json.dumps({"conversations": [
                {"id": "convo1", "contactId": "c1", "contactName": "Braydon",
                 "lastMessageDirection": "inbound", "lastMessageBody": "lets book a call",
                 "lastMessageType": "TYPE_SMS", "email": ""},
            ]}))
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {})
        monkeypatch.setattr(proposal_factory, "build", lambda **k: {"link": ""})
        monkeypatch.setattr(convo_context, "fetch_context", lambda *a, **k: [])
        called = {"n": 0}
        monkeypatch.setattr(convo_meeting, "slots_line",
                            lambda n=2: called.__setitem__("n", called["n"] + 1) or "slots text")
        monkeypatch.setattr(
            planner, "_cli",
            lambda *a, **k: json.dumps([
                {"real": True, "intent": "interested",
                 "reply": "great, does Thursday work for you?"}]))
        monkeypatch.setattr(planner, "_config", lambda: {})
        monkeypatch.setattr(planner, "notify", lambda *a, **k: True)

        added = reply_watch.run()
        assert called["n"] == 0  # draft already names a day, no slots appended
        assert added[0]["draft"] == "great, does Thursday work for you?"

    def test_calendar_failure_degrades_gracefully(self, isolated, monkeypatch):
        monkeypatch.setattr(reply_watch, "_loc", lambda: "loc1")
        monkeypatch.setattr(
            ghl_social, "_api",
            lambda *a, **k: json.dumps({"conversations": [
                {"id": "convo1", "contactId": "c1", "contactName": "Braydon",
                 "lastMessageDirection": "inbound", "lastMessageBody": "lets book a call",
                 "lastMessageType": "TYPE_SMS", "email": ""},
            ]}))
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {})
        monkeypatch.setattr(proposal_factory, "build", lambda **k: {"link": ""})
        monkeypatch.setattr(convo_context, "fetch_context", lambda *a, **k: [])

        def _boom(n=2):
            raise RuntimeError("calendar unreachable")
        monkeypatch.setattr(convo_meeting, "slots_line", _boom)
        monkeypatch.setattr(
            planner, "_cli",
            lambda *a, **k: json.dumps([
                {"real": True, "intent": "interested", "reply": "great, when works?"}]))
        monkeypatch.setattr(planner, "_config", lambda: {})
        monkeypatch.setattr(planner, "notify", lambda *a, **k: True)

        added = reply_watch.run()
        assert len(added) == 1  # doesn't crash the whole run
        assert added[0]["draft"] == "great, when works?"  # no slots appended, degrades cleanly
