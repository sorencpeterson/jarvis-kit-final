#!/usr/bin/env python3
"""The composed sourcing pipeline — wires li_scoring + li_history + li_budget +
li_whythem together into ONE function, since none of those modules alone is
"sourcing": each does one stage, and until this file existed nothing called
them in sequence. This is what a future net_scan-equivalent headless sourcing
step (fed by operator-scraped candidate profiles, per this system's
architecture: sourcing/scoring/drafting run headless, ACTUAL browsing doesn't)
would call.

Pipeline stages, in order:
  1. li_history.filter_unattempted   (A4: drop anyone ever touched before)
  2. li_history.filter_cooldown_companies  (A9: drop companies on cooldown)
  3. li_quality.is_never_engage HARD filter (A40: MLM/competitor — explicit,
     not left as an accidental side effect of the score floor; a well-
     disguised MLM headline stuffed with ICP keywords could otherwise score
     high enough to slip past step 4 below)
  4. li_scoring.rank_targets          (A1: score + sort + apply quality floor)
  5. li_budget.filter_diversity       (A8: cap per-company this week)
  6. li_whythem.why_them per survivor  (A5: attach the one-line why)

Input candidates are RAW sourced dicts (headline/location/url/mutuals_count/
etc — see li_scoring.score_target()'s docstring for the exact shape). This
function does NOT source candidates itself (no browsing happens anywhere in
this lane) — candidates must already exist, either from an operator run's
scrape output or, for testing, from FIXTURE data clearly marked as such.

run_pipeline(candidates, fixture=True) with fixture=True skips the A5 LLM
fallback entirely (why_them(..., allow_llm=False)) so a dry/fixture run is
100% deterministic, zero LLM spend, zero network — exactly what the mission's
VERIFY step calls for ("RUN networking sourcing in dry/fixture mode").
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import li_history  # noqa: E402
import li_scoring  # noqa: E402
import li_budget  # noqa: E402
import li_whythem  # noqa: E402
import li_quality  # noqa: E402


def run_pipeline(candidates: list[dict], *, score_floor: float | None = None,
                  fixture: bool = True) -> dict:
    """Returns {"input_n": N, "after_dedupe": N, "after_cooldown": N,
    "after_score_floor": N, "after_diversity": N, "queued_ready": [...]}
    where queued_ready is the final list of candidates (each with _score/
    _score_components/_why added) that would be handed to
    networking.queue_connections()/queue_comments() etc.

    Never mutates any store — purely a filter/scoring pipeline. Actual
    queueing is a separate, explicit step the caller takes afterward (this
    function's job ends at "here are the good ones, with scores and reasons
    attached")."""
    floor = score_floor
    if floor is None:
        cfg = li_budget._net_config()
        floor = float(cfg.get("score_floor", 0) or 0)

    n_in = len(candidates)

    step1 = li_history.filter_unattempted(candidates)
    step2 = li_history.filter_cooldown_companies(step1)
    step3 = [t for t in step2
             if not li_quality.is_never_engage(t.get("headline", ""), t.get("name", ""), t.get("headline", ""))]
    step4 = li_scoring.rank_targets(step3, floor=floor)
    step5 = li_budget.filter_diversity(step4)

    final = []
    for t in step5:
        scored = {"score": t.get("_score", 0), "components": t.get("_score_components", {}),
                  "tier": t.get("_geo_tier", 0), "tz": t.get("_tz", "")}
        t = dict(t)
        t["_why"] = li_whythem.why_them(t, scored=scored, allow_llm=not fixture)
        final.append(t)

    return {
        "input_n": n_in,
        "after_dedupe": len(step1),
        "after_cooldown": len(step2),
        "after_never_engage": len(step3),
        "after_score_floor": len(step4),
        "after_diversity": len(step5),
        "queued_ready": final,
        "fixture_mode": fixture,
    }


# ---- FIXTURE candidates for dry-run verification (clearly marked, never real
# LinkedIn data — see 500-IDEAS-AGGREGATORS.md's "NO invented LinkedIn data
# ever: fixtures clearly marked" requirement) ----

FIXTURE_CANDIDATES = [
    {"name": "FIXTURE Founder Agency Owner", "headline": "Founder @ FIXTURE Digital Agency | White-label web for agencies",
     "location": "Austin, Texas Area", "url": "https://linkedin.com/in/fixture-strong-target-1",
     "mutuals_count": 9, "is_commenter": True, "last_active": "2026-07-02"},
    {"name": "FIXTURE Weak Target", "headline": "FIXTURE Student", "location": "",
     "url": "https://linkedin.com/in/fixture-weak-target-2", "mutuals_count": 0},
    {"name": "FIXTURE MLM Person", "headline": "FIXTURE Be your own boss, join my team, financial freedom!",
     "location": "Boise, Idaho", "url": "https://linkedin.com/in/fixture-mlm-3", "mutuals_count": 2},
    {"name": "FIXTURE Disguised MLM High Scorer", "headline": "FIXTURE Founder @ Digital Marketing Agency | "
     "Join my team, be your own boss, financial freedom awaits", "location": "Austin, Texas Area",
     "url": "https://linkedin.com/in/fixture-mlm-disguised-6", "mutuals_count": 10, "is_commenter": True,
     "last_active": "2026-07-02"},
    {"name": "FIXTURE Director Ops", "headline": "FIXTURE Director of Operations at FIXTURE Marketing Co",
     "location": "Chicago", "url": "https://linkedin.com/in/fixture-target-4", "mutuals_count": 4,
     "last_active": "2026-06-29"},
    {"name": "FIXTURE Second Person Same Company", "headline": "FIXTURE Owner at FIXTURE Marketing Co",
     "location": "Chicago", "url": "https://linkedin.com/in/fixture-target-5", "mutuals_count": 3,
     "last_active": "2026-06-30"},
]


if __name__ == "__main__":
    import json
    result = run_pipeline(FIXTURE_CANDIDATES, fixture=True)
    print(json.dumps({k: v for k, v in result.items() if k != "queued_ready"}, indent=2))
    print(f"\n{len(result['queued_ready'])} ready-to-queue target(s):")
    for t in result["queued_ready"]:
        print(f"  [{t.get('_score')}] {t.get('name')} -- {t.get('_why')}")
