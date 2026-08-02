#!/usr/bin/env python3
"""geo_check fail-closed guarantee (FABLE-MEGA-BACKLOG D8 missing-test #1).

agents/geo_check.py is the exit-IP gate the job-apply operator runs BEFORE
submitting a single form: Alex applies to US-remote roles from Europe, so a
lookup failure must mean NOT SAFE (exit 3), never allowed-by-default. Every
test here monkeypatches urllib — no real network is ever touched.

Run: .venv/bin/python -m pytest tests/test_geo_check.py -q
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import geo_check  # noqa: E402


class _Resp:
    """Minimal stand-in for urlopen's response: just .read() -> bytes."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


def _urlopen_returning(*payloads):
    """Fake urlopen that serves payloads in order (one per provider call).
    A payload that is an Exception instance/class is raised instead."""
    queue = list(payloads)
    calls = []

    def fake(url, timeout=None):
        calls.append({"url": url, "timeout": timeout})
        item = queue.pop(0) if queue else TimeoutError("exhausted")
        if isinstance(item, BaseException) or (isinstance(item, type) and issubclass(item, BaseException)):
            raise item
        return _Resp(item)

    fake.calls = calls
    return fake


@pytest.fixture()
def no_network(monkeypatch):
    """Belt-and-braces: any unpatched urlopen call in this file explodes loudly."""

    def boom(*a, **k):
        raise AssertionError("test tried to hit the real network")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    return monkeypatch


class TestFailClosed:
    def test_all_providers_down_means_not_allowed(self, no_network):
        # Both providers raise (network down / DNS dead) -> ok MUST be False.
        no_network.setattr(urllib.request, "urlopen",
                           _urlopen_returning(TimeoutError("t/o"), OSError("conn refused")))
        r = geo_check.check()
        assert r["ok"] is False
        assert "error" in r  # and it says WHY, not just a bare flag

    def test_garbage_response_means_not_allowed(self, no_network):
        # Provider answers with an HTML error page / non-JSON garbage on BOTH tries.
        no_network.setattr(urllib.request, "urlopen",
                           _urlopen_returning(b"<html>503</html>", b"\x00\xffnot json"))
        assert geo_check.check()["ok"] is False

    def test_json_without_country_means_not_allowed(self, no_network):
        # Valid JSON but no usable country field (provider changed its schema).
        no_network.setattr(urllib.request, "urlopen",
                           _urlopen_returning(json.dumps({"ip": "1.2.3.4"}).encode(),
                                              json.dumps({"status": "rate limited"}).encode()))
        assert geo_check.check()["ok"] is False

    def test_empty_country_string_means_not_allowed(self, no_network):
        no_network.setattr(urllib.request, "urlopen",
                           _urlopen_returning(json.dumps({"country": ""}).encode(),
                                              json.dumps({"country": ""}).encode()))
        assert geo_check.check()["ok"] is False

    def test_lookup_uses_a_timeout(self, no_network):
        # An 8s timeout is passed to urlopen so a hung provider can't stall the
        # gate forever (a hang that never resolves would otherwise block the
        # apply run instead of failing closed).
        fake = _urlopen_returning(json.dumps({"country": "US"}).encode())
        no_network.setattr(urllib.request, "urlopen", fake)
        geo_check.check()
        assert fake.calls and fake.calls[0]["timeout"] == 8


class TestCountryDecision:
    def test_us_is_allowed(self, no_network):
        no_network.setattr(urllib.request, "urlopen",
                           _urlopen_returning(json.dumps({"country": "US", "region": "TX",
                                                          "city": "Austin", "ip": "1.2.3.4"}).encode()))
        r = geo_check.check()
        assert r["ok"] is True and r["country"] == "US"

    def test_lowercase_us_is_uppercased_and_allowed(self, no_network):
        no_network.setattr(urllib.request, "urlopen",
                           _urlopen_returning(json.dumps({"country": "us"}).encode()))
        r = geo_check.check()
        assert r["ok"] is True and r["country"] == "US"

    def test_non_us_country_is_not_allowed(self, no_network):
        # The exact scenario the gate exists for: a European exit IP.
        no_network.setattr(urllib.request, "urlopen",
                           _urlopen_returning(json.dumps({"country": "DE", "city": "Berlin"}).encode()))
        r = geo_check.check()
        assert r["ok"] is False and r["country"] == "DE"

    def test_second_provider_rescues_first_provider_outage(self, no_network):
        # Provider redundancy: first one down does NOT fail the gate when the
        # second answers -- and ifconfig.co's `country_iso` field is honored.
        no_network.setattr(urllib.request, "urlopen",
                           _urlopen_returning(OSError("provider 1 down"),
                                              json.dumps({"country_iso": "US"}).encode()))
        r = geo_check.check()
        assert r["ok"] is True and r["country"] == "US"


class TestExitCodes:
    """The apply playbook shells out and branches on the exit code, so the
    documented contract (0=US, 2=not-US, 3=lookup-failed) is load-bearing."""

    def test_exit_0_when_us(self, no_network, capsys):
        no_network.setattr(urllib.request, "urlopen",
                           _urlopen_returning(json.dumps({"country": "US"}).encode()))
        assert geo_check.main() == 0

    def test_exit_2_when_not_us(self, no_network, capsys):
        no_network.setattr(urllib.request, "urlopen",
                           _urlopen_returning(json.dumps({"country": "FR"}).encode()))
        assert geo_check.main() == 2

    def test_exit_3_when_lookup_failed(self, no_network, capsys):
        # Fail-closed at the shell level too: 3 is non-zero, so `geo_check || stop`
        # stops the apply run on a network hiccup instead of green-lighting it.
        no_network.setattr(urllib.request, "urlopen",
                           _urlopen_returning(TimeoutError(), TimeoutError()))
        assert geo_check.main() == 3

    def test_main_prints_machine_readable_json(self, no_network, capsys):
        no_network.setattr(urllib.request, "urlopen",
                           _urlopen_returning(json.dumps({"country": "US"}).encode()))
        geo_check.main()
        out = json.loads(capsys.readouterr().out.strip())
        assert out["ok"] is True
