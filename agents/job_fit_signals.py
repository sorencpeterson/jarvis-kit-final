#!/usr/bin/env python3
"""D223, D229, D230, D231, D248, D251, D254, D258: additive fit/quality signals
layered on TOP of jobs.py's existing guards. Every function here is read-only
scoring/classification -- NONE of them touch or weaken the postmortem guards
already enforced in jobs.approved_to_apply() (employer dedupe, 8+ YOE gate,
fit floor 62). This module only ADDS new skip reasons alongside those, on the
same additive pattern jobs.py's own `_blocked()` already uses.

D223 resume keyword-match: score overlap between a job's title/query text and
[OWNER]'s real skill/resume terms (sourced from application_profile.json +
store/star_bank.md + the JOBS-SPRINT-10 resume-critique bullets -- the closest
things to verified resume content available; get_resume via the MCP is
rate-limited per agents/job_mcp_notes.md, so this does NOT wait on it).
<40% overlap -> skip reason 'keyword_mismatch'.

D229 salary-fit gate: comp_max present AND below the [SALARY_ANCHOR] target's floor
(config job_min_yearly already gates this in jobs.py at SOURCE time; this adds
a SECOND, slightly different check at APPLY time for jobs whose comp_max
became known only after sourcing, or where config changed after a job was
already queued -- catches drift, never loosens the original gate).

D230 seniority-ladder scoring: title-tier score bonus/penalty (Manager sweet
spot per the postmortem's 68% Manager-submitted, near-zero response so far
data + the postmortem's explicit call-out that Director/VP-with-10-YOE reqs
are close to auto-reject territory).

D231 company blocklist: applied-and-rejected 2x, or manually blocklisted
(spam listers like the postmortem's "Clients Blackbox, Inc." pattern).

D248 red-flag lint: unpaid trial / commission-only / 60+ hr language in the
job's own text fields.

D251 recency decay (steeper): jobs.py's _fit() already applies a linear
age penalty; this adds an explicit STEEPER decay multiplier for the >14d
tier specifically, as its own inspectable signal (score_seniority /
score_recency_decay are additive score components a caller can layer onto
jobs._fit(), not a replacement for it).

D254 multi-location dedupe: the postmortem's concrete Sidetrade case (same
role posted 4x across Paris/Calgary/US/London, all 4 got submitted). jobs.py's
_ckey() already dedupes on normalized company+title, which SHOULD have caught
this -- the postmortem gap was that _ckey only truncates title to 24 chars and
doesn't strip location tokens, so "VP Demand Generation - Paris" and
"VP Demand Generation - Calgary" collapse to the same key today (24-char title
prefix "vpdemandgenerationparis" vs "...calgary" -- actually these DON'T
collide because location is often a suffix past char 24, or embedded in the
apply_url/id, not the title). This module adds an explicit, title-based
location-token strip BEFORE the existing dedupe key so location-variant
reposts collapse correctly regardless of where in the string the location
sits.

D258 recruiter-vs-direct detection: agency/recruiter language vs a real
employer's own listing, tagged (never blocks -- see D258 section) so it can
feed source-quality ranking (D303) later.

Every check here returns EITHER a reason-string (means: block) or '' / None
(means: pass) exactly like jobs.approved_to_apply()'s own `_blocked()`, so
wiring into that function is a single additive `or _extra_block(x)` line.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import jobs  # noqa: E402

BLOCKLIST_STORE = ROOT / "store" / "job_blocklist.json"

# ---- D223 resume keyword-match ----
# Sourced, not invented: application_profile.json fields (current_title,
# experience_stance skills list) + store/star_bank.md real story nouns +
# JOBS-SPRINT-10.md's literal recommended resume bullets (the closest thing
# to verified resume text on file -- get_resume is rate-limited, see
# agents/job_mcp_notes.md). Kept as a flat term list, not a frequency model:
# TF overlap against a 3-5 word job title is noisy at n=1 word counts, so
# this uses SET overlap (does the job mention terms he'd list?) which is the
# more robust of the two for short job-title-only text.
_RESUME_TERMS = {
    "marketing", "seo", "search", "web", "wordpress", "elementor", "growth",
    "demand", "generation", "lifecycle", "digital", "brand", "ppc", "paid",
    "performance", "ecommerce", "commerce", "social", "email", "sms", "crm",
    "acquisition", "content", "operations", "automation", "gohighlevel",
    "ghl", "analytics", "reporting", "conversion", "cro", "landing",
    "campaigns", "google", "ads", "meta", "revops", "revenue", "product",
    "leadership", "coo", "agency", "fractional", "delivery", "fulfillment",
    "onboarding", "retention", "churn", "funnel", "pipeline", "manager",
    "specialist", "lead", "strategist", "director", "head", "developer",
    "frontend", "front-end", "css", "html", "landing-page", "cms", "saas",
    "b2b", "smb", "startup", "remote", "customer", "success", "solutions",
    "implementation", "consultant", "consultancy",
}
_TERM_STOP = {"the", "and", "for", "with", "you", "our", "are", "at", "of", "a"}


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9][a-z0-9-]+", (text or "").lower())
            if t not in _TERM_STOP and len(t) > 1}


def resume_keyword_score(job: dict) -> float:
    """Overlap ratio (0.0-1.0): of the job's own meaningful tokens (title +
    query + seniority, same source job_cover._job_text() reads -- jobs.jsonl
    doesn't store a full description), what fraction also appear in
    _RESUME_TERMS. A job titled 'Marketing Manager' scores high near-trivially
    (both words are resume terms); a job titled 'Warehouse Associate' that
    slipped through TITLE_KW some other way would score near zero. Returns
    1.0 (never blocks) if the job has no meaningful tokens at all, since an
    empty signal shouldn't be treated as a mismatch."""
    text = " ".join([job.get("title") or "", job.get("query") or "", job.get("seniority") or ""])
    toks = _tokenize(text)
    if not toks:
        return 1.0
    hits = sum(1 for t in toks if t in _RESUME_TERMS or any(t in rt or rt in t for rt in _RESUME_TERMS if len(rt) > 3))
    return round(hits / len(toks), 3)


def keyword_mismatch_reason(job: dict, floor: float = 0.40) -> str:
    score = resume_keyword_score(job)
    if score < floor:
        return f"keyword_mismatch ({int(score*100)}% overlap, floor {int(floor*100)}%)"
    return ""


# ---- D229 salary-fit gate (apply-time re-check, additive to source-time gate) ----
def salary_gate_reason(job: dict) -> str:
    cm = job.get("comp_max")
    if not cm:
        return ""  # unknown comp -- never block on missing data, matches jobs.py's own stance
    if (job.get("comp_unit") or "year") != "year":
        # comp_max is hourly (or another non-annual unit) -- not comparable to the annual floor
        # (Codex end-to-end pass, 2026-07-14): a raw $50/hr rate must not read as "< $40000/yr" and
        # get gated out. The sourcing-side comp_unit fix already keeps hourly in its own unit.
        return ""
    try:
        floor = jobs._min_yearly()
    except Exception:  # noqa: BLE001
        floor = 95000
    if cm < floor:
        return f"salary_gate (comp_max ${cm} < floor ${floor})"
    return ""


# ---- D230 seniority-ladder scoring ----
# Postmortem data: 68% of the 135 submitted were "Manager" titled, that's the
# volume sweet spot; Director (10%) and VP/Head (5%) skewed toward the 8-10
# YOE reqs that trip the existing YOE gate anyway, and the postmortem calls
# out Director/VP-with-10-YOE as "close to auto-reject territory... regardless
# of actual capability." This is a SCORE ADJUSTMENT (feeds into a combined
# score a caller can add to jobs._fit()'s own output), not a hard gate --
# the YOE gate already hard-blocks the worst offenders; this just deprioritizes
# the rest of that tier in ranking.
_SENIORITY_BONUS = {
    "manager": 8, "specialist": 5, "lead": 4, "coordinator": 2,
    "senior": 0, "director": -6, "vp": -10, "head": -8, "chief": -10,
    "principal": -4, "staff": -2, "associate": 3, "entry": -6,
}


def seniority_score(job: dict) -> int:
    """Additive bonus/penalty from title-tier language. Sums every matching
    tier word found (a title can legitimately carry more than one, e.g.
    'Senior Marketing Manager' = senior(0) + manager(8) = 8) rather than
    picking just one, so compound titles score on their real composition."""
    t = (job.get("title") or "").lower()
    bonus = 0
    for kw, val in _SENIORITY_BONUS.items():
        if re.search(r"\b" + re.escape(kw) + r"\b", t):
            bonus += val
    return bonus


# ---- D231 company blocklist ----
def _load_blocklist() -> dict:
    try:
        return json.loads(BLOCKLIST_STORE.read_text())
    except (OSError, json.JSONDecodeError):
        return {"manual": [], "auto_rejected_2x": []}


def _save_blocklist(d: dict) -> None:
    BLOCKLIST_STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = BLOCKLIST_STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=1))
    tmp.replace(BLOCKLIST_STORE)


def _conorm(company: str) -> str:
    # Delegates to jobs._conorm (R2-17, 2026-07-13 hunt): that function now also strips
    # legal-entity suffixes (Inc/LLC/Corp/Co/...) so 'Acme' and 'Acme, Inc.' collapse to the
    # same key. This module keeps its own local `_conorm` name (existing callers/tests use
    # it), but reuses the canonical implementation instead of a second, drifting copy that
    # would otherwise let a blocklisted 'Acme' miss a later 'Acme, Inc.' rejection record.
    return jobs._conorm(company)


def rebuild_auto_blocklist() -> list[str]:
    """Companies to refuse further applications to, rebuilt from rejection
    history in jobs.jsonl. Two triggers, unioned:

      1. Rejected 2+ times (the original rule): refuse a 3rd application to the
         same employer even after a status refresh.
      2. Rejected ONCE with reject_reason == 'state_restriction' (D-lane reject-
         intel, 2026-07-07): a company that can't legally employ an SD resident
         this month can't next month either, and [OWNER] has chosen to keep his
         South Dakota domicile, so one geo-reject is enough to stop wasting an
         application slot on that employer. This is stricter than the 2x rule ON
         PURPOSE, and only for this one structural, non-recurring-fixable reason.

    Only reads jobs.jsonl (statuses + the reject_reason job_replies now writes),
    no new store dependency beyond the blocklist cache file itself."""
    from collections import Counter
    rej = Counter()
    geo = set()
    for j in jobs.load_jobs():
        if j.get("status") == "rejected":
            c = _conorm(j.get("company"))
            if c:
                rej[c] += 1
                if j.get("reject_reason") == "state_restriction":
                    geo.add(c)
    auto = sorted(set(c for c, n in rej.items() if n >= 2) | geo)
    d = _load_blocklist()
    d["auto_rejected_2x"] = auto
    _save_blocklist(d)
    return auto


def blocklist_reason(job: dict) -> str:
    d = _load_blocklist()
    c = _conorm(job.get("company"))
    if not c:
        return ""
    if c in {_conorm(x) for x in d.get("manual", [])}:
        return "blocklisted (manual)"
    if c in set(d.get("auto_rejected_2x", [])):
        return "blocklisted (rejected 2x)"
    return ""


# ---- D248 red-flag lint ----
_RED_FLAGS = [
    (re.compile(r"\bunpaid\s+trial\b", re.I), "unpaid trial"),
    (re.compile(r"\bcommission[\s-]only\b", re.I), "commission-only"),
    (re.compile(r"\b(60|70|80)\s*\+?\s*(hour|hr)s?\b", re.I), "60+ hr expectation"),
    (re.compile(r"\bmust\s+be\s+willing\s+to\s+work\s+weekends\b", re.I), "mandatory weekends"),
    (re.compile(r"\bequity\s+only\b", re.I), "equity-only comp"),
    (re.compile(r"\bno\s+pay\s+until\b", re.I), "deferred/no pay"),
]


def red_flags(job: dict) -> list[str]:
    """Scans whatever text the job record carries (title/query -- jobs.jsonl
    has no full description field). Returns a list of matched flag labels,
    never a block -- red flags on a title alone are rare and this is meant to
    SURFACE for a human glance (dashboard/needs-manual), not auto-skip on
    thin text. A caller that wants a hard block can treat a non-empty list
    as one; this module doesn't force that choice."""
    text = " ".join([job.get("title") or "", job.get("query") or ""])
    return [label for pat, label in _RED_FLAGS if pat.search(text)]


# ---- D251 recency decay (steeper tier on top of jobs._fit()'s linear one) ----
def recency_decay_penalty(job: dict) -> int:
    """jobs._fit() already does `15 - age` linear. This adds an EXTRA steep
    penalty specifically for the >14d tier (postmortem/D251 language: "halved"
    beyond 14d), as an inspectable additive component rather than changing
    the existing formula (which stays untouched per the guard-preservation
    rule)."""
    age = jobs._age_days(job.get("posted"))
    if age is None or age <= 14:
        return 0
    return -min(20, (age - 14) * 2)  # -2/day past day 14, capped at -20


# ---- D254 multi-location dedupe (postmortem Sidetrade case) ----
_LOC_TOKENS = re.compile(
    r"\b(paris|calgary|london|toronto|new\s*york|nyc|sf|san\s*francisco|"
    r"chicago|austin|remote|us[\s,-]*only|canada|uk|europe|emea|apac|"
    r"united\s*states|usa|hybrid|on-?site)\b", re.I)


def location_stripped_ckey(company: str, title: str) -> str:
    """Same normalization jobs._ckey() does, but strips known location/region
    tokens out of the title FIRST so 'VP Demand Generation - Paris' and
    'VP Demand Generation (Calgary)' collapse to the identical key regardless
    of where the location token sits in the string. jobs._ckey() truncates to
    24 chars post-normalization, which can already coincidentally collide or
    miss depending on prefix length; this is a stricter, location-aware key
    a caller can check IN ADDITION to jobs._ckey() (never replaces it --
    jobs.py's own dedupe stays as-is per the guard-preservation rule)."""
    t = _LOC_TOKENS.sub("", (title or "").lower())
    c = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    t = re.sub(r"[^a-z0-9]", "", t)[:32]
    return c + "|" + t


# R2-18 tie-break ranks: lower wins (survives). Already-submitted/terminal always outranks a
# merely-approved sibling -- resubmitting to the same effective role is the exact harm being
# prevented, and the submitted one already happened and can't be undone.
_DUPE_RANK_STATUS = {"applied": 0, "confirmed": 0, "interview": 0, "replied": 0, "approved": 1}


def _dupe_rank(rec: dict) -> tuple:
    """Deterministic tie-break key for 'which of a group of location-variant duplicates
    survives' (R2-18). Already-submitted beats merely-approved; within the same tier the
    earliest `created` wins (the one [OWNER] saw/approved first); id is the final tiebreak so
    ranking never depends on iteration order or a missing/equal timestamp."""
    return (_DUPE_RANK_STATUS.get(rec.get("status"), 1), rec.get("created") or "", str(rec.get("id") or ""))


def multi_location_dupe_reason(job: dict, all_jobs: list[dict]) -> str:
    """Flags a job as a location-variant repost of one already approved/
    submitted, using the stricter location-stripped key. Only fires when the
    OTHER record is a DIFFERENT id (never flags a job against itself).

    R2-18 (2026-07-13 hunt): this used to fire whenever ANY other same-key record was
    approved/applied/confirmed/interview/replied. When TWO location-variant reposts were
    BOTH still 'approved' (the common case -- neither has been acted on yet), each one saw
    the OTHER as a dup and got blocked -> mutual skip -> ZERO candidates survived, instead of
    the intended one. Now a job is only blocked if a BETTER-ranked (see _dupe_rank) duplicate
    exists; the single best-ranked record in the group never finds one and survives."""
    key = location_stripped_ckey(job.get("company"), job.get("title"))
    my_rank = _dupe_rank(job)
    for other in all_jobs:
        if other.get("id") == job.get("id"):
            continue
        if other.get("status") not in ("approved", "applied", "confirmed", "interview", "replied"):
            continue
        if location_stripped_ckey(other.get("company"), other.get("title")) != key:
            continue
        if _dupe_rank(other) < my_rank:
            return f"multi_location_dupe (variant of {other.get('id')})"
    return ""


# ---- cross-listed requisition guard (sibling field report 2026-08-12, F1) ----
_REQ_NUM = re.compile(r"\b\d{4,}\b")


def _req_numbers(title: str) -> set[str]:
    """4+ digit requisition numbers in a title, excluding bare years: 'Marketing
    Manager (32693)' carries a req number; 'Marketing Manager 2026' carries a date."""
    return {n for n in _REQ_NUM.findall(title or "")
            if not (1900 <= int(n) <= 2099)}


def req_number_dupe_reason(job: dict, all_jobs: list[dict]) -> str:
    """One requisition posted under TWO company names (a staffing firm and the end
    client, or two sibling brands) evades every company-keyed dedupe -- two real
    cases hit a sibling install ('Marketing Manager (32693)' under both the
    consultancy and the client; adjacent Greenhouse ids for the same role under two
    Precision-brand names). A shared requisition number in the title is
    company-AGNOSTIC evidence it is the same req; applying twice reads as careless
    and burns the one-per-employer guard on a duplicate. Same better-ranked-survivor
    rule as multi_location_dupe_reason (R2-18) so two still-approved siblings can't
    mutually block each other into zero candidates."""
    nums = _req_numbers(job.get("title"))
    if not nums:
        return ""
    my_rank = _dupe_rank(job)
    for other in all_jobs:
        if other.get("id") == job.get("id"):
            continue
        if other.get("status") not in ("approved", "applied", "confirmed", "interview", "replied"):
            continue
        if not (nums & _req_numbers(other.get("title"))):
            continue
        if _dupe_rank(other) < my_rank:
            return f"req_number_dupe (same requisition as {other.get('id')})"
    return ""


# ---- D232 remote-verification (hybrid disguised as remote) ----
_HYBRID_TELL = re.compile(
    r"\b(hybrid|(\d+)\s*days?\s*(a|per)\s*week\s*(in|on-?site|in-?office)|"
    r"must\s+(be\s+)?(willing\s+to\s+)?(relocate|commute)|"
    r"in-?office\s+\d+\s*days?|onsite\s+\d+\s*days?)\b", re.I)


def hybrid_disguised_as_remote(job: dict) -> bool:
    """Flags a job whose own title/query text carries hybrid-tell language
    even though it made it through jobs.py's is_us/remote-eligible filters.
    jobs.jsonl doesn't store a full description (see job_cover._job_text()),
    so this only catches hybrid language that leaked into the TITLE itself
    (e.g. 'Marketing Manager (Hybrid, 3 days/week)') — a real but partial
    signal, not a complete remote-verification system. Tag/report signal,
    same as is_recruiter_listing below — doesn't block on its own since a
    false positive (a title that happens to contain 'hybrid' in an unrelated
    sense) has real cost and this text source is thin."""
    text = " ".join([job.get("title") or "", job.get("query") or ""])
    return bool(_HYBRID_TELL.search(text))


# ---- D236 referral-path check ----
def referral_path_note(job: dict) -> str:
    """LinkedIn-connections-at-target-company surfacing is explicitly
    networking.py's lane per CLAUDE.md (LinkedIn automation lives there, not
    in this jobs lane) — this jobs lane has no LinkedIn session/API access to
    check connections live. What this CAN do without crossing that boundary:
    build the exact manual-check URL so a human (or a future networking.py-
    lane integration) has a one-click path to check, without this module
    scraping or automating LinkedIn itself."""
    company = (job.get("company") or "").strip()
    if not company:
        return ""
    return ("https://www.linkedin.com/search/results/people/?keywords="
            + re.sub(r"\s+", "%20", company) + "&network=%5B%22F%22%5D")  # F = 1st-degree connections


# ---- D258 recruiter-vs-direct detection (tag only, never blocks) ----
_RECRUITER_MARKERS = re.compile(
    r"\b(staffing|recruiting|recruitment|talent\s+partners?|search\s+partners?|"
    r"headhunt|placement\s+agency|clients?\b.{0,20}\binc\b|on\s+behalf\s+of\s+our\s+client)\b",
    re.I)


def is_recruiter_listing(job: dict) -> bool:
    """Company-name heuristic for the postmortem's 'Clients Blackbox, Inc.'
    pattern (agency/job-board spam posting near-identical titles). Tag only
    -- feeds D303 source-quality ranking later, never gates on its own since
    real recruiters do place real candidates and this is a cheap heuristic,
    not a proven-bad signal."""
    return bool(_RECRUITER_MARKERS.search(job.get("company") or ""))


# ---- D-lane state-eligibility pre-filter (needs jobs.py's new description field) ----
# Motivation (2026-07-07 audit): [OWNER]'s South Dakota domicile triggers geo
# auto-rejects at companies that "can only hire in the states listed". The
# rejection is now LEARNED (classify_reject_reason -> blocklist), but the
# cheaper win is to never apply in the first place when the job's OWN description
# already spells out an eligible-states list that excludes SD. This is a
# PRE-filter that reads job["description"] (jobs._extract() now captures it).
#
# HARD SAFETY RULE: fail-open. Return "" (allow) on any doubt -- no description,
# no geo language, an allow-list we can't confirm excludes SD, etc. Over-skipping
# a winnable job is worse than one wasted geo-reject (which the learning loop
# already catches downstream). We only skip when the description AFFIRMATIVELY
# proves SD is ineligible.
_SD_TOKENS = re.compile(r"\b(south\s*dakota|s\.?\s*d\.?|\bSD\b)\b", re.I)
# Any US state name -- used to confirm an allow-list window ACTUALLY enumerates
# states (so "must reside in one of the following states." with the list living
# elsewhere, e.g. the structured workplace_states field, does NOT get read as
# 'SD absent -> ineligible'; that would over-skip. Only a window that names real
# states can prove SD's absence from an in-prose list).
_US_STATE_NAMES = re.compile(
    r"\b(alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|"
    r"florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|"
    r"maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|"
    r"nebraska|nevada|new\s+hampshire|new\s+jersey|new\s+mexico|new\s+york|"
    r"north\s+carolina|north\s+dakota|ohio|oklahoma|oregon|pennsylvania|rhode\s+island|"
    r"south\s+carolina|south\s+dakota|tennessee|texas|utah|vermont|virginia|"
    r"washington|west\s+virginia|wisconsin|wyoming)\b", re.I)
# ALLOW-list lead-ins: "we can only hire in <list>", "must reside in one of <list>",
# "eligible states: <list>". If SD is NOT in the text that follows, it's ineligible.
_ALLOW_LEADIN = re.compile(
    r"(must reside in (?:one of )?|only (?:able to |ableto )?hire (?:in|within) |"
    r"eligible states\s*:?|can only (?:hire|employ) (?:in|within) |"
    r"hiring (?:is )?(?:only )?(?:available |open )?(?:in|within|to) (?:the )?(?:following )?states|"
    r"authorized to (?:hire|employ) (?:in|within) |"
    r"currently (?:only )?(?:hire|employ) (?:in|within) )", re.I)
# DENY-list lead-ins: "not available in <list>", "cannot hire in <list>",
# "excluding <list>". If SD IS in the text that follows, it's ineligible.
_DENY_LEADIN = re.compile(
    r"(not available in |cannot hire in |can(?:'|no)t (?:hire|employ) in |"
    r"unable to (?:hire|employ) in |excluding |not (?:hiring|employing) in |"
    r"we do not (?:hire|employ) in )", re.I)


def _window_has_sd(text: str, at: int, span: int = 240) -> bool:
    """Does South Dakota appear in the ~span chars FOLLOWING a lead-in match?
    A state list right after 'eligible states:' is usually short; 240 chars
    comfortably covers a 50-state or a handful-of-states enumeration without
    bleeding into an unrelated later paragraph."""
    return bool(_SD_TOKENS.search(text[at:at + span]))


# Restriction language that, when present ANYWHERE in the prose, means the
# workplace_states list is an ELIGIBILITY list (not just a "where the office is"
# tag). Only then does a structured states-without-SD trigger a skip -- a bare
# "New York, US" workplace tag on an otherwise-remote role must NEVER skip.
_RESTRICTION_TELL = re.compile(
    r"(must reside|only (?:able to )?hire|eligible (?:to work |for employment )?in|"
    r"located in (?:one of |specific )|registered to employ|"
    r"authorized to (?:work|hire|employ)|residents? of|based in (?:one of )?)", re.I)


def state_eligibility_reason(job: dict) -> str:
    """Return a skip reason if the job AFFIRMATIVELY proves South Dakota is
    ineligible, else "" (allow). FAIL-OPEN by contract: no description / no
    structured states / no geo language -> "" (never over-skip on missing data).

      (a) PROSE allow-list ("must reside in one of", "only hire in", "eligible
          states:" followed by a state list): skip UNLESS SD is in that list.
      (b) PROSE deny-list ("not available in", "cannot hire in", "excluding"):
          skip only if SD appears in that clause.
      (c) STRUCTURED workplace_states list from the blob: skip ONLY when the prose
          ALSO carries restriction language (so the states are an eligibility
          list, not a mere office-location tag) AND SD is not in the list. This
          belt-and-suspenders keeps a plain single-state office tag on a remote
          role from ever over-skipping.

    The FIRST proof of ineligibility wins."""
    desc = job.get("description") or ""
    # (a)/(b) prose checks first (the brief's primary contract).
    if desc:
        for mm in _DENY_LEADIN.finditer(desc):
            if _window_has_sd(desc, mm.end()):
                return "state_ineligible (SD excluded by job's own location list)"
        for mm in _ALLOW_LEADIN.finditer(desc):
            win = desc[mm.end():mm.end() + 240]
            # Only conclude "SD is absent from the list" when the window ACTUALLY
            # enumerates states. A lead-in whose list lives in the structured
            # field (window has no state names) must fall through to (c), not
            # skip here -- otherwise we over-skip an SD-eligible job.
            if _US_STATE_NAMES.search(win) and not _SD_TOKENS.search(win):
                return "state_ineligible (SD absent from job's eligible-states list)"
    # (c) structured states list, guarded by restriction language in the prose.
    wstates = job.get("workplace_states") or []
    if isinstance(wstates, str):
        wstates = [wstates]
    if wstates and desc and _RESTRICTION_TELL.search(desc):
        joined = " ".join(str(s) for s in wstates)
        if not _SD_TOKENS.search(joined):
            return "state_ineligible (SD absent from job's eligible-states list [structured])"
    return ""  # no geo proof of ineligibility -> allow


def _state_filter_on() -> bool:
    """Config knob store/config.json:job_state_filter, DEFAULT FALSE. [OWNER] has
    NOT opted into auto-skipping geo-ineligible jobs yet; this ships OFF and is
    ready to flip. When off, callers still COMPUTE the would-skip count for
    visibility (see run()), they just don't act on it."""
    try:
        return bool(json.loads((ROOT / "store" / "config.json").read_text()).get("job_state_filter", False))
    except (OSError, json.JSONDecodeError):
        return False


# ---- combined additive check, safe to layer into jobs.approved_to_apply() ----
def extra_block_reason(job: dict, all_jobs: list[dict] | None = None) -> str:
    """Single entrypoint bundling every ADDITIVE guard in this module, checked
    in cheapest-first order. Returns '' if the job clears all of them. This is
    the one function jobs.py would call alongside its own `_blocked()` --
    see the docstring at the top of this file for the wiring contract. Never
    calls jobs.set_status() itself (leaves side effects to the caller, same
    separation jobs.py's own _blocked() keeps from approved_to_apply())."""
    r = keyword_mismatch_reason(job)
    if r:
        return r
    r = salary_gate_reason(job)
    if r:
        return r
    r = blocklist_reason(job)
    if r:
        return r
    # D-lane state-eligibility pre-filter -- GATED behind the job_state_filter
    # config knob (default OFF). Only when the knob is true does a proven-
    # ineligible job get skipped here; when off this is a no-op (the would-skip
    # impact is still surfaced by run() so [OWNER] can see it before enabling).
    if _state_filter_on():
        r = state_eligibility_reason(job)
        if r:
            return r
    if all_jobs is not None:
        r = multi_location_dupe_reason(job, all_jobs)
        if r:
            return r
        r = req_number_dupe_reason(job, all_jobs)
        if r:
            return r
        try:
            import job_pipeline_quality
            r = job_pipeline_quality.repost_variance_dupe_reason(job, all_jobs)
            if r:
                return r
        except Exception:  # noqa: BLE001
            pass
        try:
            import job_efficiency
            r = job_efficiency.burst_guard_reason(job, all_jobs)
            if r:
                return r
        except Exception:  # noqa: BLE001
            pass
    return ""


def run():
    """Standalone: rebuild the auto-blocklist from rejection history and
    report what the additive guards WOULD do against the current approved
    queue, without changing any statuses (dry surface, matches the pattern
    atsstats.py uses for its own standalone report-only run)."""
    auto = rebuild_auto_blocklist()
    all_jobs = jobs.load_jobs()
    approved = [j for j in all_jobs if j.get("status") == "approved"]
    would_block = {}
    for j in approved:
        r = extra_block_reason(j, all_jobs)
        if r:
            would_block.setdefault(r.split(" (")[0], []).append(j.get("company"))
    print(f"job_fit_signals: auto-blocklist {len(auto)} company(ies) (rejected 2x OR one geo-reject)")
    print(f"job_fit_signals: {len(approved)} approved jobs checked, "
          f"{sum(len(v) for v in would_block.values())} would be additionally blocked")
    for reason, cos in would_block.items():
        print(f"  {reason}: {len(cos)} ({', '.join(str(c) for c in cos[:5])}{'...' if len(cos) > 5 else ''})")
    # State-eligibility pre-filter impact, ALWAYS computed even when the knob is
    # off, so [OWNER] can see the would-skip count before deciding to flip it on.
    geo_skip = [j.get("company") for j in approved if state_eligibility_reason(j)]
    on = _state_filter_on()
    print(f"job_fit_signals: state_eligibility pre-filter is {'ON' if on else 'OFF'} "
          f"(job_state_filter); would skip {len(geo_skip)} approved job(s) if ON"
          + (f": {', '.join(str(c) for c in geo_skip[:5])}" if geo_skip else ""))
    return would_block


if __name__ == "__main__":
    run()
