#!/usr/bin/env python3
"""Unit tests for agents/li_companies.py (A18 company-page follow list
builder). Isolated to tmp_path, never the real store.

Run: .venv/bin/python -m pytest tests/test_li_companies.py -v
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
import li_companies  # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(networking, "QUEUE", tmp_path / "network.jsonl")
    return tmp_path


def _write(path: Path, records: list[dict]):
    with path.open("a") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _rec(**kw) -> dict:
    base = {"id": "id1", "kind": "connect", "author": "X", "target": "",
            "url": "https://linkedin.com/in/x", "draft": "", "status": "pending",
            "created": "2026-06-01T08:00:00-07:00"}
    base.update(kw)
    return base


class TestFollowList:
    def test_empty_queue_empty_list(self, isolated):
        assert li_companies.follow_list() == []

    def test_single_company_appears(self, isolated, tmp_path):
        _write(tmp_path / "network.jsonl", [
            _rec(id="a1", target="Founder @ Nimbusrp", url="https://linkedin.com/in/a1"),
        ])
        result = li_companies.follow_list()
        assert len(result) == 1
        assert result[0]["company"] == "nimbusrp"
        assert result[0]["target_count"] == 1

    def test_multiple_targets_same_company_deduped_by_person(self, isolated, tmp_path):
        _write(tmp_path / "network.jsonl", [
            _rec(id="a1", target="Founder @ Nimbusrp", url="https://linkedin.com/in/person1"),
            _rec(id="a2", target="Owner @ Nimbusrp", url="https://linkedin.com/in/person2"),
        ])
        result = li_companies.follow_list()
        assert result[0]["company"] == "nimbusrp"
        assert result[0]["target_count"] == 2

    def test_same_person_multiple_records_counted_once(self, isolated, tmp_path):
        # same URL touched twice (e.g. connect + a later comment) should count
        # as ONE distinct target for that company, not two
        _write(tmp_path / "network.jsonl", [
            _rec(id="a1", kind="connect", target="Founder @ Nimbusrp", url="https://linkedin.com/in/person1"),
            _rec(id="a2", kind="comment", target="Founder @ Nimbusrp", url="https://www.linkedin.com/in/person1/"),
        ])
        result = li_companies.follow_list()
        assert result[0]["target_count"] == 1

    def test_ranked_by_target_count_descending(self, isolated, tmp_path):
        _write(tmp_path / "network.jsonl", [
            _rec(id="a1", target="Founder @ Small Co", url="https://linkedin.com/in/p1"),
            _rec(id="a2", target="Founder @ Big Co", url="https://linkedin.com/in/p2"),
            _rec(id="a3", target="Owner @ Big Co", url="https://linkedin.com/in/p3"),
            _rec(id="a4", target="CEO @ Big Co", url="https://linkedin.com/in/p4"),
        ])
        result = li_companies.follow_list()
        assert result[0]["company"] == "big co"
        assert result[0]["target_count"] == 3

    def test_no_extractable_company_excluded(self, isolated, tmp_path):
        _write(tmp_path / "network.jsonl", [
            _rec(id="a1", target="no company pattern here", url="https://linkedin.com/in/p1"),
        ])
        assert li_companies.follow_list() == []

    def test_min_targets_filter(self, isolated, tmp_path):
        _write(tmp_path / "network.jsonl", [
            _rec(id="a1", target="Founder @ Solo Co", url="https://linkedin.com/in/p1"),
            _rec(id="a2", target="Founder @ Team Co", url="https://linkedin.com/in/p2"),
            _rec(id="a3", target="Owner @ Team Co", url="https://linkedin.com/in/p3"),
        ])
        result = li_companies.follow_list(min_targets=2)
        companies = [r["company"] for r in result]
        assert "team co" in companies
        assert "solo co" not in companies
