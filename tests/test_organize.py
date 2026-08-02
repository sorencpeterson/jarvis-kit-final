#!/usr/bin/env python3
"""Pytest suite for the E331 pure helpers in agents/organize.py: domain-history
join-key, last-write-wins lookup, correction detection, few-shot rendering.
No LLM calls, no store I/O — every test passes in-memory data directly.

Run: .venv/bin/python -m pytest tests/test_organize.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "dashboard", ROOT / "agents"):
    sys.path.insert(0, str(p))

import organize  # noqa: E402


class TestItemKey:
    def test_prefers_ref_id(self):
        assert organize._item_key({"ref_id": "tdo_123", "text": "foo"}) == "tdo_123"

    def test_falls_back_to_normalized_text(self):
        assert organize._item_key({"text": "  Foo Bar  "}) == "foo bar"

    def test_no_ref_id_no_text(self):
        assert organize._item_key({}) == ""


class TestLastAutoDomainByKey:
    def test_last_write_wins(self):
        history = [
            {"key": "a", "domain": "webdev", "ts": "2026-07-01"},
            {"key": "a", "domain": "systems", "ts": "2026-07-02"},
        ]
        assert organize._last_auto_domain_by_key(history) == {"a": "systems"}

    def test_multiple_keys(self):
        history = [
            {"key": "a", "domain": "webdev", "ts": "2026-07-01"},
            {"key": "b", "domain": "career", "ts": "2026-07-01"},
        ]
        result = organize._last_auto_domain_by_key(history)
        assert result == {"a": "webdev", "b": "career"}

    def test_empty_history(self):
        assert organize._last_auto_domain_by_key([]) == {}

    def test_skips_records_without_key(self):
        history = [{"domain": "webdev", "ts": "2026-07-01"}]
        assert organize._last_auto_domain_by_key(history) == {}


class TestDetectCorrections:
    def test_finds_a_real_correction(self):
        last_auto = {"a": "systems"}
        locked = [{"text": "item a", "ref_id": "a", "domain": "webdev"}]
        out = organize.detect_corrections(locked, last_auto)
        assert out == [{"text": "item a", "from": "systems", "to": "webdev"}]

    def test_same_domain_is_not_a_correction(self):
        last_auto = {"a": "webdev"}
        locked = [{"text": "item a", "ref_id": "a", "domain": "webdev"}]
        assert organize.detect_corrections(locked, last_auto) == []

    def test_no_prior_history_is_not_a_correction(self):
        # locked item with no matching history entry -> nothing to compare, no false signal
        last_auto = {}
        locked = [{"text": "item a", "ref_id": "a", "domain": "webdev"}]
        assert organize.detect_corrections(locked, last_auto) == []

    def test_multiple_locked_items_mixed(self):
        last_auto = {"a": "systems", "b": "career", "c": "health"}
        locked = [
            {"text": "item a", "ref_id": "a", "domain": "webdev"},   # correction
            {"text": "item b", "ref_id": "b", "domain": "career"},   # no change
            {"text": "item c", "ref_id": "c", "domain": "finance"},  # correction
        ]
        out = organize.detect_corrections(locked, last_auto)
        assert len(out) == 2
        assert {"text": "item a", "from": "systems", "to": "webdev"} in out
        assert {"text": "item c", "from": "health", "to": "finance"} in out

    def test_empty_locked_list(self):
        assert organize.detect_corrections([], {"a": "webdev"}) == []


class TestFewshotBlock:
    def test_empty_corrections_gives_empty_string(self):
        assert organize._fewshot_block([]) == ""

    def test_renders_correction(self):
        corrections = [{"text": "item a", "from": "systems", "to": "webdev"}]
        block = organize._fewshot_block(corrections)
        assert "item a" in block
        assert "systems" in block
        assert "webdev" in block
        assert "CORRECTED" in block

    def test_caps_at_fewshot_max(self):
        corrections = [{"text": f"item {i}", "from": "a", "to": "b"} for i in range(20)]
        block = organize._fewshot_block(corrections)
        # count occurrences of "item " lines rendered
        rendered_count = sum(1 for i in range(20) if f'"item {i}"' in block)
        assert rendered_count == organize.FEWSHOT_MAX

    def test_caps_keep_most_recent(self):
        corrections = [{"text": f"item {i}", "from": "a", "to": "b"} for i in range(10)]
        block = organize._fewshot_block(corrections)
        # the earliest ones (0..4) should be dropped, most recent (5..9) kept
        assert '"item 0"' not in block
        assert '"item 9"' in block
