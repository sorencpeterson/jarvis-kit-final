#!/usr/bin/env python3
"""A4 (FABLE-BUILD-QUEUE Section 5, MED): the take-home assignment helper.
A take-home buried in a reply email is a deadline nobody is tracking; this
spots the mention, stages the todo, and lays out the working scaffold so
the assignment starts organized instead of in a panic.

WHAT: scans for take-home / assignment / assessment mentions in:
        - MAIL_STORES raw lines (store/mail_signals.jsonl is the spec-named
          primary and does not exist yet, mail_signals.py writes per-kind
          suggestion files today; mail_triage.jsonl and
          mail_thread_summaries.jsonl are the live sources), matched to a
          live-stage job by company name (word-boundary, case-insensitive)
        - the job record's own mail-derived text fields (reason, the last
          mail classification, and rejection_snippet)
      On a hit: stages ONE todo (store/todos.jsonl via append_todo under
      store_lib's _flock, dedup by source_ref "takehome_<job_id>"), writes a
      scaffold at store/takehomes/<job_id>.md (requirements checklist,
      timebox suggestion, submission checklist; never overwritten), pushes
      one self-notification, and records the job in store/takehome_state.json
      so detection dedups FOREVER (a re-mention next week does not re-stage).
WHEN: daily, morning chain, after mail_brain.py and job_replies.py have
      refreshed the mail stores and job statuses.
RAILS: read-only against jobs.jsonl and every mail store. Writes only the
      todo line, its own scaffold + state file. Push is a local ntfy nudge
      to [OWNER] himself; nothing outward sends. No LLM. Fresh install (no
      jobs, no mail stores) prints and exits 0.

Run:  .venv/bin/python agents/takehome_helper.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import append_todo, existing_source_refs, new_id, now_iso  # noqa: E402
import planner  # noqa: E402
import jobs  # noqa: E402

# ---- tunables ----
STATE = ROOT / "store" / "takehome_state.json"
OUT_DIR = ROOT / "store" / "takehomes"
TODOS = ROOT / "store" / "todos.jsonl"
LIVE_STATUSES = ("applied", "confirmed", "replied", "interview")
KEYWORDS = ("take-home", "take home", "takehome", "work sample", "skills test",
            "homework", "assignment", "case study exercise", "practical exercise",
            "assessment link", "complete the assessment", "coding challenge")
TIMEBOX_HOURS = 4  # the standing rule: cap the free work, confirm scope if bigger
MAIL_STORES = [
    ROOT / "store" / "mail_signals.jsonl",
    ROOT / "store" / "mail_triage.jsonl",
    ROOT / "store" / "mail_thread_summaries.jsonl",
]


def _safe(jid: str) -> str:
    """Filesystem-safe file stem for a job id (ids carry ':' and arbitrary chars)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", jid)[:140]


def _load_state() -> dict:
    try:
        data = json.loads(STATE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True))
    tmp.replace(STATE)


def _kw_hit(text: str) -> str | None:
    low = (text or "").lower()
    for kw in KEYWORDS:
        if kw in low:
            return kw
    return None


def _mail_lines_with_keywords() -> list[str]:
    """Raw lines from the mail stores that mention any take-home keyword."""
    hits = []
    for store in MAIL_STORES:
        if not store.exists():
            continue
        try:
            text = store.read_text()
        except OSError:
            continue
        for line in text.splitlines():
            if _kw_hit(line):
                hits.append(line)
    return hits


def detect() -> dict[str, dict]:
    """job_id -> {company, evidence, keyword} for every live-stage job with a
    take-home mention in mail or on its own record."""
    live = [j for j in jobs.load_jobs()
            if j.get("status") in LIVE_STATUSES and j.get("id")]
    if not live:
        return {}
    found: dict[str, dict] = {}
    # lane 1: the job record's own mail-derived text
    for j in live:
        own = " ".join(str(j.get(k) or "") for k in ("reason", "rejection_snippet"))
        kw = _kw_hit(own)
        if kw:
            found[j["id"]] = {"company": j.get("company") or "?",
                              "evidence": own.strip()[:200], "keyword": kw}
    # lane 2: mail store lines matched to a job by company name
    kw_lines = _mail_lines_with_keywords()
    if kw_lines:
        for j in live:
            if j["id"] in found:
                continue
            name = (j.get("company") or "").strip()
            if len(name) < 3:
                continue
            pat = re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE)
            for line in kw_lines:
                if pat.search(line):
                    found[j["id"]] = {"company": name,
                                      "evidence": line.strip()[:200],
                                      "keyword": _kw_hit(line) or "?"}
                    break
    return found


def scaffold(job_id: str, info: dict) -> str:
    company = info.get("company") or "?"
    return "\n".join([
        f"# Take-home: {company}",
        f"_detected {now_iso()[:16]} via \"{info.get('keyword')}\"; "
        f"evidence: {info.get('evidence', '')[:160]}_",
        "",
        "## Requirements checklist",
        "- [ ] Re-read the brief twice; list EVERY deliverable they name",
        "- [ ] Deadline written here: ____ (and blocked on the calendar)",
        "- [ ] Format they want (doc/deck/repo/loom): ____",
        "- [ ] Who reviews it and what they care about: ____",
        "- [ ] Anything ambiguous: ask ONE clarifying email today, not day 3",
        "",
        "## Timebox",
        f"- Cap it at {TIMEBOX_HOURS} focused hours. If the brief honestly needs more, "
        "say so and confirm scope before spending it: free work has a ceiling.",
        "- Schedule the block now; a take-home without a calendar slot slips.",
        "",
        "## Submission checklist",
        "- [ ] Every named deliverable present, in their format",
        "- [ ] One paragraph up top: the approach and the tradeoffs (operators read this first)",
        "- [ ] Numbers checked twice; claims match the [PRIOR_RESULT] facts if referenced",
        "- [ ] Sent BEFORE the deadline with a 2-line note, not at 11:59pm",
        "- [ ] Follow-up queued for day 3 if silent",
        "",
    ])


def run(dry_run: bool = False) -> list[str]:
    found = detect()
    if not found:
        print("takehome_helper: no take-home mentions on live-stage jobs")
        return []
    state = _load_state()
    refs = existing_source_refs(TODOS)
    staged = []
    for jid, info in found.items():
        if jid in state:
            continue  # dedup forever
        company = info["company"]
        ref = f"takehome_{jid}"
        md_path = OUT_DIR / (_safe(jid) + ".md")
        text = f"Take-home from {company}: scope it, timebox it, ship it early"
        if dry_run:
            print(f"[dry-run] would stage todo '{text}' + scaffold {md_path}")
            staged.append(company)
            continue
        if ref not in refs:
            rec = {"id": new_id(ref + text), "text": text, "status": "inbox",
                   "created": now_iso(), "source": "takehome_helper", "source_ref": ref,
                   "project": None, "priority": 1, "scheduled_time": None,
                   "duration_min": None, "gcal_event_id": None, "notes": None}
            append_todo(rec, TODOS)  # append_todo takes _flock itself
        if not md_path.exists():  # never clobber a scaffold he already filled
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            md_path.write_text(scaffold(jid, info))
        state[jid] = {"found": now_iso(), "company": company,
                      "keyword": info.get("keyword")}
        _save_state(state)
        staged.append(company)
        print(f"takehome_helper: staged {company} -> {md_path}")

    # ONE summary push per run, never N (a single digest line naming several
    # live-stage companies must not machine-gun the phone). Mirrors open_pulse's
    # single-notification pattern: the todos/scaffolds are the per-item surface.
    if staged and not dry_run:
        if len(staged) == 1:
            title = f"Take-home detected: {staged[0]}"
            body = (f"Scaffold staged. Timebox {TIMEBOX_HOURS}h, confirm the deadline "
                    "today. See the todo + store/takehomes/.")
        else:
            title = f"{len(staged)} take-home mentions detected"
            body = (f"{', '.join(staged)}. Scaffolds + todos staged. Timebox {TIMEBOX_HOURS}h "
                    "each, confirm each deadline today.")
        planner.notify(title, body, tags="hourglass,briefcase")
    if not staged:
        print(f"takehome_helper: {len(found)} mention(s), all already handled (dedup forever)")
    return staged


def main() -> int:
    ap = argparse.ArgumentParser(description="Take-home detection: todo + working scaffold")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would stage; no write, no push")
    args = ap.parse_args()
    if args.dry_run:
        run(dry_run=True)
        return 0
    from runlog import track
    with track("takehome_helper"):
        run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
