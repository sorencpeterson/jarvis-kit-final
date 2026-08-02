#!/usr/bin/env python3
"""Unit tests for agents/li_digest.py (A17 weekly networking digest). Isolated
to tmp_path fixtures, never the real store. Verifies the digest never invents
accepted/replied numbers when no data source exists (the honest [E] behavior).

Run: .venv/bin/python -m pytest tests/test_li_digest.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import networking  # noqa: E402
import li_conveyor  # noqa: E402
import li_digest  # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(networking, "QUEUE", tmp_path / "network.jsonl")
    monkeypatch.setattr(li_conveyor, "ACCEPTED", tmp_path / "li_accepted.jsonl")
    monkeypatch.setattr(li_digest, "OUT", tmp_path / "li_digest.json")
    return tmp_path


def _write_queue(path: Path, records: list[dict]):
    with path.open("a") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


class TestBuildDigestEmptyState:
    def test_empty_queue_all_zero(self, isolated):
        d = li_digest.build_digest(week="2026-W27")
        assert d["sourced"] == 0
        assert d["sent"] == 0
        assert d["accepted"] == 0
        assert d["replied"] == 0

    def test_no_accepted_source_honest_note(self, isolated):
        d = li_digest.build_digest(week="2026-W27")
        assert d["accepted_note"] is not None
        assert "no data source" in d["accepted_note"]

    def test_replied_always_has_note_no_source_exists_yet(self, isolated):
        d = li_digest.build_digest(week="2026-W27")
        assert d["replied"] == 0
        assert "no data source" in d["replied_note"]


class TestBuildDigestWithData:
    def test_sourced_counts_this_week(self, isolated, tmp_path):
        _write_queue(tmp_path / "network.jsonl", [
            {"id": "a1", "kind": "comment", "author": "X", "target": "", "url": "u1",
             "draft": "x", "status": "pending", "created": "2026-07-01T08:00:00-07:00"},  # a Wed, W27
            {"id": "a2", "kind": "connect", "author": "Y", "target": "", "url": "u2",
             "draft": "", "status": "pending", "created": "2026-07-02T08:00:00-07:00"},
        ])
        d = li_digest.build_digest(week="2026-W27")
        assert d["sourced"] == 2
        assert d["by_kind_sourced"] == {"comment": 1, "connect": 1}

    def test_prior_week_items_excluded(self, isolated, tmp_path):
        _write_queue(tmp_path / "network.jsonl", [
            {"id": "a1", "kind": "comment", "author": "X", "target": "", "url": "u1",
             "draft": "x", "status": "pending", "created": "2026-06-01T08:00:00-07:00"},  # older week
        ])
        d = li_digest.build_digest(week="2026-W27")
        assert d["sourced"] == 0

    def test_sent_counts_done_items_acted_this_week(self, isolated, tmp_path):
        _write_queue(tmp_path / "network.jsonl", [
            {"id": "a1", "kind": "like", "author": "X", "target": "", "url": "u1",
             "draft": "", "status": "done", "created": "2026-06-01T08:00:00-07:00",
             "acted_at": "2026-07-01T08:00:00-07:00"},
        ])
        d = li_digest.build_digest(week="2026-W27")
        assert d["sent"] == 1
        assert d["by_kind_sent"] == {"like": 1}

    def test_pending_status_not_counted_as_sent(self, isolated, tmp_path):
        _write_queue(tmp_path / "network.jsonl", [
            {"id": "a1", "kind": "like", "author": "X", "target": "", "url": "u1",
             "draft": "", "status": "pending", "created": "2026-07-01T08:00:00-07:00"},
        ])
        d = li_digest.build_digest(week="2026-W27")
        assert d["sent"] == 0

    def test_accepted_count_from_conveyor_store(self, isolated, tmp_path):
        with (tmp_path / "li_accepted.jsonl").open("a") as f:
            f.write(json.dumps({"url": "u1", "name": "X", "accepted_at": "2026-07-01T08:00:00-07:00"}) + "\n")
        d = li_digest.build_digest(week="2026-W27")
        assert d["accepted"] == 1
        assert d["accepted_note"] is None  # has real data, no caveat needed

    def test_queue_depth_pending_reflects_current_pending(self, isolated, tmp_path):
        _write_queue(tmp_path / "network.jsonl", [
            {"id": "a1", "kind": "like", "author": "X", "target": "", "url": "u1",
             "draft": "", "status": "pending", "created": "2026-07-01T08:00:00-07:00"},
            {"id": "a2", "kind": "like", "author": "Y", "target": "", "url": "u2",
             "draft": "", "status": "done", "created": "2026-07-01T08:00:00-07:00"},
        ])
        d = li_digest.build_digest(week="2026-W27")
        assert d["queue_depth_pending"] == 1


class TestRun:
    def test_writes_output_file(self, isolated):
        li_digest.run()
        assert li_digest.OUT.exists()
        data = json.loads(li_digest.OUT.read_text())
        assert "week" in data
        assert "sourced" in data

    def test_output_is_valid_json_with_expected_keys(self, isolated):
        li_digest.run()
        data = json.loads(li_digest.OUT.read_text())
        expected_keys = {"week", "generated", "sourced", "sent", "by_kind_sourced",
                          "by_kind_sent", "accepted", "accepted_note", "replied",
                          "replied_note", "queue_depth_pending"}
        assert expected_keys.issubset(data.keys())
