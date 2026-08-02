#!/usr/bin/env python3
"""Per-target "why them" one-liner — A5.

li_scoring.py produces a deterministic NUMBER (score_target()). This module
produces the human-readable ONE LINE that explains it, for operator-brief
quality (A5's own wording: "operator brief quality") — so when a Sonnet
operator picks up a connect/comment item, it isn't just "score 78," it's
"Founder at a white-label web agency, posted about fulfillment bottlenecks
3 days ago, 6 mutual connections."

why_them() is deterministic-first: it builds the line directly from
li_scoring's own components (no LLM call needed for the common case), falling
back to a cheap Haiku call ONLY when the deterministic template would be too
thin to be useful (e.g. no headline keyword hit, no mutuals, no recency — the
score components are empty and the target only surfaced via A2 commenter
mining, so the "why" has to come from what they actually said).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import li_scoring  # noqa: E402
import planner  # noqa: E402
from store_lib import humanize  # noqa: E402


def _deterministic_line(target: dict, scored: dict) -> str:
    """Build the why-them line straight from score components, no LLM. Returns
    '' if there's not enough signal to say anything specific (caller should
    fall back to the LLM path in that case)."""
    bits = []
    headline = (target.get("headline") or "").strip()
    if li_scoring.title_lexicon_hit(headline) and headline:
        bits.append(headline.split("|")[0].split(",")[0].strip()[:60])

    if scored["components"].get("recency", 0) >= 20:
        bits.append("posted recently")
    elif scored["components"].get("recency", 0) >= 10:
        bits.append("active this month")

    mutuals = target.get("mutuals_count", 0) or 0
    if mutuals >= 3:
        bits.append(f"{mutuals} mutual connections")

    if target.get("is_commenter"):
        bits.append("engaged with target-adjacent content")

    tier = scored.get("tier", 0)
    if tier == 1:
        bits.append("US beachhead metro")

    if not bits:
        return ""
    return humanize(", ".join(bits) + ".")


WHY_PROMPT = """In ONE short line (under 20 words, no em-dashes, no fluff), explain why
this LinkedIn person is worth [OWNER] reaching out to. He runs white-label web builds for
marketing/digital agency owners. Be SPECIFIC to what's actually here, never generic
("good prospect," "seems relevant"). If genuinely nothing stands out, say so plainly.

Person: %s
Headline: %s
Recent activity/post context: %s

Return ONLY the one line, no quotes, no preamble."""


def _llm_line(target: dict) -> str:
    name = target.get("name", target.get("author", "")) or "(unknown)"
    headline = target.get("headline", "") or "(none captured)"
    context = (target.get("post_context") or target.get("text") or "")[:400] or "(none captured)"
    out = planner._cli(WHY_PROMPT % (name, headline, context), timeout=60, feature="plan")
    return humanize((out or "").strip().strip('"'))[:200]


def why_them(target: dict, scored: dict | None = None, *, allow_llm: bool = True) -> str:
    """target: the sourced dict (headline/location/mutuals_count/is_commenter/
    post_context/etc). scored: pre-computed li_scoring.score_target() output,
    or None to compute it here. allow_llm=False forces deterministic-only
    (useful for tests/fixture mode — zero LLM calls, zero cost, zero
    nondeterminism)."""
    scored = scored if scored is not None else li_scoring.score_target(target)
    line = _deterministic_line(target, scored)
    if line:
        return line
    if not allow_llm:
        return "insufficient signal captured at sourcing time"
    return _llm_line(target) or "insufficient signal captured at sourcing time"


if __name__ == "__main__":
    demo = {"headline": "Founder @ Acme Digital Agency | White-label web for agencies",
            "location": "Austin, Texas Area", "mutuals_count": 8, "is_commenter": True}
    print(why_them(demo, allow_llm=False))
