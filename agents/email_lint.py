#!/usr/bin/env python3
"""Outbound email lint (2026-07-11, [OWNER]: "make sure all jarvis emails are tech and
content ready to send with good deliverability").

One function, three consumers: a batch CLI (audit everything sendable), the proposal
send route, and the reply send route (both gate on hard errors at the outbox tap).

HARD ERRORS (block the send — these get mail junked or embarrass us):
  - any non-https link, or a link to ts.net / localhost / 127.0.0.1 (pre-flip remnants)
  - an off-brand link domain (everything should ride [OWNER_SITE] or the GHL
    tracking domain; a random shortener is a spam signal)
  - a link that doesn't answer 2xx/3xx right now
  - em/en-dashes (the voice rule, and a tell)
  - empty subject on a NEW email (replies ride the thread subject)
WARNINGS (send allowed, surfaced so [OWNER] can polish):
  - spam-trigger phrases (act now, risk-free, 100% free, guarantee, limited time, $$$)
  - subject > 65 chars, ALL-CAPS words in subject, or '!' anywhere in subject
  - "Hi there"-style greeting (personalization fell through)
  - body outside the 30-220 word cold-outreach band, or more than 3 links
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
import owner  # noqa: E402

BRAND_DOMAINS = (owner.get("site", "example.com"), "leadconnectorhq.com",
                 "msgsndr.com")  # last two: GHL click-tracking rewrites
SPAM_PHRASES = ("act now", "risk-free", "risk free", "100% free", "guarantee", "no obligation",
                "limited time", "once in a lifetime", "$$$", "click here now", "winner",
                "congratulations", "urgent", "final notice")
_URL = re.compile(r"https?://[^\s)>\"']+")


def _link_ok(url: str, timeout: int = 8) -> bool:
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "email-lint"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 400
    except Exception:  # noqa: BLE001
        # some hosts refuse HEAD; one GET retry before calling it dead
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "email-lint"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return 200 <= r.status < 400
        except Exception:  # noqa: BLE001
            return False


def lint(subject: str, body: str, *, is_reply: bool = False, check_links: bool = True) -> dict:
    errors, warns = [], []
    body = body or ""
    subject = subject or ""

    if "—" in body + subject or "–" in body + subject:
        errors.append("em/en-dash present (voice rule)")
    if not is_reply and not subject.strip():
        errors.append("empty subject")

    links = _URL.findall(body)
    for u in links:
        if u.startswith("http://"):
            errors.append(f"insecure http link: {u[:60]}")
        if any(bad in u for bad in (".ts.net", "localhost", "127.0.0.1")):
            errors.append(f"internal/pre-flip link leaked: {u[:60]}")
        dom = u.split("/")[2].lower() if "//" in u else ""
        if dom and not any(dom.endswith(b) for b in BRAND_DOMAINS):
            errors.append(f"off-brand link domain: {dom}")
        elif check_links and not _link_ok(u):
            errors.append(f"dead link: {u[:60]}")
    if len(links) > 3:
        warns.append(f"{len(links)} links (2 is the sweet spot)")

    low = (subject + "\n" + body).lower()
    hits = [p for p in SPAM_PHRASES if p in low]
    if hits:
        warns.append("spam-trigger phrasing: " + ", ".join(hits[:4]))
    if len(subject) > 65:
        warns.append(f"subject {len(subject)} chars (>65 clips on mobile)")
    if "!" in subject:
        warns.append("'!' in subject")
    if re.search(r"\b[A-Z]{4,}\b", subject):
        warns.append("ALL-CAPS word in subject")
    if re.search(r"^\s*(hi|hey|hello)?\s*there\b", body, re.I):
        warns.append("generic 'there' greeting (personalization fell through)")
    words = len(body.split())
    if words and not is_reply and not (30 <= words <= 220):
        warns.append(f"{words} words (cold sweet spot is 30-220)")

    return {"ok": not errors, "errors": errors, "warns": warns}


def audit_all(check_links: bool = True) -> dict:
    """Batch: every staged proposal email + every pending reply draft."""
    import proposal_factory as pf
    import reply_watch as rw
    out = {"proposals": [], "replies": []}
    for p in pf.load_queue():
        if p.get("status") != "staged":
            continue
        r = lint(p.get("email_subject", ""), p.get("email_draft", ""), check_links=check_links)
        out["proposals"].append({"id": p.get("id"), "company": (p.get("company") or p.get("name") or "?")[:36],
                                 **r})
    for x in rw._load():
        if x.get("status") != "pending":
            continue
        r = lint("", x.get("draft", ""), is_reply=True, check_links=check_links)
        out["replies"].append({"id": x.get("id"), **r})
    return out


if __name__ == "__main__":
    res = audit_all(check_links="--no-links" not in sys.argv)
    bad = 0
    for kind in ("proposals", "replies"):
        for r in res[kind]:
            flag = "OK  " if r["ok"] and not r["warns"] else ("WARN" if r["ok"] else "FAIL")
            if flag != "OK  ":
                bad += 1
            print(f"[{flag}] {kind[:-1]} {r.get('company') or r['id']}: "
                  + "; ".join(r["errors"] + r["warns"]) if flag != "OK  " else f"[OK  ] {kind[:-1]} {r.get('company') or r['id']}")
    print(f"\n{sum(len(res[k]) for k in res)} checked, {bad} with findings")
