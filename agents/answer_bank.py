#!/usr/bin/env python3
"""Answer bank (#67): mine the apply-operator transcripts for recurring screener
questions and the truthful answers already given, so every future application answers
identically (and cheaper — injected verbatim instead of re-reasoned each time)."""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402

LOG = ROOT / "agents" / "launch.log"
BANK = ROOT / "store" / "answer_bank.json"

# These answers get replayed VERBATIM into every future apply-operator prompt (a
# tool-wielding browser agent). The transcripts they're mined from can contain text
# influenced by a malicious job page, so an unfiltered extraction is a prompt-injection
# persistence path (2026-07-07 audit S3). A blocklist was trivially evadable (unicode,
# zero-width chars, keyword-free steering — red-team F2), so this now INVERTS the gate:
# NFKC-normalize + strip zero-width, then ALLOW only benign screener-answer characters and
# reject anything else. A real screener Q/A is short plain text; anything with URLs, angle
# brackets, backslashes, or exotic characters is not a legitimate answer and is dropped.
import unicodedata as _ud

# plain screener text: letters/digits/space + ordinary punctuation. No :/ (URLs), no <>
# (tags), no {}/[] (templating), no backslashes/pipes/backticks (shell/markup).
_ALLOWED_RE = re.compile(r"^[\w\s.,!?'\"@#%&()+=/$-]*$")
# a few instruction-shaped phrases still worth an explicit reject even within allowed chars
_INJECT_RE = re.compile(
    r"(ignore (the |all |previous)|disregard|instead,? (navigate|go|submit|send)|"
    r"system prompt|you are now|new instructions?|\bcurl\b|\bwget\b|www\.|http|"
    r"\bapi[_-]?key\b|password|bank account|routing number|\bwire\b|ssn|social security)", re.I)


def _norm(s: str) -> str:
    """NFKC-fold (collapses full-width/look-alike unicode) + strip zero-width joiners so a
    disguised payload can't slip past the character allowlist."""
    s = _ud.normalize("NFKC", str(s))
    return "".join(ch for ch in s if ch not in "​‌‍⁠﻿")


def _clean_qa(qa: list) -> list:
    out = []
    for x in qa:
        q, a = _norm(x.get("q", "")), _norm(x.get("a", ""))
        if not q or not a or len(a) > 320 or len(q) > 200:
            continue
        # allowlist: reject any Q/A with characters outside benign screener text
        if not (_ALLOWED_RE.match(q) and _ALLOWED_RE.match(a)):
            continue
        if _INJECT_RE.search(q) or _INJECT_RE.search(a):
            continue
        out.append({"q": q, "a": a})
    return out


def run():
    if not LOG.exists():
        print("answer bank: no launch.log yet")
        return 0
    bank = {"ts": "", "qa": []}
    try:
        bank = json.loads(BANK.read_text())
    except (OSError, json.JSONDecodeError):
        pass
    import os
    if bank.get("ts") and os.path.getmtime(LOG) < os.path.getmtime(BANK):
        print(f"answer bank: current ({len(bank.get('qa', []))} answers, log unchanged)")
        return 0
    tail = LOG.read_text(errors="replace")[-120_000:]
    out = planner._cli(
        "Below are job-application operator transcripts. Extract RECURRING screener "
        "questions and the answers given for [OWNER] (work authorization, sponsorship, "
        "salary, notice period, remote, years of experience, portfolio/links, referral "
        "source, address basics). Answers must be exactly as truthful as what was "
        "written — never stronger. Return ONLY JSON {\"qa\":[{\"q\":\"...\",\"a\":\"...\"}]} "
        "with at most 20 pairs, each answer under 200 chars.\n\nTRANSCRIPTS:\n" + tail,
        timeout=150, feature="plan") or ""
    j = planner._extract_json(out) or {}
    qa = _clean_qa([x for x in (j.get("qa") or []) if x.get("q") and x.get("a")])[:20]
    if not qa:
        print("answer bank: nothing extracted")
        return 0
    seen = {}
    for x in (bank.get("qa") or []) + qa:
        seen[" ".join(x["q"].lower().split())[:60]] = x
    BANK.write_text(json.dumps({"ts": now_iso(), "qa": list(seen.values())[:30]}, indent=1))
    planner.feed_add("agent", f"Answer bank updated: {len(seen)} standard answers")
    print(f"answer bank: {len(seen)} answers -> store/answer_bank.json")
    return 0


# The universal screeners nearly every ATS asks. Deliberately absent: salary (a per-job
# directive from the posting's own band overrides the bank) and EEO/veteran/disability
# (those belong in the application profile's eeo block, answered or declined there).
_SEED_QS = (
    ("Are you legally authorized to work in the United States?", "Yes"),
    ("Will you now or in the future require visa sponsorship?", "No"),
    ("Are you willing to work remotely?", "Yes"),
    ("Are you willing to relocate?", "No"),
    ("What is your notice period / when can you start?", "Two weeks from offer"),
    ("Have you ever worked for this company before?", "No"),
    ("How did you hear about this position?", "Job board"),
    ("Are you at least 18 years of age?", "Yes"),
    ("Do you have a bachelor's degree?", ""),
)


def seed(auto: bool = False) -> int:
    """Seed the bank with the universal screeners in one minute: no LLM, no transcripts.
    A fresh install ships an EMPTY bank and nothing warns; every application then
    regenerates every screener answer from scratch, the single largest recurring
    per-application LLM cost after page navigation (field report 2026-08-12, C2 --
    a sibling install ran at zero pairs for weeks with no symptom but the bill).
    Interactive by default: Enter keeps the default, '-' skips, anything else is
    stored verbatim. NEVER store an answer that isn't true for you; skipping beats
    guessing, the operator handles unknowns per-form."""
    bank = {"ts": "", "qa": []}
    try:
        bank = json.loads(BANK.read_text())
    except (OSError, json.JSONDecodeError):
        pass
    pairs = []
    if not auto:
        print("Seeding the screener answer bank. Enter = keep the default, '-' = skip.\n")
    for q, dflt in _SEED_QS:
        if auto:
            a = dflt
        else:
            raw = input(f"  {q}\n    [{dflt or 'skip'}] > ").strip()
            a = dflt if raw == "" else ("" if raw == "-" else raw)
        if a:
            pairs.append({"q": q, "a": a})
    pairs = _clean_qa(pairs)
    if not pairs:
        print("answer bank: nothing to seed")
        return 0
    seen = {}
    for x in (bank.get("qa") or []) + pairs:
        seen[" ".join(x["q"].lower().split())[:60]] = x
    BANK.parent.mkdir(parents=True, exist_ok=True)
    BANK.write_text(json.dumps({"ts": now_iso(), "qa": list(seen.values())[:30]}, indent=1))
    print(f"answer bank: {len(seen)} answers -> store/answer_bank.json")
    return 0


if __name__ == "__main__":
    if "--seed" in sys.argv:
        raise SystemExit(seed(auto="--auto" in sys.argv))
    raise SystemExit(run())
