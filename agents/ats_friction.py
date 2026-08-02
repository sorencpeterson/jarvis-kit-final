#!/usr/bin/env python3
"""ATS friction scoring — route apply attempts around CAPTCHA/login/2FA walls (2026-07-15).

[OWNER] applies to US-remote roles through an operator that fills each ATS form. Some ATS
platforms force a CAPTCHA, an account/login, or a 2FA/verification code far more often
than others. We do NOT defeat those controls (out of scope, on purpose). We ROUTE around
them: order the cleanest ATSes first, and stop spending automated attempts on the ones
that almost always wall, sending those straight to the human-finish pile.

Two signals combine per ATS:
  - a STATIC prior walled-rate (what the platform is generally like), and
  - a LEARNED rate from [OWNER]'s OWN history (jobs skipped as a wall vs jobs applied),
Bayesian-smoothed so an early noisy signal (1 skip on 1 try) can't strand a whole ATS.
The prior acts as PRIOR_STRENGTH pseudo-observations; the learned rate takes over as real
samples accumulate.

Pure module: it never reads the store. Callers pass the jobs list they already loaded.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# Reasons a job was set to "skipped" that a HUMAN can still finish (a wall, not a real
# disqualifier). These are what "walled" means for the learned rate and for the manual
# pile. closed/unqualified/missing_info are genuine no-gos, never counted as walls.
WALLED_REASONS = ("captcha", "login", "verify", "wizard")

# Statuses that mean the application actually went out (the denominator's "clean" side).
APPLIED_STATUSES = ("applied", "confirmed", "interview", "replied", "rejected")

# registrable domain -> canonical ATS key. Matched SUFFIX-aware against the apply_url host
# (host == domain or host.endswith("." + domain)), never substring: substring matching lets
# a hostile "greenhouse.io.evil.com" read as greenhouse (Codex 2026-07-15). No page fetch.
_HOST_ATS = (
    ("greenhouse.io", "greenhouse"),
    ("lever.co", "lever"),
    ("ashbyhq.com", "ashby"),
    ("workable.com", "workable"),
    ("jazz.co", "jazzhr"), ("applytojob.com", "jazzhr"),
    ("breezy.hr", "breezy"),
    ("rippling.com", "rippling"),
    ("recruitee.com", "recruitee"),
    ("careerplug.com", "careerplug"),
    ("smartrecruiters.com", "smartrecruiters"),
    ("icims.com", "icims"),
    ("myworkdayjobs.com", "workday"), ("workday.com", "workday"),
    ("taleo.net", "taleo"),
    ("jobvite.com", "jobvite"),
    ("bamboohr.com", "bamboohr"),
    ("paylocity.com", "paylocity"),
    ("adp.com", "adp"),
)

# Static prior walled-rate per ATS (0.0 clean .. 1.0 always walls). Best-guess platform
# behavior; the learned rate overrides as [OWNER]'s own data accrues. Kept deliberately
# middling for the login-heavy enterprise ATSes (icims/workday/taleo/adp) because their
# wall is usually mandatory account creation, which is a human-finish, not a bot job.
_PRIOR = {
    "greenhouse": 0.10, "lever": 0.22, "ashby": 0.10, "recruitee": 0.12,
    "workable": 0.30, "jazzhr": 0.45, "breezy": 0.25, "rippling": 0.20,
    "careerplug": 0.30, "smartrecruiters": 0.32, "jobvite": 0.35, "bamboohr": 0.28,
    "paylocity": 0.45, "icims": 0.62, "workday": 0.70, "taleo": 0.66, "adp": 0.55,
    "unknown": 0.42,
}

PRIOR_STRENGTH = 4.0      # prior counts as this many pseudo-observations (ranking smoothing)
DIVERT_RATE = 0.80        # learned walled-rate at/above which we stop auto-attempting
DIVERT_MIN_SAMPLES = 10   # ...but only once this many real attempts back it up (Codex: 5 was
#                           too twitchy on a personal-scale sample; 10 needs a real pattern)


def _host_match(host: str) -> str:
    """Suffix-aware host -> ATS. host must EQUAL the domain or be a subdomain of it."""
    for domain, ats in _HOST_ATS:
        if host == domain or host.endswith("." + domain):
            return ats
    return ""


def detect_ats(apply_url: str = "", source: str = "") -> str:
    """Canonical ATS key from the apply_url host, falling back to the `source` label.
    Returns 'unknown' when neither identifies a known platform (board-listing URLs that
    redirect elsewhere, or a bare board name like 'remoteok'). No network."""
    host = ""
    try:
        host = (urlparse(apply_url).hostname or "").lower()
    except (ValueError, TypeError):
        host = ""
    if host:
        hit = _host_match(host)
        if hit:
            return hit
    # fall back to the source label (hiring.cafe sets it to the ATS name directly). Match a
    # domain's leading label (greenhouse.io -> "greenhouse") against the normalized source.
    s = re.sub(r"[^a-z0-9]", "", (source or "").lower())
    if s:
        for domain, ats in _HOST_ATS:
            lead = re.sub(r"[^a-z0-9]", "", domain.split(".")[0])
            if lead and lead in s:
                return ats
        if s in _PRIOR:
            return s
    return "unknown"


def _history_counts(jobs_list: list) -> dict:
    """{ats: (walled_count, applied_count)} from [OWNER]'s own job history."""
    out: dict[str, list] = {}
    for j in jobs_list or []:
        if not isinstance(j, dict):
            continue
        ats = detect_ats(j.get("apply_url") or "", j.get("source") or "")
        rec = out.setdefault(ats, [0, 0])
        status = j.get("status")
        reason = (j.get("reason") or "").lower()
        if status == "skipped" and any(reason.startswith(w) for w in WALLED_REASONS):
            rec[0] += 1
        elif status in APPLIED_STATUSES:
            rec[1] += 1
    return out


def walled_rate(ats: str, jobs_list: list | None = None) -> float:
    """Bayesian-smoothed walled-rate for an ATS: prior blended with learned history.
    rate = (walled + prior*K) / (walled + applied + K). With no history it equals the
    prior; it converges to the empirical rate as real samples accumulate."""
    prior = _PRIOR.get(ats, _PRIOR["unknown"])
    walled = applied = 0
    if jobs_list:
        walled, applied = _history_counts(jobs_list).get(ats, [0, 0])
    return (walled + prior * PRIOR_STRENGTH) / (walled + applied + PRIOR_STRENGTH)


def friction_score(job: dict, jobs_list: list | None = None) -> float:
    """0.0 (cleanest) .. 1.0 (always walls) for a single job. Sort ascending to try the
    least-walled ATSes first."""
    ats = detect_ats(job.get("apply_url") or "", job.get("source") or "")
    return walled_rate(ats, jobs_list)


def friction_band(job: dict, jobs_list: list | None = None) -> int:
    """Coarse 0/1/2 band for apply ORDERING. Bands (not the raw score) drive the sort so fit
    still dominates WITHIN a band and only the genuinely wall-heavy ATSes sink below cleaner
    ones: band 0 = mostly clean (greenhouse/lever/ashby/workable/jazzhr/breezy all land here,
    so the common case stays pure fit-order), 1 = login-wall-prone (icims/adp/taleo), 2 =
    near-always walls (workday). A soft nudge, distinct from should_divert's hard removal."""
    s = friction_score(job, jobs_list)
    if s < 0.5:
        return 0
    if s < 0.7:
        return 1
    return 2


def should_divert(job: dict, jobs_list: list | None = None) -> tuple[bool, str]:
    """(divert?, reason). True when this ATS has EARNED a divert in [OWNER]'s own data:
    a high LEARNED walled-rate backed by enough real attempts. Purely prior-driven ATSes
    are never diverted (we still let the operator try once and learn from it) so a bad
    static guess can't silently kill a platform. Diverted jobs go straight to the
    human-finish pile instead of burning an operator attempt."""
    ats = detect_ats(job.get("apply_url") or "", job.get("source") or "")
    # NEVER divert on the 'unknown' bucket (Codex 2026-07-15): it pools every board-listing
    # and custom-domain job, so its aggregate wall-rate says nothing about any one of them --
    # diverting it would strand a pile of unrelated jobs the operator could have submitted.
    if ats == "unknown":
        return False, ""
    walled, applied = (_history_counts(jobs_list).get(ats, [0, 0]) if jobs_list else [0, 0])
    n = walled + applied
    if n < DIVERT_MIN_SAMPLES:
        return False, ""
    empirical = walled / n if n else 0.0
    if empirical >= DIVERT_RATE:
        return True, f"ats_wall_divert ({ats}: {walled}/{n} attempts walled; human-finish is faster)"
    return False, ""
