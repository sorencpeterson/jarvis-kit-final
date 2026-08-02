#!/usr/bin/env python3
"""C174 same-company dedupe + C180 multi-party/group-thread detection for reply_watch.py.

C174: two different people replying from the same business (e.g. the owner AND their
office manager both texted in about the same site build) should be coordinated, not
treated as two independent unrelated leads that each get their own from-scratch
proposal build. group_by_company() clusters candidate rows by a normalized company
name so reply_watch.py can flag the 2nd+ contact from a company already in this
run's candidate list instead of silently double-building.

C180: a single message can itself reveal a group/multi-party thread (a spouse or
business partner is being looped in, or explicitly CC'd/mentioned) -- playbook #44
("I need to ask my wife/partner/business partner") is exactly this pattern from the
OTHER direction (them naming a decision-maker who isn't on the thread yet); C180 is
the detection when that third party is ALREADY visible in the message itself (e.g.
"looping in my partner Alex" or "cc: alex@..."). detect_multi_party() flags it so
reply_watch can route to playbook #44's language instead of addressing just the one
name on the GHL record.

All functions here are pure (no file I/O, no LLM, no GHL calls) so every case is a
plain fixture.
"""
from __future__ import annotations

import re

_COMPANY_SUFFIXES = re.compile(
    r'\b(llc|inc|incorporated|corp|corporation|co|company|ltd|limited|group|'
    r'enterprises|holdings|pllc|pc)\.?\b', re.IGNORECASE)
_APOSTROPHE_RE = re.compile(r"['’]")  # straight + curly apostrophe: drop, don't space-break
_PUNCT_RE = re.compile(r'[^\w\s]')
_WS_RE = re.compile(r'\s+')


def normalize_company(name: str) -> str:
    """Lowercase, strip legal suffixes (LLC/Inc/Corp/...) and punctuation, collapse
    whitespace. 'Legacy Plumbing, LLC.' and 'Legacy Plumbing' both normalize to
    'legacy plumbing' so they cluster together. Apostrophes are dropped rather than
    turned into a space ("Braydon's" -> "braydons", not "braydon s") so possessives
    don't fracture into a spurious extra word. Empty/None input -> ''."""
    if not name:
        return ""
    n = name.lower().strip()
    n = _APOSTROPHE_RE.sub("", n)
    n = _PUNCT_RE.sub(" ", n)
    n = _COMPANY_SUFFIXES.sub(" ", n)
    n = _WS_RE.sub(" ", n).strip()
    return n


def group_by_company(candidates: list[dict], company_field: str = "company") -> dict[str, list[dict]]:
    """Cluster candidate rows by normalized company name. Rows with no company
    value (empty/missing) are never clustered (each stands alone -- an empty
    string matching another empty string would falsely group unrelated contacts
    with no company on file). Returns {normalized_company: [rows]}, only keys
    with 2+ rows are 'real' clusters but ALL keys are returned so callers can
    decide their own threshold."""
    out: dict[str, list[dict]] = {}
    for c in candidates:
        norm = normalize_company(c.get(company_field) or "")
        if not norm:
            continue
        out.setdefault(norm, []).append(c)
    return out


def same_company_flags(candidates: list[dict], company_field: str = "company",
                       contact_id_field: str = "contact_id") -> dict[str, str]:
    """For each candidate with 2+ others sharing its normalized company (excluding
    itself), returns {contact_id: note} where note names the other contact(s) so
    reply_watch can attach a 'coordinate, don't duplicate' note to the record
    instead of building each in isolation. Keyed by contact_id (falls back to id()
    of the dict for candidates with no contact_id, so it still works defensively,
    though in practice every real candidate has one)."""
    clusters = group_by_company(candidates, company_field)
    flags: dict[str, str] = {}
    for norm, rows in clusters.items():
        if len(rows) < 2:
            continue
        for i, row in enumerate(rows):
            cid = row.get(contact_id_field) or f"_anon_{id(row)}"
            others = [r.get("name") or r.get(company_field) or "someone else"
                     for j, r in enumerate(rows) if j != i]
            flags[cid] = (f"same company as {', '.join(others)} in this batch "
                         f"(company: {row.get(company_field)}) -- coordinate, don't duplicate")
    return flags


# C180: signals that a third party is already visible in the message itself, not
# just the one name on the GHL record.
_MULTI_PARTY_RE = re.compile(
    r'\b(my (?:wife|husband|partner|co-?founder|business partner|colleague|manager|'
    r'boss|assistant|office manager)\b[^.!?]{0,60}(?:is|will|would|wants|said|'
    r'thinks|asked)?|'
    r'cc[: ]|cc\'?d|looping in|loop(?:ing)? (?:in|them|him|her)|'
    r'\+[\w.\-]+@[\w.\-]+|adding [\w\' ]{1,30}(?:to this|here)|'
    r'we (?:both|all) (?:think|want|need|agree))\b',
    re.IGNORECASE)
_EMAIL_RE = re.compile(r'\b[\w.\-]+@[\w.\-]+\.\w+\b')


def detect_multi_party(their_msg: str, known_email: str = "") -> tuple[bool, str]:
    """True if the inbound message itself signals a group/multi-party thread:
    naming a spouse/partner/colleague, an explicit 'cc' mention, a second email
    address that ISN'T the contact's own known_email, or 'we both/all' language.
    Returns (is_multi_party, matched_signal) so reply_watch can log why. This is
    the mirror of playbook #44 -- if THEY reveal a co-decision-maker mid-thread,
    route toward #44's "want me on a call with both of you" language instead of
    a solo pitch."""
    t = their_msg or ""
    if not t.strip():
        return False, ""
    m = _MULTI_PARTY_RE.search(t)
    if m:
        return True, m.group(0)[:60]
    emails = _EMAIL_RE.findall(t)
    other_emails = [e for e in emails if e.lower() != (known_email or "").lower()]
    if other_emails:
        return True, f"second email address mentioned: {other_emails[0]}"
    return False, ""
