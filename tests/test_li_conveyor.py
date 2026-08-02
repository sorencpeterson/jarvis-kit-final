#!/usr/bin/env python3
"""Unit tests for agents/li_conveyor.py (A3 accepted-connection follow-up
conveyor, A35 follow-up ladder, A36 dead-thread closer, A61 accepted-but-
silent re-engage). All tests isolate networking.QUEUE + li_conveyor's own
store paths to tmp_path fixtures, and mock planner._cli_json so no real LLM
calls happen in the suite. Mirrors the manual end-to-end verification already
run (dry-run path, real queueing path, idempotency, quality-gate rejection)
against disposable scratch dirs — these are the committed regression tests
for that same behavior.

Run: .venv/bin/python -m pytest tests/test_li_conveyor.py -v
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import networking  # noqa: E402
import planner  # noqa: E402
import li_conveyor  # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Point networking.QUEUE and li_conveyor's own stores at tmp_path, never
    the real store/ directory."""
    monkeypatch.setattr(networking, "QUEUE", tmp_path / "network.jsonl")
    monkeypatch.setattr(li_conveyor, "ACCEPTED", tmp_path / "li_accepted.jsonl")
    monkeypatch.setattr(li_conveyor, "CONVEYOR_STATE", tmp_path / "li_conveyor_state.jsonl")
    return tmp_path


def _append_accepted(path: Path, rec: dict):
    with path.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def _accepted_fixture(days_ago: int, **kw) -> dict:
    ts = (date.today() - timedelta(days=days_ago)).isoformat() + "T08:00:00-07:00"
    base = {"url": "https://linkedin.com/in/fixture-person", "name": "FIXTURE Person",
            "accepted_at": ts, "connect_item_id": "fixture_connect_id",
            "headline": "FIXTURE headline", "context": "FIXTURE context"}
    base.update(kw)
    return base


class TestLoadAccepted:
    def test_no_file_returns_empty(self, isolated):
        assert li_conveyor.load_accepted() == []

    def test_single_row_loaded(self, isolated):
        _append_accepted(li_conveyor.ACCEPTED, _accepted_fixture(3))
        rows = li_conveyor.load_accepted()
        assert len(rows) == 1
        assert rows[0]["name"] == "FIXTURE Person"

    def test_url_variants_dedupe_last_write_wins(self, isolated):
        _append_accepted(li_conveyor.ACCEPTED,
                          _accepted_fixture(5, url="https://www.linkedin.com/in/x/", name="Old Name"))
        _append_accepted(li_conveyor.ACCEPTED,
                          _accepted_fixture(3, url="https://linkedin.com/in/x?ref=1", name="New Name"))
        rows = li_conveyor.load_accepted()
        assert len(rows) == 1
        assert rows[0]["name"] == "New Name"


class TestRunConveyorEmptyState:
    def test_no_accepted_rows_reports_e_gap_note(self, isolated):
        result = li_conveyor.run_conveyor(dry=False)
        assert result["accepted_count"] == 0
        assert "note" in result
        assert not networking.QUEUE.exists() or networking.QUEUE.read_text() == ""


class TestDryRun:
    def test_identifies_day2_due_writes_nothing(self, isolated, monkeypatch):
        _append_accepted(li_conveyor.ACCEPTED, _accepted_fixture(3))
        called = {"n": 0}
        monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {"draft": "x"})
        result = li_conveyor.run_conveyor(dry=True)
        assert result["accepted_count"] == 1
        assert len(result["would_queue"]) == 1
        assert result["would_queue"][0]["stage"] == "day2"
        assert called["n"] == 0  # dry run must never call the LLM
        assert not networking.QUEUE.exists() or networking.QUEUE.read_text() == ""

    def test_too_recent_not_due_yet(self, isolated):
        # accepted TODAY (0 days), under DAY2_MIN_DAYS=2 -> not due
        _append_accepted(li_conveyor.ACCEPTED, _accepted_fixture(0))
        result = li_conveyor.run_conveyor(dry=True)
        assert result["would_queue"] == []


class TestRealQueueingPath:
    def test_good_draft_queued_as_dm_pending(self, isolated, monkeypatch):
        _append_accepted(li_conveyor.ACCEPTED, _accepted_fixture(3))
        fake_draft = "Good to connect. Ran into a similar bottleneck scaling client sites last year."
        monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: {"draft": fake_draft})

        result = li_conveyor.run_conveyor(dry=False)
        assert len(result["queued"]) == 1

        q = networking.load_queue()
        assert len(q) == 1
        assert q[0]["kind"] == "dm"
        assert q[0]["status"] == "pending"
        assert q[0]["draft"] == fake_draft
        assert q[0]["conveyor_stage"] == "day2"

    def test_bad_draft_never_queued(self, isolated, monkeypatch):
        _append_accepted(li_conveyor.ACCEPTED, _accepted_fixture(3))
        bad_draft = "Love your energy! Check out my site https://example.com \U0001F525"
        monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: {"draft": bad_draft})

        result = li_conveyor.run_conveyor(dry=False)
        assert result["queued"] == []
        assert networking.load_queue() == []

    def test_empty_llm_output_never_queued(self, isolated, monkeypatch):
        _append_accepted(li_conveyor.ACCEPTED, _accepted_fixture(3))
        monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: None)
        result = li_conveyor.run_conveyor(dry=False)
        assert result["queued"] == []
        assert networking.load_queue() == []

    def test_state_recorded_after_queueing(self, isolated, monkeypatch):
        _append_accepted(li_conveyor.ACCEPTED, _accepted_fixture(3))
        monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: {"draft": "Ran into this exact issue last year."})
        li_conveyor.run_conveyor(dry=False)
        state_lines = li_conveyor.CONVEYOR_STATE.read_text().splitlines()
        assert len(state_lines) == 1
        rec = json.loads(state_lines[0])
        assert rec["stage"] == "day2"


class TestIdempotency:
    def test_second_run_does_not_redraft_day2(self, isolated, monkeypatch):
        _append_accepted(li_conveyor.ACCEPTED, _accepted_fixture(3))
        monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: {"draft": "Ran into this exact issue last year."})

        first = li_conveyor.run_conveyor(dry=False)
        assert len(first["queued"]) == 1

        monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: {"draft": "SHOULD NOT APPEAR"})
        second = li_conveyor.run_conveyor(dry=False)
        assert second["queued"] == []

        q = networking.load_queue()
        assert len(q) == 1  # still just the one item, no duplicate


class TestFollowUpLadder:
    def test_day4_followup_fires_after_day2_sent_and_aged(self, isolated, monkeypatch):
        _append_accepted(li_conveyor.ACCEPTED, _accepted_fixture(10))
        monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: {"draft": "Ran into this exact issue last year."})
        first = li_conveyor.run_conveyor(dry=False)
        day2_id = first["queued"][0]

        # mark the day2 item as DONE (sent), backdated 5 days so day4 threshold is crossed
        rec = next(r for r in networking.load_queue() if r["id"] == day2_id)
        rec["status"] = "done"
        rec["acted_at"] = (date.today() - timedelta(days=5)).isoformat() + "T08:00:00-07:00"
        networking.save_item(rec)

        monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: {"draft": "One more thought on that fulfillment issue."})
        second = li_conveyor.run_conveyor(dry=False)
        assert len(second["queued"]) == 1
        q = networking.load_queue()
        dm_items = [x for x in q if x["kind"] == "dm"]
        assert any(x.get("conveyor_stage") == "day4" for x in dm_items)

    def test_no_followup_if_day2_not_yet_sent(self, isolated, monkeypatch):
        _append_accepted(li_conveyor.ACCEPTED, _accepted_fixture(10))
        monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: {"draft": "Ran into this exact issue last year."})
        li_conveyor.run_conveyor(dry=False)
        # day2 item still status=pending (never marked done/sent) -> no follow-up should fire
        second = li_conveyor.run_conveyor(dry=False)
        assert second["queued"] == []


class TestAcceptedButSilent:
    def test_stale_no_conveyor_history_flagged(self, isolated):
        _append_accepted(li_conveyor.ACCEPTED, _accepted_fixture(45))  # > STALE_NO_DM_DAYS=30
        silent = li_conveyor.find_accepted_but_silent()
        assert len(silent) == 1

    def test_recent_acceptance_not_flagged(self, isolated):
        _append_accepted(li_conveyor.ACCEPTED, _accepted_fixture(5))
        silent = li_conveyor.find_accepted_but_silent()
        assert silent == []

    def test_has_conveyor_history_not_flagged_even_if_old(self, isolated, monkeypatch):
        _append_accepted(li_conveyor.ACCEPTED, _accepted_fixture(45))
        monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: {"draft": "Ran into this exact issue last year."})
        li_conveyor.run_conveyor(dry=False)  # gives it conveyor history
        silent = li_conveyor.find_accepted_but_silent()
        assert silent == []  # has history now, not "silent" anymore


class TestNeverRaisesOnMalformedInput:
    def test_missing_accepted_at_skipped_not_crashed(self, isolated):
        _append_accepted(li_conveyor.ACCEPTED, _accepted_fixture(3, accepted_at=""))
        result = li_conveyor.run_conveyor(dry=True)
        assert result["would_queue"] == []  # skipped, no crash

    def test_malformed_json_line_ignored(self, isolated):
        li_conveyor.ACCEPTED.parent.mkdir(parents=True, exist_ok=True)
        with li_conveyor.ACCEPTED.open("a") as f:
            f.write("not valid json\n")
            f.write(json.dumps(_accepted_fixture(3)) + "\n")
        rows = li_conveyor.load_accepted()
        assert len(rows) == 1  # bad line skipped, good line kept


class TestQueueDmAndAdvance:
    """R2-36: _queue_dm_and_advance() locks the dm-queue append + conveyor-state write
    together, so a crash or an overlapping sweep between them can't leave a queued dm
    with no matching state (which used to redraft + re-queue a duplicate on the next
    sweep)."""

    def test_queues_item_and_advances_state_together(self, isolated, monkeypatch):
        rec = {"url": "https://linkedin.com/in/fixture-person", "name": "FIXTURE Person",
               "headline": "", "context": ""}
        item = li_conveyor._queue_dm_and_advance(rec, "Good to connect, ran into this exact "
                                                  "thing scaling client sites last year.", "day2")
        assert item is not None
        assert item["conveyor_stage"] == "day2"

        import li_history
        uk = li_history._url_key(rec["url"])
        state = li_conveyor._conveyor_state()
        assert state[uk]["stage"] == "day2"
        assert state[uk]["queue_item_id"] == item["id"]  # queued item + state agree

    def test_state_stage_can_differ_from_dm_stage_tag(self, isolated):
        # the closer dm is tagged conveyor_stage="closer" but the LADDER's state moves to
        # the terminal "closed" -- state_stage lets the two diverge on purpose.
        rec = {"url": "https://linkedin.com/in/fixture-person", "name": "FIXTURE Person"}
        item = li_conveyor._queue_dm_and_advance(rec, "Wanted to leave the door open, no "
                                                  "pressure either way.", "closer", state_stage="closed")
        assert item["conveyor_stage"] == "closer"
        import li_history
        uk = li_history._url_key(rec["url"])
        assert li_conveyor._conveyor_state()[uk]["stage"] == "closed"

    def test_rejected_draft_writes_no_orphan_state(self, isolated):
        # same bad-draft shape TestRealQueueingPath already proves li_quality rejects
        rec = {"url": "https://linkedin.com/in/fixture-person", "name": "FIXTURE Person"}
        bad_draft = "Love your energy! Check out my site https://example.com \U0001F525"
        item = li_conveyor._queue_dm_and_advance(rec, bad_draft, "day2")
        assert item is None
        assert li_conveyor._conveyor_state() == {}  # nothing queued -> no state either


class TestDeadThreadCloserTiming:
    """R2-36: the closer's wait must be measured from the day-12 item's OWN send (the
    docstring's "21d past the day-12 follow-up"), not from FOLLOWUP_DAY12 + DEAD_THREAD_DAY
    (33), which double-counted the day-12 gap and drifted further whenever an earlier
    stage's send was delayed by approval lag."""

    def _seed_day12(self, isolated, url: str, days_since_sent: int, item_id="dm_day12_fixture"):
        sent = (date.today() - timedelta(days=days_since_sent)).isoformat() + "T08:00:00-07:00"
        networking.save_item({"id": item_id, "kind": "dm", "author": "FIXTURE Person",
                              "target": "", "url": url, "draft": "prior follow-up",
                              "status": "done", "acted_at": sent, "created": sent,
                              "conveyor_stage": "day12"})
        li_conveyor._save_state(url, "day12", {"queue_item_id": item_id})

    def test_closer_fires_21_days_after_day12_sent(self, isolated, monkeypatch):
        url = "https://linkedin.com/in/fixture-person"
        _append_accepted(li_conveyor.ACCEPTED, _accepted_fixture(90, url=url))
        self._seed_day12(isolated, url, li_conveyor.DEAD_THREAD_DAY)  # exactly 21 days
        monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: {"draft": "graceful closer line"})

        result = li_conveyor.run_conveyor(dry=False)
        assert len(result["queued"]) == 1

        closer_items = [x for x in networking.load_queue() if x.get("conveyor_stage") == "closer"]
        assert len(closer_items) == 1
        state_lines = [json.loads(ln) for ln in li_conveyor.CONVEYOR_STATE.read_text().splitlines()]
        assert state_lines[-1]["stage"] == "closed"

    def test_closer_does_not_fire_one_day_early(self, isolated, monkeypatch):
        url = "https://linkedin.com/in/fixture-person"
        _append_accepted(li_conveyor.ACCEPTED, _accepted_fixture(90, url=url))
        self._seed_day12(isolated, url, li_conveyor.DEAD_THREAD_DAY - 1)  # 20 days, not yet due
        monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: {"draft": "SHOULD NOT APPEAR"})

        result = li_conveyor.run_conveyor(dry=False)
        assert result["queued"] == []
