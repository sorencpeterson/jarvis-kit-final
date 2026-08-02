#!/usr/bin/env python3
"""Unit tests for agents/li_openers.py (A11 connection-note / opener A/B bank
with per-opener tracking). Isolated to tmp_path, never the real store.

Run: .venv/bin/python -m pytest tests/test_li_openers.py -v
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
import li_openers  # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(networking, "QUEUE", tmp_path / "network.jsonl")
    monkeypatch.setattr(li_openers, "OPENER_LOG", tmp_path / "li_opener_log.jsonl")
    return tmp_path


class TestOpenerBank:
    def test_exactly_four_openers(self):
        # A11 explicitly asks for 4 openers rotated
        assert len(li_openers.OPENER_BANK) == 4

    def test_all_openers_have_required_fields(self):
        for o in li_openers.OPENER_BANK:
            assert o.get("key")
            assert o.get("label")
            assert o.get("instruction")

    def test_keys_are_unique(self):
        keys = [o["key"] for o in li_openers.OPENER_BANK]
        assert len(keys) == len(set(keys))

    def test_opener_by_key_found(self):
        o = li_openers.opener_by_key("diagnosis_flip")
        assert o is not None
        assert o["key"] == "diagnosis_flip"

    def test_opener_by_key_not_found_returns_none(self):
        assert li_openers.opener_by_key("nonexistent_key") is None


class TestNextOpener:
    def test_empty_log_returns_a_valid_opener(self, isolated):
        o = li_openers.next_opener()
        assert o["key"] in [x["key"] for x in li_openers.OPENER_BANK]

    def test_rotates_away_from_recently_used(self, isolated):
        first = li_openers.next_opener()
        li_openers.record_opener_use("item1", first["key"])
        second = li_openers.next_opener()
        assert second["key"] != first["key"]

    def test_all_four_get_used_across_repeated_calls(self, isolated):
        seen = set()
        for i in range(4):
            o = li_openers.next_opener()
            li_openers.record_opener_use(f"item{i}", o["key"])
            seen.add(o["key"])
        assert len(seen) == 4  # true rotation, not stuck on one


class TestRecordOpenerUse:
    def test_writes_one_log_line(self, isolated):
        li_openers.record_opener_use("item1", "diagnosis_flip", kind="comment")
        lines = li_openers.OPENER_LOG.read_text().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["item_id"] == "item1"
        assert rec["opener_key"] == "diagnosis_flip"
        assert rec["kind"] == "comment"

    def test_multiple_uses_append(self, isolated):
        li_openers.record_opener_use("item1", "receipt")
        li_openers.record_opener_use("item2", "receipt")
        lines = li_openers.OPENER_LOG.read_text().splitlines()
        assert len(lines) == 2


class TestOpenerStats:
    def test_no_usage_all_zero(self, isolated):
        stats = li_openers.opener_stats()
        assert all(v["used"] == 0 for v in stats.values())
        assert set(stats.keys()) == {"diagnosis_flip", "shared_experience", "receipt", "plain_agreement_plus"}

    def test_used_count_increments(self, isolated):
        li_openers.record_opener_use("item1", "receipt")
        li_openers.record_opener_use("item2", "receipt")
        stats = li_openers.opener_stats()
        assert stats["receipt"]["used"] == 2

    def test_joins_against_queue_status(self, isolated, tmp_path):
        with (tmp_path / "network.jsonl").open("a") as f:
            f.write(json.dumps({"id": "item1", "kind": "comment", "author": "X", "target": "",
                                 "url": "", "draft": "x", "status": "done",
                                 "created": "2026-06-01T08:00:00-07:00"}) + "\n")
        li_openers.record_opener_use("item1", "receipt", kind="comment")
        stats = li_openers.opener_stats()
        assert stats["receipt"]["by_status"].get("done") == 1

    def test_unknown_item_id_status_unknown(self, isolated):
        li_openers.record_opener_use("nonexistent_item", "receipt")
        stats = li_openers.opener_stats()
        assert stats["receipt"]["by_status"].get("unknown") == 1

    def test_unrecognized_opener_key_in_log_ignored(self, isolated):
        li_openers.OPENER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with li_openers.OPENER_LOG.open("a") as f:
            f.write(json.dumps({"ts": "x", "item_id": "i1", "opener_key": "made_up_key",
                                "kind": "comment"}) + "\n")
        stats = li_openers.opener_stats()
        assert "made_up_key" not in stats  # never crashes, just not tracked
