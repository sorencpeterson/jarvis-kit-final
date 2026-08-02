#!/usr/bin/env python3
"""webhook_processor routing + idempotency (FABLE-MEGA-BACKLOG D8 missing-test #4).

Pins every documented route in agents/webhook_processor.py:
  bounce-family  -> bounce_events.jsonl shape campaign_guard.py already reads
  unsub-family   -> suppress.jsonl shape matching reply_watch._suppress() exactly
  reply-family   -> webhook_replies_seen.jsonl, SIGNAL ONLY (never replies.jsonl)
  anything else  -> ghl_events_unrouted.jsonl (nothing silently dropped)
plus idempotency by event id AND by content-hash when the id is missing.

Run: .venv/bin/python -m pytest tests/test_webhook_processor.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import webhook_processor as wp  # noqa: E402


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """Point every store path at throwaway files; planner.feed_add is recorded,
    never written, so no real store is touched."""
    monkeypatch.setattr(wp, "EVENTS", tmp_path / "ghl_events.jsonl")
    monkeypatch.setattr(wp, "STATE", tmp_path / "webhook_processor_state.json")
    monkeypatch.setattr(wp, "SUPPRESS", tmp_path / "suppress.jsonl")
    monkeypatch.setattr(wp, "BOUNCE_LOG", tmp_path / "bounce_events.jsonl")
    monkeypatch.setattr(wp, "REPLIES_SEEN", tmp_path / "webhook_replies_seen.jsonl")
    monkeypatch.setattr(wp, "UNROUTED", tmp_path / "ghl_events_unrouted.jsonl")
    feed = []
    monkeypatch.setattr(wp.planner, "feed_add", lambda *a, **k: feed.append(a))
    return SimpleNamespace(path=tmp_path, feed=feed)


def _write_events(sandbox, *events):
    lines = [e if isinstance(e, str) else json.dumps(e) for e in events]
    (sandbox.path / "ghl_events.jsonl").write_text("\n".join(lines) + "\n")


def _lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


class TestRouteEvent:
    def test_bounce_shape_matches_campaign_guard_reader(self):
        dest, rec = wp.route_event({"event": "bounce", "contactId": "c1", "email": "A@B.com",
                                    "ts": "2026-07-07T09:00:00", "messageId": "m1",
                                    "reason": "550 user unknown"})
        assert dest == "bounce"
        # exact key set campaign_guard's #162 heuristic already writes/reads
        assert set(rec) == {"ts", "convo", "sender", "body", "contact_id", "email", "src"}
        # campaign_guard._bounce_count_today() slices rec["ts"][:10] and its
        # watcher dedupes on rec["convo"] -- both must be populated
        assert rec["ts"][:10] == "2026-07-07" and rec["convo"] == "m1"
        assert rec["email"] == "a@b.com"  # normalized lowercase
        assert rec["src"] == "webhook"

    @pytest.mark.parametrize("kind", sorted(wp.BOUNCE_EVENTS))
    def test_every_bounce_alias_routes_to_bounce(self, kind):
        assert wp.route_event({"event": kind})[0] == "bounce"

    def test_bounce_body_capped_at_200(self):
        _, rec = wp.route_event({"event": "bounce", "reason": "x" * 999})
        assert len(rec["body"]) == 200

    @pytest.mark.parametrize("kind", sorted(wp.UNSUB_EVENTS))
    def test_every_unsub_alias_routes_to_suppress(self, kind):
        dest, rec = wp.route_event({"event": kind, "contactId": "c9", "email": "x@y.com"})
        assert dest == "suppress"
        assert kind in rec["why"]  # audit trail says WHICH event suppressed them

    def test_suppress_shape_is_readable_by_reply_watch(self, sandbox, monkeypatch):
        """The contract that makes webhook suppressions real: reply_watch's
        _is_suppressed() (same file cold_feeder reads) must recognize a record
        the webhook route wrote -- by contact_id AND by email."""
        import reply_watch  # heavy import kept local to this one test
        dest, rec = wp.route_event({"event": "sms_opt_out", "contactId": "c42",
                                    "email": "Stop@Now.com"})
        wp._append(wp.SUPPRESS, rec)
        monkeypatch.setattr(reply_watch, "SUPPRESS", wp.SUPPRESS)
        assert reply_watch._is_suppressed("c42", "") is True
        assert reply_watch._is_suppressed("", "stop@now.com") is True
        assert reply_watch._is_suppressed("other", "someone@else.com") is False
        # and the key set is exactly what reply_watch._suppress() itself writes
        reply_watch._suppress("cX", "a@b.com", "manual")
        native = _lines(wp.SUPPRESS)[-1]
        assert set(rec) == set(native)

    @pytest.mark.parametrize("kind", sorted(wp.REPLY_EVENTS))
    def test_every_reply_alias_is_signal_only(self, kind):
        dest, rec = wp.route_event({"event": kind, "contactId": "c1",
                                    "conversationId": "conv7", "email": "x@y.com"})
        assert dest == "reply_seen"  # NOT replies.jsonl -- reply_watch owns drafting
        assert rec["convo"] == "conv7" and rec["kind"] == kind

    def test_unknown_event_goes_to_unrouted_with_payload_preserved(self):
        evt = {"event": "contact_created", "contactId": "c1", "custom": "field"}
        dest, rec = wp.route_event(evt)
        assert dest == "unrouted"
        assert rec["custom"] == "field"  # nothing dropped: full payload kept for eyes

    def test_missing_event_field_is_unrouted_not_crash(self):
        assert wp.route_event({"contactId": "c1"})[0] == "unrouted"

    def test_kind_is_case_and_whitespace_insensitive(self):
        assert wp.route_event({"event": "  UNSUBSCRIBE "})[0] == "suppress"
        assert wp.route_event({"type": "Bounce"})[0] == "bounce"  # "type" alias too

    def test_missing_ts_gets_stamped(self):
        _, rec = wp.route_event({"event": "bounce"})
        assert rec["ts"]  # now_iso() fallback, never an empty ts

    def test_contact_id_snake_case_alias(self):
        _, rec = wp.route_event({"event": "reply", "contact_id": "c77"})
        assert rec["contact_id"] == "c77"


class TestEventKey:
    def test_id_wins_when_present(self):
        assert wp._event_key({"id": 123, "event": "bounce"}) == "123"

    def test_content_hash_is_stable_across_key_order(self):
        a = {"event": "bounce", "email": "x@y.com"}
        b = {"email": "x@y.com", "event": "bounce"}
        assert wp._event_key(a) == wp._event_key(b)  # sort_keys -> same hash

    def test_different_content_different_hash(self):
        assert wp._event_key({"event": "bounce", "email": "a@a.com"}) != \
            wp._event_key({"event": "bounce", "email": "b@b.com"})


class TestProcess:
    def test_routes_one_of_each_to_the_right_file(self, sandbox):
        _write_events(sandbox,
                      {"id": "e1", "event": "bounce", "email": "a@b.com"},
                      {"id": "e2", "event": "unsubscribe", "email": "a@b.com"},
                      {"id": "e3", "event": "reply", "email": "a@b.com"},
                      {"id": "e4", "event": "weird_new_thing"})
        counts = wp.process()
        assert counts == {"bounce": 1, "suppress": 1, "reply_seen": 1, "unrouted": 1,
                          "skipped_dup": 0}
        assert len(_lines(wp.BOUNCE_LOG)) == 1
        assert len(_lines(wp.SUPPRESS)) == 1
        assert len(_lines(wp.REPLIES_SEEN)) == 1
        assert len(_lines(wp.UNROUTED)) == 1

    def test_idempotent_by_event_id_within_batch_and_across_runs(self, sandbox):
        # GHL redelivers: same id lands twice in the file before the first run
        _write_events(sandbox,
                      {"id": "dup1", "event": "bounce", "email": "a@b.com"},
                      {"id": "dup1", "event": "bounce", "email": "a@b.com"})
        c1 = wp.process()
        assert c1["bounce"] == 1 and c1["skipped_dup"] == 1  # batch-level dedupe
        assert len(_lines(wp.BOUNCE_LOG)) == 1
        # second run: checkpoint remembers the key -> nothing re-routed
        c2 = wp.process()
        assert c2["bounce"] == 0 and c2["skipped_dup"] == 2
        assert len(_lines(wp.BOUNCE_LOG)) == 1  # STILL exactly one line

    def test_idempotent_by_content_hash_when_id_missing(self, sandbox):
        same = {"event": "unsubscribe", "email": "x@y.com", "ts": "2026-07-07T09:00:00"}
        other = {"event": "unsubscribe", "email": "z@w.com", "ts": "2026-07-07T09:00:00"}
        _write_events(sandbox, same, same, other)
        counts = wp.process()
        assert counts["suppress"] == 2 and counts["skipped_dup"] == 1
        assert len(_lines(wp.SUPPRESS)) == 2  # identical no-id event routed once

    def test_dry_run_writes_nothing_and_keeps_no_checkpoint(self, sandbox):
        _write_events(sandbox, {"id": "e1", "event": "bounce"})
        counts = wp.process(dry=True)
        assert counts["bounce"] == 1
        assert not wp.BOUNCE_LOG.exists() and not wp.STATE.exists()
        # a later REAL run still processes it (dry never burned the key)
        assert wp.process()["bounce"] == 1
        assert len(_lines(wp.BOUNCE_LOG)) == 1

    def test_replay_reprocesses_everything(self, sandbox):
        # --replay ignores the checkpoint BY DESIGN, which means it appends the
        # routed record a second time. Documented behavior, pinned here so a
        # future change to replay semantics is a conscious one.
        _write_events(sandbox, {"id": "e1", "event": "bounce"})
        wp.process()
        counts = wp.process(replay=True)
        assert counts["bounce"] == 1 and counts["skipped_dup"] == 0
        assert len(_lines(wp.BOUNCE_LOG)) == 2  # duplicate append, by design

    def test_corrupt_event_line_skipped_good_ones_survive(self, sandbox):
        _write_events(sandbox, "{corrupt not json", {"id": "ok", "event": "bounce"})
        assert wp.process()["bounce"] == 1

    def test_feed_notified_only_for_bounce_or_suppress(self, sandbox):
        _write_events(sandbox, {"id": "r1", "event": "reply"})
        wp.process()
        assert sandbox.feed == []  # replies are a signal, not a feed headline
        _write_events(sandbox, {"id": "r1", "event": "reply"},
                      {"id": "b1", "event": "bounce"})
        wp.process()
        assert len(sandbox.feed) == 1

    def test_missing_events_file_is_a_quiet_noop(self, sandbox):
        counts = wp.process()
        assert counts == {"bounce": 0, "suppress": 0, "reply_seen": 0, "unrouted": 0,
                          "skipped_dup": 0}


class TestStateFile:
    def test_processed_keys_capped_at_5000(self, sandbox):
        state = {"processed_keys": [f"k{i}" for i in range(6000)]}
        wp._save_state(state)
        kept = json.loads(wp.STATE.read_text())["processed_keys"]
        assert len(kept) == 5000
        assert kept[0] == "k1000" and kept[-1] == "k5999"  # the TAIL is kept

    def test_corrupt_state_file_resets_instead_of_crashing(self, sandbox):
        wp.STATE.write_text("{broken json")
        assert wp._load_state() == {"processed_keys": []}
