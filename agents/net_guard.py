#!/usr/bin/env python3
"""SSRF guard: is a URL safe to fetch from the server / hand to a crawler?

Untrusted URLs reach outbound fetches from several places (a GHL contact's website
field, a scraped job's apply_url, the /api/audit box). Without a guard, an attacker who
can set one of those to http://127.0.0.1:8765, http://169.254.169.254 (cloud metadata),
or an internal Tailscale host turns our own server into their proxy (SSRF) or points a
PII-carrying browser operator at a phishing clone (2026-07-07 security audit S3/S4).

`public_url_ok(url)` returns (ok, reason). It requires an http(s) scheme and resolves
EVERY address the host maps to, rejecting if any is private/loopback/link-local/
reserved/multicast (DNS-rebinding-resistant: we check the resolved IPs, not the name).
Fail-closed: a resolution failure returns not-ok.

`safe_urlopen(url)` additionally PINS the connection to the exact IPs that were
validated (red-team #3, DNS-rebinding TOCTOU): validate-then-urlopen re-resolves at
connect time, so a fast-flip DNS record could pass validation as 1.2.3.4 and then
connect to 127.0.0.1. Here the socket connects to an IP from the single validation
resolution; the Host header stays the original hostname, and for HTTPS the TLS SNI and
certificate hostname verification also stay on the original hostname (default verified
ssl context, check_hostname on). Redirects are followed manually: every hop is
re-resolved, re-validated, and re-pinned the same way. Proxies from the environment are
deliberately ignored (a proxy would bypass the pin).
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlparse

_MAX_REDIRECTS = 10


def _ip_is_public(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    # unwrap IPv4-mapped IPv6 (::ffff:127.0.0.1) and 6to4/teredo so a mapped internal
    # address can't slip past the v6 flags (2026-07-07 adversarial re-audit)
    mapped = getattr(a, "ipv4_mapped", None)
    if mapped is not None:
        a = mapped
    # CGNAT / RFC6598 shared space (100.64.0.0/10) reports is_private=False but is
    # non-globally-routable internal infra, block it explicitly (red-team #3)
    if a.version == 4 and ipaddress.ip_address(int(a)) in ipaddress.ip_network("100.64.0.0/10"):
        return False
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_reserved
                or a.is_multicast or a.is_unspecified)


def _resolve_pin(url: str):
    """(pinned_ips, reason). One single DNS resolution: the address set we validate is
    the address set we hand to the socket, so a record that flips between validation and
    connect cannot redirect the connection (TOCTOU-safe). pinned_ips is a non-empty list
    of validated IP strings on success, None on rejection (reason says why)."""
    if not url or not isinstance(url, str):
        return None, "empty url"
    u = url.strip()
    # R2-39: urlparse and a browser's WHATWG URL parser disagree on backslash. Browsers
    # treat \ the same as / for http(s) (a "special" scheme), so
    # "http://127.0.0.1:8765\@example.com/" makes Chromium navigate to host 127.0.0.1
    # (the "\@example.com/" becomes path), while urlparse(...).hostname below reads the
    # SAME string as userinfo "127.0.0.1:8765\" @ host "example.com" -> validates as
    # public. That let a crafted URL pass this guard while a real browser actually loaded
    # the internal/token-carrying host. No legitimate http(s) URL needs a literal
    # backslash, so reject outright rather than try to replicate WHATWG normalization.
    if "\\" in u:
        return None, "backslash in url not allowed"
    if not u.startswith(("http://", "https://")):
        return None, "scheme must be http or https"
    try:
        host = urlparse(u).hostname
    except ValueError:
        return None, "unparseable url"
    if not host:
        return None, "no host"
    # a bare IP literal in the URL: check it directly, don't resolve
    try:
        ipaddress.ip_address(host)
        return ([host], "") if _ip_is_public(host) else (None, f"non-public IP {host}")
    except ValueError:
        pass  # it's a hostname, resolve below
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, socket.timeout, UnicodeError, OSError):
        return None, f"could not resolve {host}"
    if not infos:
        return None, f"no addresses for {host}"
    ips = list(dict.fromkeys(info[4][0] for info in infos))  # dedupe, keep order
    for ip in ips:
        if not _ip_is_public(ip):
            return None, f"{host} resolves to non-public {ip}"
    return ips, ""


def public_url_ok(url: str) -> tuple[bool, str]:
    """(ok, reason). ok=True only for an http(s) URL whose host resolves entirely to
    public IPs. Everything else (bad scheme, unresolvable, any internal IP) is rejected."""
    ips, why = _resolve_pin(url)
    return (True, "") if ips else (False, why)


def _connect_pinned(ips, port, timeout, source_address):
    """Open a TCP socket to the first reachable IP from the validated set. IP literals
    only: socket.create_connection's internal getaddrinfo never touches DNS for these,
    so no re-resolution can occur here."""
    err = None
    for ip in ips:
        try:
            return socket.create_connection((ip, port), timeout, source_address)
        except OSError as e:
            err = e
    if err is not None:
        raise err
    raise OSError("no pinned address to connect to")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection that connects to a pre-validated IP instead of re-resolving the
    hostname. Host header still carries the original hostname (set by urllib)."""
    def __init__(self, host, *args, pinned_ips=None, **kw):
        super().__init__(host, *args, **kw)
        self._pinned_ips = list(pinned_ips or [])

    def connect(self):
        self.sock = _connect_pinned(self._pinned_ips, self.port,
                                    self.timeout, self.source_address)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection pinned to a pre-validated IP. TLS SNI and certificate hostname
    verification stay on the ORIGINAL hostname (self.host), so the pin does not weaken
    TLS: the server must still present a valid cert for the name in the URL."""
    def __init__(self, host, *args, pinned_ips=None, **kw):
        super().__init__(host, *args, **kw)
        self._pinned_ips = list(pinned_ips or [])

    def connect(self):
        sock = _connect_pinned(self._pinned_ips, self.port,
                               self.timeout, self.source_address)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, pinned_ips):
        super().__init__()
        self._pinned_ips = pinned_ips

    def http_open(self, req):
        return self.do_open(_PinnedHTTPConnection, req, pinned_ips=self._pinned_ips)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, pinned_ips):
        super().__init__()
        self._pinned_ips = pinned_ips
        self._ctx = ssl.create_default_context()  # CERT_REQUIRED + check_hostname=True

    def https_open(self, req):
        return self.do_open(_PinnedHTTPSConnection, req,
                            pinned_ips=self._pinned_ips, context=self._ctx)


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """Re-validate EVERY redirect hop: a public URL that 302s to http://127.0.0.1
    would otherwise defeat a one-shot pre-check (urllib follows redirects by default).
    NOTE: this handler validates but does NOT pin; safe_urlopen uses _CaptureRedirect
    on top of this so each hop also gets its own pinned connection."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        ok, why = public_url_ok(newurl)
        if not ok:
            raise urllib.error.HTTPError(newurl, code, f"redirect to blocked host: {why}", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _RedirectSignal(Exception):
    """Internal: hands a validated redirect target back to safe_urlopen's hop loop so
    the next request gets its own re-resolve + re-validate + re-pin."""
    def __init__(self, url):
        super().__init__(url)
        self.url = url


class _CaptureRedirect(_GuardedRedirect):
    """Reject blocked hops exactly like _GuardedRedirect (HTTPError, fail-closed), but
    never let urllib follow the hop itself: raise _RedirectSignal so safe_urlopen
    restarts the request with a freshly pinned connection for the new host."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        ok, why = public_url_ok(newurl)
        if not ok:
            raise urllib.error.HTTPError(newurl, code, f"redirect to blocked host: {why}", headers, fp)
        try:
            fp.read()
            fp.close()
        except Exception:  # noqa: BLE001
            pass
        raise _RedirectSignal(newurl)


def safe_urlopen(url: str, timeout: int = 12, headers: dict | None = None,
                 method: str = "GET"):
    """urlopen that refuses internal hosts on the initial URL AND on every redirect hop,
    and pins each connection to the exact IPs validated in that hop's single DNS
    resolution (DNS-rebinding/TOCTOU-safe, red-team #3). Use this for any server-side
    fetch of an externally-influenced URL.

    Raises ValueError if the (initial or redirected) host is not public or the redirect
    chain is too long; blocked redirect hops raise urllib.error.HTTPError (unchanged
    from the pre-pin version). Returns the usual urllib response object (.read(),
    .status, .headers, .geturl(), context manager). Environment proxies are ignored:
    a proxy would connect wherever it likes and defeat the pin."""
    current = url
    for _hop in range(_MAX_REDIRECTS + 1):
        ips, why = _resolve_pin(current)
        if not ips:
            raise ValueError(f"blocked: {why}")
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),   # no env proxies: they would bypass the pin
            _PinnedHTTPHandler(ips),
            _PinnedHTTPSHandler(ips),
            _CaptureRedirect(),
        )
        req = urllib.request.Request(current, headers=headers or {}, method=method)
        try:
            return opener.open(req, timeout=timeout)
        except _RedirectSignal as sig:
            current = sig.url  # validated by the handler; re-resolved + re-pinned next loop
    raise ValueError("blocked: too many redirects")


if __name__ == "__main__":
    import sys
    for arg in sys.argv[1:] or ["http://127.0.0.1:8765", "http://169.254.169.254",
                                "https://example.com", "file:///etc/passwd",
                                "http://[::ffff:127.0.0.1]/", "http://0x7f000001/"]:
        print(public_url_ok(arg), arg)
