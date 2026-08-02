#!/usr/bin/env python3
"""Pytest suite for agents/brainlib.py — pure functions only, no LLM/network/GHL.

Run: .venv/bin/python -m pytest tests/test_brainlib.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import brainlib  # noqa: E402


# ---- normalize_name (E378) ----

class TestNormalizeName:
    def test_basic_lowercase(self):
        assert brainlib.normalize_name("Braydon Bergeson") == "braydon bergeson"

    def test_mixed_case_matches(self):
        assert brainlib.normalize_name("BRAYDON BERGESON") == brainlib.normalize_name("braydon bergeson")

    def test_strips_suffix(self):
        assert brainlib.normalize_name("Braydon Bergeson Jr.") == "braydon bergeson"

    def test_strips_title(self):
        assert brainlib.normalize_name("Dr. Jane Smith") == "jane smith"

    def test_collapses_whitespace(self):
        assert brainlib.normalize_name("  Braydon   Bergeson  ") == "braydon bergeson"

    def test_none_returns_empty(self):
        assert brainlib.normalize_name(None) == ""

    def test_empty_returns_empty(self):
        assert brainlib.normalize_name("") == ""

    def test_keeps_hyphen_and_apostrophe(self):
        assert brainlib.normalize_name("Mary-Jane O'Brien") == "mary-jane o'brien"

    def test_two_different_people_differ(self):
        assert brainlib.normalize_name("Braydon Bergeson") != brainlib.normalize_name("Brandon Bergeson")


class TestDisplayName:
    def test_title_cases(self):
        assert brainlib.display_name("braydon bergeson") == "Braydon Bergeson"

    def test_empty(self):
        assert brainlib.display_name(None) == ""
        assert brainlib.display_name("") == ""


# ---- fmt_phone (E380) ----

class TestFmtPhone:
    def test_with_country_code(self):
        assert brainlib.fmt_phone("+14155551234") == "(415) 555-1234"

    def test_without_country_code(self):
        assert brainlib.fmt_phone("4155551234") == "(415) 555-1234"

    def test_with_punctuation(self):
        assert brainlib.fmt_phone("(415) 555-1234") == "(415) 555-1234"

    def test_empty_falls_back(self):
        assert brainlib.fmt_phone("") == "(555) 000-0000"

    def test_none_falls_back(self):
        assert brainlib.fmt_phone(None) == "(555) 000-0000"

    def test_garbage_falls_back_to_original(self):
        assert brainlib.fmt_phone("call me") == "call me"

    def test_matches_proposal_factory_pretty_phone(self):
        # brainlib.fmt_phone must stay behaviorally identical to
        # proposal_factory._pretty_phone (ported pattern, not imported).
        import proposal_factory
        for raw in ("+14155551234", "4155551234", "", "5551234"):
            assert brainlib.fmt_phone(raw) == proposal_factory._pretty_phone(raw)


# ---- fmt_currency (E379) ----

class TestFmtCurrency:
    def test_simple(self):
        assert brainlib.fmt_currency(800) == "$800"

    def test_thousands_comma(self):
        assert brainlib.fmt_currency(1234567) == "$1,234,567"

    def test_cents(self):
        assert brainlib.fmt_currency(19.5, cents=True) == "$19.50"

    def test_negative(self):
        assert brainlib.fmt_currency(-42) == "-$42"

    def test_none_is_zero(self):
        assert brainlib.fmt_currency(None) == "$0"

    def test_zero(self):
        assert brainlib.fmt_currency(0) == "$0"

    def test_string_number_coerces(self):
        assert brainlib.fmt_currency("800") == "$800"

    def test_unparseable_string_is_zero(self):
        assert brainlib.fmt_currency("not a number") == "$0"


# ---- dedupe_by / dedupe_by_email (E381) ----

class TestDedupeBy:
    def test_keeps_last_by_default(self):
        items = [{"id": 1, "v": "a"}, {"id": 1, "v": "b"}, {"id": 2, "v": "c"}]
        out = brainlib.dedupe_by(items, key=lambda x: x["id"])
        assert out == [{"id": 1, "v": "b"}, {"id": 2, "v": "c"}]

    def test_keep_first(self):
        items = [{"id": 1, "v": "a"}, {"id": 1, "v": "b"}]
        out = brainlib.dedupe_by(items, key=lambda x: x["id"], keep="first")
        assert out == [{"id": 1, "v": "a"}]

    def test_preserves_first_seen_order(self):
        items = [{"id": 2}, {"id": 1}, {"id": 2}]
        out = brainlib.dedupe_by(items, key=lambda x: x["id"])
        assert [x["id"] for x in out] == [2, 1]

    def test_empty_input(self):
        assert brainlib.dedupe_by([], key=lambda x: x) == []

    def test_invalid_keep_raises(self):
        import pytest
        with pytest.raises(ValueError):
            brainlib.dedupe_by([{"id": 1}], key=lambda x: x["id"], keep="middle")


class TestDedupeByEmail:
    def test_case_insensitive(self):
        out = brainlib.dedupe_by_email([{"email": "A@x.com", "v": 1}, {"email": "a@x.com", "v": 2}])
        assert out == [{"email": "a@x.com", "v": 2}]

    def test_no_email_items_pass_through_unmerged(self):
        items = [{"email": "", "v": 1}, {"email": "", "v": 2}]
        out = brainlib.dedupe_by_email(items)
        assert len(out) == 2

    def test_mixed_email_and_blank(self):
        items = [{"email": "a@x.com", "v": 1}, {"email": "", "v": 2}, {"email": "a@x.com", "v": 3}]
        out = brainlib.dedupe_by_email(items)
        assert len(out) == 2  # a@x.com collapses to 1, blank stays as its own


# ---- dedupe_feed_window (E325) ----

class TestDedupeFeedWindow:
    def test_collapses_within_window(self):
        entries = [
            {"title": "x", "ts": "2026-07-03T08:00:00+00:00"},
            {"title": "x", "ts": "2026-07-03T08:05:00+00:00"},
            {"title": "y", "ts": "2026-07-03T08:06:00+00:00"},
        ]
        out = brainlib.dedupe_feed_window(entries)
        assert len(out) == 2
        assert out[0]["count"] == 2
        assert out[1]["count"] == 1

    def test_outside_window_not_collapsed(self):
        entries = [
            {"title": "x", "ts": "2026-07-01T08:00:00+00:00"},
            {"title": "x", "ts": "2026-07-03T08:00:00+00:00"},
        ]
        out = brainlib.dedupe_feed_window(entries, window_hours=24)
        assert len(out) == 2
        assert all(o["count"] == 1 for o in out)

    def test_non_consecutive_same_title_not_collapsed(self):
        # x, y, x — the two x's are not adjacent, so both remain separate
        entries = [
            {"title": "x", "ts": "2026-07-03T08:00:00+00:00"},
            {"title": "y", "ts": "2026-07-03T08:01:00+00:00"},
            {"title": "x", "ts": "2026-07-03T08:02:00+00:00"},
        ]
        out = brainlib.dedupe_feed_window(entries)
        assert len(out) == 3

    def test_does_not_mutate_input(self):
        entries = [{"title": "x", "ts": "2026-07-03T08:00:00+00:00"}]
        snapshot = json.loads(json.dumps(entries))
        brainlib.dedupe_feed_window(entries)
        assert entries == snapshot

    def test_empty_input(self):
        assert brainlib.dedupe_feed_window([]) == []


# ---- read_jsonl_mmap (E350) ----

class TestReadJsonlMmap:
    def test_missing_file_returns_empty(self, tmp_path):
        assert brainlib.read_jsonl_mmap(tmp_path / "nope.jsonl") == []

    def test_empty_file_returns_empty(self, tmp_path):
        p = tmp_path / "empty.jsonl"
        p.write_text("")
        assert brainlib.read_jsonl_mmap(p) == []

    def test_small_file_plain_path(self, tmp_path):
        p = tmp_path / "small.jsonl"
        p.write_text('{"a": 1}\n{"a": 2}\n')
        out = brainlib.read_jsonl_mmap(p, mmap_threshold_bytes=1_000_000)
        assert out == [{"a": 1}, {"a": 2}]

    def test_forces_mmap_path_with_low_threshold(self, tmp_path):
        p = tmp_path / "forced.jsonl"
        p.write_text('{"a": 1}\n{"a": 2}\n{"a": 3}\n')
        out = brainlib.read_jsonl_mmap(p, mmap_threshold_bytes=1)  # forces the mmap branch
        assert out == [{"a": 1}, {"a": 2}, {"a": 3}]

    def test_skips_malformed_lines(self, tmp_path):
        p = tmp_path / "bad.jsonl"
        p.write_text('{"a": 1}\nnot json\n{"a": 2}\n')
        out = brainlib.read_jsonl_mmap(p, mmap_threshold_bytes=1)
        assert out == [{"a": 1}, {"a": 2}]

    def test_skips_blank_lines(self, tmp_path):
        p = tmp_path / "blanks.jsonl"
        p.write_text('{"a": 1}\n\n\n{"a": 2}\n')
        out = brainlib.read_jsonl_mmap(p, mmap_threshold_bytes=1)
        assert out == [{"a": 1}, {"a": 2}]

    def test_mmap_and_plain_agree(self, tmp_path):
        p = tmp_path / "agree.jsonl"
        p.write_text("\n".join(json.dumps({"i": i}) for i in range(50)) + "\n")
        small = brainlib.read_jsonl_mmap(p, mmap_threshold_bytes=1_000_000)
        forced = brainlib.read_jsonl_mmap(p, mmap_threshold_bytes=1)
        assert small == forced


# ---- ConfigWatcher (E355 config hot-reload) ----

class TestConfigWatcher:
    def test_reads_initial_value(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"a": 1}))
        w = brainlib.ConfigWatcher(p)
        assert w.get("a") == 1

    def test_missing_key_returns_default(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"a": 1}))
        w = brainlib.ConfigWatcher(p)
        assert w.get("missing", "fallback") == "fallback"

    def test_missing_file_returns_default(self, tmp_path):
        w = brainlib.ConfigWatcher(tmp_path / "nonexistent.json")
        assert w.get("a", "fallback") == "fallback"

    def test_picks_up_live_edit_via_mtime(self, tmp_path):
        import os
        import time
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"a": 1}))
        w = brainlib.ConfigWatcher(p)
        assert w.get("a") == 1
        time.sleep(0.02)
        p.write_text(json.dumps({"a": 2}))
        os.utime(p, None)  # force a fresh mtime even on fast filesystems
        assert w.get("a") == 2

    def test_unchanged_file_skips_reparse(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"a": 1}))
        w = brainlib.ConfigWatcher(p)
        w.get("a")
        mtime_after_first = w._mtime
        w.get("a")
        w.get("a")
        assert w._mtime == mtime_after_first

    def test_all_returns_full_dict(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"a": 1, "b": 2}))
        w = brainlib.ConfigWatcher(p)
        assert w.all() == {"a": 1, "b": 2}

    def test_malformed_json_degrades_to_empty(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text("not valid json{{{")
        w = brainlib.ConfigWatcher(p)
        assert w.get("a", "fallback") == "fallback"
        assert w.all() == {}

    def test_accepts_string_path(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"a": 1}))
        w = brainlib.ConfigWatcher(str(p))
        assert w.get("a") == 1
