#!/usr/bin/env python3
"""D224/D225: per-ATS confirmation/rejection email pattern library.

The email fleet owns agents/mail_patterns.py (per-ATS regex classification for
the general inbox) but it does not exist yet as of this build (checked live,
2026-07-03: `find . -iname "*mail_pattern*"` returns nothing beyond this
file). Per this lane's brief: write a minimal version scoped to jobs, and
document the merge path for whenever the email fleet ships theirs.

job_replies.py currently classifies EVERY candidate email with a full LLM call
(planner._cli, Haiku, one call per batch of up to 30). That's not wrong (it's
already cheap and handles novel phrasing an LLM catches that regex can't) but
it has zero fast-path: even an obviously-templated Greenhouse/Ashby/Lever
auto-reply burns the same LLM classification as an ambiguous human reply. This
module adds a REGEX FAST PATH that job_replies.py can check FIRST (cheap, free,
instant) and only fall through to the existing LLM classify for whatever the
regexes don't confidently resolve -- additive, never replaces the LLM path
(a regex miss just means "let the LLM decide," never "silently drop").

MERGE PATH once agents/mail_patterns.py exists (email fleet's file, owned by
them): this module's PATTERNS dict is intentionally self-contained (just
sender-domain -> (type, subject/body regex) tuples, no cross-imports) so it
can be handed over wholesale -- either job_replies.py switches its import to
the shared module once it exists, or the shared module absorbs these patterns
as its "ats" category. Either way this file stays a strict subset of what a
general mail_patterns.py would need (job-application senders only), so there's
no conflicting logic to reconcile, only patterns to fold in.
"""
from __future__ import annotations

import re

# Each ATS: (sender_domain_fragment, confirmation_regex, rejection_regex,
# interview_regex). Regexes run against "subject + snippet" (job_replies.py's
# existing listing format is `From ... | Subj: ... | snippet`, so a single
# combined haystack matches how the data actually arrives). ORDER matters and
# the MORE CONSEQUENTIAL class wins: interview first, then rejection, then
# confirmation last. Rejection emails routinely end with a boilerplate
# "thank you for applying" footer -- checking confirmation first classified
# those as plain confirmations, and because the regex result overrides the
# LLM's type in job_replies.py, that corrupted interview/rejection detection.
# Confirmation only wins when nothing more consequential matched.
PATTERNS: dict[str, dict] = {
    "greenhouse": {
        "domain": re.compile(r"greenhouse\.io", re.I),
        "confirmation": re.compile(
            r"(thank(s| you) for (applying|your interest)|"
            r"we('ve| have) received your application|"
            r"application (has been )?received)", re.I),
        "rejection": re.compile(
            r"(decided to (move forward|proceed) with other candidates|"
            r"will not be (moving|proceeding)|"
            r"not (be )?mov(e|ing) forward|"
            r"pursue other candidates|position (has been |was )?filled|"
            r"unfortunately)", re.I),
        "interview": re.compile(
            r"(schedule (a |an )?(call|interview|chat)|"
            r"would like to (speak|talk|connect)|"
            r"move (you )?to the next (round|stage))", re.I),
    },
    "lever": {
        "domain": re.compile(r"hire\.lever\.co", re.I),
        "confirmation": re.compile(
            r"(thank(s| you) for applying|application (has been )?received|"
            r"we('ve| have) received)", re.I),
        "rejection": re.compile(
            r"(not (be )?mov(e|ing) forward|other candidates|"
            r"decided not to proceed|position (has been )?filled)", re.I),
        "interview": re.compile(
            r"(schedule (a |an )?(call|interview)|"
            r"phone screen|would like to chat)", re.I),
    },
    "ashby": {
        "domain": re.compile(r"ashbyhq\.com", re.I),
        "confirmation": re.compile(
            r"(application (has been |was )?received|thank(s| you) for applying|"
            r"we('ve| have) got your application)", re.I),
        "rejection": re.compile(
            r"(not (be )?mov(e|ing) forward|other candidates|"
            r"pursuing other|will not be proceeding)", re.I),
        "interview": re.compile(
            r"(schedule (a |an )?(call|interview)|"
            r"interview invitation|book (a |your )?time)", re.I),
    },
    "workable": {
        "domain": re.compile(r"workablemail\.com", re.I),
        "confirmation": re.compile(
            r"(application (has been |was )?received|thank(s| you) for applying)", re.I),
        "rejection": re.compile(
            r"(not (be )?mov(e|ing) forward|decided to move forward with other|"
            r"other applicants|position (has been )?filled)", re.I),
        "interview": re.compile(
            r"(schedule (a |an )?(call|interview))", re.I),
    },
    "breezy": {
        "domain": re.compile(r"breezy\.hr", re.I),
        "confirmation": re.compile(
            r"(application (has been |was )?received|thank(s| you) for applying)", re.I),
        "rejection": re.compile(
            r"(not (be )?mov(e|ing) forward|other candidates|will not be proceeding)", re.I),
        "interview": re.compile(
            r"(schedule (a |an )?(call|interview))", re.I),
    },
    "rippling": {
        "domain": re.compile(r"rippling\.com", re.I),
        "confirmation": re.compile(
            r"(application (has been |was )?received|thank(s| you) for applying|"
            r"we('ve| have) received your application)", re.I),
        "rejection": re.compile(
            r"(not (be )?mov(e|ing) forward|other candidates|position (has been )?filled)", re.I),
        "interview": re.compile(
            r"(schedule (a |an )?(call|interview))", re.I),
    },
    "careerplug": {
        "domain": re.compile(r"careerplug\.com", re.I),
        "confirmation": re.compile(r"(application (has been |was )?received|thank(s| you) for applying)", re.I),
        "rejection": re.compile(r"(not (be )?mov(e|ing) forward|other candidates)", re.I),
        "interview": re.compile(r"(schedule (a |an )?(call|interview))", re.I),
    },
    "jazzhr": {
        "domain": re.compile(r"jazz\.co", re.I),
        "confirmation": re.compile(r"(application (has been |was )?received|thank(s| you) for applying)", re.I),
        "rejection": re.compile(r"(not (be )?mov(e|ing) forward|other candidates)", re.I),
        "interview": re.compile(r"(schedule (a |an )?(call|interview))", re.I),
    },
    "myworkday": {
        "domain": re.compile(r"myworkday\.com", re.I),
        "confirmation": re.compile(r"(application (has been |was )?received|thank(s| you) for applying)", re.I),
        "rejection": re.compile(
            r"(not (be )?mov(e|ing) forward|other candidates|"
            r"decided to pursue other|position (has been )?filled)", re.I),
        "interview": re.compile(r"(schedule (a |an )?(call|interview))", re.I),
    },
    "applytojob": {
        "domain": re.compile(r"applytojob\.com", re.I),
        "confirmation": re.compile(r"(application (has been |was )?received|thank(s| you) for applying)", re.I),
        "rejection": re.compile(r"(not (be )?mov(e|ing) forward|other candidates)", re.I),
        "interview": re.compile(r"(schedule (a |an )?(call|interview))", re.I),
    },
}

# Generic fallback (no sender-domain match, but subject/body still templated
# enough to resolve without an LLM call) -- checked only if no per-ATS domain
# matched, same interview-then-rejection-then-confirmation precedence.
_GENERIC = {
    "confirmation": re.compile(
        r"(we('ve| have) received your application|application (has been |was )?received|"
        r"thank(s| you) for (applying|your application))", re.I),
    # broadened 2026-07-12 (full-pipeline re-scan): added the common phrasings the old set
    # missed so the re-scan can terminally mark a rejection wherever it truly is one.
    "rejection": re.compile(
        r"(not (be )?mov(e|ing) forward|(won't|will not) be (mov(e|ing)|proceed|advanc)|"
        r"decided to (move forward|proceed) with other|move forward with (other |another )?candidate|"
        r"pursue other candidates|position (has been |was )?filled|regret to inform|"
        r"(were |was )?not selected|not (be )?selected|"
        r"other candidates whose (experience|qualifications|background)|"
        r"decided not to (move|proceed)|unable to (move forward|offer))", re.I),
    "interview": re.compile(
        r"(schedule (a |an )?(call|interview|chat)|phone screen|"
        r"would like to (speak|talk|connect)|interview invitation|"
        r"book (a |your )?(time|call)|your availability (for|to)|move (you )?to the next (round|stage)|"
        # soft screening-invite phrasings a recruiter uses for a first chat (2026-07-12 re-scan:
        # these are real first-touch interviews, e.g. CacheFly's "open to a quick chat about the role")
        r"open to (a |having )?(quick |brief |short )?(chat|call|conversation)|"
        r"(hop|jump) on a (quick )?call|chat about (the|your|this) (role|position|opportunity)|"
        r"grab (some |a few minutes of )?time|set (up|aside) (some )?time to (chat|talk|speak))", re.I),
}


# NOTE: signature is (sender, subject, snippet) -> str|None. agents/mail_patterns.py has a
# DIFFERENT classify(subject, body, sender) -> dict. This is the one job_replies.py uses;
# do not cross-wire them (a swap fails silently).
def classify(sender: str, subject: str, snippet: str) -> str | None:
    """Regex fast-path classification. Returns 'confirmation'/'rejection'/
    'interview', or None if nothing confidently matched (caller should fall
    through to the existing LLM classify -- never returns a wrong guess just
    to avoid the fallback; the whole point is a regex miss is safe, it just
    means 'ask the LLM' same as it already does for everything today)."""
    haystack = f"{subject or ''} {snippet or ''}"
    sender = sender or ""
    ats = None
    for name, pats in PATTERNS.items():
        if pats["domain"].search(sender):
            ats = pats
            break
    candidates = [ats] if ats else []
    candidates.append(_GENERIC)
    # Precedence: most consequential class first, checked ACROSS every candidate pattern
    # set together (R2-21), not one set at a time. A rejection (or interview invite) very
    # often ends with a boilerplate "thank you for applying / your application was received"
    # footer; confirmation-first classified those as 'confirmation' and, because job_replies.py
    # lets the regex result override the LLM's type, silently ate rejections and interview
    # invites. A per-set (ats-then-generic) sequential check has the SAME failure one level
    # up: a known ATS's own narrower confirmation regex can match and return before the
    # broader _GENERIC interview/rejection patterns (e.g. the soft "grab some time to chat"
    # invite) ever get a look. So both passes below scan ALL candidate sets before
    # confirmation is allowed to win -- confirmation is the fallback, never the front door.
    hit_interview = any(pats["interview"].search(haystack) for pats in candidates)
    hit_rejection = any(pats["rejection"].search(haystack) for pats in candidates)
    if hit_interview and hit_rejection:
        # Both consequential families match ("unfortunately... but happy to
        # discuss next steps" style mail): the regex is NOT confident, and
        # a wrong regex answer would override the LLM downstream. Punt to
        # the LLM -- a None here is safe by design, a wrong guess is not.
        return None
    if hit_interview:
        return "interview"
    if hit_rejection:
        return "rejection"
    for pats in candidates:
        if pats["confirmation"].search(haystack):
            return "confirmation"
    return None


def ats_for_sender(sender: str) -> str | None:
    """Which ATS a sender domain belongs to, or None. Useful standalone (e.g.
    a caller wanting to log/report ATS coverage) without running classify()."""
    for name, pats in PATTERNS.items():
        if pats["domain"].search(sender or ""):
            return name
    return None
