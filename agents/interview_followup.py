#!/usr/bin/env python3
"""A5 (FABLE-BUILD-QUEUE Section 5, HIGH): follow-up-after-interview timer.
agents/thankyou.py covers day 1 (the thank-you draft, checked 2026-07-07: it
only drafts the note and files a todo, it never nudges later). Nothing covers
the silence that follows. This does days 5 and 10.

WHAT: for every job at status=interview in store/jobs.jsonl, work out how long
      ago it flipped to interview and push a nudge when it goes silent:
        day 5:  "5 days since the <company> interview and no word. Send the
                check-in note."
        day 10: a firmer push + a feed line (decide: one firm nudge or write
                it off).
      Each stage fires ONCE per job (store/interview_followup_state.json); a
      job that is already past day 10 on first run gets ONLY the day-10 nudge,
      never a stale day-5 push behind it.
WHEN: daily (morning chain). Cheap: pure local reads, no LLM, no network
      beyond the ntfy push itself.
RAILS: read-only against jobs.jsonl, thankyou_drafts.jsonl, and store/prep/.
      Writes only its own state file. Nothing outward sends; pushes go to
      [OWNER]'s phone only. --dry-run prints what would fire and touches nothing.
      Fresh install (no stores at all) prints "no interview-stage jobs" and exits 0.

WHICH TIMESTAMP (checked against the real store 2026-07-07): jobs.set_status()
stamps NO updated-at on a status flip; the appended record only changes
status/reason. The live interview record (CacheFly) carries just created,
applied_at, and posted. So the flip time is resolved in this order, first hit
wins, and the chosen source is printed next to every decision:
  1. interview_at / stage_updated on the record itself (none exist today;
     future-proofs against a later stamper without a rebuild here)
  2. the job's thankyou_drafts.jsonl ts: thankyou.py drafts within hours of the
     flip (same cron chain that flips the status), the closest live proxy
  3. store/prep/<id>.md mtime: interview_prep.py builds the pack minutes after
     the flip
  4. applied_at, then created: worst case the nudge errs a few days EARLY,
     which beats an interview going silent forever.
KNOWN LIMIT: job_replies.py only moves status forward (interview outranks
replied), so a recruiter reply AFTER the flip does not change the record and a
nudge can fire even though they wrote back. It is one push to ignore, and this
agent never sends anything outward itself.

Run:  .venv/bin/python agents/interview_followup.py [--dry-run]
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402
import jobs  # noqa: E402

# ---- tunables ----
DAY5 = 5     # first "send the check-in note" nudge
DAY10 = 10   # firmer nag + feed line

STATE = ROOT / "store" / "interview_followup_state.json"
DRAFTS = ROOT / "store" / "thankyou_drafts.jsonl"
PREP_DIR = ROOT / "store" / "prep"


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


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    """Atomic: a mid-write kill must never leave {} (which would re-fire day5/day10
    nudges for every interview). tmp + os.replace, like siblings."""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    os.replace(tmp, STATE)


def _flip_ts(job: dict) -> tuple[str, str]:
    """Best available (timestamp, source_label) for when this job flipped to
    interview. Priority order documented in the module docstring."""
    for k in ("interview_at", "stage_updated"):
        if job.get(k):
            return str(job[k]), k
    jid = job.get("id")
    for r in _read_jsonl(DRAFTS):
        if r.get("job_id") == jid and r.get("ts"):
            return str(r["ts"]), "thankyou_draft"
    p = PREP_DIR / (str(jid) + ".md")
    if p.exists():
        try:
            ts = datetime.fromtimestamp(p.stat().st_mtime).astimezone()
            return ts.isoformat(timespec="seconds"), "prep_mtime"
        except (OSError, OverflowError, ValueError):
            pass
    for k in ("applied_at", "created"):
        if job.get(k):
            return str(job[k]), k
    return "", "none"


def _parse_ts(ts: str) -> datetime | None:
    """Aware datetime from an ISO string, or None on any parse failure."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if not dt.tzinfo:
            dt = dt.astimezone()
        return dt
    except (ValueError, TypeError):
        return None


def _age_days(ts: str) -> float:
    """Days since an ISO timestamp; 0.0 on any parse failure (one bad record
    must never crash the run or fire a bogus nudge)."""
    dt = _parse_ts(ts)
    if dt is None:
        return 0.0
    now = datetime.now(dt.tzinfo)
    return max(0.0, (now - dt).total_seconds() / 86400.0)


# Anchors that are trustworthy flip proxies: they are stamped AT or within hours of
# the interview flip, so their age is real and must NOT be clamped. Only the weak
# fallbacks (applied_at/created, which can be many days older than the interview) get
# clamped to first_seen, so a stale application can't manufacture an instant day-10.
STRONG_ANCHORS = ("interview_at", "stage_updated", "thankyou_draft", "prep_mtime")


def _silence_anchor(ts: str, src: str, first_seen: str) -> str:
    """The effective anchor for the silence clock. For a STRONG anchor (a real flip
    proxy) the anchor is trusted as-is. For a weak fallback (applied_at/created) the
    clock starts no earlier than the moment WE first saw the job at interview: the
    LATER of ts and first_seen. On any parse trouble the original ts is used (fail
    toward the documented early-not-late behavior)."""
    if src in STRONG_ANCHORS:
        return ts
    a, b = _parse_ts(ts), _parse_ts(first_seen)
    if a is None:
        return first_seen or ts
    if b is None:
        return ts
    return ts if a >= b else first_seen


def run(dry_run: bool = False) -> list[str]:
    interviews = [j for j in jobs.load_jobs() if j.get("status") == "interview"]
    if not interviews:
        print("interview_followup: no interview-stage jobs")
        return []
    state = _load_state()
    fired = []
    for j in interviews:
        jid = j.get("id")
        if not jid:
            continue
        company = j.get("company") or jid
        ts, src = _flip_ts(j)
        if not ts:
            print(f"interview_followup: {company} has no usable timestamp, skipping")
            continue
        st = state.get(jid, {})
        # First sight at interview: stamp a stable first_seen and persist it now, so a
        # stale applied_at/created fallback can't fire day-10 the morning after the
        # interview. The silence clock is measured from max(flip proxy, first_seen).
        first_seen = st.get("first_seen")
        if not first_seen:
            first_seen = now_iso()
            if not dry_run:
                st["first_seen"] = first_seen
                state[jid] = st
                _save_state(state)
        anchor_ts = _silence_anchor(ts, src, first_seen)
        age = _age_days(anchor_ts)
        stage = None
        if age >= DAY10 and "day10" not in st:
            stage = "day10"
        elif age >= DAY5 and "day5" not in st and "day10" not in st:
            stage = "day5"
        if not stage:
            done = "both nudges sent" if "day10" in st else \
                (f"next nudge at day {DAY10}" if "day5" in st else f"first nudge at day {DAY5}")
            print(f"interview_followup: {company} at day {age:.1f} (anchor: {src}), {done}")
            continue
        if dry_run:
            print(f"[dry-run] would push {stage} nudge for {company} "
                  f"(day {age:.1f}, anchor: {src})")
            continue
        if stage == "day5":
            sent = planner.notify(f"Interview follow-up: {company}",
                           f"5 days since the {company} interview and no word. "
                           "Send the check-in note.",
                           tags="briefcase,hourglass_flowing_sand")
        else:
            sent = planner.notify(f"Interview gone quiet: {company}",
                           f"10 days of silence since the {company} interview. "
                           "Send one firm nudge today or write it off. Decide.",
                           tags="briefcase,rotating_light")
            # feed line is independent of the push: ntfy down must never mean a
            # day-10 silence goes unrecorded (job_replies H2 lesson).
            try:
                planner.feed_add("jobs", f"Interview follow-up overdue: {company}, "
                                 f"day {int(age)} with no word")
            except Exception:  # noqa: BLE001
                pass
        # gate the stamp on the push landing (2026-07-13 hunt): a failed notify() must not stamp
        # this stage as sent, or the nudge is silently eaten and never retried. The day-10 feed
        # line above is the independent record; the phone push retries next run.
        if not sent:
            print(f"interview_followup: {company} {stage} push failed, not stamped (retries next run)")
            continue
        st[stage] = now_iso()
        state[jid] = st
        _save_state(state)
        fired.append(f"{company}:{stage}")
        print(f"interview_followup: pushed {stage} nudge for {company} (day {age:.1f}, anchor: {src})")
    return fired


def main() -> int:
    dry = "--dry-run" in sys.argv
    if dry:
        run(dry_run=True)
        return 0
    from runlog import track
    with track("interview_followup"):
        run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
