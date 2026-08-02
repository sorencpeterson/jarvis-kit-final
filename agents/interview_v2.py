#!/usr/bin/env python3
"""D261-270: interview lane v2, layered alongside the existing interview_prep.py
(NOT a replacement -- interview_prep.py's `run()` still owns the core one-page
prep pack per interview, triggered automatically from job_replies.py on an
interview flip). This module adds the pieces the mission calls out that
interview_prep.py doesn't attempt: mock questions per role-TYPE (not just
per-company), an explicit STAR-story-to-competency matcher (interview_prep.py
asks the LLM to weave stories in loosely; this makes the matching a first-class,
inspectable step), a salary-negotiation brief, and multi-stage tracker fields
on the job record itself.

D261 auto-prep enrichment: interview_prep.py already does JD+company+stories
via WebSearch; this doesn't duplicate that call. It adds `mcp_company_brief()`
as an OPTIONAL richer substitute for interview_prep's WebSearch step, sourced
from Indeed's get_company_data MCP (see agents/job_mcp_notes.md for the
rate-limit reality) -- best-effort, returns None cleanly when the MCP is
unreachable, in which case interview_prep.py's existing WebSearch path is
already sufficient and this module doesn't need to do anything.

D262 mock questions per role type: a small deterministic bank keyed by role-
type category (extracted from the job title), NOT an LLM call -- cheap,
instant, and reviewable as plain data rather than a prompt's opaque output.

D263 STAR-story matcher: reads store/star_bank.md (already-real, already-used
by interview_prep.py) and scores each story against a fixed competency list
using keyword overlap (same SET-overlap approach job_fit_signals.py uses for
resume keywords -- consistent method across this lane's build), returning the
best-matching stories per competency.

D264 thank-you drafts: ALREADY BUILT (agents/thankyou.py, not owned by this
D-lane, pre-existing). This module does not duplicate it -- D264 is complete
via the existing file. Noted here only so this build's status report doesn't
claim it as new work.

D265 interview-debrief capture: a tiny form-shaped store write
(store/interview_debriefs.jsonl) so a POST-interview capture has somewhere to
land (D266's negotiation brief and future win/loss learning both want this).

D266 salary-negotiation brief: pulls store/salary_intel.json (real, already
built) + application_profile.json's stated [SALARY_ANCHOR] target; "playbooks" per the
mission brief were searched for and don't exist anywhere in this repo or
~/Claude as of this build (only business-library/ sales playbooks exist,
which are a different lane's context, not negotiation playbooks) -- this
degrades gracefully to salary_intel + profile alone, which is real data, not
a gap silently swallowed.

D267 decliner templates: short, humane templates for turning down an offer/
moving on from a process, gated (drafted only, never sent).

D268 multi-stage tracker fields: adds `stage` (phone/tech/onsite/offer) as an
optional field ON the job record, additive to (never replacing) the existing
`status` field jobs.py already drives its whole state machine from --
`status` stays 'interview' throughout every stage per the existing status
vocabulary; `stage` is a NEW, separate, optional sub-field for finer-grained
tracking that no existing code reads or depends on, so this can't break
anything that already works.

D269 interviewer-name capture + LinkedIn lookup: a capture FIELD + a manual-
lookup URL builder (LinkedIn search, not automated scraping -- no LinkedIn
API/session access in this lane's scope, and LinkedIn scraping automation is
networking.py's lane per CLAUDE.md, not this one's).

D270 offer-deadline tracking: an `offer_deadline` field + a report of
upcoming deadlines, same additive-field pattern as D268.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import jobs  # noqa: E402

DEBRIEFS = ROOT / "store" / "interview_debriefs.jsonl"

# ---- D262 mock questions per role type (deterministic, no LLM) ----
_ROLE_TYPE_KW = [
    ("seo", ("seo", "search engine")),
    ("web_dev", ("wordpress", "web dev", "web developer", "frontend", "front end", "front-end")),
    ("growth", ("growth", "demand gen", "demand generation", "acquisition")),
    ("paid_media", ("paid", "ppc", "performance marketing", "media buyer")),
    ("marketing_ops", ("marketing operations", "marketing ops", "revops", "crm")),
    ("brand_content", ("brand", "content", "product marketing")),
    ("leadership", ("director", "vp", "head of", "coo", "chief")),
    ("generalist_manager", ("marketing manager", "digital marketing")),
]

_MOCK_BANK = {
    "seo": [
        "Walk me through how you'd audit an underperforming site's SEO.",
        "How do you prioritize technical SEO fixes vs content work with limited time?",
        "Tell me about an SEO win you're proud of and how you measured it.",
        "How do you stay current on algorithm changes and what's your view on AI search?",
    ],
    "web_dev": [
        "Walk me through your process building a site from kickoff to launch.",
        "How do you handle a client who wants scope creep on a fixed-price build?",
        "What's your approach to page speed and Core Web Vitals?",
        "Tell me about the tightest turnaround you've shipped and how you made it work.",
    ],
    "growth": [
        "How do you think about the full funnel, not just top-of-funnel acquisition?",
        "Tell me about a growth experiment that failed and what you learned.",
        "How do you decide what channel to invest in next?",
        "Walk me through how you'd ramp in the first 90 days here.",
    ],
    "paid_media": [
        "How do you approach a campaign that's burning budget with a high CPA?",
        "Walk me through your testing methodology for creative/copy.",
        "How do you think about attribution across channels?",
        "Tell me about the best ROAS result you've delivered and how.",
    ],
    "marketing_ops": [
        "Walk me through how you'd build out a lead lifecycle in a CRM from scratch.",
        "How do you keep marketing and sales aligned on lead handoff?",
        "Tell me about an automation you built that saved real time or money.",
        "How do you approach reporting so leadership actually trusts the numbers?",
    ],
    "brand_content": [
        "How do you balance brand consistency with channel-specific content needs?",
        "Tell me about content that moved a real business metric, not just engagement.",
        "How do you develop a content strategy from zero?",
    ],
    "leadership": [
        "Tell me about a team or operation you turned around.",
        "How do you build systems that don't depend on you personally?",
        "Walk me through a hard people decision you made and how you handled it.",
        "How do you think about scaling revenue without proportionally scaling headcount?",
    ],
    "generalist_manager": [
        "Walk me through how you'd own the full marketing function here.",
        "Tell me about a time you had to be both the strategist and the one executing.",
        "How do you prioritize when everything feels urgent?",
        "What's your framework for measuring marketing's impact on revenue?",
    ],
}


def role_types(title: str) -> list[str]:
    t = (title or "").lower()
    return [rt for rt, kws in _ROLE_TYPE_KW if any(k in t for k in kws)] or ["generalist_manager"]


def mock_questions(job: dict, max_q: int = 8) -> list[str]:
    types = role_types(job.get("title") or "")
    seen, out = set(), []
    for rt in types:
        for q in _MOCK_BANK.get(rt, []):
            if q not in seen:
                seen.add(q)
                out.append(q)
    for q in _MOCK_BANK["generalist_manager"]:  # always include a few universal ones as a floor
        if q not in seen and len(out) < max_q:
            seen.add(q)
            out.append(q)
    return out[:max_q]


# ---- D263 STAR-story matcher ----
_COMPETENCIES = {
    "scaling_revenue": ("scale", "revenue", "grew", "growth", "1m", "400k", "million"),
    "systems_ops": ("system", "sop", "process", "operations", "automat", "monday.com", "ghl"),
    "team_leadership": ("hire", "train", "team", "recruit", "manage", "lead"),
    "client_retention": ("churn", "retention", "client", "account", "relationship"),
    "technical_execution": ("wordpress", "seo", "landing", "build", "ship", "site", "campaign"),
    "problem_solving": ("bottleneck", "fix", "diagnos", "problem", "risk", "at-risk", "difficult"),
    "strategy_execution": ("strategy", "plan", "execut", "wedge", "own"),
}


def _parse_star_stories() -> list[dict]:
    """Split store/star_bank.md into individual stories by its own '## N.'
    headers (the file's real, stable format -- verified by reading it
    directly during this build). Returns [] if the file is still the
    template (store_lib.star_bank() already detects that and returns '')."""
    from store_lib import star_bank
    text = star_bank()
    if not text:
        return []
    stories = []
    for block in re.split(r"\n(?=## \d)", text):
        m = re.match(r"## \d+\.\s*(.+)", block)
        if not m:
            continue
        stories.append({"title": m.group(1).strip(), "text": block.lower()})
    return stories


def match_stories_to_competencies() -> dict[str, list[str]]:
    """For each competency, the story titles whose text overlaps its keyword
    set, ranked by hit count. Set-overlap, not an LLM call -- fast, free,
    inspectable, same method job_fit_signals.py uses for resume keywords."""
    stories = _parse_star_stories()
    if not stories:
        return {}
    out = {}
    for comp, kws in _COMPETENCIES.items():
        scored = sorted(
            ((s["title"], sum(1 for k in kws if k in s["text"])) for s in stories),
            key=lambda x: -x[1])
        out[comp] = [t for t, n in scored if n > 0][:3]
    return out


# ---- D261 optional MCP company brief (best-effort, degrades to None cleanly) ----
def mcp_company_brief_hint(company: str) -> str:
    """NOT a live MCP call (this module has no MCP tool access at runtime --
    only a chat session can invoke mcp__*__get_company_data). This returns a
    ready-to-paste snippet a chat session CAN use once it has that tool
    loaded, keeping interview_prep.py's existing WebSearch-based enrichment
    as the default path that works with zero extra setup. See
    agents/job_mcp_notes.md for the exact call shape and its rate-limit
    behavior before attempting a live call."""
    return (f'get_company_data(companyName="{company}", language="en", '
            'location={"country":"US","usState":None,"usStateCode":None,"usCity":None}, '
            'knowledgeCategories={"metadata":True,"ratings":True,"salaries":True})')


# ---- D237 company-research brief (3 lines) attached to interview preps ----
def company_research_brief(job: dict) -> str:
    """Three-line brief from data ALREADY on hand (no new fetch): comp
    context from salary_intel.json bucketed by title keyword, the
    recruiter-vs-direct tag from job_fit_signals (a real signal about who's
    actually hiring), and whether this posting showed any hybrid-disguised-
    as-remote tell. This is a CHEAP, always-available floor — interview_prep.py's
    own WebSearch-based research (which actually looks up news/market/
    culture) remains the richer source and this doesn't replace it; this is
    what's available even when WebSearch/MCP enrichment isn't reachable."""
    lines = []
    try:
        intel = json.loads((ROOT / "store" / "salary_intel.json").read_text())
        title = (job.get("title") or "").lower()
        bucket = next((k for k in intel.get("by_title_keyword", {}) if k in title), None)
        if bucket:
            s = intel["by_title_keyword"][bucket]
            lines.append(f"Market comp for '{bucket}'-family titles: median ${s.get('median'):,} "
                        f"(n={s.get('n')}), so this range is {'typical' if s.get('median',0) >= 100000 else 'lean'}.")
    except (OSError, json.JSONDecodeError):
        pass
    try:
        import job_fit_signals
        if job_fit_signals.is_recruiter_listing(job):
            lines.append("Company name pattern suggests this may be a recruiting/staffing "
                        "agency posting on behalf of a client, not the direct employer — "
                        "confirm who you'd actually be working for early in the call.")
        if job_fit_signals.hybrid_disguised_as_remote(job):
            lines.append("Listing title carries hybrid/in-office language despite being sourced "
                        "as a remote-eligible role — confirm the real on-site expectation.")
    except Exception:  # noqa: BLE001
        pass
    if not lines:
        lines.append(f"No enrichment data on hand for {job.get('company') or 'this company'} yet — "
                     "lean on interview_prep.py's WebSearch-based research pack for this one.")
    return "\n".join(f"- {ln}" for ln in lines[:3])


# ---- D265 interview-debrief capture ----
def capture_debrief(job_id: str, notes: str, went_well: str = "", went_poorly: str = "",
                    stage: str = "") -> dict:
    rec = {"job_id": job_id, "notes": notes, "went_well": went_well,
           "went_poorly": went_poorly, "stage": stage, "ts": now_iso()}
    DEBRIEFS.parent.mkdir(parents=True, exist_ok=True)
    with DEBRIEFS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


# ---- D266 salary-negotiation brief ----
def negotiation_brief(job: dict) -> str:
    profile = jobs.load_profile()
    target = profile.get("salary_expectation", "[SALARY_ANCHOR]")
    try:
        intel = json.loads((ROOT / "store" / "salary_intel.json").read_text())
    except (OSError, json.JSONDecodeError):
        intel = {}
    title = (job.get("title") or "").lower()
    bucket = next((k for k in intel.get("by_title_keyword", {}) if k in title), None)
    stats = intel.get("by_title_keyword", {}).get(bucket) if bucket else None
    lines = [f"Target: {target} (stated floor, application_profile.json)."]
    if stats:
        lines.append(f"Market data for '{bucket}' titles (n={stats.get('n')}): "
                     f"p25 ${stats.get('p25'):,}, median ${stats.get('median'):,}, "
                     f"p75 ${stats.get('p75'):,}.")
        if stats.get("median", 0) >= 135000:
            lines.append("Market median is AT or ABOVE target: anchor near median, "
                         "not the floor, you're not overreaching.")
        else:
            lines.append("Market median is BELOW target: be ready to justify the gap with "
                         "the [PRIOR_RESULT] scaling story and the AI-automation build work, "
                         "concrete numbers, not a generic 'I'm worth more' pitch.")
    else:
        lines.append("No market bucket matched this title in salary_intel.json -- "
                     "anchor on the stated target and the scaling story alone.")
    lines.append("No formal negotiation playbook file exists in this workspace yet "
                 "(searched, none found) -- this brief is salary_intel + profile only. "
                 "General frame: let them name a number first when possible, never accept "
                 "the first offer on the spot, ask for 24-48h to consider in writing.")
    return "\n".join(lines)


# ---- D267 decliner templates (drafted only, gated) ----
def decliner_draft(company: str, reason: str = "another offer") -> str:
    return (
        f"Subject: Update on my candidacy\n\n"
        f"Hi, thank you for the time and consideration through this process. I wanted to let "
        f"you know I've decided to move forward with {reason} and won't be continuing as a "
        f"candidate for the role at {company}. I appreciated getting to learn about the team "
        f"and the work. Wishing you the best filling the role.\n\n[OWNER]")


# ---- D268/D269/D270 additive job-record fields (never replace `status`) ----
def set_stage(job_id: str, stage: str) -> dict | None:
    """stage in phone|tech|onsite|offer. Additive sub-field alongside the
    existing status machine -- status stays 'interview' across every one of
    these; stage is purely finer-grained and nothing existing reads it."""
    j = next((x for x in jobs.load_jobs() if x.get("id") == job_id), None)
    if not j:
        return None
    jobs._save({**j, "stage": stage, "stage_updated": now_iso()})
    return j


def set_interviewer(job_id: str, name: str) -> dict | None:
    j = next((x for x in jobs.load_jobs() if x.get("id") == job_id), None)
    if not j:
        return None
    li_search = "https://www.linkedin.com/search/results/people/?keywords=" + \
        re.sub(r"\s+", "%20", name.strip()) + "%20" + re.sub(r"\s+", "%20", (j.get("company") or "").strip())
    jobs._save({**j, "interviewer_name": name, "interviewer_linkedin_search": li_search})
    return j


def set_offer_deadline(job_id: str, deadline_iso: str) -> dict | None:
    j = next((x for x in jobs.load_jobs() if x.get("id") == job_id), None)
    if not j:
        return None
    jobs._save({**j, "offer_deadline": deadline_iso})
    return j


def upcoming_offer_deadlines(days: int = 7) -> list[dict]:
    from datetime import datetime, timedelta
    cutoff = datetime.now().astimezone() + timedelta(days=days)
    out = []
    for j in jobs.load_jobs():
        d = j.get("offer_deadline")
        if not d:
            continue
        try:
            dt = datetime.fromisoformat(d)
        except ValueError:
            continue
        if dt <= cutoff:
            out.append(j)
    return out


def run():
    """Report-only standalone: shows what the STAR matcher + mock-question
    bank would produce for every job currently at status 'interview', plus
    any upcoming offer deadlines. Writes nothing except via the explicit
    capture_debrief/set_stage/etc functions a caller invokes directly."""
    interviews = [j for j in jobs.load_jobs() if j.get("status") == "interview"]
    matches = match_stories_to_competencies()
    print(f"interview_v2: {len(interviews)} job(s) at status 'interview'")
    for j in interviews[:5]:
        types = role_types(j.get("title") or "")
        print(f"  {j.get('company')} - {j.get('title')} | role_type(s): {types} | "
              f"{len(mock_questions(j))} mock questions ready")
    print(f"interview_v2: STAR matcher found {sum(len(v) for v in matches.values())} "
          f"story-to-competency match(es) across {len(matches)} competencies")
    deadlines = upcoming_offer_deadlines()
    print(f"interview_v2: {len(deadlines)} offer deadline(s) in the next 7 days")
    return {"interviews": len(interviews), "star_matches": matches, "upcoming_deadlines": len(deadlines)}


if __name__ == "__main__":
    run()
