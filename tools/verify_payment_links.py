#!/usr/bin/env python3
"""Payment-link verifier (FABLE-BUILD-QUEUE Section 4: "Payment-links verifier +
populate the 5 Stripe links").

store/config.json has a payment_links block that proposal_factory._pay_link() and
proposal_timers._pay_link_for() read to put a deposit CTA in staged proposals and
in the WON_PAYMENT_NUDGE follow-up. If a tier's link is empty, a won contact gets
the "I'll get the deposit link over to you" fallback instead of a link. If a
tier's link is DEAD, a real prospect clicks into a 404. This tool makes both
states visible in one run:

  1. For every expected tier key: configured vs missing.
  2. For every configured URL: shape check first (https + host on a short
     allowlist of known payment hosts, e.g. buy.stripe.com). Anything else is
     rejected loudly and counted as dead. No exceptions: a payment link pointing
     at a non-payment host is exactly the bug this guard exists to catch.
  3. Shape-valid URLs get a HEAD request (10s timeout). Redirects are NOT
     followed blindly: a 3xx only counts as alive if its Location also passes
     the same https + allowlist check.

Exit 0 if no configured link is dead. Exit 1 if any configured link is dead or
rejected. Missing links do not fail the run (they are a to-do, not a breakage),
but they are listed, with the exact config keys, in the closing block.

Prices come from playbooks/pricing-tree.md and agents/proposal_factory.PRICING.
They are load-bearing. Never change a number here without changing it in BOTH
of those places first.

Run: .venv/bin/python tools/verify_payment_links.py   (or python3, stdlib only)
"""
from __future__ import annotations

import http.client
import json
import ssl
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "store" / "config.json"

# The 5 links the build queue calls for. Keys match store/config.json
# payment_links and proposal_factory.PRICING exactly.
CORE_TIERS = {
    "landing":    {"label": "Landing page",         "price": 800},
    "standard":   {"label": "Standard site",        "price": 1200},
    "booking":    {"label": "E-com / Booking site", "price": 2500},
    "whiteglove": {"label": "White-Glove build",    "price": 3500},
    "webfix":     {"label": "Site fix bundle",      "price": 450},
}

# Also present in config.json's payment_links block; checked when configured,
# but not part of the "create these 5 first" push.
EXTRA_TIERS = {
    "agencyfirst": {"label": "Agency first build", "price": 1000},
    "install":     {"label": "AI Ops Install",     "price": 5000},
    "care_growth": {"label": "Care Growth",        "price": 150,
                    "note": "per month, plus 250 onboarding (recurring link)"},
}

ALL_TIERS = {**CORE_TIERS, **EXTRA_TIERS}

# Known payment hosts. Stripe Payment Links live on buy.stripe.com; Checkout
# sessions on checkout.stripe.com. Extend deliberately, never wildcard.
ALLOWED_HOSTS = ("buy.stripe.com", "checkout.stripe.com")

HEAD_TIMEOUT = 10.0


# ---------------- pure parts (unit-tested in tests/test_payment_links.py) ----


def check_url_shape(url: str) -> tuple[bool, str]:
    """https + exact-host allowlist. Returns (ok, reason)."""
    if not isinstance(url, str) or not url.strip():
        return False, "empty"
    parts = urlsplit(url.strip())
    if parts.scheme != "https":
        return False, f"scheme is {parts.scheme or 'missing'!r}, must be https"
    host = (parts.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        return False, (f"host {host!r} is not a known payment host "
                       f"(allowed: {', '.join(ALLOWED_HOSTS)})")
    return True, "ok"


def load_links(config_path: Path) -> dict:
    """payment_links block from config.json. Missing file, unparseable file,
    or missing block all come back as {} so the report still runs on a fresh
    restore."""
    try:
        cfg = json.loads(Path(config_path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    block = cfg.get("payment_links")
    return block if isinstance(block, dict) else {}


def classify(links: dict) -> tuple[dict, list]:
    """Split expected tiers into (configured: key->url, missing: [keys]).
    A key set to '' or whitespace counts as missing."""
    configured, missing = {}, []
    for key in ALL_TIERS:
        url = links.get(key)
        if isinstance(url, str) and url.strip():
            configured[key] = url.strip()
        else:
            missing.append(key)
    return configured, missing


def judge(status: int, location: str) -> tuple[bool, str]:
    """Alive/dead from a HEAD response. 2xx alive; 3xx alive only if the
    redirect target itself passes the https + allowlist check (never follow
    blindly); everything else dead."""
    if 200 <= status < 300:
        return True, f"HTTP {status}"
    if 300 <= status < 400:
        ok, reason = check_url_shape(location or "")
        if ok:
            return True, f"HTTP {status} redirect to allowed host ({location})"
        return False, (f"HTTP {status} redirect REJECTED, target fails the "
                       f"payment-host check: {reason} ({location or 'no Location header'})")
    if status == 403:
        # Measured 2026-07-07: buy.stripe.com answers 403 for a nonexistent link
        # path on HEAD and GET alike. If a link you KNOW is live shows here,
        # open it in a browser once; a 403 on a working link means Stripe is
        # bot-challenging this checker, not that the link is dead.
        return False, f"HTTP {status} (Stripe uses 403 for bad link paths; if this link works in a browser, it is a bot challenge, not a dead link)"
    return False, f"HTTP {status}"


# ---------------- network (mocked in tests) ----------------


def head_check(url: str, timeout: float = HEAD_TIMEOUT) -> tuple[bool, str]:
    """One HEAD request, no redirect following. Returns (alive, detail)."""
    parts = urlsplit(url)
    conn = http.client.HTTPSConnection(
        parts.hostname, parts.port or 443, timeout=timeout,
        context=ssl.create_default_context())
    try:
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        conn.request("HEAD", path, headers={"User-Agent": "second-brain-linkcheck/1.0"})
        resp = conn.getresponse()
        status, location = resp.status, resp.getheader("Location", "") or ""
        # Some hosts refuse HEAD; one GET retry (status line only, body unread).
        if status in (405, 501):
            conn.close()
            conn = http.client.HTTPSConnection(
                parts.hostname, parts.port or 443, timeout=timeout,
                context=ssl.create_default_context())
            conn.request("GET", path, headers={"User-Agent": "second-brain-linkcheck/1.0"})
            resp = conn.getresponse()
            status, location = resp.status, resp.getheader("Location", "") or ""
        return judge(status, location)
    except OSError as e:
        return False, f"network error: {e}"
    finally:
        conn.close()


# ---------------- report ----------------


def run(config_path: Path = CONFIG_PATH, checker=head_check, out=print) -> int:
    links = load_links(config_path)
    configured, missing = classify(links)

    out("Payment links check (store/config.json -> payment_links)")
    out("")

    dead = []
    for key, url in configured.items():
        t = ALL_TIERS[key]
        ok, reason = check_url_shape(url)
        if not ok:
            dead.append(key)
            out(f"  REJECTED  {key:<11} ({t['label']}, ${t['price']}): {reason}")
            out(f"            configured value: {url}")
            continue
        alive, detail = checker(url)
        if alive:
            out(f"  ALIVE     {key:<11} ({t['label']}, ${t['price']}): {detail}")
        else:
            dead.append(key)
            out(f"  DEAD      {key:<11} ({t['label']}, ${t['price']}): {detail}")
            out(f"            {url}")

    for key in missing:
        t = ALL_TIERS[key]
        tag = "MISSING" if key in CORE_TIERS else "MISSING (optional tier)"
        out(f"  {tag:<25} {key:<11} ({t['label']}, ${t['price']})")

    out("")
    core_missing = [k for k in missing if k in CORE_TIERS]
    if dead:
        out(f"RESULT: {len(dead)} configured link(s) DEAD or REJECTED. Fix before any proposal goes out.")
    elif configured:
        out(f"RESULT: all {len(configured)} configured link(s) alive.")
    else:
        out("RESULT: no payment links configured yet. Nothing is broken, but every "
            "deposit CTA falls back to 'I will send the link separately'.")

    # ---- plain-English close for [OWNER] ----
    out("")
    out("WHAT TO DO ([OWNER], about 20 minutes):")
    if core_missing:
        out("  1. Stripe dashboard -> Payment Links -> + New "
            "(https://dashboard.stripe.com/payment-links).")
        out("  2. Create one link per tier below. Hard rule from pricing-tree.md: "
            "50 percent deposit books the slot, so set each link to the deposit amount.")
        for key in core_missing:
            t = CORE_TIERS[key]
            out(f"       {t['label']:<20} full ${t['price']:<5} -> deposit link ${t['price'] // 2}")
        out("     (webfix is small enough that full price in one link is also fine; your call.)")
        out("  3. Paste each URL into store/config.json under \"payment_links\", "
            "keys exactly as follows:")
        for key in core_missing:
            out(f'       "{key}": "https://buy.stripe.com/..."')
        extra_missing = [k for k in missing if k in EXTRA_TIERS]
        if extra_missing:
            out(f"     Optional extra keys, same block, when you want them: "
                f"{', '.join(extra_missing)}.")
        out("  4. Re-run this check: .venv/bin/python tools/verify_payment_links.py")
    elif dead:
        out("  Replace the dead links above in Stripe (Payment Links -> the tier -> "
            "copy fresh URL), paste into store/config.json payment_links, re-run this check.")
    else:
        out("  Nothing. All core tier links are configured and alive.")

    return 1 if dead else 0


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
