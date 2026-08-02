#!/usr/bin/env python3
"""LinkedIn target relevance scoring — A1, A5, A12, A13, A14, A57 from section A.

score_target() replaces "just keyword hits" (the old implicit sourcing filter) with
a weighted composite: ICP-fit keywords in headline + activity recency + mutual-space
signal + geo tier + freshness. Pure function, no LLM, no network — the LLM is only
used (in li_conveyor.py / networking.py callers) to write the "why them" ONE-LINE
(A5), never to compute the number itself, so the number stays deterministic and
testable.

Score is 0-100. Callers apply their own floor (A7: daily source cap config with a
quality floor — see networking._net_caps()/config "network.score_floor").

Nothing here browses LinkedIn. Every input is a dict the SOURCING step (a headless
claude -p call reading operator-fed profile text, or later an operator's structured
scrape) already produced. No invented LinkedIn data — if a field is missing, its
component scores 0, it never gets guessed.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

# ---- A13: agency-owner title lexicon (expanded beyond "owner"/"founder") ----
TITLE_LEXICON = [
    "founder", "co-founder", "owner", "co-owner", "principal", "president",
    "ceo", "chief executive", "managing director", "managing partner",
    "director of operations", "director of ops", "coo", "chief operating officer",
    "head of operations", "vp of operations", "vp operations",
    "agency owner", "agency founder", "creative director", "operations manager",
    "fractional coo", "general manager",
]

# ICP-fit keywords: agency / local-service business language from icp-and-personas.md
# + business-profile.md. "local-service keywords" per A1's own wording covers the
# adjacent SMB-service audience (plumbers/dentists/salons etc as AGENCY clients'
# clients — i.e. agencies serving that space), not just pure marketing agencies.
ICP_KEYWORDS = [
    "agency", "agencies", "marketing agency", "digital agency", "web agency",
    "white-label", "white label", "fulfillment", "client delivery",
    "web design", "website design", "webflow", "wordpress",
    "local service", "local business", "smb", "marketing consultant",
    "growth partner", "fractional cmo", "demand gen", "lead generation",
    "paid ads", "seo agency", "creative agency", "branding agency",
]

# US metro beachhead tiers (A14). Tier 1 = highest priority. Extend as the beachhead
# plan (business-library) names specific metros; this stays a reasonable US-general
# default until then. Timezone is stored alongside so callers can apply A55's
# LinkedIn-hours window per-target later without a second lookup.
GEO_TIERS = {
    # metro substring (lowercased) -> (tier, tz)
    "new york": (1, "America/New_York"),
    "los angeles": (1, "America/Los_Angeles"),
    "chicago": (1, "America/Chicago"),
    "san francisco": (1, "America/Los_Angeles"),
    "austin": (1, "America/Chicago"),
    "dallas": (1, "America/Chicago"),
    "atlanta": (1, "America/New_York"),
    "denver": (1, "America/Denver"),
    "phoenix": (1, "America/Phoenix"),
    "seattle": (1, "America/Los_Angeles"),
    "miami": (1, "America/New_York"),
    "boston": (1, "America/New_York"),
}
UNKNOWN_GEO_TIER = 0  # location field absent/blank -> no signal, scores 0 (never guessed)
DEFAULT_GEO_TIER = 2  # a KNOWN but non-metro-listed US location: tier 2, tz unknown
NON_US_GEO_TIER = 3


def title_lexicon_hit(headline: str) -> bool:
    h = (headline or "").lower()
    return any(t in h for t in TITLE_LEXICON)


def icp_keyword_hits(headline: str) -> int:
    h = (headline or "").lower()
    return sum(1 for kw in ICP_KEYWORDS if kw in h)


def geo_tier(location: str) -> tuple[int, str]:
    """(A14) Returns (tier, tz_or_empty). Location string match is substring/
    lowercased, so 'Austin, Texas Area' matches 'austin'. Non-US-looking strings
    (heuristic: contains a country name outside common US patterns) get tier 3;
    this is intentionally soft since geo strings on LinkedIn are free text.

    An EMPTY/missing location is UNKNOWN_GEO_TIER (0), distinct from
    DEFAULT_GEO_TIER (2, a real US location we just don't have a metro
    mapping for) — geo_score() must not award points for a field that was
    simply never captured (no invented data)."""
    loc = (location or "").lower().strip()
    if not loc:
        return (UNKNOWN_GEO_TIER, "")
    for metro, (tier, tz) in GEO_TIERS.items():
        if metro in loc:
            return (tier, tz)
    non_us_markers = ("united kingdom", "canada", "india", "australia", "germany",
                       "france", "philippines", "pakistan", "nigeria", "brazil",
                       "mexico", "spain", "italy", "netherlands")
    if any(m in loc for m in non_us_markers):
        return (NON_US_GEO_TIER, "")
    return (DEFAULT_GEO_TIER, "")


def _days_since(iso_date: str, today: date | None = None) -> int | None:
    if not iso_date:
        return None
    today = today or date.today()
    try:
        d = datetime.fromisoformat(iso_date[:10]).date()
    except ValueError:
        return None
    return max(0, (today - d).days)


def recency_score(last_active: str, today: date | None = None) -> float:
    """0-25. last_active: ISO date of their last visible post/activity, if known.
    Unknown -> 0 (never guessed). Full 25 within 3 days, decays to 0 by 30 days,
    matching A57's 'posted this week' freshness bias at the top end."""
    days = _days_since(last_active, today)
    if days is None:
        return 0.0
    if days <= 3:
        return 25.0
    if days >= 30:
        return 0.0
    return round(25.0 * (1 - (days - 3) / 27.0), 1)


def icp_fit_score(headline: str) -> float:
    """0-40. Title lexicon hit (A13) is worth more than a bare keyword hit, since
    a title match means 'decision-maker,' a keyword hit just means 'adjacent
    language.' Capped so keyword-stuffing a headline can't blow past a real title
    match's ceiling."""
    score = 0.0
    if title_lexicon_hit(headline):
        score += 22.0
    score += min(icp_keyword_hits(headline) * 4.5, 18.0)
    return round(min(score, 40.0), 1)


def mutual_signal_score(mutuals_count: int, in_target_group: bool = False) -> float:
    """0-20. (A12) 2nd-degree/mutuals count captured AT SOURCING TIME (the caller's
    job — this just scores what it's given). (A73) group-membership overlap also
    counts as a mutual-space signal. mutuals_count is clamped; a 40-mutual person
    isn't meaningfully 'more mutual' than a 12-mutual one for THIS purpose."""
    m = max(0, int(mutuals_count or 0))
    score = min(m, 12) / 12.0 * 15.0
    if in_target_group:
        score += 5.0
    return round(min(score, 20.0), 1)


def geo_score(location: str) -> float:
    """0-15. Tier 1 metro = full marks, tier 2 (known non-metro US) = partial,
    tier 3 (non-US) and tier 0 (unknown/blank) = 0 (per A14's 'US metros first'
    beachhead plan; a blank location is never worth points)."""
    tier, _tz = geo_tier(location)
    return {1: 15.0, 2: 7.0, 3: 0.0, 0: 0.0}.get(tier, 0.0)


def engagement_context_bonus(is_commenter: bool = False) -> float:
    """0-10. A2: people who comment on target-adjacent posts are warmer than cold
    profiles. This is the one component that's a flat bonus rather than a curve,
    since 'did they engage with target-adjacent content' is binary at sourcing time."""
    return 10.0 if is_commenter else 0.0


def score_target(target: dict, *, today: date | None = None) -> dict:
    """Composite 0-100 relevance score (A1) for a sourced LinkedIn person.

    target: {headline, location, last_active (ISO date, optional),
             mutuals_count (int, optional), in_target_group (bool, optional),
             is_commenter (bool, optional)}

    Returns {"score": float, "components": {...}, "tier": geo tier int,
             "tz": geo tz str}. Never raises; missing fields just score 0 for
    their component, never invented.
    """
    headline = target.get("headline", "") or ""
    location = target.get("location", "") or ""
    tier, tz = geo_tier(location)

    components = {
        "icp_fit": icp_fit_score(headline),
        "recency": recency_score(target.get("last_active", ""), today),
        "mutual_signal": mutual_signal_score(
            target.get("mutuals_count", 0), bool(target.get("in_target_group"))),
        "geo": geo_score(location),
        "engagement_context": engagement_context_bonus(bool(target.get("is_commenter"))),
    }
    total = round(sum(components.values()), 1)
    return {"score": min(total, 100.0), "components": components, "tier": tier, "tz": tz}


def rank_targets(targets: list[dict], *, floor: float = 0.0, today: date | None = None) -> list[dict]:
    """Score + sort a batch of sourced targets, best first. Each input dict gets
    a '_score' key merged in (non-destructive to other fields). Targets scoring
    below `floor` are DROPPED, not just sorted last (A7's quality floor)."""
    scored = []
    for t in targets:
        v = score_target(t, today=today)
        if v["score"] < floor:
            continue
        merged = dict(t)
        merged["_score"] = v["score"]
        merged["_score_components"] = v["components"]
        merged["_geo_tier"] = v["tier"]
        merged["_tz"] = v["tz"]
        scored.append(merged)
    scored.sort(key=lambda t: t["_score"], reverse=True)
    return scored


if __name__ == "__main__":
    demo = {"headline": "Founder @ Acme Digital Agency | White-label web for agencies",
             "location": "Austin, Texas Area", "last_active": date.today().isoformat(),
             "mutuals_count": 8, "is_commenter": True}
    v = score_target(demo)
    print(f"score={v['score']} components={v['components']} tier={v['tier']} tz={v['tz']}")
