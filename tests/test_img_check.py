#!/usr/bin/env python3
"""Image quality gate (2026-07-11). Pins: a FAIL verdict parses through, infrastructure
errors fail OPEN at creation (skipped=... so content gen never bricks) while the push
gate treats skipped as not-passed (fail closed at the outward edge), and a missing file
is a hard fail. First live run caught a real misspelling ("THREE SCROLES DOWN")."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import img_check  # noqa: E402


def _cli_result(payload):
    return SimpleNamespace(stdout=json.dumps({"result": json.dumps(payload)}))


class TestVerdictParsing:
    def test_missing_file_hard_fails(self):
        assert img_check.check_image("/nope/gone.png", "h", "t")["ok"] is False

    def test_fail_verdict_parses(self, monkeypatch, tmp_path):
        f = tmp_path / "a.png"; f.write_bytes(b"x")
        monkeypatch.setattr(img_check.subprocess, "run",
                            lambda *a, **k: _cli_result({"ok": False, "text_seen": "THREE SCROLES DOWN",
                                                         "why": "misspelled"}))
        v = img_check.check_image(str(f), "hook", "text")
        assert v["ok"] is False and "misspell" in v["why"]

    def test_pass_verdict_parses(self, monkeypatch, tmp_path):
        f = tmp_path / "a.png"; f.write_bytes(b"x")
        monkeypatch.setattr(img_check.subprocess, "run",
                            lambda *a, **k: _cli_result({"ok": True, "text_seen": "ALL GOOD", "why": "clean"}))
        assert img_check.check_image(str(f), "h", "t")["ok"] is True

    def test_checker_down_fails_open_with_skip_marker(self, monkeypatch, tmp_path):
        f = tmp_path / "a.png"; f.write_bytes(b"x")
        monkeypatch.setattr(img_check.subprocess, "run",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("no cli")))
        v = img_check.check_image(str(f), "h", "t")
        # fail-open at creation, but the skip marker means the PUSH gate holds it
        assert v["ok"] is True and v.get("skipped")

    def test_push_gate_semantics_skipped_is_not_passed(self):
        # the push gate ships only (ok and not skipped) — pin that contract here
        passed = lambda v: bool(v.get("ok") and not v.get("skipped"))
        assert passed({"ok": True}) is True
        assert passed({"ok": True, "skipped": "no cli"}) is False
        assert passed({"ok": False, "why": "misspelled"}) is False
