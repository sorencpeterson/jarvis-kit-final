#!/usr/bin/env python3
"""Unit tests for agents/backup_verify.py's _check_store_files (R3#6).

_check_store_files is pure given a directory of files, so these tests write a
fake "clone" directory under tmp_path and never touch git or the real store.

Run: .venv/bin/python -m pytest tests/test_backup_verify.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "agents"):
    sys.path.insert(0, str(p))

import backup_verify  # noqa: E402


def _write_all(clone_path: Path, contents: dict[str, str]):
    """contents: {relative_path: raw_text}. Any KEY_STORE_FILES entry not given
    a value is skipped (treated as missing), matching real clone shapes where a
    file can legitimately not exist."""
    for rel, text in contents.items():
        p = clone_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


class TestCheckStoreFilesNonEmpty:
    """R3#6: a zero-byte (or all-blank) store file used to pass "present and
    parseable" vacuously -- the parse loop never runs on empty content, never
    raises, so it silently "passed" as an intact backup of business data that
    should never legitimately be at zero records."""

    def test_zero_byte_file_reported_corrupt_not_ok(self, tmp_path):
        contents = {rel: "" for rel in backup_verify.KEY_STORE_FILES}
        _write_all(tmp_path, contents)
        result = backup_verify._check_store_files(tmp_path)
        assert result["missing"] == []
        assert sorted(result["corrupt"]) == sorted(backup_verify.KEY_STORE_FILES)

    def test_all_blank_lines_reported_corrupt_not_ok(self, tmp_path):
        contents = {rel: "\n\n\n" for rel in backup_verify.KEY_STORE_FILES}
        _write_all(tmp_path, contents)
        result = backup_verify._check_store_files(tmp_path)
        assert sorted(result["corrupt"]) == sorted(backup_verify.KEY_STORE_FILES)

    def test_file_with_one_real_record_passes(self, tmp_path):
        contents = {rel: json.dumps({"id": "a1"}) + "\n" for rel in backup_verify.KEY_STORE_FILES}
        _write_all(tmp_path, contents)
        result = backup_verify._check_store_files(tmp_path)
        assert result["missing"] == [] and result["corrupt"] == []

    def test_mixed_good_empty_and_corrupt(self, tmp_path):
        good, empty, unparseable = backup_verify.KEY_STORE_FILES[0], \
            backup_verify.KEY_STORE_FILES[1], backup_verify.KEY_STORE_FILES[2]
        missing = backup_verify.KEY_STORE_FILES[3]
        contents = {
            good: json.dumps({"id": "x"}) + "\n",
            empty: "",
            unparseable: "{not valid json\n",
        }
        _write_all(tmp_path, contents)
        result = backup_verify._check_store_files(tmp_path)
        assert result["missing"] == [missing]
        assert sorted(result["corrupt"]) == sorted([empty, unparseable])
        assert good not in result["corrupt"] and good not in result["missing"]

    def test_missing_file_reported_missing_not_corrupt(self, tmp_path):
        # write nothing at all -- every KEY_STORE_FILES entry is absent
        result = backup_verify._check_store_files(tmp_path)
        assert sorted(result["missing"]) == sorted(backup_verify.KEY_STORE_FILES)
        assert result["corrupt"] == []

    def test_verify_clone_status_reflects_zero_byte_files(self, tmp_path, monkeypatch):
        """End-to-end through _verify_clone's status derivation: a real clone
        (git succeeds, KEY_FILES present) with an empty store file must report
        corrupt_store_files, not ok."""
        clone_path = tmp_path / "clone"
        clone_path.mkdir()
        for f in backup_verify.KEY_FILES:
            p = clone_path / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# stub\n")
        contents = {rel: json.dumps({"id": "a1"}) + "\n" for rel in backup_verify.KEY_STORE_FILES}
        contents[backup_verify.KEY_STORE_FILES[0]] = ""  # one store file is a zero-byte shell
        _write_all(clone_path, contents)

        class _FakeRun:
            returncode = 0
            stderr = ""

        monkeypatch.setattr(backup_verify.subprocess, "run", lambda *a, **k: _FakeRun())
        monkeypatch.setattr(backup_verify.tempfile, "mkdtemp", lambda *a, **k: str(tmp_path))
        monkeypatch.setattr(backup_verify.shutil, "rmtree", lambda *a, **k: None)
        result = backup_verify._verify_clone("fake://remote")
        assert result["status"] == "corrupt_store_files"
        assert backup_verify.KEY_STORE_FILES[0] in result["store_files_corrupt"]
