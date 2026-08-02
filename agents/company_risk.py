#!/usr/bin/env python3
"""A6 (FABLE-BUILD-QUEUE Section 5, MED): pre-apply company risk research.
Approved jobs go to the apply operator with zero context on the employer;
this stamps each one with a cheap risk read BEFORE the application burns a
one-per-employer slot.

WHAT: for every APPROVED job in store/jobs.jsonl not yet assessed, computes
      deterministic red flags from data already on disk:
        - ghost-job signals: the same company+title posted more than once
          across the full queue history (reposted), or a posting older than
          GHOST_AGE_DAYS days (stale listing, likely evergreen/ghost)
        - comp mismatch: posted comp_max under the salary anchor floor
          (application_profile salary_expectation, fallback ANCHOR_FLOOR),
          or no posted comp at all
        - layoff words in any local store line mentioning the company
      then ONE planner._cli call per company (capped at CLI_CAP jobs/run,
      note reused across same-company jobs in a batch) folds the job fields
      plus local mentions into a 2-3 sentence risk note.
      LOCAL DATA ONLY: this version makes NO web calls. The "listing text"
      is the job record itself (title/salary/seniority/posted; full listing
      descriptions are not stored in jobs.jsonl), plus grep hits over
      MENTION_STORES. A web-research version is a later upgrade.
WHEN: daily, morning chain, after jobs.py/job_ats_watch.py have staged and
      approved. Each run drains up to CLI_CAP unassessed jobs; the rest wait
      for the next run.
RAILS: read-only against jobs.jsonl and every mention store. Only write is
      store/company_risk.jsonl (append under _flock, one line per job,
      already-assessed job_ids are skipped forever). No pushes, no sends,
      nothing outward. LLM is planner._cli (Max plan, metered), capped, and
      skipped entirely on --dry-run. Fresh install (no jobs.jsonl) prints
      and exits 0.

Run:  .venv/bin/python agents/company_risk.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import _flock, humanize, now_iso  # noqa: E402
import planner  # noqa: E402
import jobs  # noqa: E402

# ---- tunables ----
OUT = ROOT / "store" / "company_risk.jsonl"
CLI_CAP = 5            # max LLM-noted jobs per run; the queue drains daily
GHOST_AGE_DAYS = 30    # posting older than this smells evergreen/ghost
ANCHOR_FLOOR = 135000  # fallback only; application_profile salary_expectation wins
NOTE_TIMEOUT = 90      # seconds per LLM note
MENTION_CAP = 6        # max store snippets fed to the note per company
LAYOFF_WORDS = ("layoff", "laid off", "restructuring", "hiring freeze",
                "downsizing", "riffed", "rif ")
# Stores grepped for company mentions (raw line match, no JSON parse needed).
# store/mail_signals.jsonl does not exist yet (mail_signals.py writes per-kind
# suggestion files); it stays first on the list so it is picked up the day it appears.
MENTION_STORES = [
    ROOT / "store" / "mail_signals.jsonl",
    ROOT / "store" / "mail_triage.jsonl",
    ROOT / "store" / "mail_thread_summaries.jsonl",
    ROOT / "store" / "feed.jsonl",
    ROOT / "store" / "insights.jsonl",
]

RISK_PROMPT = """Pre-apply company research from LOCAL data only (you have no web access; do not invent facts).

Job [OWNER] is about to apply to:
%s

Deterministic risk flags already computed: %s
Local store lines mentioning the company (may be empty):
%s

Write a 2-3 sentence pre-apply risk note in plain text: what the flags mean for THIS application and the one thing to verify in a first screen. Direct, no fluff, no em-dashes, no preamble, no markdown."""


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _anchor_floor() -> int:
    """Salary floor for the comp-mismatch flag: digits of the profile's
    salary_expectation ("[SALARY_ANCHOR]" -> 135000), constant fallback."""
    try:
        exp = jobs.load_profile().get("salary_expectation") or ""
        digits = re.sub(r"[^0-9]", "", exp.split("/")[0])
        if digits and int(digits) >= 40000:
            return int(digits)
    except Exception:  # noqa: BLE001 - profile surprises never block a risk pass
        pass
    return ANCHOR_FLOOR


def _mentions(company: str) -> list[str]:
    """Raw store lines mentioning the company (word-boundary, case-insensitive),
    capped at MENTION_CAP snippets. Names under 3 chars are skipped (noise)."""
    name = (company or "").strip()
    if len(name) < 3:
        return []
    pat = re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE)
    hits: list[str] = []
    for store in MENTION_STORES:
        if not store.exists():
            continue
        try:
            text = store.read_text()
        except OSError:
            continue
        for line in text.splitlines():
            if pat.search(line):
                hits.append(line.strip()[:220])
                if len(hits) >= MENTION_CAP:
                    return hits
    return hits


def _flags(job: dict, ckey_counts: Counter, mentions: list[str], floor: int) -> list[str]:
    """Deterministic red flags. Order is stable so tests and diffs stay readable."""
    flags = []
    key = jobs._ckey(job.get("company", ""), job.get("title", ""))
    if ckey_counts.get(key, 0) >= 2:
        flags.append("reposted")
    age = jobs._age_days(job.get("posted"))
    if age is not None and age > GHOST_AGE_DAYS:
        flags.append(f"stale_{GHOST_AGE_DAYS}d")
    cm = job.get("comp_max")
    try:
        cm = int(cm) if cm is not None else None
    except (TypeError, ValueError):
        cm = None
    if cm is None:
        flags.append("no_posted_comp")
    elif cm < floor:
        flags.append("comp_below_anchor")
    blob = " ".join(mentions).lower()
    if any(w in blob for w in LAYOFF_WORDS):
        flags.append("layoff_mentions")
    return flags


def _fallback_note(job: dict, flags: list[str]) -> str:
    if not flags:
        return (f"No local red flags on {job.get('company') or 'this company'}. "
                "Still verify team size and why the role is open in the first screen.")
    return (f"Flags: {', '.join(flags)}. Ask why the role is open and confirm the "
            "budget band in the first screen before investing more time.")


def _note(job: dict, flags: list[str], mentions: list[str]) -> str:
    listing = "\n".join(
        f"- {k}: {job.get(k)}" for k in
        ("company", "title", "salary", "comp_max", "seniority", "posted", "source")
        if job.get(k) not in (None, ""))
    out = planner._cli(
        RISK_PROMPT % (listing, ", ".join(flags) or "none",
                       "\n".join(mentions) or "(no local mentions)"),
        timeout=NOTE_TIMEOUT)
    out = (out or "").strip()
    if not out:
        return _fallback_note(job, flags)
    return humanize(out)[:600]


def _append(rec: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with _flock(OUT), OUT.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def run(dry_run: bool = False) -> int:
    """Assess up to CLI_CAP unassessed approved jobs. Returns count assessed."""
    all_jobs = jobs.load_jobs()
    if not all_jobs:
        print("company_risk: no jobs.jsonl yet, nothing to assess")
        return 0
    approved = [j for j in all_jobs if j.get("status") == "approved" and j.get("id")]
    if not approved:
        print("company_risk: no approved jobs in the queue")
        return 0
    assessed = {r.get("job_id") for r in _read_jsonl(OUT)}
    todo = [j for j in approved if j["id"] not in assessed]
    if not todo:
        print(f"company_risk: all {len(approved)} approved job(s) already assessed")
        return 0

    ckey_counts = Counter(jobs._ckey(j.get("company", ""), j.get("title", ""))
                          for j in all_jobs)
    floor = _anchor_floor()
    batch = todo[:CLI_CAP]
    note_cache: dict[str, str] = {}  # one LLM call per company per run
    n = 0
    for j in batch:
        company = j.get("company") or "?"
        mentions = _mentions(company)
        flags = _flags(j, ckey_counts, mentions, floor)
        if dry_run:
            print(f"[dry-run] {company}: {j.get('title')}")
            print(f"  flags: {', '.join(flags) or 'none'}; "
                  f"mentions: {len(mentions)}; LLM note skipped, nothing written")
            continue
        if company not in note_cache:
            note_cache[company] = _note(j, flags, mentions)
        rec = {"job_id": j["id"], "company": company, "risk_flags": flags,
               "note": note_cache[company], "ts": now_iso()}
        _append(rec)
        n += 1
        print(f"company_risk: {company} [{', '.join(flags) or 'clean'}] -> {rec['note'][:90]}")
    left = len(todo) - len(batch)
    if left > 0:
        print(f"company_risk: {left} unassessed approved job(s) wait for the next run "
              f"(cap {CLI_CAP}/run)")
    if dry_run:
        print(f"[dry-run] would assess {len(batch)} job(s), wrote nothing")
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Pre-apply company risk flags (local data only)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print flags for the next batch; no LLM call, no write")
    args = ap.parse_args()
    if args.dry_run:
        run(dry_run=True)
        return 0
    from runlog import track
    with track("company_risk"):
        run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
