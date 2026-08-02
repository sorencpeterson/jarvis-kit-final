#!/usr/bin/env python3
"""Interview-prep packs. When a job hits status 'interview' (Gmail reply-detection flips it),
auto-build a one-pager: company research + likely questions + [OWNER]'s angle on each + a salary
anchor. Interviews are where the [SALARY_ANCHOR] actually converts, and there was zero machinery for them.

Uses a Sonnet claude -p with WebSearch for real company research. Safe: writes a local doc + push.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso, star_bank  # noqa: E402
import planner  # noqa: E402
import jobs  # noqa: E402

PREP = ROOT / "store" / "prep"

_SAFE_CHAR = re.compile(r"[A-Za-z0-9.-]")


def _safe(jid: str) -> str:
    """ONE injective, path-safe filesystem stem for a job id, shared identically with
    agents/interview_war_room.py (2026-07-13 fix, R2-25). Job ids are external/board-controlled
    (they can carry '/', ':', arbitrary bytes), so this used to just be `j["id"]` used RAW as a
    path component here: a '/' changed the path (surprise subdirectory) and '..' could escape
    PREP entirely -- a real path-traversal write, not just a theoretical one.
    Every character outside [A-Za-z0-9.-] is percent-style escaped as '_XX' (its UTF-8 bytes,
    2 lower-hex digits each) and a literal '_' is escaped as '__', so the mapping is reversible /
    injective: 'board:a/b' and 'board:a:b' can no longer collide on one file. Already-safe ids
    (no '_', letters/digits/dot/hyphen only) pass through unchanged.
    MUST match agents/interview_war_room._safe EXACTLY -- interview_war_room.assemble() looks up
    the prep pack this writes at store/prep/<_safe(id)>.md; if the two diverge, war_room silently
    stops finding prep packs interview_prep.py just built.
    NOT the same function as agents/interview_war_room's OLD `_safe` (dropped there): that one
    was `re.sub(r"[^A-Za-z0-9._-]+", "_", jid)[:140]`, shared byte-identically with
    agents/interview_postmortem.py's OWN copy (out of scope for this fix -- see
    interview_war_room.py's docstring for the flagged consequence).

    R1#9 (regression fix, post-17bf56c): the per-character escape above IS injective on its
    own, but the two steps that used to follow it -- truncating to 140 chars, then stripping
    every LEADING literal dot -- are each individually lossy and can collide two DIFFERENT
    ids onto the identical stem (e.g. '.foo' and 'foo' both end up 'foo'; two ids that only
    differ after the 140-char mark both truncate identically). A short hash of the FULL,
    untruncated original id is appended ONLY when one of those lossy steps actually fired (so
    an id that fits under 140 chars and has no leading dot -- the common case -- is completely
    unaffected, preserving both the plain-filename UX and parity with interview_postmortem's
    un-fixed, out-of-scope copy for a plain id; see tests/test_agent_hardening.py)."""
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


def _blurb() -> str:
    try:
        p = jobs.load_profile()
        return (f"{p.get('current_title', 'Full-stack marketer/operator')}, ~{p.get('years_experience', 6)} yrs. "
                "Strengths: SEO, WordPress/web, Google Ads + Analytics (certified), paid media, CRO, "
                f"marketing automation, ops/leadership. Remote. Salary target {p.get('salary_expectation', '[SALARY_ANCHOR]')}.")
    except Exception:  # noqa: BLE001
        return "Full-stack marketer and operator, ~6 years, remote."


def _salary_directive(job: dict) -> str:
    """Job-specific salary-anchor instruction for the prep-pack prompt. A posted comp band
    always wins over the flat default: telling the model to frame every role around one
    fixed figure put a [SALARY_ANCHOR] ask above the ceiling of a real $75k-$100k posting (2026-07-15
    Codex audit). Mirrors interview_war_room._anchor's rules; duplicated rather than shared
    since the two agents don't import each other for this."""
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
    cm = hi or lo
    if cm:
        top = max(1, round(cm * 0.95))
        if (job.get("comp_unit") or "year") == "hour":
            return f"anchor at ${top}/hour, near the top of their posted range, never above ${cm}/hour"
        return f"anchor at ${top:,}, near the top of their posted band, never above ${cm:,}"
    try:
        target = jobs.load_profile().get("salary_expectation") or "[SALARY_ANCHOR]"
    except Exception:  # noqa: BLE001
        target = "[SALARY_ANCHOR]"
    return f"anchor around {target} (no comp band is posted for this role)"


def _pack(job: dict):
    cli = planner._find_claude_cli()
    if not cli:
        return None
    sb = star_bank()
    prompt = (
        f"Build a tight one-page interview-prep pack (markdown, no fluff, NO em-dashes) for [OWNER]'s "
        f"interview for '{job.get('title')}' at {job.get('company')}. [OWNER]: {_blurb()}\n"
        + (f"His real STAR stories to weave into answers:\n{sb}\n" if sb else "")
        + f"Use WebSearch to research {job.get('company')}. Include:\n"
        f"1. What {job.get('company')} does, their market, any recent news (3-4 lines)\n"
        "2. Eight likely interview questions for this exact role\n"
        "3. For each question, one line on the angle [OWNER] should hit given his background\n"
        "4. Three sharp questions for [OWNER] to ask them\n"
        f"5. A salary anchor and one line on how to frame it: {_salary_directive(job)}\n"
        "Be specific to this company and role, not generic.")
    try:
        out = subprocess.run(
            ["perl", "-e", "alarm 235; exec @ARGV", cli, "-p", prompt,
             "--model", "claude-sonnet-4-6", "--allowedTools", "WebSearch"],
            capture_output=True, text=True, timeout=255, cwd="/tmp").stdout
        return (out or "").strip() or None
    except Exception:  # noqa: BLE001
        return None


def run():
    PREP.mkdir(exist_ok=True)
    todo = [j for j in jobs.load_jobs()
            if j.get("id") and j.get("status") == "interview"
            and not (PREP / (_safe(j["id"]) + ".md")).exists()]
    made = []
    for j in todo[:5]:
        pack = _pack(j)
        if pack:
            (PREP / (_safe(j["id"]) + ".md")).write_text(
                f"# Interview prep: {j.get('company')} — {j.get('title')}\n_{now_iso()[:10]}_\n\n" + pack)
            made.append(j.get("company"))
    if made:
        planner.feed_add("prep", "Interview prep ready: " + ", ".join(made))
        planner.notify("Interview prep ready", f"Packs built for {', '.join(made)}. Open the dashboard.",
                       tags="books")
    print("prep packs:", made or "none needed")
    return made


if __name__ == "__main__":
    run()
