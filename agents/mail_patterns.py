#!/usr/bin/env python3
"""B141-150: per-ATS regex pattern library for job-application email classification.

CONTRACT: this module is a pure regex library with NO Gmail/network/store I/O of its
own — it's meant to be imported by agents/job_replies.py (jobs-fleet-owned; we don't
edit that file, they import this one) as a fast, zero-cost first pass BEFORE their
existing LLM classify call, or as a fallback classifier when the LLM call fails/is
rate-limited. Also usable standalone for regex-based classification without any LLM
cost at all.

Public API:
    classify(subject: str, body: str, sender: str = "") -> dict
        Returns {"type": "confirmation"|"rejection"|"interview"|"assessment"|"other",
                 "confidence": "high"|"low", "matched": "<pattern name or None>"}
        confidence="low" means no domain/pattern match fired (caller should fall back
        to their own LLM classify — this ISN'T meant to replace judgment on ambiguous mail).
    ATS_DOMAINS: tuple of known ATS sending domains (superset of job_replies.py's
        hardcoded list — kept in sync manually since job_replies.py owns its own copy
        and we don't want a cross-file coupling that breaks if they refactor).
    is_ats_sender(sender: str) -> bool

Patterns below are built from REAL ATS mail pulled from [OWNER]'s mailbox for
confirmation/rejection language (Ashby, Breezy, Rippling senders, 2026-07 window) —
see the CONFIRMATION_PATTERNS / REJECTION_PATTERNS comments for which are real-mail-
sourced vs standard-industry-phrasing (interview/assessment patterns; his mailbox
didn't have enough real interview-invite samples yet to mine from, so those are
built from well-documented common ATS phrasing and marked as such below).

Tests: tests/test_mail_patterns.py (pure-function, no LLM/network — runs under
`make doctor`'s pytest step alongside the existing test suite).
"""
from __future__ import annotations

import re

ATS_DOMAINS = (
    "ashbyhq.com", "hire.lever.co", "lever.co", "greenhouse.io", "workablemail.com",
    "rippling.com", "ats.rippling.com", "careerplug.com", "jazz.co", "jazzhr.com",
    "breezy.hr", "breezy-mail.com", "applytojob.com", "myworkday.com", "icims.com",
    "smartrecruiters.com", "bamboohr.com", "jobvite.com", "taleo.net", "successfactors.com",
)


def is_ats_sender(sender: str) -> bool:
    lo = (sender or "").lower()
    return any(d in lo for d in ATS_DOMAINS)


# --- REJECTION patterns (real-mail-sourced: Ashby "myTomorrows"/"829 Studios"/
# "Character.AI", Breezy "Epic Cleantec"/"Bullseye Strategy" rejection emails,
# 2026-07 window) -------------------------------------------------------------
REJECTION_PATTERNS = [
    (re.compile(r"(?i)we (?:regret to inform you|won'?t be moving forward|decided not to move forward)"), "regret_wont_move_forward"),
    (re.compile(r"(?i)(?:will not|won'?t) be (?:moving forward|proceeding) with your (?:application|candidacy)"), "not_proceeding_with_application"),
    (re.compile(r"(?i)pursu(?:e|ing) other candidates"), "pursuing_other_candidates"),
    (re.compile(r"(?i)position (?:has been|was) filled"), "position_filled"),
    (re.compile(r"(?i)not (?:a|the right) fit at this time"), "not_a_fit_this_time"),
    (re.compile(r"(?i)decided to (?:go|move) (?:in )?(?:a )?different direction"), "different_direction"),
    (re.compile(r"(?i)unfortunately.{0,60}(?:not|won'?t|unable to)"), "unfortunately_not"),
]

# --- CONFIRMATION patterns (real-mail-sourced: Rippling "XWP"/"Franki"/"xCures"/
# "Expo"/"IP House"/"DealerOn", Ashby "Character.AI"/"Scan.com" confirmation emails,
# 2026-07 window) --------------------------------------------------------------
CONFIRMATION_PATTERNS = [
    (re.compile(r"(?i)thank(?:s| you) for (?:applying|your interest|submitting|taking the time)"), "thanks_for_applying"),
    (re.compile(r"(?i)we(?:'ve| have) (?:received|successfully received) your application"), "application_received"),
    (re.compile(r"(?i)(?:currently )?review(?:ing)? (?:your (?:resume|application|profile|background)|resumes)"), "under_review"),
    (re.compile(r"(?i)if we think there(?:'s| is) a good fit"), "will_contact_if_fit"),
    (re.compile(r"(?i)will (?:be in touch|reach out|contact you) if"), "will_reach_out_conditional"),
]

# --- INTERVIEW patterns (standard ATS phrasing — his mailbox had confirmation/
# rejection samples in volume but not enough real interview-invite mail yet to mine
# from; these come from well-documented common ATS interview-scheduling language,
# NOT verified against his real mail. Flag for a future pass once real volume exists,
# per B141's "reserve: apply learnings as patterns emerge from real volume" note) ---
INTERVIEW_PATTERNS = [
    # Negative lookbehind excludes conditional framing ("if... wish to schedule an
    # interview with you", "may contact you to schedule") — real false positive found
    # against a live "IP House" confirmation email: its boilerplate says "you will be
    # contacted if... or wish to schedule an interview with you", which is a hypothetical
    # buried in a generic confirmation, not an actual invite. A genuine invite states the
    # scheduling as the email's own action ("please schedule", "let's schedule"), not as
    # a possible future contingency — that's the real distinguishing signal, not just
    # keyword presence.
    (re.compile(r"(?i)(?<!if )(?<!or )(?:please |let'?s )(?:schedule|scheduling) (?:a|an) "
                r"(?:call|interview|chat|conversation)(?: with you)?"), "schedule_interview_with_you"),
    (re.compile(r"(?i)schedule your (?:call|interview|chat)"), "schedule_your_interview"),
    (re.compile(r"(?i)(?<!if )(?:next step|move forward) (?:is|would be|includes)? ?a? ?(?:call|interview) with (?:you|our)"), "next_step_is_interview"),
    (re.compile(r"(?i)would you be available"), "would_you_be_available"),
    (re.compile(r"(?i)(?:book|pick|select) a time (?:that works|to (?:chat|talk|meet))"), "book_a_time"),
    (re.compile(r"(?i)calendly\.com|calendar\.google\.com/.*appointment"), "scheduling_link"),
]

# --- ASSESSMENT patterns (same provenance note as INTERVIEW_PATTERNS) ---------
ASSESSMENT_PATTERNS = [
    (re.compile(r"(?i)complete (?:a|the|an) (?:assessment|assignment|skills? test|coding challenge)"), "complete_assessment"),
    (re.compile(r"(?i)take-home (?:assignment|project|test)"), "take_home"),
    (re.compile(r"(?i)hackerrank|codesignal|karat\.com"), "assessment_platform"),
]

_ALL_TYPED = (
    ("rejection", REJECTION_PATTERNS),
    ("interview", INTERVIEW_PATTERNS),
    ("assessment", ASSESSMENT_PATTERNS),
    ("confirmation", CONFIRMATION_PATTERNS),
)


# NOTE: signature is (subject, body, sender) -> dict. agents/job_mail_patterns.py has a
# DIFFERENT classify(sender, subject, snippet) -> str|None. job_replies.py imports the
# JOB one; do not cross-wire these (a swap fails silently: dict is always truthy).
def classify(subject: str, body: str, sender: str = "") -> dict:
    """Regex-first classification. Checks rejection/interview/assessment before
    confirmation deliberately — a rejection or interview-invite email often ALSO
    contains "thank you for applying" boilerplate near the top, so the more specific
    (and more actionable) pattern must win when both fire."""
    text = f"{subject}\n{body}"
    for type_name, patterns in _ALL_TYPED:
        for pattern, name in patterns:
            if pattern.search(text):
                return {"type": type_name, "confidence": "high", "matched": name}
    if is_ats_sender(sender):
        # Known ATS domain but no language pattern matched — still worth routing to
        # jobs lane, just without a confident sub-type.
        return {"type": "other", "confidence": "low", "matched": None}
    return {"type": "other", "confidence": "low", "matched": None}


if __name__ == "__main__":
    # Smoke test against the real-mail-sourced examples baked into this file's
    # docstring provenance, so `python agents/mail_patterns.py` is a fast manual check.
    samples = [
        ("Your application for Product Marketing Manager | myTomorrows",
         "After carefully reviewing your experience and motivation, we regret to inform you "
         "that we will not be moving forward.", "no-reply@ashbyhq.com", "rejection"),
        ("Thank you for applying to xCures, Inc.",
         "Thank you for applying to xCures, Inc.! We have received your application and will "
         "review it promptly.", "no-reply@ats.rippling.com", "confirmation"),
        ("Re: Bullseye Strategy opportunity",
         "We appreciate your interest in Bullseye Strategy and the time you've invested. "
         "At this time we will not be moving forward with your application.",
         "no-reply@bullseye-strategy.breezy-mail.com", "rejection"),
    ]
    ok = 0
    for subj, body, sender, expected in samples:
        r = classify(subj, body, sender)
        status = "PASS" if r["type"] == expected else "FAIL"
        ok += status == "PASS"
        print(f"{status}: expected={expected} got={r['type']} (matched={r['matched']})")
    print(f"\n{ok}/{len(samples)} smoke samples passed")
