#!/usr/bin/env python3
"""Send-gate regression tests (2026-07-06 Fable audit).

The rule these defend: every outward send (job applications, LinkedIn actions,
GHL sends) is gated behind Alex's explicit confirm or a config knob he flips.
A regression here means chat could fire a real send with no confirm bubble.

Run: .venv/bin/python -m pytest tests/test_send_gates.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import commander  # noqa: E402


class TestChatSendGates:
    def test_job_apply_remaps_to_confirm_lane(self):
        assert commander._gate_action("launch", {"which": "job_apply"}) == "launch_send"

    def test_net_run_remaps_to_confirm_lane(self):
        assert commander._gate_action("launch", {"which": "net_run"}) == "launch_send"

    def test_scans_stay_auto_run(self):
        assert commander._gate_action("launch", {"which": "job_scan"}) == "launch"
        assert commander._gate_action("launch", {"which": "net_scan"}) == "launch"

    def test_gate_handles_missing_args(self):
        assert commander._gate_action("launch", None) == "launch"
        assert commander._gate_action("add_todo", {}) == "add_todo"

    def test_launch_send_lives_in_outward(self):
        assert "launch_send" in commander.OUTWARD
        assert "launch_send" not in commander.SAFE

    def test_outward_actions_never_leak_into_safe(self):
        # the SAFE dict must never grow a direct send-capable action
        for name in commander.OUTWARD:
            assert name not in commander.SAFE, f"outward action '{name}' is auto-runnable"


class TestAuthNeverFailsOpen:
    def test_brain_token_is_always_set(self):
        # server.py mints and persists a token when .env lacks one; empty means
        # every /api route would be unauthenticated for a local web page (CSRF).
        import server
        assert server._BRAIN_TOKEN, "auth gate is open: _BRAIN_TOKEN is empty"
