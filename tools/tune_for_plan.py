#!/usr/bin/env python3
"""Tune the job-apply chain for your Claude plan.

    python3 tools/tune_for_plan.py --pro     # $20 plan: cheapest settings that still work
    python3 tools/tune_for_plan.py --max     # Max plan: throughput over economy
    python3 tools/tune_for_plan.py --show    # what is set right now

WHERE THE TOKENS ACTUALLY GO
Sourcing jobs is free: agents/jobs.py fetches boards over plain HTTP with zero
LLM calls. Hundreds of postings cost nothing.

Applying is the expensive half. Each application spawns a `claude -p` session
with Playwright browser tools, and the cost is dominated by the agentic loop:
the model snapshots the page, decides, acts, snapshots again. A long multi-page
ATS form can be dozens of round trips with page content in context each time.

So the levers that matter are, in order:
  1. the MODEL doing the form filling
  2. how many jobs one session handles before its context is fat
  3. how many sessions run at once (burst rate against a 5-hour window)
  4. not spending a session at all on forms that will wall anyway
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "store" / "config.json"

PROFILES = {
    "pro": {
        "label": "Claude Pro ($20/mo)",
        "job_apply_model": "claude-haiku-4-5-20251001",
        "job_apply_concurrency": 1,
        "job_apply_batch": 5,
        "job_daily_apply_cap": 5,
        "resume_tailor_limit": 7,
        "daily_token_budget": 400000,
        "morning_profile": "lite",
        "why": [
            "Haiku fills forms instead of Sonnet. Forms are structured work with the "
            "answers already supplied, which is what Haiku is good at.",
            "One operator at a time, 5 jobs per session: shorter context per session "
            "and a gentler burst against the 5-hour window.",
            "The ATS-friction router skips forms that have walled you before, so a "
            "session is never spent on a CAPTCHA you cannot pass anyway.",
            "Resume tailoring is capped near the apply cap. It is one Sonnet call per "
            "job, so tailoring 60 when you apply to 5 spent ~55 calls on jobs you were "
            "never going to submit that day. The PDFs cache, so nothing is lost.",
        ],
    },
    "max": {
        "label": "Claude Max",
        "job_apply_model": "claude-sonnet-4-6",
        "job_apply_concurrency": 3,
        "job_apply_batch": 30,
        "job_daily_apply_cap": 10,
        "resume_tailor_limit": 12,
        "daily_token_budget": 0,
        "morning_profile": "full",
        "why": ["Throughput over economy: Sonnet, three parallel operators, big batches."],
    },
}

KEYS = ("job_apply_model", "job_apply_concurrency", "job_apply_batch",
        "job_daily_apply_cap", "resume_tailor_limit", "daily_token_budget",
        "morning_profile")


def load() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def show() -> int:
    cfg = load()
    if not cfg:
        print("\n  No store/config.json yet. Run setup.py first.\n")
        return 1
    print("\n  Current apply settings\n  " + "-" * 44)
    for k in KEYS:
        v = cfg.get(k, "(unset)")
        print(f"    {k:<26} {v}")
    print()
    m = str(cfg.get("job_apply_model", ""))
    if "sonnet" in m and cfg.get("job_apply_concurrency", 3) > 1:
        print("  Tuned for throughput. On a $20 plan: python3 tools/tune_for_plan.py --pro\n")
    elif "haiku" in m:
        print("  Tuned for economy.\n")
    return 0


def apply_profile(name: str) -> int:
    p = PROFILES[name]
    cfg = load()
    if not cfg:
        print("\n  No store/config.json yet. Run setup.py first.\n")
        return 1

    before = {k: cfg.get(k) for k in KEYS}
    for k in KEYS:
        cfg[k] = p[k]

    tmp = CONFIG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=1))
    tmp.replace(CONFIG)
    try:
        os.chmod(CONFIG, 0o600)
    except OSError:
        pass

    print(f"\n  Tuned for {p['label']}\n  " + "-" * 44)
    for k in KEYS:
        was, now = before.get(k), cfg[k]
        mark = " " if was == now else "*"
        print(f"  {mark} {k:<26} {was}  ->  {now}")
    print("\n  Why:")
    for line in p["why"]:
        print(f"    - {line}")
    print("\n  Sourcing jobs was already free and is unchanged.")
    print("  Restart the server if it is running.\n")
    return 0


def main() -> int:
    args = set(sys.argv[1:])
    if "--show" in args or not args:
        return show()
    for name in PROFILES:
        if f"--{name}" in args:
            return apply_profile(name)
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
