#!/usr/bin/env python3
"""LinkedIn action budget + hours window + weekend pause + diversity guard —
A7, A8, A44, A54, A55, A56 from section A. Config knobs live in
store/config.json under "network" (see the _knobs_note there); this module is
the enforcement layer networking.approved_to_run() is composed with.

R2-33 (2026-07-13): networking.approved_to_run() now calls gate() on its own
output internally (see its docstring), so every caller — including the
operator brief's `python -c "...networking.approved_to_run()..."` snippet and
the executor skill (browser-agent/skills/linkedin-networking-execute.md),
neither of which compose this module explicitly — gets the hours/weekend/
budget guard for free. Before this fix gate() was an opt-in wrapper nothing
actually called, so weekend_pause etc. was silently bypassed:

    approved = networking.approved_to_run()   # already gated as of R2-33
    releasable = li_budget.gate(approved)     # still safe to call again — idempotent
                                               # (a second trim of an already-trimmed
                                               # list is a same-or-smaller no-op)

gate() itself is unchanged and still exposed here for anything that wants to
apply the guard to a candidate list that didn't come from approved_to_run().
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import LOCAL_TZ, now_iso  # noqa: E402
import networking  # noqa: E402
import planner  # noqa: E402
import li_history  # noqa: E402

DEFAULTS = {
    "daily_action_budget": 40,
    "hours_window": {"start": 8, "end": 18},
    "weekend_pause": True,
    "sourcing_runs_per_week": 2,
    "score_floor": 35,
    "max_per_company_week": 3,
    "max_per_niche_week": 10,
    "queue_depth_floor": 10,
    "source_mix_commenter_pct": 40,
}


def _net_config() -> dict:
    cfg = planner._config().get("network", {}) if hasattr(planner, "_config") else {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in cfg.items() if not k.startswith("_") and k in DEFAULTS})
    return out


# ---- A55: LinkedIn-hours window (his tz only) ----

def in_hours_window(now: datetime | None = None) -> bool:
    now = now or datetime.now(LOCAL_TZ)
    cfg = _net_config()
    window = cfg.get("hours_window") or DEFAULTS["hours_window"]
    start, end = int(window.get("start", 8)), int(window.get("end", 18))
    return start <= now.hour < end


# ---- A56: weekend pause default ----

def is_weekend(now: datetime | None = None) -> bool:
    now = now or datetime.now(LOCAL_TZ)
    return now.isoweekday() in (6, 7)  # Sat=6, Sun=7


def weekend_paused(now: datetime | None = None) -> bool:
    cfg = _net_config()
    if not cfg.get("weekend_pause", True):
        return False
    return is_weekend(now)


# ---- A54: daily action budget across ALL LinkedIn activity ----

def actions_today() -> int:
    """Total DONE actions today across every kind (connect+comment+like+reply),
    reusing networking.usage_today() so this stays in lockstep with the per-kind
    daily caps' own notion of 'today' (same _acted_date/local-tz logic)."""
    return sum(networking.usage_today().values())


def budget_remaining_today() -> int:
    cfg = _net_config()
    budget = int(cfg.get("daily_action_budget", 0) or 0)
    if budget <= 0:
        return 10 ** 6  # 0 = unlimited, same convention as _net_caps()
    return max(0, budget - actions_today())


# ---- A44: queue depth floor alert ----

def queue_depth() -> int:
    return sum(1 for x in networking.load_queue() if x.get("status") == "pending")


def check_queue_depth_alert(notify: bool = True) -> dict:
    """(A44) If pending targets fall below queue_depth_floor, push a notify via
    planner.notify (same mechanism owner_report.py already uses) saying 'source
    more.' Returns the check result regardless of notify so callers/tests can
    inspect it without triggering a real push."""
    cfg = _net_config()
    floor = int(cfg.get("queue_depth_floor", 0) or 0)
    depth = queue_depth()
    low = floor > 0 and depth < floor
    result = {"depth": depth, "floor": floor, "low": low}
    if low and notify:
        result["pushed"] = planner.notify(
            "LinkedIn queue low",
            f"{depth} pending targets left (floor {floor}). Run sourcing again.",
            tags="warning",
        )
    return result


# ---- A8: target diversity guard (max N per company, max M per niche / week) ----

def _week_start(now: datetime | None = None) -> str:
    """ISO date of the Monday that starts the current week, in his local tz."""
    now = now or datetime.now(LOCAL_TZ)
    monday = now.date() - timedelta(days=now.isoweekday() - 1)
    return monday.isoformat()


def _queued_this_week(kind: str = "connect", now: datetime | None = None) -> list[dict]:
    week_start = _week_start(now)
    return [x for x in networking.load_queue()
            if x.get("kind") == kind and (x.get("created") or "")[:10] >= week_start]


def company_counts_this_week() -> Counter:
    counts = Counter()
    for rec in _queued_this_week("connect"):
        company = li_history._company_from_target_text(rec.get("target", ""))
        if company:
            counts[company.lower()] += 1
    return counts


def diversity_ok_for_company(company: str, counts: Counter | None = None) -> bool:
    """(A8) True if queueing one MORE target from this company would stay at or
    under max_per_company_week. Company '' (unknown) always passes — diversity
    can't be enforced on data we don't have, and it must never block sourcing
    just because extraction failed."""
    if not company:
        return True
    cfg = _net_config()
    limit = int(cfg.get("max_per_company_week", 0) or 0)
    if limit <= 0:
        return True
    counts = counts if counts is not None else company_counts_this_week()
    return counts.get(company.lower(), 0) < limit


def filter_diversity(targets: list[dict]) -> list[dict]:
    """Apply the A8 company cap to a batch of sourcing candidates BEFORE they're
    queued, incrementally (so 5 targets from the same brand-new company in one
    batch don't all sail through — the 4th and 5th get dropped once the running
    count for that batch hits the limit)."""
    cfg = _net_config()
    limit = int(cfg.get("max_per_company_week", 0) or 0)
    if limit <= 0:
        return list(targets)
    counts = company_counts_this_week()
    out = []
    for t in targets:
        company = li_history._company_from_target_text(t.get("target", ""), t.get("headline", ""))
        if company and counts.get(company.lower(), 0) >= limit:
            continue
        if company:
            counts[company.lower()] += 1
        out.append(t)
    return out


# ---- composition: the full queue-release gate ----

def release_reason_blocked() -> str | None:
    """Returns a human reason string if NOTHING should release right now
    (hours window / weekend pause), else None. Budget and diversity are
    per-item concerns handled in gate(); this is the all-or-nothing time gate."""
    if weekend_paused():
        return "weekend pause active"
    if not in_hours_window():
        return "outside LinkedIn-hours window"
    return None


def gate(candidates: list[dict]) -> list[dict]:
    """The composed release gate: time window + weekend pause + all-activity
    daily budget, applied ON TOP OF whatever per-kind caps already trimmed
    `candidates` down to (e.g. networking.approved_to_run()'s output). Does
    NOT re-check per-kind caps (that's still networking.allowance()'s job) —
    this only adds the cross-cutting A54/A55/A56 layer.

    Never mutates the queue; purely a filter. Order-preserving (candidates
    should already be low-risk-first per networking.approved_to_run())."""
    if release_reason_blocked():
        return []
    remaining = budget_remaining_today()
    return candidates[:remaining]


if __name__ == "__main__":
    print(f"in_hours_window: {in_hours_window()}")
    print(f"weekend_paused: {weekend_paused()}")
    print(f"budget_remaining_today: {budget_remaining_today()}")
    print(f"queue_depth_alert: {check_queue_depth_alert(notify=False)}")
    print(f"release_reason_blocked: {release_reason_blocked()}")
