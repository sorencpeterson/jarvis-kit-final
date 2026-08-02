#!/usr/bin/env python3
"""Mullvad US auto-connect (2026-07-11). Pins: already-US = instant ok (no reconnect),
non-US triggers set+connect and reports the relay, and every failure mode returns
ok=False WITHOUT raising (fail-soft: the server geo-gate is the real safety net)."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import vpn  # noqa: E402


def _fake_run(script):
    calls = []
    def run(args, timeout=15):
        calls.append(args)
        return script(args, calls)
    return run, calls


class TestEnsureUS:
    def test_already_us_is_instant_no_reconnect(self, monkeypatch):
        run, calls = _fake_run(lambda a, c: "Connected\n    Relay:                  us-chi-wg-307\n")
        monkeypatch.setattr(vpn, "_run", run)
        r = vpn.ensure_us()
        assert r["ok"] and "us-" in r["relay"]
        assert ["connect"] not in calls and ["relay", "set", "location", "us"] not in calls

    def test_non_us_connects_then_reports_us(self, monkeypatch):
        state = {"phase": 0}
        def script(args, c):
            if args[:1] == ["status"]:
                # first status = disconnected, after connect = US
                return ("Connected\n    Relay:                  us-nyc-wg-1\n"
                        if state["phase"] else "Disconnected\n")
            if args[:1] == ["connect"]:
                state["phase"] = 1
            return "ok"
        run, calls = _fake_run(script)
        monkeypatch.setattr(vpn, "_run", run)
        monkeypatch.setattr(vpn.time, "sleep", lambda s: None)
        r = vpn.ensure_us(timeout=10)
        assert r["ok"] and r["relay"].startswith("us-")
        assert ["relay", "set", "location", "us"] in calls and ["connect"] in calls

    def test_connect_failure_is_soft_false(self, monkeypatch):
        run, _ = _fake_run(lambda a, c: "Disconnected\n" if a[:1] == ["status"] else "ok")
        monkeypatch.setattr(vpn, "_run", run)
        monkeypatch.setattr(vpn.time, "sleep", lambda s: None)
        r = vpn.ensure_us(timeout=1)
        assert r["ok"] is False and "detail" in r  # never raised

    def test_relay_set_error_is_soft_false(self, monkeypatch):
        run, _ = _fake_run(lambda a, c: "Disconnected\n" if a[:1] == ["status"]
                           else "__err__ TimeoutExpired")
        monkeypatch.setattr(vpn, "_run", run)
        r = vpn.ensure_us(timeout=1)
        assert r["ok"] is False

    def test_status_parses_us_flag(self, monkeypatch):
        monkeypatch.setattr(vpn, "_run",
                            lambda a, timeout=15: "Connected\n    Relay:                  se-sto-wg-1\n")
        assert vpn.status()["us"] is False  # connected but Sweden, not US
