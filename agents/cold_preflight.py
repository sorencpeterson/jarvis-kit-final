#!/usr/bin/env python3
"""Deliverability preflight for cold email — automates EMAIL-DELIVERABILITY-RUNBOOK.md.

Read-only: digs SPF / DKIM / DMARC for every configured sending domain and checks the
GHL location's from-address isn't sitting on a client's domain. The COLD panel shows
a red light until every check passes; nothing cold should send while it's red.

Run standalone for a human-readable table, or import check_all() (the server caches it).

--daily mode (#164) additionally resolves the sending domain's A record and checks its
IP against two DNSBLs (Spamhaus ZEN, Barracuda) via the standard reversed-octet DNS
lookup: query <reversed-ip>.<bl-domain> and any A-record answer means listed. Appends
one result line to store/domain_health.jsonl. Read-only DNS only; never touches GHL.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
import owner  # noqa: E402
from store_lib import now_iso  # noqa: E402

DOMAIN_HEALTH = ROOT / "store" / "domain_health.jsonl"
# Standard reversed-IP DNS blacklists. An A-record answer at the query name means listed
# (the exact returned IP encodes a reason code we don't need; presence alone is the signal).
DNSBLS = ("zen.spamhaus.org", "b.barracudacentral.org")

# get.* is the cold sending subdomain [OWNER] actually configured in GHL/Mailgun;
# [OWNER_SITE] is listed informationally for the warm/nurture sends.
DEFAULT_DOMAINS = [owner.get("site", "example.com")]
# TODO: stale, hand-maintained, single-domain snapshot of "a client we've built for"
# (dates to the dropped medspa lane -- see business-library no-medspas rule). A new
# client's domain silently slips through this hardcoded gate unless someone remembers
# to add it here by hand. Should source this dynamically (client roster / GHL) instead
# of a hardcoded tuple; left as-is rather than guessing at a safe dynamic source.
CLIENT_DOMAINS = ("aestheticsofamerica.com",)  # never send [OWNER]'s outreach as a client


def _config() -> dict:
    try:
        return json.loads((ROOT / "store" / "config.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _dig(rrtype: str, name: str) -> list[str]:
    try:
        r = subprocess.run(["dig", "+short", rrtype, name],
                           capture_output=True, text=True, timeout=10)
        return [ln.strip().strip('"') for ln in r.stdout.splitlines() if ln.strip()]
    except Exception:  # noqa: BLE001
        return []


def _dkim_selector(d: str) -> str:
    """GHL native uses s1/s2 CNAMEs; the Mailgun-backed path uses a TXT key
    (k=rsa;p=...) at smtp/_domainkey or similar. Return the selector that answers."""
    for sel in ("s1", "s2", "smtp", "k1", "krs", "mailo"):
        if _dig("CNAME", f"{sel}._domainkey.{d}"):
            return sel + " (cname)"
        if any("p=" in t for t in _dig("TXT", f"{sel}._domainkey.{d}")):
            return sel + " (txt)"
    return ""


def check_domain(d: str) -> dict:
    txt = _dig("TXT", d)
    spf = next((t for t in txt if t.lower().startswith("v=spf1")), "")
    # GHL/LeadConnector sends through its pool (or Mailgun); SPF must authorize it.
    spf_lc = ("leadconnectorhq.com" in spf) or ("mailgun.org" in spf)
    dkim_sel = _dkim_selector(d)
    dmarc = any("v=dmarc1" in t.lower() for t in _dig("TXT", f"_dmarc.{d}"))
    return {"domain": d, "spf": bool(spf), "spf_leadconnector": spf_lc,
            "dkim": bool(dkim_sel), "dkim_selector": dkim_sel, "dmarc": dmarc,
            "ready": bool(spf) and spf_lc and bool(dkim_sel) and dmarc}


def ghl_from_address() -> str:
    try:
        import ghl_social
        out = ghl_social._api(["GET", "/locations/{loc}"])
        j = json.loads(out[out.find("{"):])
        loc = j.get("location", j) or {}
        return (loc.get("email") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def check_all() -> dict:
    domains = _config().get("cold_domains") or DEFAULT_DOMAINS
    results = [check_domain(d) for d in domains]
    frm = ghl_from_address()
    frm_domain = frm.split("@")[-1].lower() if "@" in frm else ""
    from_ok = bool(frm_domain) and frm_domain not in CLIENT_DOMAINS and any(
        frm_domain == d["domain"] for d in results)
    # Cold-send gate: the domain the from-address actually uses must be fully
    # authenticated. Other listed domains (e.g. the nurture domain) are informational.
    frm_ready = any(d["ready"] and d["domain"] == frm_domain for d in results)
    # R2#6 (2026-07-14): a DNSBL check on frm_domain was briefly wired into this
    # gate (commit 17bf56c's "Q" note), but check_dnsbl()/_resolve_all_a() resolve
    # the domain's WEB A record -- for a Cloudflare-fronted domain like
    # get.thenobsmarketing.com that's the CDN edge IP, not the LeadConnector/
    # Mailgun sending pool that actually delivers the mail. That made the check
    # BOTH a false positive (an unrelated shared CDN IP catches a DNSBL hit and
    # halts cold sending for no real reason) and a false negative (the ACTUAL
    # sending pool could be blacklisted and this would never notice, which was
    # the entire point of adding it). Reverted from the gate rather than ship a
    # check that's wrong in both directions -- run_daily() still logs the same
    # (equally web-IP-scoped) check to domain_health.jsonl for human review only,
    # never as an auto-gate. TODO: to do this right, resolve the ESP's real
    # outbound IPs (e.g. walk the SPF `include:` chain for leadconnectorhq.com /
    # mailgun.org down to their published ip4: ranges) before gating sends on
    # DNSBL again.
    return {"domains": results, "from_address": frm, "from_ok": from_ok,
            "ready": from_ok and frm_ready,
            "runbook": "~/Claude/EMAIL-DELIVERABILITY-RUNBOOK.md"}


def _resolve_all_a(domain: str) -> list[str]:
    """ALL IPv4 A-records for domain (not just the first) — a Cloudflare-fronted domain
    like get.thenobsmarketing.com round-robins across multiple real IPs (confirmed live:
    the same domain returned 104.18.35.90 on one query and 172.64.152.166 on the next),
    so checking only the first-seen IP would miss a blacklisted secondary. Only keeps
    values that look like a dotted-quad IPv4 (dig +short can occasionally hand back a
    CNAME target string instead of a resolved IP for some record types; guard against
    ever feeding that into the reversed-octet DNSBL query)."""
    import re as _re
    ipv4 = _re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
    return [ip for ip in _dig("A", domain) if ipv4.match(ip)]


def _reversed_ip(ip: str) -> str:
    parts = ip.split(".")
    return ".".join(reversed(parts)) if len(parts) == 4 else ""


def check_dnsbl(domain: str) -> dict:
    """Resolve ALL of domain's A records, query EACH against each DNSBL (see
    _resolve_all_a for why not just the first). An empty ip list (domain doesn't
    resolve directly, e.g. it's CNAME/MX-only for mail) is reported, not treated as a
    failure — cold-sending subdomains often have no A record of their own. `ip` in the
    result is the first checked IP (for display); `ips_checked` has all of them."""
    ips = _resolve_all_a(domain)
    listed: dict[str, bool | None] = {bl: None for bl in DNSBLS}
    per_ip: dict[str, dict] = {}
    for ip in ips:
        rev = _reversed_ip(ip)
        if not rev:
            continue
        ip_listed = {}
        for bl in DNSBLS:
            hit = bool(_dig("A", f"{rev}.{bl}"))
            ip_listed[bl] = hit
            if hit:
                listed[bl] = True
            elif listed[bl] is None:
                listed[bl] = False
        per_ip[ip] = ip_listed
    any_listed = any(v is True for v in listed.values())
    return {"domain": domain, "ip": ips[0] if ips else "", "ips_checked": ips,
            "per_ip": per_ip, "listed": listed, "blacklisted": any_listed}


def _append_domain_health(rec: dict):
    DOMAIN_HEALTH.parent.mkdir(parents=True, exist_ok=True)
    with DOMAIN_HEALTH.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def run_daily() -> dict:
    """#164: SPF/DKIM/DMARC (reuse check_all) + DNSBL check for every configured sending
    domain. Appends one summary record per run to store/domain_health.jsonl. Read-only;
    never writes to GHL or touches send knobs — this is diagnostic only, campaign_guard
    (agents/campaign_guard.py) is the one that acts on deliverability signals."""
    auth = check_all()
    domains = _config().get("cold_domains") or DEFAULT_DOMAINS
    dnsbl = [check_dnsbl(d) for d in domains]
    any_blacklisted = any(d["blacklisted"] for d in dnsbl)
    rec = {"ts": now_iso(), "auth_ready": auth["ready"], "from_address": auth["from_address"],
           "dnsbl": dnsbl, "any_blacklisted": any_blacklisted}
    _append_domain_health(rec)
    return {"auth": auth, "dnsbl": dnsbl, "any_blacklisted": any_blacklisted}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", action="store_true",
                    help="#164: also run the DNSBL blacklist probe and log to domain_health.jsonl")
    args = ap.parse_args()

    res = check_all()
    for r in res["domains"]:
        marks = " ".join(f"{k}:{'OK' if r[k] else 'MISSING'}"
                         for k in ("spf", "spf_leadconnector", "dkim", "dmarc"))
        print(f"{'READY' if r['ready'] else 'NOT READY':9} {r['domain']:32} {marks}")
    print(f"from-address: {res['from_address'] or 'unknown'} "
          f"({'ok' if res['from_ok'] else 'WRONG — fix in GHL Settings > Email Services'})")
    print("overall:", "READY TO SEND" if res["ready"] else "DO NOT SEND COLD YET — see " + res["runbook"])

    if args.daily:
        daily = run_daily()
        for d in daily["dnsbl"]:
            if not d["ips_checked"]:
                print(f"  dnsbl {d['domain']:32} no A record (skipped, likely mail-only subdomain)")
                continue
            marks = " ".join(f"{bl}:{'LISTED' if v else ('?' if v is None else 'clean')}"
                             for bl, v in d["listed"].items())
            ip_note = d["ip"] if len(d["ips_checked"]) == 1 else f"{d['ip']} (+{len(d['ips_checked'])-1} more checked)"
            print(f"  dnsbl {d['domain']:32} ip={ip_note:28} {marks}")
        if daily["any_blacklisted"]:
            print("BLACKLIST HIT — a sending domain is on a DNSBL. Do not send cold until delisted.")
        print(f"  logged -> {DOMAIN_HEALTH.relative_to(ROOT)}")

    return 0 if res["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
