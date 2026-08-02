#!/usr/bin/env python3
"""A1 (FABLE-BUILD-QUEUE Section 5, HIGH): the interview war room. One live
interview (CacheFly) is worth more than any staged proposal, and the materials
for it exist but live in four places. This assembles them into ONE doc.

WHAT: for every job at status=interview in store/jobs.jsonl, merge into
      store/war_room/<job_id>.md:
        - the job record itself (company, title, posted salary, ATS, applied date)
        - the salary anchor: band-aware off the job's own posted comp_min/comp_max/
          comp_unit when a band exists (anchor near ITS top, never above it), else
          store/application_profile.json's flat target, plus the negotiation rules:
          never give a number first if avoidable, anchor high when forced
        - a calendar conflict check for the next 7 days, via the running
          server's /api/gcal or a direct schedule/gcal_write import; if neither
          is reachable the doc SAYS the check was skipped instead of implying
          a clear calendar
        - the prep pack (store/prep/<id>.md) if interview_prep.py built one
        - his filled STAR bank (store_lib.star_bank)
        - a closing 5-line "walk in with" summary (Claude CLI, free on the Max
          plan, with a deterministic fallback so the section is never empty)
WHEN: any time after job_replies.py flips a job to interview (morning chain is
      the natural slot). Fires the push + feed line ONCE per job, guarded by
      store/war_room_state.json; --force rebuilds the doc, --dry-run prints the
      doc instead of writing anything.
RAILS: read-only against jobs.jsonl, prep packs, star_bank, the profile, and
      the calendar. Writes only store/war_room/<id>.md + its own state file.
      Nothing outward sends; the push is a local ntfy nudge to [OWNER] himself.
      Fresh install (no jobs.jsonl, no prep, template star bank) degrades to
      honest placeholder lines, never a crash.

Run:  .venv/bin/python agents/interview_war_room.py [--dry-run] [--force]
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import humanize, now_iso, secret, star_bank  # noqa: E402
import planner  # noqa: E402
import jobs  # noqa: E402


_SAFE_CHAR = re.compile(r"[A-Za-z0-9.-]")


def _safe(jid: str) -> str:
    """ONE injective, path-safe filesystem stem for a job id, shared identically with
    agents/interview_prep.py (2026-07-13 fix, R2-25). Job ids come from external ATS/board
    APIs and carry ':' and arbitrary chars; a raw '/' or '..' would escape OUT_DIR (path
    traversal write sink) -- that part hasn't changed. What changed: every character outside
    [A-Za-z0-9.-] is now percent-style escaped as '_XX' (its UTF-8 bytes, 2 lower-hex digits
    each) and a literal '_' is escaped as '__', so the mapping is reversible/injective.
    Already-safe ids (no '_', letters/digits/dot/hyphen only) pass through unchanged.

    SUPERSEDES the old lossy version (`re.sub(r"[^A-Za-z0-9._-]+", "_", jid)[:140]`), which
    collapsed every RUN of disallowed characters to a single '_' -- 'board:a/b' and
    'board:a:b' both became 'board_a_b' and silently overwrote one company's war-room doc.

    FLAG for whoever owns agents/interview_postmortem.py: its OWN `_safe()` is an independent
    copy of the OLD lossy version (its docstring says it must match this function EXACTLY,
    and tests/test_agent_hardening.py used to pin that). That file is OUT OF SCOPE for this
    fix (not one of the 5 files this pass may touch), so interview_postmortem._safe() now
    computes a DIFFERENT stem than this function for any id with a disallowed character --
    its _doc_age_days() age-check will look for the OLD filename this code no longer writes,
    so due_jobs() will stop finding war-room docs old enough to trigger a post-mortem. Give
    interview_postmortem.py this identical recipe to restore that.

    R1#9 (regression fix, post-17bf56c): the per-character escape above IS injective on its
    own, but the two steps that used to follow it -- truncating to 140 chars, then stripping
    every LEADING literal dot -- are each individually lossy and can collide two DIFFERENT
    ids onto the identical stem (e.g. '.foo' and 'foo' both end up 'foo'; two ids that only
    differ after the 140-char mark both truncate identically). A short hash of the FULL,
    untruncated original id is appended ONLY when one of those lossy steps actually fired (so
    an id that fits under 140 chars and has no leading dot -- the common case -- is completely
    unaffected, preserving both the plain-filename UX and parity with interview_postmortem's
    un-fixed, out-of-scope copy for a plain id; see tests/test_agent_hardening.py). MUST stay
    byte-identical to agents/interview_prep.py's copy of this same fix."""
    out = []
    for ch in (jid or ""):
        if ch != "_" and _SAFE_CHAR.match(ch):
            out.append(ch)
        elif ch == "_":
            out.append("__")
        else:
            out.append("".join(f"_{b:02x}" for b in ch.encode("utf-8", "surrogatepass")))
    full = "".join(out)
    truncated = full[:140]
    stripped = truncated.lstrip(".")
    lossy = len(full) > 140 or stripped != truncated
    s = stripped or "job"
    if lossy:
        s = f"{s}-{hashlib.sha256((jid or '').encode('utf-8', 'surrogatepass')).hexdigest()[:10]}"
    return s


# ---- tunables ----
SALARY_ANCHOR = ""  # no default anchor: set salary_expectation in your profile  # fallback only: applies when no comp band is posted (profile.salary_expectation wins first)
CAL_DAYS = 7                     # calendar conflict window
SUMMARY_TIMEOUT = 120            # seconds for the walk-in-with LLM call
PROMPT_DOC_CHARS = 6000          # how much of the doc the summary prompt sees
ANCHOR_PCT = 0.95                # how close to the posted max a banded anchor sits

PREP_DIR = ROOT / "store" / "prep"
OUT_DIR = ROOT / "store" / "war_room"
STATE = ROOT / "store" / "war_room_state.json"
SERVER_BASE = "http://127.0.0.1:8765"


def _comp_band(job: dict) -> tuple[int | None, int | None, str]:
    """(lo, hi, unit) off the job's own posted comp_min/comp_max/comp_unit, kept in their
    NATIVE unit (never cross-converted between year and hour, see agents/jobs.py's _comp()).
    (None, None, unit) when no band was posted at all. lo/hi get swapped back into order for
    a reversed/malformed scraped record, same guard as jobs.salary_target."""
    lo, hi = job.get("comp_min"), job.get("comp_max")
    try:
        lo = int(lo) if lo else None
    except (TypeError, ValueError):
        lo = None
    try:
        hi = int(hi) if hi else None
    except (TypeError, ValueError):
        hi = None
    if lo and hi and lo > hi:
        lo, hi = hi, lo
    return lo, hi, (job.get("comp_unit") or "year")


def _anchor(job: dict | None = None) -> str:
    """Band-aware salary anchor string ("$NNN,NNN/year" or "$NN/hour"). A posted comp band
    always wins over the flat profile/fallback figure: asserting one fixed number as if it
    sat inside every posted band is what steered a sub-[SALARY_ANCHOR] role's negotiation above the
    posting's own ceiling (2026-07-15 Codex audit). Anchors near the TOP of a posted band,
    never above it; stays in native $/hr for an hourly posting, never annualized. Falls back
    to the profile (or the flat default) only when the job posts no band at all."""
    lo, hi, unit = _comp_band(job or {})
    cm = hi or lo
    if cm:
        top = max(1, round(cm * ANCHOR_PCT))
        return f"${top:,}/hour" if unit == "hour" else f"${top:,}/year"
    try:
        return jobs.load_profile().get("salary_expectation") or SALARY_ANCHOR
    except Exception:  # noqa: BLE001
        return SALARY_ANCHOR


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    """Atomic: a mid-write kill must never leave {} (which would re-LLM the doc and
    re-push 'War room ready' for every interview). tmp + os.replace, like siblings."""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    os.replace(tmp, STATE)


def _calendar_lines(days: int = CAL_DAYS) -> tuple[list[str], bool]:
    """Events in the next `days` days for the conflict check. Tries the running
    server's /api/gcal first (cached, zero extra deps), then a direct
    schedule/gcal_write.read_events import. Returns (lines, checked):
    checked=False means the doc must say the check was skipped."""
    events = None
    try:
        import urllib.request
        req = urllib.request.Request(SERVER_BASE + "/api/gcal",
                                     headers={"X-Brain-Token": secret("brain_token")})
        with urllib.request.urlopen(req, timeout=10) as r:
            payload = json.loads(r.read())
        events = payload.get("events")
        if payload.get("error") and not events:
            events = None
    except Exception:  # noqa: BLE001 — server down is a normal state, fall through
        events = None
    if events is None:
        try:
            sys.path.insert(0, str(ROOT / "schedule"))
            import gcal_write
            events = gcal_write.read_events(days_back=0, days_fwd=days)
        except BaseException:  # noqa: BLE001 — gcal_write raises SystemExit(2) on missing deps/creds
            return (["Calendar check SKIPPED: server /api/gcal unreachable and gcal_write "
                     "not importable. Check the next 7 days for conflicts by hand."], False)
    today = datetime.now().astimezone().date()
    horizon = today + timedelta(days=days)
    lines = []
    for e in events or []:
        d = str(e.get("date") or "")[:10]
        try:
            dd = datetime.fromisoformat(d).date()
        except (ValueError, TypeError):
            continue
        if today <= dd <= horizon:
            lines.append(f"- {e.get('when', d)}  {e.get('text', '(busy)')}")
    if not lines:
        lines = [f"No calendar events in the next {days} days. Clear runway to schedule or prep."]
    return (lines, True)


def _salary_lines(job: dict) -> list[str]:
    anchor = _anchor(job)
    anchor_num = anchor.split("/")[0]  # "$142,500", "$52", or the flat "[SALARY_ANCHOR]" fallback
    lo, hi, unit = _comp_band(job)
    cm = hi or lo
    if cm:
        per = "/hour" if unit == "hour" else ""
        if lo and hi and lo != hi:
            posted = job.get("salary") or f"${lo:,}-{hi:,}{per}"
        else:
            posted = job.get("salary") or f"${cm:,}{per}"
        return [
            f"- They posted {posted}. Anchor near the top of THEIR band: {anchor}. "
            "Never state a number above their own posted max.",
            "- Rule 1: never give a number first if you can avoid it. Deflect once: "
            '"Fit matters most to me here. What range did you budget for the role?"',
            f"- Rule 2: forced to name one, anchor high inside their band: \"I am targeting "
            f"{anchor_num}{per}, and I am flexible on the full package for the right role.\"",
        ]
    return [
        f"- Anchor: {anchor}. That is the floor, not the midpoint.",
        "- Rule 1: never give a number first if you can avoid it. Deflect once: "
        '"Fit matters most to me here. What range did you budget for the role?"',
        f"- Rule 2: forced to name one, anchor high: \"I am targeting {anchor_num}, "
        "and I am flexible on the full package for the right role.\"",
    ]


WALK_IN_PROMPT = """Read this interview war-room doc and write EXACTLY 5 numbered lines [OWNER] walks into the %s interview with:
1. the one-sentence positioning
2. the single best STAR story to lead with (name it)
3. the salary line (%s anchor, never name a number first)
4. the sharpest question to ask them
5. the close
Plain text, exactly 5 lines, "1." through "5.", under 25 words each, direct and punchy, no em-dashes, no preamble, no commentary.

DOC:
%s"""


def _walk_in(company: str, title: str, job: dict, doc: str) -> str:
    """5-line summary via the CLI, deterministic fallback if the model is offline
    or breaks format. The section must never ship empty."""
    anchor_num = _anchor(job).split("/")[0]
    out = planner._cli(WALK_IN_PROMPT % (company, anchor_num, doc[:PROMPT_DOC_CHARS]),
                       timeout=SUMMARY_TIMEOUT)
    lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    if len(lines) >= 5:
        return "\n".join(lines[:5])
    return "\n".join([
        f"1. Positioning: operator who sets the strategy and personally ships it, built for {title} at {company}.",
        "2. Lead story: the [PRIOR_RESULT] agency rebuild, systems plus team, churn turned into 2-year retention.",
        f"3. Money: anchor {anchor_num}. Never name a number first, deflect to their range once.",
        "4. Ask them: what does success look like at 90 days, and what is the biggest gap today?",
        "5. Close: I want this role. What is the next step, and when do you decide?",
    ])


def assemble(job: dict, cal_lines: list[str], cal_checked: bool) -> tuple[str, bool]:
    """Returns (doc, prep_ok). prep_ok is False when store/prep/<id>.md is missing, unreadable,
    or blank -- run() uses it to gate the war-room 'built' stamp so a job whose prep pack
    hasn't landed yet gets retried instead of frozen with the placeholder forever."""
    jid = job.get("id", "")
    company = job.get("company") or "?"
    title = job.get("title") or "?"
    # _safe(), not raw jid (2026-07-13 fix, R2-25): interview_prep.py now writes prep packs
    # keyed by _safe(id); a raw jid here used to be merely "consistent" with prep's OLD raw-id
    # write (both unsafe), and reading a raw id back out is itself a traversal-read risk.
    prep_path = PREP_DIR / (_safe(jid) + ".md")
    try:
        prep = prep_path.read_text().strip() if prep_path.exists() else ""
    except OSError:
        prep = ""
    sb = star_bank()
    parts = [
        f"# War room: {company}, {title}",
        f"_assembled {now_iso()[:16]}_",
        "",
        "## The job",
        f"- Company: {company}",
        f"- Title: {title}",
        f"- Posted salary: {job.get('salary') or 'not posted'}",
        f"- ATS/source: {job.get('source') or '?'}",
        f"- Seniority: {job.get('seniority') or '?'}",
        f"- Applied: {(job.get('applied_at') or '?')[:10]}",
        f"- Listing: {job.get('apply_url') or '?'}",
        "",
        "## Money: the salary anchor",
        *_salary_lines(job),
        "",
        f"## Calendar, next {CAL_DAYS} days" + ("" if cal_checked else " (SKIPPED)"),
        *cal_lines,
        "",
        "## Prep pack",
        prep or f"No prep pack found at store/prep/{_safe(jid)}.md. Run agents/interview_prep.py first.",
        "",
        "## STAR stories",
        sb or "STAR bank is still the template. Fill store/star_bank.md before the interview.",
        "",
    ]
    summary = _walk_in(company, title, job, "\n".join(parts))
    parts += ["## Walk in with", summary, ""]
    return humanize("\n".join(parts)), bool(prep)


def run(dry_run: bool = False, force: bool = False) -> list[str]:
    interviews = [j for j in jobs.load_jobs() if j.get("status") == "interview"]
    if not interviews:
        print("war_room: no interview-stage jobs")
        return []
    state = _load_state()
    built = []
    for j in interviews:
        jid = j.get("id")
        company = j.get("company") or jid or "?"
        if not jid:
            continue
        # gate on "built" specifically, not mere presence in state (2026-07-15 Codex audit): the
        # old check skipped a job forever the moment it was assembled once, even with an empty
        # prep pack, so a prep pack that landed later never made it into the doc.
        if jid in state and state[jid].get("built") and not (force or dry_run):
            print(f"war_room: {company} already assembled "
                  f"({str(state[jid].get('built', '?'))[:10]}), skipping. --force rebuilds.")
            continue
        cal_lines, cal_checked = _calendar_lines()
        doc, prep_ok = assemble(j, cal_lines, cal_checked)
        out_path = OUT_DIR / (_safe(jid) + ".md")
        if dry_run:
            print(f"\n=== DRY RUN: would write {out_path} ===\n")
            print(doc)
            print(f"=== DRY RUN: would push 'War room ready: {company}' + feed line "
                  f"(first assembly only) ===")
            continue
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(doc)
        first_notify = not state.get(jid, {}).get("notified")
        # notify BEFORE persisting the notified-state (2026-07-13 hunt): if we stamp first and the
        # push then fails or the process dies, the next run sees jid notified and skips it forever,
        # so [OWNER] never learns his live interview's war-room doc is ready. Stamp only once the
        # push lands; the doc is already written, so a retry just re-notifies (idempotent).
        sent = True
        if first_notify:
            sent = planner.notify(f"War room ready: {company}",
                           f"{j.get('title') or '?'} at {company}. Prep, STAR bank, the "
                           f"{_anchor(j).split('/')[0]} anchor and the calendar check in one doc: "
                           f"store/war_room/{_safe(jid)}.md",
                           tags="briefcase,books")
            try:
                planner.feed_add("jobs", f"War room assembled: {company} ({j.get('title') or '?'})")
            except Exception:  # noqa: BLE001 — feed hiccup must not fail the build
                pass
        if not sent:
            print(f"war_room: {company} assembled but push failed, not stamped (retries next run)")
            continue
        entry = dict(state.get(jid) or {})
        if first_notify:
            entry["notified"] = now_iso()
        # only stamp "built" once the prep pack itself is real (2026-07-15 Codex audit): an
        # empty/missing pack must never freeze the job as done, or a pack that lands later
        # never gets merged in. Clearing it when prep_ok is False also self-heals a job that
        # was wrongly stamped before this fix, the moment it gets rebuilt.
        if prep_ok:
            entry["built"] = now_iso()
        else:
            entry.pop("built", None)
        state[jid] = entry
        _save_state(state)
        built.append(company)
        if prep_ok:
            print(f"war_room: assembled {out_path}")
        else:
            print(f"war_room: assembled {out_path} (no prep pack yet, retries until interview_prep.py fills it)")
    return built


def main() -> int:
    dry = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    if dry:
        run(dry_run=True, force=force)
        return 0
    from runlog import track
    with track("interview_war_room"):
        run(force=force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
