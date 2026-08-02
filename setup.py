#!/usr/bin/env python3
"""First-run setup. Asks who you are, writes config/owner.json, scaffolds store/.

    python3 setup.py

Safe to re-run: it shows current values as defaults and never overwrites data
files that already have content in them.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_DIR = ROOT / "config"
OWNER = CONFIG_DIR / "owner.json"
STORE = ROOT / "store"

# (key, question, default, required)
FIELDS = [
    ("name", "Your full name", "", True),
    ("email", "Your email", "", True),
    ("site", "Your website (blank if none)", "", False),
    ("company", "Your company name (blank if none)", "", False),
    ("linkedin", "LinkedIn URL or handle", "", False),
    ("city", "City, State", "", False),
    ("what_you_do", "One line: who you help and with what", "", False),
    ("icp", "Who you want to reach (your ideal client)", "", False),
    ("offer", "Your core offer and price (blank if job-hunting only)", "", False),
    ("voice", "How you write",
     "Direct and punchy, no fluff. Short sentences. Contractions. No em-dashes.", False),
]

JOB_FIELDS = [
    ("current_title", "Current/most recent job title", "", False),
    ("years_experience", "Years of experience", "", False),
    ("work_authorization", "Work authorization", "US citizen", False),
    ("salary_expectation", "Salary expectation (or 'Open')", "Open", False),
    ("availability", "Availability to start", "2 weeks", False),
]

# store files the system expects to exist. Empty is fine, it fills them as it runs.
EMPTY_JSONL = [
    "jobs.jsonl", "network.jsonl", "ledger.jsonl", "replies.jsonl", "proposals.jsonl",
    "runs.jsonl", "usage.jsonl", "metrics.jsonl", "todos.jsonl", "insights.jsonl",
    "mail_triage.jsonl", "mail_drafts.jsonl", "suppress.jsonl", "cold_pipeline.jsonl",
    "warm_dispo.jsonl", "objections.jsonl", "li_engagers.jsonl", "li_accepted.jsonl",
]
EMPTY_JSON = {
    "attention.json": {},
    "convo_states.json": {},
    "answer_bank.json": {"qa": []},
    "li_history.json": {},
}


def ask(q: str, default: str = "", required: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            v = input(f"  {q}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nsetup cancelled")
            sys.exit(1)
        v = v or default
        if v or not required:
            return v
        print("    (required)")


def main() -> int:
    print("\n  JARVIS setup\n  " + "-" * 40)
    existing = {}
    if OWNER.exists():
        try:
            existing = json.loads(OWNER.read_text())
            print("  Found an existing config. Enter to keep each value.\n")
        except json.JSONDecodeError:
            pass

    cfg: dict = {}
    print("\n  WHO YOU ARE\n")
    for key, q, dflt, req in FIELDS:
        cfg[key] = ask(q, str(existing.get(key, dflt)), req)

    cfg["handle"] = (cfg.get("linkedin", "").rstrip("/").split("/")[-1]
                     or cfg["name"].lower().replace(" ", ""))

    print("\n  JOB SEARCH (enter to skip if you are not job hunting)\n")
    for key, q, dflt, req in JOB_FIELDS:
        cfg[key] = ask(q, str(existing.get(key, dflt)), req)

    cfg["home"] = str(Path.home())
    cfg["app_root"] = str(ROOT)

    CONFIG_DIR.mkdir(exist_ok=True)
    tmp = OWNER.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    tmp.replace(OWNER)
    os.chmod(OWNER, 0o600)
    print(f"\n  wrote {OWNER.relative_to(ROOT)}")

    # scaffold store/ without ever clobbering real data
    STORE.mkdir(exist_ok=True)
    made = 0
    for fn in EMPTY_JSONL:
        p = STORE / fn
        if not p.exists():
            p.write_text(""); made += 1
    for fn, blank in EMPTY_JSON.items():
        p = STORE / fn
        if not p.exists():
            p.write_text(json.dumps(blank, indent=1)); made += 1
    cfgp = STORE / "config.json"
    if not cfgp.exists():
        cfgp.write_text(json.dumps({
            "_note": "Runtime knobs. Everything outward-facing ships OFF.",
            "job_auto": False,
            "job_daily_apply_cap": 10,
            "cold_daily_enroll": 0,
            "models": {
                "default": "claude-haiku-4-5-20251001",
                "interpret": "claude-haiku-4-5-20251001",
                "plan": "claude-haiku-4-5-20251001",
                "tone_screen": "claude-haiku-4-5-20251001",
                "brief": "claude-haiku-4-5-20251001",
                "chat_fast": "claude-haiku-4-5-20251001",
                "content": "claude-sonnet-4-6",
                "networking": "claude-sonnet-4-6",
                "reply": "claude-sonnet-4-6",
                "proposal": "claude-sonnet-4-6",
                "quality_grade": "claude-sonnet-4-6",
                "tailor": "claude-sonnet-4-6",
                "jarvis": "claude-opus-4-8",
            },
            "_models_note": "Which model each feature routes to. Cheap models for "
                            "internal steps, stronger ones for anything a human reads.",
            "network": {"daily": {"connect": 10, "comment": 6, "like": 20, "dm": 5},
                        "weekly": {"connect": 100},
                        "daily_action_budget": 40,
                        "hours_window": {"start": 8, "end": 18},
                        "weekend_pause": True},
        }, indent=1)); made += 1

    # application profile for the job side, seeded from what we just collected
    ap = STORE / "application_profile.json"
    if not ap.exists():
        parts = cfg["name"].split()
        ap.write_text(json.dumps({
            "full_name": cfg["name"],
            "first_name": parts[0] if parts else "",
            "last_name": parts[-1] if len(parts) > 1 else "",
            "email": cfg["email"], "phone": "",
            "city_state": cfg.get("city", ""), "country": "United States",
            "linkedin": cfg.get("linkedin", ""), "portfolio": cfg.get("site", ""),
            "current_title": cfg.get("current_title", ""),
            "years_experience": cfg.get("years_experience", ""),
            "work_authorization": cfg.get("work_authorization", "US citizen"),
            "requires_sponsorship": "No",
            "salary_expectation": cfg.get("salary_expectation", "Open"),
            "availability": cfg.get("availability", "2 weeks"),
            "default_cover": "", "education": "",
        }, indent=1)); made += 1
    print(f"  scaffolded {made} file(s) in store/")

    # voice file drives every content/outreach prompt
    voice = ROOT / "content" / "voice.md"
    voice.parent.mkdir(exist_ok=True)
    if not voice.exists() or "[OWNER]" in voice.read_text():
        voice.write_text(
            f"# Voice\n\n{cfg.get('voice', '')}\n\n"
            f"## What I do\n{cfg.get('what_you_do', '')}\n\n"
            f"## Who I help\n{cfg.get('icp', '')}\n\n"
            f"## Offer\n{cfg.get('offer', '')}\n\n"
            "## Rules\n- No em-dashes\n- Short sentences, contractions\n"
            "- First person, opinionated\n- No corporate filler\n")
        print("  wrote content/voice.md")

    # copy the default agent cadence table on first run
    cad_src, cad_dst = ROOT / "store-templates" / "agent_cadences.json", STORE / "agent_cadences.json"
    if cad_src.exists() and not cad_dst.exists():
        cad_dst.write_text(cad_src.read_text())
        print("  copied default agent cadences")
    # the resume tailor needs a base template to substitute into
    r_src, r_dst = ROOT / "store-templates" / "resume-draft.html", STORE / "resume-draft.html"
    if r_src.exists() and not r_dst.exists():
        r_dst.write_text(r_src.read_text())
        print("  copied resume template -> store/resume-draft.html (edit it with your real resume)")

    print("\n  " + "-" * 40)
    print(f"  Done. You are set up as: {cfg['name']}\n")
    print("  Next:")
    print("    1. python3 connect.py      hook up your accounts (all optional)")
    print("    2. Put your real resume in store/resume-draft.html (job search only)")
    print("    3. cp -r skills/yours/* ~/.claude/skills/   the fastest win")
    print("    4. Read README.md for what to turn on first")
    print("\n  Nothing sends anything until you explicitly enable it.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
