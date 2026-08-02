#!/usr/bin/env python3
"""Pytest suite for agents/schema_docs.py's pure inference/rendering logic.
No store I/O for the unit tests (synthetic records); a real full-store run
was verified manually against the live 89-file store/ directory.

Run: .venv/bin/python -m pytest tests/test_schema_docs.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "agents"):
    sys.path.insert(0, str(p))

import schema_docs as sd  # noqa: E402


class TestTypeName:
    def test_none(self):
        assert sd._type_name(None) == "null"

    def test_bool_before_int(self):
        # bool is a subclass of int in Python — must check bool FIRST or every
        # bool gets misreported as "int"
        assert sd._type_name(True) == "bool"
        assert sd._type_name(False) == "bool"

    def test_int(self):
        assert sd._type_name(5) == "int"

    def test_float(self):
        assert sd._type_name(5.5) == "float"

    def test_str(self):
        assert sd._type_name("x") == "str"

    def test_list(self):
        assert sd._type_name([1, 2]) == "list"

    def test_dict(self):
        assert sd._type_name({"a": 1}) == "dict"


class TestExample:
    def test_truncates_long_strings(self):
        long_str = "x" * 200
        out = sd._example(long_str)
        assert len(out) <= sd.EXAMPLE_MAX_CHARS + 3  # + "..."
        assert out.endswith("...")

    def test_short_string_unchanged(self):
        assert sd._example("short") == "short"

    def test_removes_newlines(self):
        assert "\n" not in sd._example("line1\nline2")

    def test_non_string_json_encoded(self):
        assert sd._example(42) == "42"
        assert sd._example(True) == "true"


class TestInferSchema:
    def test_basic_field_presence(self):
        records = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
        result = sd.infer_schema(records)
        assert result["total_sampled"] == 2
        assert result["fields"]["a"]["present"] == 2
        assert result["fields"]["a"]["types"] == {"int": 2}

    def test_missing_field_lower_presence(self):
        records = [{"a": 1, "b": "x"}, {"a": 2}]
        result = sd.infer_schema(records)
        assert result["fields"]["a"]["present"] == 2
        assert result["fields"]["b"]["present"] == 1

    def test_mixed_types_tracked(self):
        records = [{"a": 1}, {"a": None}, {"a": "text"}]
        result = sd.infer_schema(records)
        types = result["fields"]["a"]["types"]
        assert types == {"int": 1, "null": 1, "str": 1}

    def test_empty_records(self):
        result = sd.infer_schema([])
        assert result["total_sampled"] == 0
        assert result["fields"] == {}

    def test_example_skips_null_and_empty(self):
        records = [{"a": None}, {"a": ""}, {"a": "real value"}]
        result = sd.infer_schema(records)
        assert result["fields"]["a"]["example"] == "real value"

    def test_example_none_when_all_empty(self):
        records = [{"a": None}, {"a": ""}]
        result = sd.infer_schema(records)
        assert result["fields"]["a"]["example"] is None


class TestShouldSkip:
    def test_skips_lock_files(self):
        assert sd._should_skip(Path("todos.lock")) is True

    def test_skips_db_files(self):
        assert sd._should_skip(Path("recall.db")) is True

    def test_skips_named_files(self):
        assert sd._should_skip(Path("todos.schema.json")) is True

    def test_does_not_skip_normal_jsonl(self):
        assert sd._should_skip(Path("replies.jsonl")) is False


class TestReadJsonlTail:
    def test_gets_last_n_records_in_order(self, tmp_path):
        p = tmp_path / "test.jsonl"
        p.write_text('{"i": 1}\n{"i": 2}\n{"i": 3}\n{"i": 4}\n')
        records = sd._read_jsonl_tail(p, 2)
        assert [r["i"] for r in records] == [3, 4]

    def test_skips_malformed_lines(self, tmp_path):
        p = tmp_path / "test.jsonl"
        p.write_text('{"i": 1}\nnot json\n{"i": 2}\n')
        records = sd._read_jsonl_tail(p, 10)
        assert [r["i"] for r in records] == [1, 2]

    def test_missing_file(self, tmp_path):
        assert sd._read_jsonl_tail(tmp_path / "nonexistent.jsonl", 10) == []

    def test_skips_non_dict_records(self, tmp_path):
        p = tmp_path / "test.jsonl"
        p.write_text('[1,2,3]\n{"i": 1}\n')
        records = sd._read_jsonl_tail(p, 10)
        assert records == [{"i": 1}]


class TestRenderMarkdown:
    def test_renders_a_table_for_each_file(self):
        docs = {"foo.jsonl": {"total_sampled": 1, "fields": {
            "a": {"types": {"str": 1}, "present": 1, "example": "hi"}}}}
        md = sd.render_markdown(docs)
        assert "## foo.jsonl" in md
        assert "| a |" in md

    def test_renders_note_for_unreadable(self):
        docs = {"bad.json": {"total_sampled": 0, "fields": {}, "note": "unreadable"}}
        md = sd.render_markdown(docs)
        assert "unreadable" in md

    def test_renders_empty_note(self):
        docs = {"empty.jsonl": {"total_sampled": 0, "fields": {}}}
        md = sd.render_markdown(docs)
        assert "no records to sample" in md

    def test_escapes_pipe_in_example(self):
        docs = {"foo.jsonl": {"total_sampled": 1, "fields": {
            "a": {"types": {"str": 1}, "present": 1, "example": "has|pipe"}}}}
        md = sd.render_markdown(docs)
        assert "has\\|pipe" in md
