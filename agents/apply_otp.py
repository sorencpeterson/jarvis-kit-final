#!/usr/bin/env python3
"""Email-OTP fetcher for the apply operator (2026-07-07, [OWNER]'s ask).

Many ATS forms now email a verification code to the applicant and won't submit
without it. The code lands in [OWNER]'S OWN Gmail, which the brain already reads
(mail_sync plumbing), so the operator doesn't need to stop at that wall: it asks
the server, the server pulls the code from the inbox, the operator types it in.

RAILS (deliberate, keep them):
  - READ-ONLY against Gmail, and the return value is ONLY {code, from-domain,
    truncated subject, age}. The operator NEVER sees a mail body or anything
    from a non-verification email — this module is the privacy firewall between
    a browser subagent on hostile third-party pages and [OWNER]'s inbox.
  - FRESH ONLY: messages older than MAX_AGE_S (10 min) are invisible. A random
    old 6-digit number in the inbox can never leak out through this.
  - VERIFICATION-SHAPED ONLY: the message must match OTP keywords in the
    subject/body before any digits are considered. No keyword, no code.
  - CONSUMED-ONCE: each Gmail message id is claimed for one job id
    (store/otp_consumed.json) so two parallel operators can't grab each
    other's codes and cross-verify the wrong application.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents", Path.home() / "Claude" / "gmail"):
    sys.path.insert(0, str(p))
import gmail_api  # noqa: E402
import jobs  # noqa: E402  (CX14: derive the expected employer from OUR OWN job record when
# the caller doesn't supply a hint -- module-level so tests can monkeypatch it like gmail_api)

CONSUMED = ROOT / "store" / "otp_consumed.json"
MAX_AGE_S = 600          # only codes from the last 10 minutes exist
SEARCH_MAX = 10

# a message must look like a verification email BEFORE we hunt digits in it
_OTP_KEYWORDS = re.compile(
    r"verif(y|ication)|one[- ]?time|security code|login code|access code|"
    r"confirmation code|\botp\b|passcode|confirm your (email|application)|"
    r"authentication code|your code", re.I)

# code extraction, most-specific first: labeled codes beat bare digit runs
_CODE_PATTERNS = [
    re.compile(r"(?:code|otp|passcode|pin)[^A-Za-z0-9]{0,12}([0-9]{4,8})\b", re.I),
    # labeled ALPHANUMERIC codes (Greenhouse/Chainguard-style 6-8 char, e.g. "code: 7K2M9QX1").
    # Requires >=1 digit so a labeled word ("code: CONFIRM") can't false-match (2026-07-11:
    # an operator hit an 8-char alphanumeric field our digits-only regex couldn't serve).
    re.compile(r"(?:code|otp|passcode|pin)[^A-Za-z0-9]{0,12}((?=[A-Z0-9]*\d)[A-Z0-9]{5,8})\b", re.I),
    re.compile(r"\b([0-9]{6})\b"),        # the overwhelming ATS default
    re.compile(r"\b([0-9]{4,8})\b"),
]
# digit runs that are near-certainly NOT a code (years, US zips already in profile,
# obvious phone fragments) — cheap false-positive dampers for the bare-digit fallback
_NOT_CODE = re.compile(r"\b(19|20)\d{2}\b")


def _load_consumed() -> dict:
    try:
        return json.loads(CONSUMED.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_consumed(d: dict):
    try:
        CONSUMED.write_text(json.dumps(d))
    except OSError:
        pass


def _extract_code(text: str) -> str:
    text = _NOT_CODE.sub(" ", text or "")
    for pat in _CODE_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1)
    return ""


def _expected_company(jid: str) -> str:
    """Best-effort employer name for `jid` from OUR OWN job record, used when the caller
    didn't pass a hint (CX14). The caller here is the browser apply-operator -- possibly
    steered by a hostile third-party page -- so it isn't a trustworthy source for who the
    OTP should come from; jobs.jsonl (written by our own pipeline) is."""
    try:
        rec = next((j for j in jobs.load_jobs() if j.get("id") == jid), None)
        return (rec or {}).get("company") or ""
    except Exception:  # noqa: BLE001
        return ""


def fetch_code(jid: str, hint: str = "") -> dict:
    """Find the freshest unconsumed verification code in the inbox for job `jid`.
    `hint` (company/ATS name), or else the job's own company on record, must be
    the actual sender/subject match for a candidate to be eligible at all (CX14)
    -- not merely a sort tiebreaker among whatever's fresh. Returns a minimal,
    body-free dict."""
    consumed = _load_consumed()
    now_ms = time.time() * 1000
    try:
        hits = gmail_api.search(
            'newer_than:1h (subject:(code OR verify OR verification OR confirm) '
            'OR "verification code" OR "one-time" OR "security code" OR "your code")',
            SEARCH_MAX)
    except Exception as e:  # noqa: BLE001 — degrade to a clean miss, operator just retries
        return {"ok": False, "error": f"gmail unavailable: {type(e).__name__}"}

    # CX14 + R1#1 (regression, post-17bf56c): the code must correspond to THIS job's actual
    # employer/ATS. The caller's `hint` comes from the browser apply-operator -- possibly
    # steered by a hostile third-party page -- so it must CORROBORATE our own job record's
    # company, never REPLACE it: a hint for a different company must not surface some OTHER
    # application's OTP under this job's id. Our own record (jobs.jsonl, written by our own
    # pipeline) is the trusted anchor whenever it exists; the hint is only consulted as a
    # fallback when we have no record to check against at all.
    expect = _expected_company(jid).strip().lower() or (hint or "").strip().lower()

    candidates = []
    for h in hits:
        mid = h.get("id") or ""
        if not mid or consumed.get(mid, jid) != jid:   # someone else's code
            continue
        try:
            m = gmail_api.get_message(mid)
        except Exception:  # noqa: BLE001
            continue
        age_s = (now_ms - int(m.get("internalDate") or 0)) / 1000
        if age_s > MAX_AGE_S:
            continue
        blob = f"{m.get('subject','')}\n{m.get('body','')}"
        if not _OTP_KEYWORDS.search(blob):
            continue
        code = _extract_code(blob)
        if not code:
            continue
        matches = bool(expect) and expect in (m.get("from", "") + " " + m.get("subject", "")).lower()
        candidates.append((matches, -age_s, mid, code, m))

    if not candidates:
        return {"ok": False, "error": "no fresh verification email yet, wait and retry"}
    # CX14: when we have an employer/ATS to authenticate against, a candidate that doesn't
    # match it is never eligible -- no silent fallback to "freshest of whatever's in the
    # inbox". Without any anchor (no hint, no matching job record) there's nothing to check
    # against, so every fresh verification-shaped candidate stays eligible as before.
    pool = [c for c in candidates if c[0]] if expect else candidates
    if not pool:
        return {"ok": False, "error": "no verification email matching this job's employer yet"}
    pool.sort(reverse=True)                             # (matches first, then freshest)

    # CX16: claim the code under a lock, re-validated against a FRESH read of `consumed` --
    # two operators calling in parallel must not both pass the "unclaimed" check on the same
    # message (using the stale snapshot taken above) and both walk away with the same code.
    from store_lib import _flock
    CONSUMED.parent.mkdir(parents=True, exist_ok=True)
    with _flock(CONSUMED):
        fresh = _load_consumed()
        pool = [c for c in pool if fresh.get(c[2], jid) == jid]
        if not pool:
            return {"ok": False, "error": "no fresh verification email yet, wait and retry"}
        pool.sort(reverse=True)
        _, neg_age, mid, code, m = pool[0]
        fresh[mid] = jid
        _save_consumed(fresh)
    sender = m.get("from", "")
    dom = sender.split("@")[-1].strip(">") if "@" in sender else sender
    return {"ok": True, "code": code, "from_domain": dom,
            "subject": (m.get("subject") or "")[:80], "age_s": round(-neg_age)}


if __name__ == "__main__":
    print(json.dumps(fetch_code(sys.argv[1] if len(sys.argv) > 1 else "cli",
                                sys.argv[2] if len(sys.argv) > 2 else "")))
