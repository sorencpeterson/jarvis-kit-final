#!/usr/bin/env python3
"""DNS-rebinding TOCTOU pin tests for net_guard.safe_urlopen (red-team #3).

The attack: a hostname resolves to a public IP when public_url_ok validates it, then
the attacker flips the DNS record to 127.0.0.1 before urllib re-resolves at connect
time. The fix pins the socket to the IPs validated in that same single resolution.

These tests need no network: socket.getaddrinfo is monkeypatched to simulate the DNS
flip, and socket.create_connection is monkeypatched to capture where the connection
would go and to serve canned HTTP bytes through a fake socket.

Run: .venv/bin/python -m pytest tests/test_net_guard_pin.py -v
"""
from __future__ import annotations

import io
import socket
import ssl
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import net_guard  # noqa: E402

PUB_A = "93.184.216.34"   # stand-in public IP (example.com)
PUB_B = "8.8.8.8"         # second public IP for redirect hops


class _FakeSock:
    """Just enough socket for http.client: replays one canned HTTP response."""
    def __init__(self, payload: bytes):
        self._payload = payload
        self.sent = b""

    def makefile(self, mode="rb", *a, **kw):
        return io.BytesIO(self._payload)

    def sendall(self, data):
        self.sent += bytes(data)

    def close(self):
        pass

    def settimeout(self, t):
        pass


def _gai_entry(ip: str, port=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


OK_200 = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi"


class TestRebindPin:
    def test_flip_after_validation_still_connects_to_validated_ip(self, monkeypatch):
        """First resolution public, every later resolution 127.0.0.1: the connection
        must go to the FIRST (validated) IP, never the flipped one."""
        calls = {"n": 0}

        def fake_gai(host, port, *a, **kw):
            calls["n"] += 1
            return _gai_entry(PUB_A if calls["n"] == 1 else "127.0.0.1")

        connected = []

        def fake_cc(addr, timeout=None, source_address=None):
            connected.append(addr[0])
            return _FakeSock(OK_200)

        monkeypatch.setattr(socket, "getaddrinfo", fake_gai)
        monkeypatch.setattr(socket, "create_connection", fake_cc)
        with net_guard.safe_urlopen("http://rebind.test/") as r:
            assert r.status == 200
            assert r.read() == b"hi"
        assert connected == [PUB_A], "connect must use the pinned validated IP"
        assert "127.0.0.1" not in connected

    def test_flipped_record_refused_on_next_call(self, monkeypatch):
        """After the flip, a fresh safe_urlopen re-resolves, sees 127.0.0.1, refuses."""
        monkeypatch.setattr(socket, "getaddrinfo",
                            lambda *a, **kw: _gai_entry("127.0.0.1"))
        connected = []
        monkeypatch.setattr(socket, "create_connection",
                            lambda addr, timeout=None, source_address=None:
                            connected.append(addr[0]) or _FakeSock(OK_200))
        with pytest.raises(ValueError, match="blocked"):
            net_guard.safe_urlopen("http://rebind.test/")
        assert connected == [], "no socket may open for a non-public resolution"

    def test_mixed_public_private_resolution_refused(self, monkeypatch):
        """A host that resolves to one public and one private IP is rejected whole."""
        monkeypatch.setattr(
            socket, "getaddrinfo",
            lambda *a, **kw: _gai_entry(PUB_A) + _gai_entry("10.0.0.5"))
        with pytest.raises(ValueError, match="blocked"):
            net_guard.safe_urlopen("http://mixed.test/")


class TestRedirectRepin:
    def test_redirect_to_internal_ip_blocked(self, monkeypatch):
        """A public host 302ing to http://127.0.0.1 must raise, never connect there."""
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: _gai_entry(PUB_A))
        connected = []
        payload = (b"HTTP/1.1 302 Found\r\n"
                   b"Location: http://127.0.0.1:8765/steal\r\n"
                   b"Content-Length: 0\r\n\r\n")

        def fake_cc(addr, timeout=None, source_address=None):
            connected.append(addr[0])
            return _FakeSock(payload)

        monkeypatch.setattr(socket, "create_connection", fake_cc)
        with pytest.raises(urllib.error.HTTPError, match="blocked"):
            net_guard.safe_urlopen("http://pub.test/")
        assert connected == [PUB_A]

    def test_public_redirect_hop_is_reresolved_and_repinned(self, monkeypatch):
        """a.test 302s to b.test: hop must trigger a fresh resolution of b.test and
        the second connection must go to b.test's own validated IP."""
        resolved = []

        def fake_gai(host, port, *a, **kw):
            resolved.append(host)
            return _gai_entry({"a.test": PUB_A, "b.test": PUB_B}[host])

        payloads = [b"HTTP/1.1 302 Found\r\nLocation: http://b.test/next\r\n"
                    b"Content-Length: 0\r\n\r\n",
                    b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\ndone"]
        connected = []

        def fake_cc(addr, timeout=None, source_address=None):
            connected.append(addr[0])
            return _FakeSock(payloads[len(connected) - 1])

        monkeypatch.setattr(socket, "getaddrinfo", fake_gai)
        monkeypatch.setattr(socket, "create_connection", fake_cc)
        with net_guard.safe_urlopen("http://a.test/") as r:
            assert r.read() == b"done"
        assert connected == [PUB_A, PUB_B]
        assert "b.test" in resolved, "hop host must be re-resolved before its connect"

    def test_redirect_hop_that_flips_private_is_refused(self, monkeypatch):
        """Hop validates public in the redirect handler, then flips to 10.0.0.5 before
        the pin resolution: the loop must refuse, and never connect to the hop."""
        seen = {}

        def fake_gai(host, port, *a, **kw):
            seen[host] = seen.get(host, 0) + 1
            if host == "a.test":
                return _gai_entry(PUB_A)
            return _gai_entry(PUB_B if seen[host] == 1 else "10.0.0.5")

        connected = []
        payload = (b"HTTP/1.1 302 Found\r\nLocation: http://b.test/\r\n"
                   b"Content-Length: 0\r\n\r\n")

        def fake_cc(addr, timeout=None, source_address=None):
            connected.append(addr[0])
            return _FakeSock(payload)

        monkeypatch.setattr(socket, "getaddrinfo", fake_gai)
        monkeypatch.setattr(socket, "create_connection", fake_cc)
        with pytest.raises(ValueError, match="blocked"):
            net_guard.safe_urlopen("http://a.test/")
        assert connected == [PUB_A], "flipped hop must never receive a connection"

    def test_redirect_loop_capped(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: _gai_entry(PUB_A))
        payload = (b"HTTP/1.1 302 Found\r\nLocation: http://a.test/again\r\n"
                   b"Content-Length: 0\r\n\r\n")
        monkeypatch.setattr(socket, "create_connection",
                            lambda addr, timeout=None, source_address=None: _FakeSock(payload))
        with pytest.raises(ValueError, match="too many redirects"):
            net_guard.safe_urlopen("http://a.test/")


class TestTLSPinDetails:
    def test_https_connect_pins_ip_but_keeps_sni_on_hostname(self, monkeypatch):
        """The socket goes to the pinned IP; wrap_socket must still get the ORIGINAL
        hostname so SNI and certificate verification are unweakened."""
        captured = {}

        class _Ctx:
            check_hostname = True

            def wrap_socket(self, sock, server_hostname=None, **kw):
                captured["server_hostname"] = server_hostname
                return sock

        def fake_cc(addr, timeout=None, source_address=None):
            captured["addr"] = addr
            return _FakeSock(b"")

        monkeypatch.setattr(socket, "create_connection", fake_cc)
        conn = net_guard._PinnedHTTPSConnection(
            "example.com", pinned_ips=[PUB_A], context=_Ctx(), timeout=5)
        conn.connect()
        assert captured["addr"][0] == PUB_A
        assert captured["server_hostname"] == "example.com"

    def test_https_handler_context_verifies_certs(self):
        h = net_guard._PinnedHTTPSHandler([PUB_A])
        assert h._ctx.check_hostname is True
        assert h._ctx.verify_mode == ssl.CERT_REQUIRED

    def test_host_header_is_hostname_not_ip(self, monkeypatch):
        """The request on the wire must say Host: <hostname>, not the pinned IP."""
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: _gai_entry(PUB_A))
        socks = []

        def fake_cc(addr, timeout=None, source_address=None):
            s = _FakeSock(OK_200)
            socks.append(s)
            return s

        monkeypatch.setattr(socket, "create_connection", fake_cc)
        with net_guard.safe_urlopen("http://pinned-host.test/page") as r:
            r.read()
        assert b"Host: pinned-host.test" in socks[0].sent
        assert PUB_A.encode() not in socks[0].sent


class TestResolvePinHelpers:
    def test_ip_literal_pins_to_itself(self):
        ips, why = net_guard._resolve_pin("http://93.184.216.34/")
        assert ips == [PUB_A] and why == ""

    def test_internal_literal_rejected(self):
        ips, why = net_guard._resolve_pin("http://169.254.169.254/latest/")
        assert ips is None and "non-public" in why

    def test_public_url_ok_unchanged_contract(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: _gai_entry(PUB_A))
        assert net_guard.public_url_ok("http://fine.test/") == (True, "")
        assert not net_guard.public_url_ok("file:///etc/passwd")[0]
        assert not net_guard.public_url_ok("http://127.0.0.1/")[0]

    def test_head_method_passthrough(self, monkeypatch):
        """qa.py uses method='HEAD' for link checks; the verb must reach the wire."""
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: _gai_entry(PUB_A))
        socks = []

        def fake_cc(addr, timeout=None, source_address=None):
            s = _FakeSock(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
            socks.append(s)
            return s

        monkeypatch.setattr(socket, "create_connection", fake_cc)
        with net_guard.safe_urlopen("http://head.test/x", method="HEAD") as r:
            assert r.status == 200
        assert socks[0].sent.startswith(b"HEAD /x HTTP/1.1")
