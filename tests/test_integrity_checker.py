#!/usr/bin/env python3
"""Pytest suite for agents/integrity_checker.py's pure logic. No real GHL
calls (injected fixture lookup functions throughout) — the real end-to-end
GHL-hitting path was verified manually against the live account.

Run: .venv/bin/python -m pytest tests/test_integrity_checker.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import integrity_checker as ic  # noqa: E402


class TestDistinctContactIds:
    def test_collects_from_both_stores(self, tmp_path, monkeypatch):
        props = tmp_path / "proposals.jsonl"
        reps = tmp_path / "replies.jsonl"
        props.write_text(json.dumps({"id": "p1", "contact_id": "c1"}) + "\n")
        reps.write_text(json.dumps({"id": "r1", "contact_id": "c2"}) + "\n")
        monkeypatch.setattr(ic, "PROPOSALS", props)
        monkeypatch.setattr(ic, "REPLIES", reps)
        refs = ic._distinct_contact_ids()
        assert set(refs.keys()) == {"c1", "c2"}
        assert refs["c1"] == ["proposal:p1"]
        assert refs["c2"] == ["reply:r1"]

    def test_skips_empty_contact_id(self, tmp_path, monkeypatch):
        props = tmp_path / "proposals.jsonl"
        props.write_text(json.dumps({"id": "p1", "contact_id": ""}) + "\n")
        monkeypatch.setattr(ic, "PROPOSALS", props)
        monkeypatch.setattr(ic, "REPLIES", tmp_path / "nonexistent.jsonl")
        assert ic._distinct_contact_ids() == {}

    def test_same_contact_id_multiple_refs(self, tmp_path, monkeypatch):
        props = tmp_path / "proposals.jsonl"
        props.write_text(
            json.dumps({"id": "p1", "contact_id": "c1"}) + "\n" +
            json.dumps({"id": "p2", "contact_id": "c1"}) + "\n"
        )
        monkeypatch.setattr(ic, "PROPOSALS", props)
        monkeypatch.setattr(ic, "REPLIES", tmp_path / "nonexistent.jsonl")
        refs = ic._distinct_contact_ids()
        assert refs["c1"] == ["proposal:p1", "proposal:p2"]


class TestCheckIds:
    def test_resolves_on_first_try(self):
        refs = {"c1": ["proposal:p1"]}
        result = ic.check_ids(refs, lookup_fn=lambda cid: {"id": cid}, retry_delay_s=0)
        assert result["checked"] == 1
        assert len(result["resolved"]) == 1
        assert len(result["did_not_resolve"]) == 0

    def test_only_flags_orphan_after_two_misses(self):
        calls = []

        def flaky_lookup(cid):
            calls.append(cid)
            return {}  # always empty -> confirmed orphan after 2 tries

        refs = {"c1": ["proposal:p1"]}
        result = ic.check_ids(refs, lookup_fn=flaky_lookup, retry_delay_s=0)
        assert len(calls) == 2  # confirms it retried once before flagging
        assert len(result["did_not_resolve"]) == 1

    def test_resolves_on_second_try_not_flagged(self):
        attempt = {"n": 0}

        def transient_fail_then_resolve(cid):
            attempt["n"] += 1
            return {} if attempt["n"] == 1 else {"id": cid}

        refs = {"c1": ["proposal:p1"]}
        result = ic.check_ids(refs, lookup_fn=transient_fail_then_resolve, retry_delay_s=0)
        assert len(result["resolved"]) == 1
        assert len(result["did_not_resolve"]) == 0

    def test_respects_sample_limit(self):
        refs = {f"c{i}": [f"proposal:p{i}"] for i in range(10)}
        result = ic.check_ids(refs, limit=3, lookup_fn=lambda cid: {"id": cid}, retry_delay_s=0)
        assert result["checked"] == 3
        assert result["skipped_over_limit"] == 7

    def test_empty_refs(self):
        result = ic.check_ids({}, lookup_fn=lambda cid: {}, retry_delay_s=0)
        assert result["checked"] == 0
        assert result["resolved"] == []
        assert result["did_not_resolve"] == []


class TestFixtureLookup:
    def test_real_prefix_resolves(self):
        assert ic._fixture_lookup("real_001") != {}

    def test_non_real_prefix_does_not_resolve(self):
        assert ic._fixture_lookup("orphan_001") == {}
        assert ic._fixture_lookup("versiontest001") == {}


class TestRun:
    def test_fixture_mode_shape(self):
        data = ic.run(fixture=True)
        assert data["fixture"] is True
        assert data["total_distinct_ids"] == 3
        assert len(data["did_not_resolve"]) == 2  # orphan_001 and versiontest001
        assert len(data["resolved"]) == 1  # real_001
