#!/usr/bin/env python3
"""Unit tests for agents/li_thread.py (A16 per-contact thread memory,
A31 last-N-messages context assembly). Isolated to tmp_path, never the real store.

Run: .venv/bin/python -m pytest tests/test_li_thread.py -v
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
import li_thread  # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(networking, "QUEUE", tmp_path / "network.jsonl")
    monkeypatch.setattr(li_thread, "THREADS_DIR", tmp_path / "li_threads")
    return tmp_path


class TestLoadThread:
    def test_no_file_returns_empty_shell(self, isolated):
        t = li_thread.load_thread("https://linkedin.com/in/nobody")
        assert t["touches"] == []
        assert t["name"] == ""

    def test_load_after_record_touch(self, isolated):
        li_thread.record_touch("https://linkedin.com/in/alice", kind="comment",
                                draft="hello", name="Alice")
        t = li_thread.load_thread("https://linkedin.com/in/alice")
        assert t["name"] == "Alice"
        assert len(t["touches"]) == 1
        assert t["touches"][0]["text"] == "hello"

    def test_corrupted_file_returns_empty_shell_not_crash(self, isolated):
        li_thread.THREADS_DIR.mkdir(parents=True, exist_ok=True)
        p = li_thread._thread_path("https://linkedin.com/in/alice")
        p.write_text("{not valid json")
        t = li_thread.load_thread("https://linkedin.com/in/alice")
        assert t["touches"] == []


class TestRecordTouch:
    def test_multiple_touches_accumulate(self, isolated):
        url = "https://linkedin.com/in/bob"
        li_thread.record_touch(url, kind="comment", draft="first", name="Bob")
        li_thread.record_touch(url, kind="dm", draft="second", name="Bob")
        t = li_thread.load_thread(url)
        assert len(t["touches"]) == 2
        assert [x["text"] for x in t["touches"]] == ["first", "second"]

    def test_name_set_once_not_overwritten_by_blank(self, isolated):
        url = "https://linkedin.com/in/carol"
        li_thread.record_touch(url, kind="comment", draft="x", name="Carol")
        li_thread.record_touch(url, kind="dm", draft="y", name="")
        t = li_thread.load_thread(url)
        assert t["name"] == "Carol"

    def test_url_variants_write_same_thread_file(self, isolated):
        li_thread.record_touch("https://www.linkedin.com/in/dave/", kind="comment", draft="a")
        li_thread.record_touch("https://linkedin.com/in/dave?x=1", kind="dm", draft="b")
        t = li_thread.load_thread("linkedin.com/in/dave")
        assert len(t["touches"]) == 2

    def test_direction_defaults_outbound(self, isolated):
        li_thread.record_touch("https://linkedin.com/in/eve", kind="comment", draft="x")
        t = li_thread.load_thread("https://linkedin.com/in/eve")
        assert t["touches"][0]["direction"] == "outbound"

    def test_inbound_direction_accepted(self, isolated):
        li_thread.record_touch("https://linkedin.com/in/eve", kind="dm", draft="their reply",
                                direction="inbound")
        t = li_thread.load_thread("https://linkedin.com/in/eve")
        assert t["touches"][0]["direction"] == "inbound"


class TestThreadContext:
    def test_empty_contact_empty_string(self, isolated):
        assert li_thread.thread_context("https://linkedin.com/in/nobody") == ""

    def test_returns_last_n_only(self, isolated):
        url = "https://linkedin.com/in/frank"
        for i in range(5):
            li_thread.record_touch(url, kind="comment", draft=f"message {i}")
        ctx = li_thread.thread_context(url, n=3)
        assert "message 2" in ctx
        assert "message 3" in ctx
        assert "message 4" in ctx
        assert "message 0" not in ctx
        assert "message 1" not in ctx

    def test_includes_kind_and_direction_tags(self, isolated):
        url = "https://linkedin.com/in/grace"
        li_thread.record_touch(url, kind="comment", draft="hi there")
        ctx = li_thread.thread_context(url)
        assert "[outbound/comment]" in ctx

    def test_skips_empty_text_entries(self, isolated):
        url = "https://linkedin.com/in/henry"
        li_thread.record_touch(url, kind="connect", draft="")  # noteless connect, empty draft
        li_thread.record_touch(url, kind="dm", draft="real message")
        ctx = li_thread.thread_context(url)
        assert "real message" in ctx
        assert ctx.count("\n") == 0  # only one non-empty line


class TestUsedOpeners:
    def test_empty_contact_empty_list(self, isolated):
        assert li_thread.used_openers("https://linkedin.com/in/nobody") == []

    def test_returns_all_outbound_texts(self, isolated):
        url = "https://linkedin.com/in/ivan"
        li_thread.record_touch(url, kind="comment", draft="opener one")
        li_thread.record_touch(url, kind="dm", draft="opener two")
        openers = li_thread.used_openers(url)
        assert openers == ["opener one", "opener two"]

    def test_inbound_not_counted_as_used_opener(self, isolated):
        url = "https://linkedin.com/in/jane"
        li_thread.record_touch(url, kind="dm", draft="their message", direction="inbound")
        assert li_thread.used_openers(url) == []


class TestSyncFromQueue:
    def _write(self, path, records):
        with path.open("a") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    def test_empty_queue_zero_synced(self, isolated):
        n = li_thread.sync_from_queue()
        assert n == 0

    def test_syncs_distinct_contacts(self, isolated, tmp_path):
        self._write(tmp_path / "network.jsonl", [
            {"id": "a1", "kind": "comment", "author": "Alice", "target": "",
             "url": "https://linkedin.com/in/alice", "draft": "hi alice",
             "status": "done", "created": "2026-06-01T08:00:00-07:00"},
            {"id": "a2", "kind": "connect", "author": "Bob", "target": "",
             "url": "https://linkedin.com/in/bob", "draft": "",
             "status": "done", "created": "2026-06-01T08:00:00-07:00"},
        ])
        n = li_thread.sync_from_queue()
        assert n == 2
        t = li_thread.load_thread("https://linkedin.com/in/alice")
        assert t["name"] == "Alice"
        assert t["touches"][0]["text"] == "hi alice"

    def test_url_variants_collapse_during_sync(self, isolated, tmp_path):
        self._write(tmp_path / "network.jsonl", [
            {"id": "a1", "kind": "comment", "author": "Alice", "target": "",
             "url": "https://www.linkedin.com/in/alice/", "draft": "first",
             "status": "done", "created": "2026-06-01T08:00:00-07:00"},
            {"id": "a2", "kind": "dm", "author": "Alice", "target": "",
             "url": "https://linkedin.com/in/alice?ref=x", "draft": "second",
             "status": "pending", "created": "2026-06-02T08:00:00-07:00"},
        ])
        n = li_thread.sync_from_queue()
        assert n == 1  # same contact, one thread file
        t = li_thread.load_thread("linkedin.com/in/alice")
        assert len(t["touches"]) == 2

    def test_sync_is_idempotent(self, isolated, tmp_path):
        self._write(tmp_path / "network.jsonl", [
            {"id": "a1", "kind": "comment", "author": "Alice", "target": "",
             "url": "https://linkedin.com/in/alice", "draft": "hi",
             "status": "done", "created": "2026-06-01T08:00:00-07:00"},
        ])
        li_thread.sync_from_queue()
        li_thread.sync_from_queue()  # run twice
        t = li_thread.load_thread("https://linkedin.com/in/alice")
        assert len(t["touches"]) == 1  # rebuild, not append -- never doubles

    def test_records_without_url_skipped(self, isolated, tmp_path):
        self._write(tmp_path / "network.jsonl", [
            {"id": "a1", "kind": "comment", "author": "NoUrl", "target": "",
             "url": "", "draft": "x", "status": "done", "created": "2026-06-01T08:00:00-07:00"},
        ])
        n = li_thread.sync_from_queue()
        assert n == 0
