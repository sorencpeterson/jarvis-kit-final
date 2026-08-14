#!/usr/bin/env python3
"""Field maps for the ATS platforms whose forms are stable enough to fill without an LLM.

WHY THIS EXISTS. Today one application = one `claude -p` session driving a browser:
snapshot the page, reason, act, snapshot again, for every field. That is the entire
cost of the job pipeline and the entire failure surface (an operator that dies mid-form
leaves the job in limbo, and its own success report is unverifiable). But the reasoning
is nearly all wasted: a Greenhouse form asks for a first name in the same input every
time. Reasoning is only worth paying for where the page is genuinely novel.

So: known ATS -> deterministic fill from a table, zero tokens. Novel or messy page ->
hand it to the LLM operator, which is what it is good at. Sourcing was already free;
this makes the common case of applying free too, which is what turns daily volume into
a question of pacing rather than budget.

WHAT IS AND IS NOT VERIFIED. The mapping LOGIC below is pure and fully tested. The
SELECTORS are written against each platform's published form structure and are NOT
verified against a live submission, because verifying them means submitting real
applications to real employers. Treat `confidence` as what it says. Anything less
than "high" should be proven with agents/apply_direct.py --dry-run, which fills and
reports without ever pressing submit, before it is trusted to run unattended.

Selectors are DATA, not code, so correcting one is a one-line change here rather
than a rewrite. When a platform changes its form, fix it here.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# A field spec is: profile key -> list of CSS selectors, tried in order.
# First selector that exists on the page wins. Order matters: most specific first.
#
# `required` fields that resolve to an empty value abort the deterministic attempt
# and hand the job to the LLM operator rather than submitting something incomplete.

GREENHOUSE = {
    "host_match": ("greenhouse.io", "job-boards.greenhouse.io", "boards.greenhouse.io"),
    "confidence": "high",          # simplest and most stable of the major boards
    "fields": {
        "first_name": ["#first_name", "input[name='job_application[first_name]']",
                       "input[autocomplete='given-name']"],
        "last_name": ["#last_name", "input[name='job_application[last_name]']",
                      "input[autocomplete='family-name']"],
        "email": ["#email", "input[name='job_application[email]']",
                  "input[type='email']"],
        "phone": ["#phone", "input[name='job_application[phone]']",
                  "input[type='tel']"],
    },
    "resume": ["input[type='file'][name*='resume']", "#resume", "input[type='file']"],
    "submit": ["#submit_app", "button[type='submit']", "input[type='submit']"],
    "required": ("first_name", "last_name", "email"),
}

LEVER = {
    "host_match": ("lever.co", "jobs.lever.co"),
    "confidence": "medium",
    "fields": {
        "full_name": ["input[name='name']", "#name"],
        "email": ["input[name='email']", "#email", "input[type='email']"],
        "phone": ["input[name='phone']", "#phone", "input[type='tel']"],
        "linkedin": ["input[name='urls[LinkedIn]']", "input[name*='LinkedIn']"],
    },
    "resume": ["input[type='file'][name='resume']", "input[type='file']"],
    "submit": ["button[type='submit']", ".postings-btn[type='submit']"],
    "required": ("full_name", "email"),
}

ASHBY = {
    "host_match": ("ashbyhq.com", "jobs.ashbyhq.com"),
    "confidence": "low",           # React-rendered; selectors shift between releases
    "fields": {
        "full_name": ["input[name='_systemfield_name']", "input[id*='_systemfield_name']"],
        "email": ["input[name='_systemfield_email']", "input[type='email']"],
        "phone": ["input[name='_systemfield_phone']", "input[type='tel']"],
    },
    "resume": ["input[type='file']"],
    "submit": ["button[type='submit']"],
    "required": ("full_name", "email"),
}

WORKABLE = {
    "host_match": ("workable.com", "apply.workable.com"),
    "confidence": "low",
    "fields": {
        "first_name": ["input[name='firstname']", "#firstname"],
        "last_name": ["input[name='lastname']", "#lastname"],
        "email": ["input[name='email']", "input[type='email']"],
        "phone": ["input[name='phone']", "input[type='tel']"],
    },
    "resume": ["input[type='file']"],
    "submit": ["button[type='submit']", "button[data-ui='submit-application']"],
    "required": ("first_name", "last_name", "email"),
}

SPECS = (GREENHOUSE, LEVER, ASHBY, WORKABLE)

# Deliberately absent, and why:
#   workday        multi-screen wizard behind mandatory account creation. A sibling
#                  install went 0-for-8 with an LLM operator. Deterministic filling
#                  cannot create accounts and should not try.
#   icims          heavy iframing plus frequent bot challenges.
#   bamboohr,      too few observations to write a spec worth trusting.
#   paylocity,
#   smartrecruiters
# Everything not listed here routes to the LLM operator, unchanged. This module only
# ever ADDS a cheap path; it never removes the existing one.


def detect(apply_url: str) -> dict | None:
    """Which spec, if any, handles this URL. Suffix-matched on the HOST only, so a
    lookalike path (example.com/greenhouse.io/apply) can never select a spec."""
    try:
        host = (urlparse(apply_url).hostname or "").lower()
    except (ValueError, AttributeError):
        return None
    if not host:
        return None
    for spec in SPECS:
        for h in spec["host_match"]:
            if host == h or host.endswith("." + h):
                return spec
    return None


def _split_name(profile: dict) -> tuple[str, str]:
    first = (profile.get("first_name") or "").strip()
    last = (profile.get("last_name") or "").strip()
    if first and last:
        return first, last
    full = (profile.get("full_name") or "").strip()
    parts = full.split()
    if not parts:
        return first, last
    return first or parts[0], last or (parts[-1] if len(parts) > 1 else "")


def values_for(spec: dict, profile: dict) -> dict:
    """profile -> {field_key: value} for this spec's fields. Pure; no browser.

    Only ever emits values the profile actually holds. A field with nothing behind
    it is omitted rather than filled with a placeholder, because a plausible-looking
    wrong answer on a real application is worse than a blank one.
    """
    first, last = _split_name(profile)
    full = (profile.get("full_name") or f"{first} {last}").strip()
    src = {
        "first_name": first,
        "last_name": last,
        "full_name": full,
        "email": (profile.get("email") or "").strip(),
        "phone": (profile.get("phone") or "").strip(),
        "linkedin": (profile.get("linkedin") or "").strip(),
    }
    return {k: src[k] for k in spec["fields"] if src.get(k)}


def missing_required(spec: dict, values: dict) -> list[str]:
    """Required fields this profile cannot fill. Non-empty means: do not attempt."""
    return [k for k in spec["required"] if not (values.get(k) or "").strip()]


# A page carrying any of these is not a plain form: hand it to a human, never
# auto-submit past it. Matched against page text, lowercased.
_WALL_MARKERS = (
    "recaptcha", "i'm not a robot", "im not a robot", "hcaptcha", "cloudflare",
    "verify you are human", "create an account", "sign in to apply",
    "create your account", "verification code", "one-time code",
)


def wall_reason(page_text: str) -> str:
    """'' when the page looks like a plain form, else the wall word to skip with.

    Deliberately conservative: a false positive costs one job routed to the human
    pile, a false negative means blindly submitting into a CAPTCHA or an account
    wall. The reason words returned here are members of jobs._HUMAN_FINISHABLE, so
    the job lands in the finish-by-hand pile rather than being lost.
    """
    low = (page_text or "").lower()
    for m in _WALL_MARKERS:
        if m in low:
            if "captcha" in m or "robot" in m or "human" in m or "cloudflare" in m:
                return "captcha"
            if "code" in m:
                return "verify"
            return "login"
    return ""


def summary() -> str:
    rows = []
    for s in SPECS:
        rows.append(f"  {s['host_match'][0]:<24} confidence={s['confidence']:<7} "
                    f"{len(s['fields'])} fields")
    return "\n".join(rows)


if __name__ == "__main__":
    print("\nDeterministic ATS form specs (zero LLM calls):\n")
    print(summary())
    print("\nEverything else routes to the LLM operator, unchanged.\n")
