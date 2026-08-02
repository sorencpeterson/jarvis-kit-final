#!/usr/bin/env python3
"""Engaged-commenters mining — A2 (★).

li_scoring.py already treats "is_commenter" as a scoring SIGNAL (a 10-point
engagement_context bonus). This module is the other half A2 actually asks for:
queue commenters WITH the post context attached, so a downstream drafting
pass (or a human reviewing the queue) can see not just "this person commented"
but WHAT they said and on WHOSE post, which is exactly the context a genuine
warm DM needs (A3's day-2 conveyor, or a direct comment-reply).

Input is operator-fed (same [E] boundary as everywhere else in this lane —
this system never browses LinkedIn itself): a list of {commenter_name,
commenter_headline, commenter_url, comment_text, post_author, post_url,
post_context} dicts, presumably scraped by an operator reading a target-
adjacent post's comment section.

queue_commenters() turns each into a scored, why-tagged, context-attached
candidate ready for li_pipeline.run_pipeline() or direct comment/connect
queueing — it does NOT call networking.save_item() itself (staying
consistent with li_pipeline.py's own boundary: scoring/filtering is this
lane's job, the ACTUAL queue write is an explicit caller action).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import li_scoring  # noqa: E402
import li_whythem  # noqa: E402


def commenter_to_candidate(row: dict) -> dict:
    """Reshape one operator-scraped commenter row into the candidate dict
    shape li_scoring.score_target()/li_pipeline.run_pipeline() expect, with
    the post context preserved so downstream drafting/why-them has it."""
    return {
        "name": row.get("commenter_name", ""),
        "headline": row.get("commenter_headline", ""),
        "url": row.get("commenter_url", ""),
        "location": row.get("commenter_location", ""),
        "mutuals_count": row.get("mutuals_count", 0),
        "last_active": row.get("last_active", ""),
        "is_commenter": True,  # always true by construction — this IS the commenter path
        "post_context": (f"Commented on {row.get('post_author', '(unknown)')}'s post: "
                          f"\"{(row.get('post_context') or '')[:200]}\" — their comment: "
                          f"\"{(row.get('comment_text') or '')[:200]}\""),
        "post_url": row.get("post_url", ""),
        "post_author": row.get("post_author", ""),
        "comment_text": row.get("comment_text", ""),
    }


def score_commenters(rows: list[dict], *, allow_llm: bool = False) -> list[dict]:
    """Score + attach why-them for a batch of commenter rows. Returns candidate
    dicts with _score/_score_components/_why merged in, sorted best-first —
    same output shape li_pipeline.run_pipeline() produces, so callers can feed
    this straight into the SAME diversity/cooldown/history filters rather than
    duplicating that logic here (this module's job ends at 'here are the
    commenters, scored, with context' — the shared filters in li_pipeline.py
    still apply on top if the caller wants A4/A8/A9 enforcement)."""
    candidates = [commenter_to_candidate(r) for r in rows]
    scored = li_scoring.rank_targets(candidates)
    for c in scored:
        s = {"score": c.get("_score", 0), "components": c.get("_score_components", {}),
             "tier": c.get("_geo_tier", 0), "tz": c.get("_tz", "")}
        c["_why"] = c.get("post_context", "") or li_whythem.why_them(c, scored=s, allow_llm=allow_llm)
    return scored


if __name__ == "__main__":
    demo_rows = [
        {"commenter_name": "FIXTURE Commenter", "commenter_headline": "Founder @ FIXTURE Agency",
         "commenter_url": "https://linkedin.com/in/fixture-commenter-1",
         "post_author": "FIXTURE Target Poster", "post_url": "https://linkedin.com/posts/fixture",
         "post_context": "Struggling to scale fulfillment without more hires.",
         "comment_text": "Same boat, ended up white-labeling overflow work instead of hiring."},
    ]
    import json
    print(json.dumps(score_commenters(demo_rows), indent=2, ensure_ascii=False))
