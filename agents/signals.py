#!/usr/bin/env python3
"""Buying-signal framework (Q255-260) — pluggable signal sources, each
returning candidate rows, all appended to store/signals.jsonl. Two sources are
FULLY IMPLEMENTED without paid APIs (tech-stack detection, domain-expiry via
the macOS `whois` CLI); the rest are documented stub classes per the task
brief ("Others: stub classes with [E] docs").

Implemented, real, no paid API:
- TechStackSignal (Q259, "tech-stack scanner in qa.py v3"): fetches a
  homepage (READ-ONLY GET, no auth, no write anywhere) and fingerprints the
  builder by markers in the HTML (Wix, Squarespace, WordPress, Elementor,
  Lovable/React). Feeds pitch-angle selection ("saw you're on Squarespace,
  here's what that's costing you").
- DomainExpirySignal (Q260, "domain-expiry watching... public whois"):
  python-whois isn't installed (confirmed), but macOS ships a `whois` CLI
  (confirmed present at /usr/bin/whois) -- this shells out to it and parses
  the expiry date out of the raw response. Renewal moments are website-
  decision moments.

Stubs ([E], documented, not implemented -- no free/public data source exists
for these without a paid API or manual work):
- NewBusinessRegistrationSignal (Q255): state LLC filing feeds are mostly
  paid or state-portal-specific with no unified free API.
- PermitPullSignal (Q256): building-permit data is per-municipality, no
  unified free feed.
- JobPostingSignal (Q257): Indeed/LinkedIn job-posting APIs are paid or
  ToS-restricted for scraping.
- ReviewVelocitySignal (Q258): Google/Yelp review APIs are paid past small
  free tiers; velocity tracking needs repeated pulls over time besides.

Each signal source's run() returns a list of candidate dicts appended (not
overwritten) to store/signals.jsonl, so the file accumulates a history of
everything ever detected. Run standalone against real targets:
.venv/bin/python agents/signals.py --url [OWNER_SITE]
.venv/bin/python agents/signals.py --url [OWNER_SITE] --domain [OWNER_SITE]
.venv/bin/python agents/signals.py --fixture
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from store_lib import now_iso  # noqa: E402

OUT = ROOT / "store" / "signals.jsonl"

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


class SignalSource(ABC):
    """Base class every signal source implements. run(**kwargs) returns a
    list of candidate dicts (each must include 'signal_type' and 'target')."""
    name: str = "base"

    @abstractmethod
    def run(self, **kwargs) -> list[dict]:
        ...


# ---------------------------------------------------------------------------
# IMPLEMENTED: tech-stack detection (Q259) -- READ-ONLY homepage fetch + fingerprint
# ---------------------------------------------------------------------------

STACK_MARKERS = {
    "wix": [r"wix\.com", r"wixstatic\.com", r"X-Wix-", r"static\.parastorage\.com"],
    "squarespace": [r"squarespace\.com", r"squarespace-cdn\.com", r"data-sqs-", r"static1\.squarespace\.com"],
    "wordpress": [r"wp-content", r"wp-includes", r"/wp-json/", r"generator[\"']?\s*content=[\"']WordPress"],
    "elementor": [r"elementor", r"data-elementor-", r"elementor-widget"],
    "lovable": [r"lovable\.dev", r"lovableproject\.com", r"__lovable"],
    "shopify": [r"cdn\.shopify\.com", r"Shopify\.theme", r"myshopify\.com"],
    "webflow": [r"webflow\.com", r"data-wf-", r"webflow\.io"],
}


def detect_stack(url: str, timeout: int = 12) -> dict:
    """Fetch a homepage (GET only, no auth, no write) and fingerprint the
    builder by regex marker match against the raw HTML + response headers.
    READ-ONLY. Returns {url, detected: [stacks...], confidence, error}."""
    result = {"url": url, "detected": [], "confidence": "none", "fetched_ok": False, "error": None}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(500_000).decode("utf-8", errors="ignore")
            headers_text = "\n".join(f"{k}: {v}" for k, v in resp.getheaders())
        result["fetched_ok"] = True
    except Exception as e:  # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {e}"
        return result

    haystack = body + "\n" + headers_text
    hits = []
    for stack, patterns in STACK_MARKERS.items():
        match_count = sum(1 for p in patterns if re.search(p, haystack, re.I))
        if match_count:
            hits.append((stack, match_count))
    hits.sort(key=lambda h: -h[1])
    result["detected"] = [h[0] for h in hits]
    if hits:
        result["confidence"] = "high" if hits[0][1] >= 2 else "low"
        result["marker_hits"] = dict(hits)
    return result


class TechStackSignal(SignalSource):
    name = "tech_stack"

    def run(self, url: str = "", **kwargs) -> list[dict]:
        if not url:
            return []
        detection = detect_stack(url)
        return [{
            "signal_type": self.name, "target": url, "ts": now_iso(),
            "detected_stack": detection["detected"], "confidence": detection["confidence"],
            "fetched_ok": detection["fetched_ok"], "error": detection["error"],
        }]


# ---------------------------------------------------------------------------
# IMPLEMENTED: domain-expiry watching (Q260) -- macOS `whois` CLI, no paid API
# ---------------------------------------------------------------------------

EXPIRY_PATTERNS = [
    r"Registry Expiry Date:\s*(.+)",
    r"Registrar Registration Expiration Date:\s*(.+)",
    r"Expiration Date:\s*(.+)",
    r"expire[s]?[:\s]+(.+)",
    r"paid-till:\s*(.+)",
]


def whois_expiry(domain: str, timeout: int = 10) -> dict:
    """Shells out to the `whois` CLI (confirmed present at /usr/bin/whois on
    this Mac; python-whois is NOT installed, per the task brief). READ-ONLY,
    public data. Parses the first matching expiry-date pattern out of the raw
    response text -- whois output format varies by registry/TLD, so multiple
    patterns are tried in order."""
    result = {"domain": domain, "expiry_raw": None, "expiry_date": None, "days_until_expiry": None, "error": None}
    try:
        proc = subprocess.run(["whois", domain], capture_output=True, text=True, timeout=timeout)
        text = proc.stdout
    except Exception as e:  # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {e}"
        return result
    if not text.strip():
        result["error"] = "empty whois response"
        return result
    for pattern in EXPIRY_PATTERNS:
        m = re.search(pattern, text, re.I)
        if m:
            raw = m.group(1).strip()
            result["expiry_raw"] = raw
            parsed = _parse_whois_date(raw)
            if parsed:
                result["expiry_date"] = parsed.date().isoformat()
                result["days_until_expiry"] = (parsed.date() - datetime.now().date()).days
            break
    if result["expiry_raw"] is None:
        result["error"] = "no recognized expiry pattern in whois response"
    return result


def _parse_whois_date(raw: str) -> datetime | None:
    # whois dates come in a handful of common formats depending on registry.
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d %H:%M:%S",
               "%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw[:len(datetime.now().strftime(fmt))] if fmt.count("%") <= 3 else raw[:19],
                                     fmt)
        except ValueError:
            continue
    # last resort: grab just the date-looking prefix (YYYY-MM-DD) if present
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d")
        except ValueError:
            pass
    return None


class DomainExpirySignal(SignalSource):
    name = "domain_expiry"

    def run(self, domain: str = "", **kwargs) -> list[dict]:
        if not domain:
            return []
        info = whois_expiry(domain)
        return [{
            "signal_type": self.name, "target": domain, "ts": now_iso(),
            "expiry_date": info["expiry_date"], "days_until_expiry": info["days_until_expiry"],
            "error": info["error"],
        }]


# ---------------------------------------------------------------------------
# STUBS: [E] documented, no free/public data source, not implemented
# ---------------------------------------------------------------------------

class NewBusinessRegistrationSignal(SignalSource):
    """[E] State new-LLC filing feeds (Q255). Most states either charge for
    bulk filing data or expose it only through per-state portals with no
    unified API; a few (DE, some others) publish open data but coverage is
    inconsistent enough that a real implementation needs [OWNER] to pick target
    states and either pay for a data feed or build per-state scrapers -- out
    of scope for a no-paid-API pass. Stub only."""
    name = "new_business_registration"

    def run(self, **kwargs) -> list[dict]:
        return [{"signal_type": self.name, "target": kwargs.get("state", "(unspecified)"),
                 "ts": now_iso(), "status": "[E] stub -- no unified free state-filing API, see class docstring"}]


class PermitPullSignal(SignalSource):
    """[E] Building-permit data (Q256) is issued per-municipality with no
    unified free feed; some cities publish open-data permit portals (Socrata-
    based, e.g. data.cityname.gov) but format and availability vary city to
    city -- a real implementation would be one connector per target metro,
    which needs [OWNER] to name target metros first. Stub only."""
    name = "permit_pull"

    def run(self, **kwargs) -> list[dict]:
        return [{"signal_type": self.name, "target": kwargs.get("metro", "(unspecified)"),
                 "ts": now_iso(), "status": "[E] stub -- per-municipality permit portals, no unified free feed"}]


class JobPostingSignal(SignalSource):
    """[E] Job-posting signal (Q257, "saw you're hiring two techs, is the
    website keeping up?"). Indeed/LinkedIn job APIs are paid or ToS-restrict
    scraping; free RSS feeds exist for some boards (Indeed used to, coverage
    now unreliable) but nothing stable enough to build against today. Stub
    only until a specific board's terms are checked and approved."""
    name = "job_posting"

    def run(self, **kwargs) -> list[dict]:
        return [{"signal_type": self.name, "target": kwargs.get("company", "(unspecified)"),
                 "ts": now_iso(), "status": "[E] stub -- job-board APIs are paid/ToS-restricted, see class docstring"}]


class ReviewVelocitySignal(SignalSource):
    """[E] Review-velocity signal (Q258, competitors' clients with sudden bad
    reviews). Google Places API and Yelp Fusion both charge past small free
    tiers, and velocity tracking needs REPEATED pulls over time (a time-series
    of review counts/ratings per business) which multiplies API cost further.
    Stub only until [OWNER] approves a specific paid tier."""
    name = "review_velocity"

    def run(self, **kwargs) -> list[dict]:
        return [{"signal_type": self.name, "target": kwargs.get("business", "(unspecified)"),
                 "ts": now_iso(), "status": "[E] stub -- Places/Yelp APIs paid past free tier, needs repeated pulls"}]


ALL_SOURCES: list[SignalSource] = [
    TechStackSignal(), DomainExpirySignal(),
    NewBusinessRegistrationSignal(), PermitPullSignal(),
    JobPostingSignal(), ReviewVelocitySignal(),
]


def _append_jsonl(rows: list[dict]) -> None:
    if not rows:
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def run_real(url: str, domain: str) -> list[dict]:
    all_rows = []
    if url:
        all_rows.extend(TechStackSignal().run(url=url))
    if domain:
        all_rows.extend(DomainExpirySignal().run(domain=domain))
    # stub sources still run (cheap, no network) so the framework proves all
    # 6 plug in correctly even though 4 are documented no-ops.
    all_rows.extend(NewBusinessRegistrationSignal().run())
    all_rows.extend(PermitPullSignal().run())
    all_rows.extend(JobPostingSignal().run())
    all_rows.extend(ReviewVelocitySignal().run())
    return all_rows


def run_fixture() -> list[dict]:
    """Proves the framework end-to-end without hitting the network: a
    synthetic tech-stack detection result + synthetic domain-expiry result,
    same shape the real sources return."""
    return [
        {"signal_type": "tech_stack", "target": "https://fixture-example.test", "ts": now_iso(),
         "detected_stack": ["squarespace"], "confidence": "high", "fetched_ok": True, "error": None},
        {"signal_type": "domain_expiry", "target": "fixture-example.test", "ts": now_iso(),
         "expiry_date": "2026-11-15", "days_until_expiry": 135, "error": None},
    ] + [row for src in (NewBusinessRegistrationSignal(), PermitPullSignal(),
                        JobPostingSignal(), ReviewVelocitySignal()) for row in src.run()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="", help="homepage URL for tech-stack detection")
    ap.add_argument("--domain", default="", help="bare domain for whois expiry lookup")
    ap.add_argument("--fixture", action="store_true")
    args = ap.parse_args()

    if args.fixture:
        rows = run_fixture()
        source = "FIXTURE"
    else:
        rows = run_real(args.url, args.domain)
        source = "REAL"

    _append_jsonl(rows)
    by_type = {}
    for r in rows:
        by_type.setdefault(r["signal_type"], []).append(r)
    summary = ", ".join(f"{k}:{len(v)}" for k, v in by_type.items())
    print(f"signals [{source}]: {len(rows)} candidate row(s) appended ({summary}) -> {OUT}")
    for r in rows:
        if r["signal_type"] == "tech_stack":
            print(f"  tech_stack {r['target']}: {r['detected_stack']} (confidence={r['confidence']})")
        elif r["signal_type"] == "domain_expiry":
            print(f"  domain_expiry {r['target']}: expires {r['expiry_date']} "
                  f"({r['days_until_expiry']} days) error={r['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
