#!/usr/bin/env python3
"""Niche-specific value-nugget bank — A34.

10 concrete, non-generic value nuggets per niche [OWNER]'s real playbooks already
cover, pulled from business-library/playbooks/objections.md (the "why" lines
from the objection counters, reframed as standalone value-add observations
rather than counters to a specific pushback) plus icp-and-personas.md. These
are meant to be dropped into a warm-DM drafting prompt as "here's a real,
specific thing you know that this person's world would find useful" so value-
add DMs aren't generic ("hope this is useful!") but actually carry something.

Read-only against business-library (never edits it — that's [OWNER]'s library,
out of this lane's write scope regardless). If objections.md is ever
restructured, NUGGETS below is a static curated list (not parsed live from the
file), so it degrades gracefully — it just goes stale rather than breaking.
"""
from __future__ import annotations

# Keyed by niche. "agency" is the primary ICP (per icp-and-personas.md); the
# other niches are the SMB/local-service world agencies serve, useful when a
# target is agency-adjacent (e.g. runs a small marketing shop FOR salons).
# Every nugget below is a real, specific observation, not filler — curated
# from business-library/playbooks/objections.md's "why" lines (2026-07-03).
NUGGETS: dict[str, list[str]] = {
    "agency": [
        "Delivery pipeline is usually the real bottleneck agencies hit, not sales, most owners just don't diagnose it that way.",
        "A working preview before the balance is due tends to kill more trust objections than any amount of reassurance.",
        "Templates can't diagnose a specific site's faults, that's the actual gap between a $200 Fiverr build and a real one.",
        "Half up front, half on delivery aligns incentives better than any guarantee language does.",
        "The agencies that scale revenue without scaling headcount usually fixed fulfillment before they fixed sales.",
        "A discount that doesn't buy something (a testimonial, an intro) trains clients to expect discounts.",
        "Scope creep protection is what protects the PRICE, not the other way around.",
        "White-labeling a vendor for overflow work is different from replacing your existing team, most owners don't separate those two decisions.",
        "The owner becoming the delivery constraint usually shows up as a revenue plateau long before anyone calls it that.",
        "Slow season is exactly when rebuilding the fulfillment pipeline pays off, not when to cut spend on it.",
    ],
    "web-design": [
        "A 5-fault teardown of an existing site does more selling than any feature list.",
        "48-72hr turnaround changes the conversation from 'can you' to 'when can you start.'",
        "Approval before anything goes live removes the single biggest objection to a fast build.",
        "Hosting is the parking spot, the build is the car, that distinction resets most price objections.",
        "A site that leaks visitors makes every dollar of ad spend less efficient, fixing the leak comes first.",
        "Spec-work mockups produce committee-flavored designs, a real working preview beats three static comps.",
        "Rebuild vs fix is a diagnosis question, not a preference, structural problems make a $450 patch a waste.",
        "A 2-minute-read proposal gets read on the call, a 10-page one gets 'I'll look later'd into never.",
        "The upgrade path (blog/booking/store later) only works if the initial build was structured for it, most aren't.",
        "DIY site builders price out fine until you price the owner's own hours building it at 11pm.",
    ],
    "local-service": [
        "Local-service owners buy 'handled,' not features, most decisions come down to how much of the process disappears for them.",
        "A missed job costs more than most owners think when they actually price it out.",
        "Busy-season traffic is the highest-value traffic a new site will ever catch, waiting wastes the best weeks.",
        "Owners who got burned by a past vendor respond well to a written, checkable process, not more reassurance.",
        "The nephew/friend-does-it-cheap objection usually resolves itself on a 2-week timeline.",
        "'Local' matters less than 'findable,' customers find local businesses on Google, not by driving past the office.",
        "A phone number that needs to ring is a different design goal than a portfolio piece, most local sites conflate the two.",
        "Payment-plan requests are usually solved by reframing existing deposit terms, not by actually discounting.",
        "A slow season is when smart local-service owners rebuild, so the site's ready before the busy season traffic hits.",
        "Intake friction (logo, photos, 20 minutes) is the real blocker on local-service builds, not the price.",
    ],
}

DEFAULT_NICHE = "agency"


def nuggets_for(niche: str) -> list[str]:
    """Returns the curated list for a niche, or the agency default if the
    niche isn't recognized (never an empty list, never invented content —
    falls back to the PRIMARY ICP's real nuggets, which is a safe default
    since agency is who this whole system targets)."""
    key = (niche or "").strip().lower()
    return list(NUGGETS.get(key, NUGGETS[DEFAULT_NICHE]))


def random_nugget(niche: str, seed: str = "") -> str:
    """Deterministic pick (hash of seed) rather than true random, so the same
    seed (e.g. a target's URL) always gets the same nugget across re-runs —
    useful for testing and for not contradicting yourself if a drafting pass
    re-runs on the same target."""
    import hashlib
    pool = nuggets_for(niche)
    if not pool:
        return ""
    if not seed:
        return pool[0]
    h = int(hashlib.sha1(seed.encode("utf-8")).hexdigest(), 16)
    return pool[h % len(pool)]


def list_niches() -> list[str]:
    return sorted(NUGGETS.keys())


if __name__ == "__main__":
    for niche in list_niches():
        print(f"\n{niche} ({len(NUGGETS[niche])} nuggets):")
        for n in NUGGETS[niche][:3]:
            print(f"  - {n}")
