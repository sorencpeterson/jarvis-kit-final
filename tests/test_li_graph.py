#!/usr/bin/env python3
"""Unit tests for agents/li_graph.py (A64 contact-graph merge feed).
Isolated to tmp_path, never the real store. Verifies the node shape is stable
(so contact_graph.py's future consumer can rely on it) and that run() only
ever writes to its OWN output file.

Run: .venv/bin/python -m pytest tests/test_li_graph.py -v
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
import li_graph  # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(networking, "QUEUE", tmp_path / "network.jsonl")
    monkeypatch.setattr(li_graph, "OUT", tmp_path / "li_graph_nodes.jsonl")
    return tmp_path


def _write(path: Path, records: list[dict]):
    with path.open("a") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


class TestBuildNodes:
    def test_empty_queue_empty_nodes(self, isolated):
        assert li_graph.build_nodes() == []

    def test_single_contact_one_node(self, isolated, tmp_path):
        _write(tmp_path / "network.jsonl", [
            {"id": "a1", "kind": "connect", "author": "Alice", "target": "",
             "url": "https://linkedin.com/in/alice", "draft": "",
             "status": "done", "created": "2026-06-01T08:00:00-07:00"},
        ])
        nodes = li_graph.build_nodes()
        assert len(nodes) == 1
        assert nodes[0]["name"] == "Alice"
        assert nodes[0]["source"] == "linkedin"

    def test_url_variants_collapse_to_one_node(self, isolated, tmp_path):
        _write(tmp_path / "network.jsonl", [
            {"id": "a1", "kind": "comment", "author": "Bob", "target": "",
             "url": "https://www.linkedin.com/in/bob/", "draft": "x",
             "status": "done", "created": "2026-06-01T08:00:00-07:00"},
            {"id": "a2", "kind": "connect", "author": "Bob", "target": "",
             "url": "https://linkedin.com/in/bob?ref=1", "draft": "",
             "status": "pending", "created": "2026-06-02T08:00:00-07:00"},
        ])
        nodes = li_graph.build_nodes()
        assert len(nodes) == 1
        assert set(nodes[0]["kinds"]) == {"comment", "connect"}
        assert nodes[0]["touch_count"] == 2

    def test_soft_connected_true_when_connect_done(self, isolated, tmp_path):
        _write(tmp_path / "network.jsonl", [
            {"id": "a1", "kind": "connect", "author": "Carol", "target": "",
             "url": "https://linkedin.com/in/carol", "draft": "",
             "status": "done", "created": "2026-06-01T08:00:00-07:00"},
        ])
        nodes = li_graph.build_nodes()
        assert nodes[0]["soft_connected"] is True

    def test_soft_connected_false_when_connect_still_pending(self, isolated, tmp_path):
        _write(tmp_path / "network.jsonl", [
            {"id": "a1", "kind": "connect", "author": "Dave", "target": "",
             "url": "https://linkedin.com/in/dave", "draft": "",
             "status": "pending", "created": "2026-06-01T08:00:00-07:00"},
        ])
        nodes = li_graph.build_nodes()
        assert nodes[0]["soft_connected"] is False

    def test_no_url_records_excluded(self, isolated, tmp_path):
        _write(tmp_path / "network.jsonl", [
            {"id": "a1", "kind": "comment", "author": "NoUrl", "target": "",
             "url": "", "draft": "x", "status": "done", "created": "2026-06-01T08:00:00-07:00"},
        ])
        assert li_graph.build_nodes() == []

    def test_nodes_sorted_by_name(self, isolated, tmp_path):
        _write(tmp_path / "network.jsonl", [
            {"id": "a1", "kind": "connect", "author": "Zoe", "target": "",
             "url": "https://linkedin.com/in/zoe", "draft": "",
             "status": "done", "created": "2026-06-01T08:00:00-07:00"},
            {"id": "a2", "kind": "connect", "author": "Amy", "target": "",
             "url": "https://linkedin.com/in/amy", "draft": "",
             "status": "done", "created": "2026-06-01T08:00:00-07:00"},
        ])
        nodes = li_graph.build_nodes()
        assert [n["name"] for n in nodes] == ["Amy", "Zoe"]

    def test_expected_node_shape_keys(self, isolated, tmp_path):
        _write(tmp_path / "network.jsonl", [
            {"id": "a1", "kind": "like", "author": "Eve", "target": "",
             "url": "https://linkedin.com/in/eve", "draft": "",
             "status": "done", "created": "2026-06-01T08:00:00-07:00"},
        ])
        nodes = li_graph.build_nodes()
        expected_keys = {"url", "name", "kinds", "touch_count", "soft_connected", "source"}
        assert set(nodes[0].keys()) == expected_keys


class TestRun:
    def test_writes_one_line_per_node(self, isolated, tmp_path):
        _write(tmp_path / "network.jsonl", [
            {"id": "a1", "kind": "connect", "author": "Frank", "target": "",
             "url": "https://linkedin.com/in/frank", "draft": "",
             "status": "done", "created": "2026-06-01T08:00:00-07:00"},
            {"id": "a2", "kind": "connect", "author": "Grace", "target": "",
             "url": "https://linkedin.com/in/grace", "draft": "",
             "status": "done", "created": "2026-06-01T08:00:00-07:00"},
        ])
        li_graph.run()
        lines = li_graph.OUT.read_text().splitlines()
        assert len(lines) == 2
        for line in lines:
            json.loads(line)  # every line is valid JSON

    def test_rerun_overwrites_not_appends(self, isolated, tmp_path):
        _write(tmp_path / "network.jsonl", [
            {"id": "a1", "kind": "connect", "author": "Henry", "target": "",
             "url": "https://linkedin.com/in/henry", "draft": "",
             "status": "done", "created": "2026-06-01T08:00:00-07:00"},
        ])
        li_graph.run()
        li_graph.run()  # run twice
        lines = li_graph.OUT.read_text().splitlines()
        assert len(lines) == 1  # overwrite, not accumulate

    def test_never_touches_network_jsonl(self, isolated, tmp_path):
        queue_path = tmp_path / "network.jsonl"
        _write(queue_path, [
            {"id": "a1", "kind": "connect", "author": "Ivy", "target": "",
             "url": "https://linkedin.com/in/ivy", "draft": "",
             "status": "done", "created": "2026-06-01T08:00:00-07:00"},
        ])
        before = queue_path.read_text()
        li_graph.run()
        after = queue_path.read_text()
        assert before == after
