#!/usr/bin/env python3
"""Job sourcing from hiringcafe.com, formerly hiring.cafe (Phase 1 of the auto-apply system).

hiring.cafe server-renders its search results into the page's __NEXT_DATA__
(props.pageProps.ssrHits), so we just GET the search URL and parse the JSON, no
auth, no fragile internal API. Each hit gives the ATS `source`, the direct
`apply_url`, title, company, salary. We filter to EASY-to-automate ATS types.

Queue lives in store/jobs.jsonl: {id, title, company, salary, source, apply_url,
seniority, commitment, posted, easy, status: pending|approved|applied|skipped}.
"""
from __future__ import annotations

import html
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import new_id, now_iso  # noqa: E402

QUEUE = ROOT / "store" / "jobs.jsonl"

# ATS platforms the scout confirmed are single-page / no-CAPTCHA / automatable
EASY_ATS = {"careerplug", "recruitee", "lever", "ashby", "ashbyhq", "jazzhr",
            "workable", "breezy", "rippling"}

# Title must contain one of these to be a real fit (kills "Director, Product" noise)
TITLE_KW = ("marketing", "seo", "web", "wordpress", "growth", "demand gen",
            "content", "digital", "brand", "ppc", "paid", "performance",
            "ecommerce", "e-commerce", "social media", "email", "crm",
            "acquisition", "lifecycle", "developer", "front end", "frontend",
            "marketing operations", "revenue operations", "revops", "marketing ops",
            "gtm", "go-to-market", "customer success", "implementation",
            # operator-lane queries (R2-11, 2026-07-13 hunt): these 5 DEFAULT_QUERIES titles
            # had no TITLE_KW match at all, so _relevant() threw away exactly the highest-fit
            # operator roles the dedicated queries were built to fetch.
            "fractional coo", "chief operating officer", "chief of staff",
            "head of operations", "director of operations", "partnership")


def _min_yearly() -> int:
    # The pool floor: a job that POSTS a max below this is dropped (jobs with no posted comp
    # are never dropped). Lowered 95000 -> 40000 (2026-07-07, [OWNER]'s "max opportunities, I'd
    # take anything" ask); config job_min_yearly overrides. Set it to 0 to keep truly every
    # posting incl. part-time/hourly-low, or back to 95000+ to re-tighten to the [SALARY_ANCHOR] lane.
    try:
        return int(json.loads((ROOT / "store" / "config.json").read_text()).get("job_min_yearly", 40000))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return 40000


def _blacklist() -> set:
    """ATS sources to skip entirely (e.g. lever after the retro flags 74% captcha)."""
    try:
        return {str(s).lower() for s in
                json.loads((ROOT / "store" / "config.json").read_text()).get("job_blacklist", [])}
    except (OSError, json.JSONDecodeError):
        return set()


_MAX_AGE = 10  # 21->10 (2026-07-12 clean-data): 11d+ postings were 51% of volume, 30/46
#              of all rejections, and 0 interviews; recruiter attention lives in week one


def _age_days(posted):
    """Days since a posting's publish date, or None if unknown/unparseable."""
    if not posted:
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(posted).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - dt).days)
    except (ValueError, TypeError):
        return None


def _ckey(company: str, title: str) -> str:
    """Normalized company+title key for cross-source, cross-history dedupe (kills 4x re-applies)."""
    c = _conorm(company)
    t = re.sub(r"[^a-z0-9]", "", (title or "").lower())[:24]
    return c + "|" + t


_CONTROL_CHARS = re.compile(r"[\r\n\t\x00-\x1f\x7f]+")


def _clean_field(s) -> str:
    """Strip newlines/control chars from an untrusted external field (job board title/company)
    before it's stored. These values get interpolated into the operator's prompt text
    (app/server.py's JOBS listing); a crafted board posting with an embedded newline could
    otherwise inject a fake extra listing row or hide part of a real one (Finding U, 2026-07-13
    hunt)."""
    return _CONTROL_CHARS.sub(" ", str(s or "")).strip()


def _safe_scalar(v):
    """Coerce a value to a hashable scalar before it's used as a dedupe set/dict key.
    hiring.cafe's loosely-typed JSON blob (and, defensively, any board feed) can ship a
    malformed id/apply_url as a list or dict; without this, ONE such hit raises TypeError deep
    inside dedupe (seen.add(...)) and used to abort the entire scan (R2-19, 2026-07-13 hunt)."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    try:
        return json.dumps(v, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(v)


def _fit(j: dict) -> int:
    """Win-probability-ish score: fresh + on-title + pays well ranks first."""
    s = 50
    age = _age_days(j.get("posted"))
    if age is not None:
        s += max(-30, 15 - age)
    t = (j.get("title") or "").lower()
    # sync the bonus list to TITLE_KW (D5 #10): developer/ppc/revops/crm/demand-gen were
    # actively searched but got zero title bonus. Capped so long titles don't inflate.
    s += min(12, sum(4 for k in TITLE_KW if k in t))
    cm = j.get("comp_max")
    # R2-12 (2026-07-13 hunt): comp_max is only comparable to this annual-dollar bonus
    # formula when it's actually posted in yearly terms. An hourly rate stays in its own
    # native unit now (see _comp()) -- plugging a raw hourly number in here would read a
    # $50/hr job as a catastrophic "salary" (or, before this fix, an annualized-at-2080
    # number that isn't real either). Missing comp_unit (older records, board listings)
    # defaults to "year", preserving prior behavior for everything that was already correct.
    if cm and (j.get("comp_unit") or "year") == "year":
        s += min(20, max(-10, (cm - _min_yearly()) // 5000))
    # SENIORITY BIAS (2026-07-12; re-grounded after the funnel re-scan): the one REAL
    # interview (CacheFly) was a 6-yoe senior role and sub-5-YOE was 0-for-127, but with
    # n=1 this is JUDGMENT aligned to his operator profile, not a measured conversion edge.
    try:
        yoe = int(str(j.get("yoe") or 0))
    except (ValueError, TypeError):
        yoe = 0
    if yoe >= 5:
        s += 15
    elif 0 < yoe < 4:
        s -= 20
    # R2-16 (2026-07-13 hunt): job_fit_signals.seniority_score() existed with no caller, so
    # title-tier language (Manager/Director/VP/...) never actually moved the score used for
    # ranking/approval. Lazy import -- job_fit_signals imports THIS module (see
    # approved_to_apply()'s own comment), so a top-level import here would be circular.
    # Any failure (bad store file, import error) is swallowed and adds 0, matching the
    # additive/never-break contract job_fit_signals.py's own docstring commits to.
    try:
        import job_fit_signals
        s += job_fit_signals.seniority_score(j)
    except Exception:  # noqa: BLE001
        pass
    return s


def auto_on() -> bool:
    try:
        return bool(json.loads((ROOT / "store" / "config.json").read_text()).get("job_auto", False))
    except (OSError, json.JSONDecodeError):
        return False


def _relevant(title: str) -> bool:
    t = (title or "").lower()
    return any(k in t for k in TITLE_KW)

_US_REMOTE = {
    "id": "FxY1yZQBoEtHp_8UEq7V", "types": ["country"],
    "address_components": [{"long_name": "United States", "short_name": "US", "types": ["country"]}],
    "formatted_address": "United States", "population": 327167434,
    "workplace_types": ["remote"], "options": {},
}
_HDRS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _fetch_hits(query: str, _try: int = 0) -> list[dict]:
    import time
    state = {"locations": [_US_REMOTE], "searchQuery": query}
    # hiring.cafe officially migrated to hiringcafe.com (2026-07); the old domain 301s here
    # but the redirect could drop any day, so hit the new domain directly.
    url = "https://hiringcafe.com/?searchState=" + urllib.parse.quote(json.dumps(state))
    req = urllib.request.Request(url, headers=_HDRS)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (429, 403) and _try < 3:   # rate-limited: back off and retry
            time.sleep(15 * (_try + 1))
            return _fetch_hits(query, _try + 1)
        raise
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', body, re.S)
    if not m:
        return []
    data = json.loads(m.group(1))
    return data.get("props", {}).get("pageProps", {}).get("ssrHits", []) or []


def _comp(v5: dict):
    """Return (display_string, min_or_None, max_or_None, unit) where unit is "year" or "hour"
    IN THE COMPENSATION'S OWN NATIVE UNIT. An hourly rate is never cross-converted to an
    annualized figure here (R2-12, 2026-07-13 hunt): silently annualizing at 2080h and
    discarding the unit made a $50/hr part-time job read as a $104k salary everywhere
    downstream that reused comp_max as if it were annual (the min-yearly floor filter, the
    _fit() comp bonus, and salary_target's operator directive). Callers that compare against
    an annual number must check `unit` first."""
    g = v5.get
    ymin = g("yearly_min_compensation") or g("listed_yearly_min_compensation")
    ymax = g("yearly_max_compensation") or g("listed_yearly_max_compensation")
    hmin, hmax = g("hourly_min_compensation"), g("hourly_max_compensation")
    if ymin or ymax:
        a, b = ymin or ymax, ymax or ymin
        a, b = min(a, b), max(a, b)   # guard a reversed/malformed band: min must never exceed max,
        #                               or salary_target would tell the operator to state a number
        #                               ABOVE the posting's real ceiling (2026-07-13 hunt)
        return f"${round(a/1000)}k-${round(b/1000)}k", int(a), int(b), "year"
    if hmin or hmax:
        a, b = hmin or hmax, hmax or hmin
        a, b = min(a, b), max(a, b)
        return f"${a}-{b}/hr", int(a), int(b), "hour"
    return "", None, None, "year"


def salary_target(job: dict, floor: int = 0) -> tuple[int, str]:
    """Per-job salary answer that maximizes interviews ([OWNER]'s ask 2026-07-07: match the
    posting, take anything, max opportunities). Ask near the BOTTOM of the posted band so
    a too-high number never trips the desired-salary filter; never exceed their max. `floor`
    (config salary_floor, default 0 = fully flexible) is an optional absolute minimum for
    when he gets choosier again. Returns (number_or_0, short_operator_directive)."""
    lo = int(job.get("comp_min") or 0)
    hi = int(job.get("comp_max") or 0)
    if lo and hi and lo > hi:
        lo, hi = hi, lo   # reversed/malformed band (2026-07-13 hunt): without this the "state lo,
        #                   never above hi" directive below would tell the operator a number ABOVE
        #                   the posting's real ceiling, the exact auto-filter this function avoids
    hourly = (job.get("comp_unit") or "year") == "hour"
    # R2-12 (2026-07-13 hunt): comp_min/comp_max now stay in their NATIVE unit (see _comp()) --
    # an hourly rate is never annualized here either. An ANNUAL config floor doesn't apply to
    # an hourly-paid role: mixing units would push a fake, too-high hourly number (a $50/hr job
    # must never be told to state "$95k" on an hourly field).
    floor = 0 if hourly else int(floor or 0)

    def _k(n: int) -> str:
        if hourly:
            return f"${n}/hr"
        return f"${n // 1000}k" if n >= 1000 else f"${n:,}"

    if lo and hi:
        t = lo
        if floor and floor > lo:
            t = min(floor, hi)          # respect a floor, but stay inside their band
        return t, f"posted {_k(lo)}-{_k(hi)}; state {_k(t)} (never above {_k(hi)})"
    if hi:                              # only a ceiling is posted
        t = max(floor, int(hi * 0.9))
        t = min(t, hi)
        return t, f"posted up to {_k(hi)}; state {_k(t)} (never above {_k(hi)})"
    if lo:                             # only a floor is posted
        t = max(floor, lo)
        return t, f"posted from {_k(lo)}; state {_k(t)}"
    if floor:
        return floor, f"no pay posted; state {_k(floor)} or answer 'Open'"
    return 0, ("no pay posted; answer 'Open' / 'Negotiable' where text is allowed. Only if a "
               "NUMBER is required, enter a modest market figure for the role, never a high anchor")


def _extract(hit: dict) -> dict:
    v5 = hit.get("v5_processed_job_data") or {}
    ji = hit.get("job_information") or {}
    ec = hit.get("enriched_company_data") or {}
    source = (hit.get("source") or "").lower()
    # Finding U (2026-07-13 hunt): strip control chars from title/company BEFORE they're
    # stored -- these get interpolated into the operator's prompt (server.py's JOBS listing)
    # and a crafted board posting with an embedded newline could inject a fake extra row.
    title = _clean_field(html.unescape(ji.get("title") or v5.get("core_job_title") or "Untitled"))
    company = _clean_field(html.unescape(ec.get("name") or ec.get("company_name")
                            or v5.get("company_name") or ji.get("company_name") or "?"))
    sal, comp_min, comp_max, comp_unit = _comp(v5)
    # D-lane state-eligibility capture (2026-07-07): grab whatever description-ish
    # prose hiring.cafe's ssrHits actually carries so the state-eligibility
    # pre-filter (job_fit_signals.state_eligibility_reason) has text to scan.
    # NOTE: hiring.cafe's ssrHits blob does NOT ship a full job body -- it's a
    # structured search-index projection (verified live 2026-07-07: no
    # 'description' key exists anywhere in the hit; the only prose field is
    # 'requirements_summary', ~<=175 chars, which does sometimes carry the
    # eligible-states language). So `description` here is best-effort prose, not
    # the full posting; the pre-filter fails open when it finds no geo language,
    # which is the common case for this thin text. Also captured: the STRUCTURED
    # workplace_states list, a cleaner eligibility signal the filter can use.
    desc = (v5.get("requirements_summary") or ji.get("description") or v5.get("description") or "")
    if isinstance(desc, str) and desc:
        desc = html.unescape(desc)[:4000]
    else:
        desc = ""
    wstates = v5.get("workplace_states") or v5.get("boundless_workplace_states") or []
    if isinstance(wstates, str):
        wstates = [wstates]
    countries = v5.get("workplace_countries") or v5.get("boundless_workplace_countries") or []
    if isinstance(countries, str):
        countries = [countries]
    # US if explicitly tagged US, or untagged (the search itself is US-scoped). Drops explicit non-US.
    is_us = (not countries) or any("united states" in str(c).lower() or str(c).upper() == "US"
                                   for c in countries)
    return {
        # R2-19 (2026-07-13 hunt): coerce id/apply_url to a hashable scalar -- hiring.cafe's
        # loosely-typed blob occasionally ships one as a list/dict, which used to raise
        # TypeError deep inside dedupe (seen.add(...)) and abort the ENTIRE scan.
        "id": _safe_scalar(hit.get("id") or hit.get("objectID")),
        "title": title, "company": company, "source": source,
        "salary": sal, "comp_min": comp_min, "comp_max": comp_max, "comp_unit": comp_unit,
        "apply_url": _safe_scalar(hit.get("apply_url")),
        "seniority": v5.get("seniority_level"), "commitment": v5.get("commitment"),
        "yoe": v5.get("min_industry_and_role_yoe"),
        "posted": v5.get("estimated_publish_date") or v5.get("publish_date"),
        "expired": bool(hit.get("is_expired")), "is_us": is_us,
        "easy": source in EASY_ATS,
        "description": desc,           # best-effort prose (requirements_summary); "" if none
        "workplace_states": wstates,   # structured eligible-states list from the blob
    }


def search(query: str) -> list[dict]:
    """Return extracted, non-expired jobs for a query (one page, ~40-58 results)."""
    out = []
    for h in _fetch_hits(query):
        try:
            j = _extract(h)
        except Exception:  # noqa: BLE001 -- one malformed hit must not abort the whole query's
            # ~40-58 postings (Codex end-to-end pass, 2026-07-14): _safe_scalar guards id/apply_url,
            # but a differently-shaped hit can still raise elsewhere in _extract; skip it, keep the rest.
            continue
        if j.get("apply_url") and not j["expired"]:
            out.append(j)
    return out


# ---- Extra boards: smaller / remote-first companies + agencies (beyond hiring.cafe's enterprise skew) ----
def _norm(board: str, jid, title: str, company: str, url: str,
          comp_max, location: str, posted) -> dict:
    """Normalize a board job into the same shape as _extract()."""
    loc = (location or "").lower()
    # require a positive US-eligible signal (or blank/anywhere) — cleaner than blacklisting every country
    is_us = (not loc) or any(k in loc for k in (
        "usa", "united states", "u.s", "north america", "northern america",
        "anywhere", "worldwide", "global", "remote, us", "us only", "us-based"))
    # Finding U (2026-07-13 hunt): strip control chars (a crafted board title with an embedded
    # newline could inject a fake extra row into the operator's prompt listing).
    return {"id": f"{board}:{jid}",
            "title": _clean_field(html.unescape(str(title or "Untitled"))),
            "company": _clean_field(html.unescape(str(company or "?"))), "source": board,
            "salary": f"${round(comp_max/1000)}k" if comp_max else "", "comp_max": comp_max,
            "comp_min": None,  # board feeds rarely expose a band minimum
            "comp_unit": "year",  # these feeds' comp_max is a real annual figure, never hourly
            "apply_url": _safe_scalar(url), "seniority": None, "commitment": None, "yoe": None,
            "posted": posted, "expired": False, "is_us": is_us,
            # R2-14 (2026-07-13 hunt): this is a LISTING page of unknown difficulty -- the
            # operator clicks through to a form that could be any ATS, easy or hard. It was
            # hardcoded True, which silently auto-approved every board listing (including ones
            # that redirect to a Workday/login-walled form) whenever job_auto was on.
            "easy": False,
            "board_listing": True}  # apply_url is a listing page: operator clicks through to the form


def _fetch_remotive() -> list[dict]:
    """Remotive: remote-first roles, skews smaller companies + agencies. Public JSON API."""
    out = []
    for cat in ("marketing", "software-dev"):
        try:
            req = urllib.request.Request(
                f"https://remotive.com/api/remote-jobs?category={cat}&limit=100", headers=_HDRS)
            data = json.loads(urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            continue
        for j in data.get("jobs", []):
            out.append(_norm("remotive", j.get("id"), j.get("title", ""),
                             j.get("company_name", ""), j.get("url"), None,
                             j.get("candidate_required_location", ""), j.get("publication_date")))
    return out


def _fetch_remoteok() -> list[dict]:
    """RemoteOK: startup / small-company remote roles. Public JSON (needs the browser UA)."""
    out = []
    try:
        req = urllib.request.Request("https://remoteok.com/api", headers=_HDRS)
        data = json.loads(urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return out
    for j in data:
        if not isinstance(j, dict) or not j.get("position"):
            continue
        blob = (j.get("position", "") + " " + " ".join(j.get("tags", []) or [])).lower()
        if not any(k in blob for k in ("marketing", "seo", "growth", "wordpress", "web dev",
                                       "content", "brand", "demand", "ppc", "paid")):
            continue
        cm = j.get("salary_max")
        out.append(_norm("remoteok", j.get("id") or j.get("slug"), j.get("position", ""),
                         j.get("company", ""), j.get("apply_url") or j.get("url"),
                         int(cm) if cm else None, j.get("location", ""), j.get("date")))
    return out


def _fetch_jobicy() -> list[dict]:
    """Jobicy: remote jobs with a NATIVE us geo filter + industry filter. Public JSON API."""
    out = []
    for ind in ("marketing",):  # marketing only — the "dev" feed is mostly backend/eng, not his web lane
        try:
            req = urllib.request.Request(
                f"https://jobicy.com/api/v2/remote-jobs?count=50&geo=usa&industry={ind}", headers=_HDRS)
            data = json.loads(urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            continue
        for j in data.get("jobs", []):
            cm = j.get("annualSalaryMax") or j.get("annualSalaryMin")
            try:
                cm = int(cm) if cm else None
            except (ValueError, TypeError):
                cm = None
            out.append(_norm("jobicy", j.get("id"), j.get("jobTitle", ""),
                             j.get("companyName", ""), j.get("url"), cm,
                             j.get("jobGeo", "USA"), j.get("pubDate")))
    return out


def fetch_boards() -> list[dict]:
    """All extra-board jobs, deduped by apply_url within the batch."""
    jobs, seen = [], set()
    for fn in (_fetch_jobicy, _fetch_remotive, _fetch_remoteok):
        try:
            for j in fn():
                u = j.get("apply_url")
                if u and u not in seen:
                    seen.add(u)
                    jobs.append(j)
        except Exception:  # noqa: BLE001
            continue
    return jobs


# ---- queue ----
def load_jobs() -> list[dict]:
    if not QUEUE.exists():
        return []
    by_id, order = {}, []
    for line in QUEUE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("id"):
            if r["id"] not in by_id:
                order.append(r["id"])
            by_id[r["id"]] = r
    return [by_id[i] for i in order]


def _save(rec: dict):
    # flock: parallel apply-operators, job_replies (6:30 cron), and dashboard routes all
    # write this file; janitor's compact holds the same lock (2026-07-06 audit).
    from store_lib import _flock
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    with _flock(QUEUE), QUEUE.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def set_status(job_id: str, status: str, reason: str | None = None,
               expect: str | set[str] | None = None) -> dict | None:
    # whole read-modify-append under the lock so two concurrent status writes for the
    # same job can't interleave stale reads (last-write-wins is by disk order, not intent)
    from store_lib import _flock
    with _flock(QUEUE):
        rec = next((x for x in load_jobs() if x.get("id") == job_id), None)
        if not rec:
            return None
        cur = rec.get("status")
        # ROOT-CAUSE FIX (regression post-17bf56c, gpt-5.6-sol review): this was a hardcoded
        # blocklist of specific (status, cur) pairs someone had thought to enumerate -- not a
        # real compare-and-swap, so every NEW racy caller needed its own bespoke re-check
        # (several forgot to, or built one with a gap -- see the callers this same pass fixed:
        # job_replies' per-message write, job_rescan's write, the apply-chain's round-cleanup
        # timeout write). `expect` (a status string, or a set/iterable of acceptable current
        # statuses) makes this a real CAS: under the SAME lock as the read and the write, if
        # `expect` is given and the job's CURRENT status isn't in it, the write is skipped and
        # the unchanged record is returned. Callers that don't pass expect keep the original
        # hardcoded terminal-protection defaults below, unchanged.
        if expect is not None:
            allowed = {expect} if isinstance(expect, str) else set(expect)
            if cur not in allowed:
                return rec
        else:
            # compare-and-swap (2026-07-13 hunt): a replayed/late operator callback (/applied or
            # /skipped, both land here) must NOT clobber a more-authoritative terminal status already
            # written from a real employer email (job_replies/job_rescan set interview|rejected|
            # confirmed|replied). Those beat a browser callback; a stale callback arriving after the
            # ATS already rejected or advanced the job would otherwise silently overwrite the truth.
            # CX-G1 (2026-07-13 codex pass): 'replied' (a human reply) was missing from this set --
            # a replayed /skipped could still overwrite a real human reply. Added.
            if status in ("applied", "skipped") and cur in ("interview", "rejected", "confirmed", "replied"):
                return rec
            # R2-10: a re-approve must never put an already-SUBMITTED job back in the apply pool --
            # that's a double-application waiting to happen. A job that hasn't gotten past
            # pending/approved/skipped/applying can still be (re)approved normally; only these
            # already-submitted/terminal statuses are protected from a backward slide to approved.
            # 'applying' (regression post-17bf56c: was missing) is now ALSO blocked from a
            # re-approve -- a job an operator is actively mid-submit on must not get pulled back
            # into the approved pool by a re-approve tap racing the in-flight window.
            if status == "approved" and cur in ("applied", "confirmed", "interview", "rejected",
                                                "replied", "applying"):
                return rec
            # R2-40: only a still-'approved' job may flip to 'applying' (mark_applying's only
            # caller). Otherwise a stale mark_applying() call -- raced against a concurrent
            # callback that already moved this job to confirmed/skipped/etc -- would stomp that
            # real status back to 'applying' and let it get (re)applied to.
            if status == "applying" and cur != "approved":
                return rec
        rec["status"] = status
        # regression fix post-17bf56c (R1#10): an "applied"->"applied" replay (duplicated
        # callback, network retry, a stale resend) must be idempotent -- it must not refresh
        # applied_at and silently move the real submission timestamp forward (which would
        # then also miscount against applied_today()'s daily-cap check on a later stale
        # replay landing on some OTHER day).
        if status == "applied" and cur != "applied":
            rec["applied_at"] = now_iso()
        if status == "applying":
            rec["applying_at"] = now_iso()
        if reason:
            rec["reason"] = reason
        QUEUE.parent.mkdir(parents=True, exist_ok=True)
        with QUEUE.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec


def mark_applying(ids: list[str]) -> None:
    """Flip a batch to 'applying' at operator spawn (2026-07-12 audit #3): leaves the
    approved pool so a lost applied-callback can't re-select and double-apply. The callback
    (/applied|/skipped) overrides it; a stuck 'applying' >2h retires to skipped in
    approved_to_apply(). expect="approved" (regression post-17bf56c: this relied on
    set_status's hardcoded default instead of saying so) -- a job that raced away from
    'approved' between the caller's own pre-check and this call is left alone, not stomped."""
    for jid in ids:
        set_status(jid, "applying", expect="approved")


def skip_reasons() -> dict:
    """Tally of why jobs were skipped — for diagnosing the apply success rate."""
    from collections import Counter
    return dict(Counter((x.get("reason") or "unspecified")
                        for x in load_jobs() if x.get("status") == "skipped"))


def promote_pending() -> int:
    """Approve all pending jobs (used in full-auto so nothing waits for manual approval)."""
    n = 0
    for x in load_jobs():
        if x.get("status") == "pending":
            set_status(x["id"], "approved"); n += 1
    return n


# Skip-reasons a HUMAN can still finish (a wall, not a dead/disqualified job). PREFIX-
# matched (2026-07-15): the old exact-match ("captcha"/"login" only) silently dropped
# every job skipped as "verify" (2FA/verification code) or "wizard" (multi-step form),
# plus the verbose variants approved_to_apply writes -- "attempt_cap (2 tries, walled by
# captcha/login)" and the new "ats_wall_divert (...)" -- so the exact roles a human most
# needs to finish never reached the manual pile. closed/unqualified/missing_info/expired/
# blacklisted_source stay OUT: those are genuinely dead or disqualified, not human-finishable.
_HUMAN_FINISHABLE = ("captcha", "login", "verify", "wizard", "attempt_cap", "ats_wall_divert")


def needs_manual() -> list[dict]:
    """Jobs the bot couldn't finish but a human still can: CAPTCHA, login/account,
    2FA-verify, multi-step wizard, attempt-capped, or ATS-friction-diverted."""
    out = []
    for x in load_jobs():
        if x.get("status") != "skipped":
            continue
        r = (x.get("reason") or "").lower()
        if any(r.startswith(w) for w in _HUMAN_FINISHABLE):
            out.append({"id": x.get("id"), "title": x.get("title"), "company": x.get("company"),
                        "salary": x.get("salary"), "apply_url": x.get("apply_url"),
                        "source": x.get("source"), "reason": x.get("reason")})
    return out


def needs_verify() -> list[dict]:
    """Jobs whose TRUE SUBMISSION STATE IS UNKNOWN -- a human should check the ATS
    directly. Three ways in: the operator died mid-flight (inflight_timeout), it hit
    the attempt cap after possibly submitting (attempt_cap), or it reported applied
    without quoting any submission confirmation (the /applied callback tags those
    'unconfirmed'). Distinct from needs_manual(), which is walls a human can finish;
    this pile is submissions nobody can prove happened. A sibling install's field
    report (2026-08-12, 9.1-9.3) found this is the system's biggest epistemic hole:
    treating these as not-applied risks double-applying, treating them as applied
    risks abandoning live opportunities, and the count grows on every failed run.
    Surface them; let the human decide."""
    out = []
    for x in load_jobs():
        st = x.get("status")
        r = (x.get("reason") or "").lower()
        uncertain = (
            (st == "skipped" and (r.startswith("inflight_timeout") or r.startswith("attempt_cap")))
            or (st == "applied" and r.startswith("unconfirmed"))
        )
        if uncertain:
            out.append({"id": x.get("id"), "title": x.get("title"), "company": x.get("company"),
                        "status": st, "apply_url": x.get("apply_url"),
                        "source": x.get("source"), "reason": x.get("reason")})
    return out


def note_fields(job_id: str, **fields) -> None:
    """Attach small metadata fields to a job record (append-only, last-write-wins),
    without touching status. First user: _build_prompt stamping resume_file so the
    applied-callback can attribute the resume that actually went OUT instead of
    re-deriving it from whatever file exists at callback time (field report
    2026-08-12, C3)."""
    from store_lib import _flock
    with _flock(QUEUE):
        rec = next((x for x in load_jobs() if x.get("id") == job_id), None)
        if not rec:
            return
        rec.update(fields)
        QUEUE.parent.mkdir(parents=True, exist_ok=True)
        with QUEUE.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _stated_yoe() -> int:
    """The owner's stated years of experience, for the auto-reject gate. Read from the
    owner config at call time -- the previous hardcoded 6 was the ORIGINAL owner's
    number, so every other install was gating (and phrasing its skip reasons) against
    a stranger's resume (field report 2026-08-12, B2)."""
    try:
        import owner
        return max(0, int(str(owner.get("years_experience", "6") or "6").strip()))
    except Exception:  # noqa: BLE001
        return 6


def _target() -> int:
    try:
        return int(json.loads((ROOT / "store" / "config.json").read_text()).get("job_scan_target", 200))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return 200


def _passes_filters(j: dict, minyr: int, seen: set, seen_urls: set, seen_co: set) -> bool:
    """The ONE shared sourcing gate (D5: this exact block was copy-pasted per
    source path inside source_and_queue): US-only, on-title, min comp,
    freshness, and id/apply_url/company+title dedupe, in that order. Mutates
    the seen-sets when a job passes so callers stay dedupe-correct across
    paths. Path-specific gates (ATS blacklist, easy_only, apply_url presence)
    stay at the call sites. agents/job_ats_watch.py mirrors this logic by
    design rather than importing it (their file, their call)."""
    if not j.get("is_us"):          # USA only
        return False
    if not _relevant(j["title"]):
        return False
    # entry-level skip (2026-07-12 funnel finder): a fractional COO / [PRIOR_RESULT] operator
    # will not land a stated sub-3-YOE role, and that band was 0/127 interviews. Skip only
    # the clear floor (stated < 3); the fit-bias handles the 3-5 vs 5+ ordering. Jobs that
    # don't state YOE are NOT skipped (keeps [OWNER]'s "take anything" for the unknowns).
    try:
        if 0 < int(str(j.get("yoe") or 0)) < 3:
            return False
    except (ValueError, TypeError):
        pass
    # R2-12 (2026-07-13 hunt): only compare comp_max to the ANNUAL floor when it's actually
    # posted in yearly terms. comp_max stays in its native unit now (see _comp()), so an
    # hourly rate would otherwise be compared directly against an annual number (e.g. a
    # $50/hr job's "50" failing/passing a $40k floor by sheer coincidence of magnitude,
    # instead of never being annualized as if it were one). Missing comp_unit (older
    # records/board listings) defaults to "year", the prior behavior.
    if (j.get("comp_unit") or "year") == "year" and j.get("comp_max") and j["comp_max"] < minyr:
        return False
    age = _age_days(j.get("posted"))
    if age is not None and age > _MAX_AGE:            # drop stale, near-dead postings
        return False
    ck = _ckey(j.get("company"), j.get("title"))
    if j["id"] in seen or j["apply_url"] in seen_urls or ck in seen_co:
        return False
    seen.add(j["id"]); seen_urls.add(j["apply_url"]); seen_co.add(ck)
    return True


def source_and_queue(queries: list[str], easy_only: bool = False, target: int | None = None) -> dict:
    """Search each query, keep US easy-apply on-target roles, dedupe, enqueue until `target`."""
    import time
    if target is None:
        target = _target()
    _all = load_jobs()
    seen = {x.get("id") for x in _all}
    seen_urls = {x.get("apply_url") for x in _all}
    # company+title de-dupe within THIS run only. Seeding it from ALL history was too aggressive
    # (a full scan hit 865 scanned -> 0 new staged): a role seen weeks ago, or a genuine re-post,
    # could never re-enter, so the queue starved. id + apply_url below still block EXACT repeats,
    # so nothing you already have re-stages; only genuinely new postings get through.
    seen_co = set()
    minyr = _min_yearly()
    bl = _blacklist()
    added, scanned = [], 0
    for q in queries:
        if len(added) >= target:
            break
        try:
            hits = search(q)
        except Exception:
            continue
        scanned += len(hits)
        for j in hits:
            # R2-19 (2026-07-13 hunt): one malformed hit (e.g. an id/apply_url that slipped
            # through as an unhashable list/dict, or any other per-record surprise) must never
            # abort the whole scan -- skip just that hit and keep going.
            try:
                if (j.get("source") or "").lower() in bl:   # retro-blacklisted ATS (e.g. lever)
                    continue
                if easy_only and not j["easy"]:
                    continue
                if not _passes_filters(j, minyr, seen, seen_urls, seen_co):
                    continue
                # only easy-apply roles auto-approve (the bot fills those cleanly). Non-easy-apply
                # roles stage PENDING for [OWNER]'s eyes -> volume in the queue without auto-spraying
                # hard applications (his postmortem: 135 sprayed apps -> 0 interviews).
                rec = {**j, "status": "approved" if (auto_on() and j.get("easy")) else "pending",
                       "query": q, "created": now_iso(), "fit": _fit(j)}
                _save(rec); added.append(rec)
            except Exception:  # noqa: BLE001 -- one bad hit must never abort the whole scan
                continue
        time.sleep(3)  # pace requests so hiring.cafe doesn't rate-limit (429)
    # Pull the smaller-company / agency boards too (Remotive, RemoteOK, + D227's WeWorkRemotely
    # and HN who's-hiring) — beyond hiring.cafe's enterprise skew. Same guard block applies to
    # both sets (identical record shape from _norm()), so they're chained into one iterable
    # rather than duplicating the filter block a third time.
    if len(added) < target:
        extra_boards = list(fetch_boards())
        try:
            import job_boards_extra
            extra_boards += job_boards_extra.fetch_all()
        except Exception:  # noqa: BLE001  (new-board fetch failure never blocks the existing boards)
            pass
        for j in extra_boards:
            if len(added) >= target:
                break
            # R2-19: same per-hit isolation as the hiring.cafe loop above -- one malformed
            # board record must never abort the rest of the scan.
            try:
                if not j.get("apply_url"):
                    continue
                # R2-14 (2026-07-13 hunt): board listings are a click-through page of UNKNOWN
                # difficulty (_norm() no longer hardcodes them easy -- see its docstring), so
                # easy_only must actually gate them here too, same as the loop above. Before
                # this fix easy_only was never even checked for this path.
                if easy_only and not j.get("easy"):
                    continue
                if not _passes_filters(j, minyr, seen, seen_urls, seen_co):
                    continue
                scanned += 1
                # R2-14: gate auto-approval on `easy` too -- previously ANY board job
                # auto-approved whenever job_auto was on, regardless of whether it was
                # actually confirmed easy (mirrors the hiring.cafe loop's own gate above).
                rec = {**j, "status": "approved" if (auto_on() and j.get("easy")) else "pending",
                       "query": j["source"], "created": now_iso(), "fit": _fit(j)}
                _save(rec); added.append(rec)
            except Exception:  # noqa: BLE001
                continue
    return {"scanned": scanned, "added": len(added), "items": added}


# Focused keyword set across his lanes, paced to stay under hiring.cafe's rate limit
# 2026-07-12 funnel finder: the 4 interviews came from senior generalist-manager + RevOps
# roles; Demand-Gen/SEO-specialist/pure-web queries were 0-interview sinks (and the whole
# sub-5-YOE / <150k band converted at 0%). Pruned the sinks, weighted toward the converting
# lanes (Manager/Director/VP marketing, RevOps, Growth, and the agency-flavored + Fractional
# COO angle that his [PRIOR_RESULT] story actually wins). SEO/web kept ONLY in the agency flavor.
DEFAULT_QUERIES = [
    # converting core (senior generalist marketing + the exact titles that interviewed)
    "Marketing Manager", "Senior Marketing Manager", "Growth Marketing Manager",
    "Senior Growth Marketing Manager", "Product Marketing Manager", "Performance Marketing Manager",
    "Director of Marketing", "Marketing Director", "Head of Marketing", "VP Marketing",
    "Head of Growth", "Digital Marketing Manager",
    # revenue operations lane (VP RevOps interviewed; this is his COO-adjacent sweet spot)
    "Revenue Operations Manager", "RevOps Manager", "Director of Revenue Operations",
    "Head of Revenue Operations", "VP Revenue Operations", "Marketing Operations Manager",
    # operator / fractional-COO angle (his strongest positioning, was under-sourced)
    "Fractional COO", "Head of Operations", "Director of Operations", "Chief of Staff",
    # channel lanes he can win at manager+ level
    "Content Marketing Manager", "Email Marketing Manager", "Paid Media Manager",
    "Marketing Automation Manager", "Ecommerce Marketing Manager", "Partnerships Manager",
    # agency-flavored (smaller shops; where the operator story lands + keeps the web/SEO angle)
    "Marketing Manager agency", "Growth Marketer agency", "Account Manager digital agency",
    "Digital Marketing agency", "Marketing Director agency",
]

ROTATION = ROOT / "store" / "job_query_rotation.json"


def active_queries() -> list[str]:
    """Queries for this scan run. HANDSHAKE with agents/job_efficiency.py (D5,
    was computed-but-never-consumed): its run() writes
    store/job_query_rotation.json as {"queries": [...], "generated": "<iso>"}
    from rotated_queries() (postmortem winners + niche skills queries + title
    synonyms ahead of the static list). When that file exists and holds a
    non-empty list of strings, it wins; on missing/corrupt/empty the static
    DEFAULT_QUERIES list is used unchanged. Delete the file to fall back."""
    try:
        qs = json.loads(ROTATION.read_text()).get("queries")
        if isinstance(qs, list) and qs and all(isinstance(q, str) and q for q in qs):
            return qs
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    # config job_queries: THE titles this owner is actually hunting. Without it the
    # scanner falls through to DEFAULT_QUERIES, which are one particular person's
    # target roles, so every scan sources jobs the owner does not want and then
    # spends fit-scoring on them. Set it in store/config.json (setup.py asks).
    try:
        cfg = json.loads((ROOT / "store" / "config.json").read_text())
        qs = cfg.get("job_queries")
        if isinstance(qs, list) and qs and all(isinstance(q, str) and q for q in qs):
            return [q.strip() for q in qs if q.strip()]
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    # last resort: build from the owner's own title rather than a stranger's list
    try:
        import owner
        title = (owner.get("current_title") or "").strip()
        if title:
            base = [title, f"Senior {title}", f"{title} remote"]
            head = title.split()[-1] if title.split() else ""
            if head and head.lower() not in ("manager", "director"):
                base.append(f"{head} Manager")
            return base
    except Exception:  # noqa: BLE001
        pass
    return list(DEFAULT_QUERIES)


# ---- Phase 2: applying ----
def load_profile() -> dict:
    try:
        return json.loads((ROOT / "store" / "application_profile.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _apply_cap() -> int:
    try:
        return int(json.loads((ROOT / "store" / "config.json").read_text()).get("job_daily_apply_cap", 40))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return 40


def applied_today() -> int:
    """Real submissions made today, counted by applied_at timestamp -- NOT current status
    (2026-07-13 hunt). A job applied today that advances same-day to confirmed/rejected/
    interview/replied is still a submission made today and must still count against the
    daily apply cap; counting only status=="applied" silently freed up cap slots the moment
    a fast reply/rejection landed."""
    today = now_iso()[:10]
    return sum(1 for x in load_jobs()
               if x.get("applied_at") and x["applied_at"][:10] == today)


# Lower = apply first (cleanest, least likely to CAPTCHA / block) → more completions per run
APPLY_RANK = {"recruitee": 0, "lever": 0, "ashby": 0, "ashbyhq": 0,
              "jazzhr": 1, "workable": 1, "breezy": 1, "rippling": 2, "careerplug": 2}


def preflight_drop(batch: list) -> list:
    """Cheaply resolve listings BEFORE spawning expensive LLM operators: HTTP 404/410 =
    dead (expired), a GET-confirmed 401/403 = bot-walled (diverted to the human pile).
    Board listing pages and everything else are kept (let the operator decide)."""
    import urllib.error
    alive = []
    for j in batch:
        u = j.get("apply_url", "")
        if not u or j.get("board_listing"):
            alive.append(j)
            continue
        # SSRF gate BEFORE the server-side HEAD (this runs before _build_prompt's gate;
        # apply_url is attacker-postable — 2026-07-07 D5 audit). Unsafe host -> drop.
        try:
            import net_guard
            if not net_guard.public_url_ok(u)[0]:
                set_status(j["id"], "skipped", reason="unsafe_url")
                continue
        except Exception:  # noqa: BLE001 — fail closed: don't HEAD an unvalidated url
            set_status(j["id"], "skipped", reason="unsafe_url")
            continue
        try:
            # pin through net_guard (red-team F1 #7): public_url_ok validates IP-set A, but a
            # bare urlopen re-resolves to IP-set B (DNS-rebind TOCTOU). safe_urlopen pins the
            # validated IP for the actual connection.
            net_guard.safe_urlopen(u, method="HEAD", timeout=8)
            alive.append(j)
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                set_status(j["id"], "expired", reason="dead_listing")
            elif e.code in (401, 403):
                # Likely bot-blocked, NOT a dead role -- but some ATSes 403 only the HEAD
                # verb, so confirm with one GET before deciding (a false divert costs a
                # perfectly automatable job). A confirmed wall goes straight to the manual
                # pile instead of spending a full operator session rediscovering the same
                # 403 (field report 2026-08-12, D3). Marking it expired would be wrong too:
                # a human browser can usually still open it. The 'ats_wall_divert' prefix
                # is a member of _HUMAN_FINISHABLE, which is what routes it to
                # needs_manual(); changing the prefix silently drops it from that pile.
                try:
                    net_guard.safe_urlopen(u, method="GET", timeout=8)
                    alive.append(j)
                except urllib.error.HTTPError as e2:
                    if e2.code in (401, 403):
                        set_status(j["id"], "skipped",
                                   reason=f"ats_wall_divert (HTTP {e2.code} at preflight; "
                                          "likely bot-blocked, a human browser can usually "
                                          "still apply)")
                    else:
                        alive.append(j)
                except Exception:  # noqa: BLE001
                    alive.append(j)
            else:
                alive.append(j)
        except Exception:  # noqa: BLE001  (network hiccup -> keep, don't drop a live job)
            alive.append(j)
    return alive


def bump_attempts(job_ids) -> None:
    """Stamp attempts+1 on each job handed to an operator, so a hung/dead operator can't
    make the chain retry the same job forever, and a submitted-but-unmarked job stops after 2."""
    from store_lib import _flock
    with _flock(QUEUE):
        by_id = {x["id"]: x for x in load_jobs()}
        stamped = [{**by_id[jid], "attempts": by_id[jid].get("attempts", 0) + 1,
                    "last_attempt": now_iso()} for jid in job_ids if jid in by_id]
        QUEUE.parent.mkdir(parents=True, exist_ok=True)
        with QUEUE.open("a") as f:
            for j in stamped:
                f.write(json.dumps(j, ensure_ascii=False) + "\n")


def stalled() -> list[dict]:
    """Approved jobs that hit the attempt ceiling without a result (needs a human look)."""
    return [x for x in load_jobs() if x.get("status") == "approved" and x.get("attempts", 0) >= 2]


# Legal-entity suffixes stripped during company normalization (R2-17, 2026-07-13 hunt) so
# 'Acme' and 'Acme, Inc.' -- the same real employer under two spellings -- collapse to ONE
# dedupe key instead of both independently passing the one-submission-per-employer guard.
_LEGAL_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "llc", "llp", "ltd", "limited",
    "co", "company", "group", "holdings", "holding", "plc", "pllc", "pc", "gmbh",
}


def _conorm(company: str) -> str:
    """Normalized company key for employer-level dedupe/blocklist matching (used by _ckey()
    for the one-submission-per-employer guard and by approved_to_apply()'s submitted_cos
    set). Strips punctuation/casing AND a trailing legal-entity suffix so 'Acme' and
    'Acme, Inc.' collapse to the same key (R2-17)."""
    words = re.sub(r"[^a-z0-9\s]", "", (company or "").lower()).split()
    while words and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return "".join(words)


def approved_to_apply() -> list[dict]:
    """Approved jobs to apply to now, best-fit first, trimmed to today's remaining cap.
    Excludes blacklisted sources and jobs that already burned 2 attempts (poison-pill guard).
    Postmortem guards (2026-07-03, 0/135 diagnosis): one submission per EMPLOYER ever
    (21.5% of the first sprint were duplicate-company submissions), no roles asking
    2+ years past the owner's stated YOE (auto-reject territory), fit floor 62 (kills the
    48-59 tail that was slipping into the queue).

    D223/D229/D231/D254 (job_fit_signals.py, 2026-07-03 D-lane build): additive
    guards layered AFTER the three postmortem guards above, same never-weaken
    rule -- resume keyword-match floor, apply-time salary re-check, company
    blocklist, location-variant repost dedupe. job_fit_signals is imported
    lazily here (not at module top) because it imports jobs itself; a
    top-level import would be circular. Any failure importing/running it
    (e.g. a bad store file) is swallowed so this never becomes a NEW way for
    approved_to_apply() to break -- it only ever adds skip reasons, never
    raises past this function."""
    rem = max(0, _apply_cap() - applied_today())
    bl = _blacklist()
    all_jobs = load_jobs()
    # "applying" = an operator is mid-submit on this job (marked at spawn, 2026-07-12 audit #3).
    # Count its company as submitted so a SIBLING job at the same employer isn't also applied
    # to while one is in flight — the old dup guard only saw terminal statuses, so a job whose
    # applied-callback was lost (compaction 401) stayed "approved" and got re-applied.
    submitted_cos = {_conorm(x.get("company")) for x in all_jobs
                     if x.get("status") in ("applied", "confirmed", "interview", "replied", "applying")}
    # Codex 2026-07-15 (double-apply hazard): a job skipped as submission-UNCERTAIN -- the
    # operator may have submitted right before dying or capping -- must KEEP reserving its
    # employer, or a sibling role at the same company gets auto-applied and we double-apply
    # there. captcha/login/verify/wizard did NOT submit (a wall stops before the button), so
    # those free the employer for a sibling; only the genuinely-ambiguous inflight_timeout
    # and attempt_cap reserve it.
    for x in all_jobs:
        if x.get("status") == "skipped":
            r = (x.get("reason") or "").lower()
            if r.startswith("inflight_timeout") or r.startswith("attempt_cap"):
                submitted_cos.add(_conorm(x.get("company")))
    submitted_cos.discard("")
    try:
        import job_fit_signals
        _extra_block = job_fit_signals.extra_block_reason
    except Exception:  # noqa: BLE001
        _extra_block = None
    # ATS-friction routing (2026-07-15): order cleanest-ATS first within a fit tier, and
    # divert a chronically-walling ATS straight to the human-finish pile. Lazy + swallowed
    # so a friction-module failure can never break apply selection (same contract as above).
    try:
        import ats_friction
        _band_fn = ats_friction.friction_band
        _divert = ats_friction.should_divert
    except Exception:  # noqa: BLE001
        _band_fn = _divert = None

    def _blocked(x: dict) -> str:
        if _conorm(x.get("company")) in submitted_cos:
            return "dup_company (already submitted to this employer)"
        # R2-15 (2026-07-13 hunt): freshness/expired were only ever checked at SOURCE time
        # (_passes_filters, when the job was first queued). A job approved while still fresh
        # can sit in the queue for days before an apply-round selects it -- re-check both
        # right here, at SELECTION time, so a since-gone-stale (or already-known-expired)
        # posting doesn't get applied to on a stale verdict. (The live "is this listing still
        # up" HTTP check stays preflight_drop()'s job, downstream of this selection.)
        if x.get("expired"):
            return "expired_at_selection"
        age = _age_days(x.get("posted"))
        if age is not None and age > _MAX_AGE:
            return f"stale_at_selection (age {age}d > {_MAX_AGE}d)"
        try:
            _yoe_stated = _stated_yoe()
            if int(str(x.get("yoe") or 0)) >= _yoe_stated + 2:
                return f"yoe_gate ({x.get('yoe')} required vs {_yoe_stated} stated)"
        except (ValueError, TypeError):
            pass
        if x.get("fit", 50) < 62:
            return f"fit_floor ({x.get('fit')})"
        if _extra_block is not None:
            try:
                why = _extra_block(x, all_jobs)
                if why:
                    return why
            except Exception:  # noqa: BLE001
                pass
        return ""

    # retire in-flight jobs whose operator died without a callback (>2h). Ambiguous — the
    # submit MAY have gone through — so they go to skipped (NOT back to approved), never
    # blindly re-applied to a real employer (2026-07-12 audit #3). [OWNER] re-approves if wanted.
    from datetime import datetime as _dt, timedelta as _td
    _stale = (_dt.now().astimezone() - _td(hours=2)).isoformat()
    for x in all_jobs:
        if x.get("status") == "applying" and (x.get("applying_at") or "") < _stale:
            # expect="applying" (Codex end-to-end pass, 2026-07-14): all_jobs is a snapshot, so a
            # delayed /applied callback may have moved this job to 'applied' AFTER the read but
            # BEFORE this write. Without the CAS, that real submission gets erased back to skipped
            # (and a later interview email can't attach). The round-cleanup sweep already got this
            # guard; this sibling sweep was missed.
            set_status(x["id"], "skipped",
                       "inflight_timeout (operator died mid-submit; verify in ATS before retrying)",
                       expect="applying")

    appr = []
    for x in all_jobs:
        if x.get("status") != "approved":
            continue
        # HONEST STATUS (2026-07-11): a job that burned its 2 attempts (captcha/login walls)
        # or lives on a blacklisted source is NOT applyable, but it used to sit at "approved"
        # forever -- so the dashboard showed "31 approved / ready" when zero could actually be
        # applied to, and the evening chain looked wedged. Retire them to skipped with a clear
        # reason so "approved" means genuinely-ready, not silently-dead.
        if x.get("attempts", 0) >= 2:
            set_status(x["id"], "skipped", "attempt_cap (2 tries, walled by captcha/login)")
            continue
        if (x.get("source") or "").lower() in bl:
            set_status(x["id"], "skipped", f"blacklisted_source ({x.get('source')})")
            continue
        why = _blocked(x)
        if why:
            set_status(x["id"], "skipped", why)
            continue
        # ATS-friction divert (2026-07-15): an ATS that has walled [OWNER]'s OWN attempts at
        # or above the divert threshold (learned, min-sample-gated -- never a static guess)
        # wastes an operator run and a poison-pill attempt. Route it straight to the human-
        # finish pile (surfaces via needs_manual + a pre-filled packet). expect="approved"
        # so a reply that advanced this job to interview/rejected between the load above and
        # this write is never clobbered back to skipped (set_status CAS).
        if _divert is not None:
            try:
                divert, dreason = _divert(x, all_jobs)
            except Exception:  # noqa: BLE001
                divert, dreason = False, ""
            if divert:
                set_status(x["id"], "skipped", dreason, expect="approved")
                continue
        appr.append(x)
        submitted_cos.add(_conorm(x.get("company")))  # cap within this batch too

    # ATS-friction BAND first, then highest win-probability (fresh + on-title + pays), then
    # APPLY_RANK. Bands (not the raw score) keep the common case pure fit-order -- only the
    # login-wall-heavy ATSes (workday/icims/taleo) sink below cleaner ones, so the operator
    # spends its daily budget on forms that actually submit instead of ones that just wall.
    def _band(x: dict) -> int:
        if _band_fn is None:
            return 0
        try:
            return _band_fn(x, all_jobs)
        except Exception:  # noqa: BLE001
            return 0
    appr.sort(key=lambda x: (_band(x), -x.get("fit", 50),
                             APPLY_RANK.get((x.get("source") or "").lower(), 3)))
    return appr[:rem]


if __name__ == "__main__":
    import collections
    # D5: prefer job_efficiency's rotation when present (see active_queries docstring);
    # server.py's job_scan action and morning.sh both invoke this __main__ path.
    res = source_and_queue(active_queries(), easy_only="--easy-only" in sys.argv)
    print(f"scanned {res['scanned']} jobs, queued {res['added']} new easy-apply roles")
    by = collections.Counter(x["source"] for x in res["items"])
    print("by ATS:", dict(by))
