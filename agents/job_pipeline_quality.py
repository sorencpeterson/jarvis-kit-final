#!/usr/bin/env python3
"""D228, D238, D239, D240, D241, D242, D243: pipeline-quality layer.

Every check/function here is either (a) a read-only report, or (b) an
ADDITIVE guard callable alongside jobs.approved_to_apply()'s existing
_blocked() — same non-negotiable rule as job_fit_signals.py: nothing here
weakens the postmortem guards (employer dedupe, 8+ YOE, fit floor 62)
already in jobs.py.

D228 stale-approved refresh: approved jobs sitting unapplied >7 days get a
HEAD re-check before the apply operator wastes a cycle on a listing that's
gone dead in the meantime (mirrors jobs.preflight_drop()'s existing 404/410
HEAD-check pattern, just gated by age instead of running on every batch).

D239 velocity governor: a NEW, SEPARATE knob (job_velocity_cap, default 15)
from the existing job_daily_apply_cap (currently live at 200 in
store/config.json). This is deliberate — the postmortem's explicit lesson
was "quality-first, max 15/day," but store/config.json's job_daily_apply_cap
is a LIVE setting on an active system, and silently overriding or lowering
a live outward-facing cap without [OWNER]'s go-ahead would cross the safety
rail on account-setting changes. So: job_velocity_cap ships here as an
opt-in-by-default (defaults to 15, matching the postmortem) SEPARATE ceiling
a caller can layer alongside the existing cap — `min(existing_cap,
velocity_cap)` when both are meant to apply — without this build silently
rewriting job_daily_apply_cap out from under him. He can raise/lower
job_velocity_cap in config.json like any other knob once he sees it.

D240 time-of-day optimization: scores whether "now" (server-local time) is a
reasonable submission window for a company's likely HQ business hours, using
ONLY signal already on the job record (nothing new to fetch) — best-effort
by design, defaults to "fine to send" when there's no timezone signal at all
so this never blocks in the (currently universal) no-timezone-data case.

D241 error taxonomy: classifies WHY a job ended up 'skipped', grouping the
existing free-text reason strings (captcha/closed/login/wizard/missing_info/
unqualified from server.py's operator contract, plus every reason string
this D-lane's own new guards write) into stable categories for routing/
reporting, without changing what gets written to jobs.jsonl.

D242 "opened it" tracking: a lightweight companion store
(store/manual_opened.json) recording which needs_manual() jobs [OWNER] has
already clicked into, so a future dashboard pass can show "seen" vs "not yet
opened" without touching jobs.jsonl's own status machine at all (same
side-channel-store discipline agents/thankyou.py and agents/ghost_check.py
already use for their own companion state).

D243 manual-apply companion: prefilled-answers file per needs_manual() job,
written to store/manual_prefill/<job_id>.json, so a human doing the actual
clicking has the exact answers ready beside the form link (profile fields +
answer_bank Q&A + this job's own cover_override if job_cover.py already
computed one).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso, sign_secret  # noqa: E402
import ats_friction  # noqa: E402
import jobs  # noqa: E402
import resume_tailor  # noqa: E402

STALE_DAYS = 7
OPENED_STORE = ROOT / "store" / "manual_opened.json"
PREFILL_DIR = ROOT / "store" / "manual_prefill"
NOTIFY_STATE = ROOT / "store" / "manual_notify_state.json"
MANUAL_NOTIFY_AT = 5      # push once the finish-by-hand pile reaches this many
MANUAL_REARM_AT = 3       # ...and don't push again until it drops back below this (hysteresis)
MANUAL_COOLDOWN_H = 12    # ...with at least this many hours between pushes regardless


# ---- D228 stale-approved refresh ----
def stale_approved(days: int = STALE_DAYS) -> list[dict]:
    """Approved-but-unapplied jobs older than `days`. Age is measured from
    `created` (when it entered the queue), the same timestamp field every
    other age check in jobs.py already reads (jobs._age_days reads `posted`,
    a DIFFERENT field for a different purpose — posting freshness vs queue
    dwell time; this is intentionally the latter)."""
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    out = []
    for j in jobs.load_jobs():
        if j.get("status") != "approved":
            continue
        ts = j.get("created")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            continue
        if dt < cutoff:
            out.append(j)
    return out


def refresh_stale(days: int = STALE_DAYS, limit: int = 50) -> dict:
    """HEAD-check every stale-approved job (same 404/410-only-drops discipline
    as jobs.preflight_drop — a network hiccup or any non-dead-listing status
    KEEPS the job, only a confirmed-dead listing gets skipped). Returns a
    tally so a caller/report can see what happened without re-deriving it."""
    stale = stale_approved(days)[:limit]
    if not stale:
        return {"checked": 0, "dead": 0, "kept": 0}
    alive = jobs.preflight_drop(stale)
    dead = len(stale) - len(alive)
    return {"checked": len(stale), "dead": dead, "kept": len(alive)}


# ---- D238 repost-variance dedupe (broader than D254's location-specific case) ----
def _title_core(title: str) -> str:
    """Strip common req-variance noise (roman/arabic numeral suffixes, '(Multiple
    Openings)', trailing punctuation) that makes the SAME role read as different
    titles across a repost. Deliberately lighter-touch than job_fit_signals'
    location-token strip — this targets req-numbering/count noise, not geography."""
    t = (title or "").lower()
    t = re.sub(r"\(multiple\s+openings?\)", "", t)
    t = re.sub(r"\b(ii|iii|iv|v|2|3|4|5)\b\s*$", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    return t


def repost_variance_key(company: str, title: str) -> str:
    c = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    t = re.sub(r"\s+", "", _title_core(title))
    return c + "|" + t


def repost_variance_dupe_reason(job: dict, all_jobs: list[dict]) -> str:
    """Same-req-reposted detection: flags a job whose (company, noise-stripped
    title) matches an already approved/submitted job under a DIFFERENT id.
    Distinct from job_fit_signals.multi_location_dupe_reason (that strips
    LOCATION tokens for the Sidetrade-style geo-repost case; this strips
    NUMBERING/count noise for the same-req-different-posting-id case)."""
    key = repost_variance_key(job.get("company"), job.get("title"))
    if not key.strip("|"):
        return ""
    for other in all_jobs:
        if other.get("id") == job.get("id"):
            continue
        if other.get("status") not in ("approved", "applied", "confirmed", "interview", "replied"):
            continue
        if repost_variance_key(other.get("company"), other.get("title")) == key:
            return f"repost_variance (variant of {other.get('id')})"
    return ""


# ---- D239 velocity governor ----
def velocity_cap() -> int:
    try:
        return int(json.loads((ROOT / "store" / "config.json").read_text()).get("job_velocity_cap", 15))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return 15


def velocity_remaining() -> int:
    """Remaining slots under the SEPARATE, quality-first velocity cap today —
    a caller wanting the postmortem's stricter number should use THIS instead
    of (or in addition to, via min()) jobs._apply_cap()'s existing (currently
    much higher, live-configured) value. See module docstring for why this
    doesn't touch job_daily_apply_cap directly."""
    return max(0, velocity_cap() - jobs.applied_today())


# ---- D240 time-of-day optimization (best-effort, defaults to "fine") ----
# US timezone offset guesses purely from region/state strings already present
# on board_listing records (WWR's <region>, etc.) or absent entirely on
# hiring.cafe records — this is intentionally coarse since no real HQ
# timezone data exists on any job record today.
_TZ_HINT = {
    "california": -8, "washington": -8, "oregon": -8, "nevada": -8,
    "new york": -5, "massachusetts": -5, "florida": -5, "georgia": -5,
    "texas": -6, "illinois": -6, "colorado": -7, "arizona": -7,
}


def _guess_hq_offset(job: dict) -> int | None:
    text = " ".join([job.get("company") or "", job.get("query") or ""]).lower()
    for k, off in _TZ_HINT.items():
        if k in text:
            return off
    return None


def business_hours_ok(job: dict, now: datetime | None = None) -> bool:
    """True if it's a reasonable business-hours window (9am-5pm) at the
    best-guess HQ timezone, OR True (never blocks) when no timezone signal
    exists at all — which is the overwhelming majority case today given job
    records carry no real HQ-location field. This is a scoring input, not a
    gate: callers should use it to REORDER a batch (business-hours jobs
    first), never to skip a job outright, since a false negative here has
    real cost (a good job silently deprioritized off a guess) while the
    upside (marginally better response odds) is unproven."""
    off = _guess_hq_offset(job)
    if off is None:
        return True
    now = now or datetime.now().astimezone()
    from datetime import timezone as _tz
    hq_now = now.astimezone(_tz(timedelta(hours=off)))
    return 9 <= hq_now.hour < 17 and hq_now.weekday() < 5


# ---- D241 error taxonomy ----
# Groups the FREE-TEXT reason strings jobs.jsonl already accumulates (from
# server.py's operator contract's fixed vocabulary, PLUS every new reason
# string this D-lane's own guards write) into stable categories for
# reporting/routing. Read-only classification of EXISTING strings — writes
# nothing new to jobs.jsonl, changes no behavior.
_TAXONOMY = [
    ("captcha", re.compile(r"\bcaptcha\b", re.I)),
    ("login_wall", re.compile(r"\b(login|account\s+required)\b", re.I)),
    ("broken_form", re.compile(r"\b(wizard|missing_info)\b", re.I)),
    ("timeout", re.compile(r"\btimeout\b", re.I)),
    ("dead_listing", re.compile(r"\b(closed|expired|dead_listing)\b", re.I)),
    ("dup_company", re.compile(r"\bdup_company\b", re.I)),
    ("yoe_gate", re.compile(r"\byoe_gate\b", re.I)),
    ("fit_floor", re.compile(r"\bfit_floor\b", re.I)),
    ("keyword_mismatch", re.compile(r"\bkeyword_mismatch\b", re.I)),
    ("salary_gate", re.compile(r"\bsalary_gate\b", re.I)),
    ("blocklisted", re.compile(r"\bblocklisted\b", re.I)),
    ("repost_dupe", re.compile(r"\b(multi_location_dupe|repost_variance)\b", re.I)),
    ("unqualified", re.compile(r"\bunqualified\b", re.I)),
]


def classify_skip_reason(reason: str) -> str:
    r = reason or ""
    for label, pat in _TAXONOMY:
        if pat.search(r):
            return label
    return "other" if r else "unspecified"


def error_taxonomy_report() -> dict:
    from collections import Counter
    c = Counter(classify_skip_reason(j.get("reason"))
               for j in jobs.load_jobs() if j.get("status") == "skipped")
    return dict(c)


# ---- D242 "opened it" tracking (side-channel, never touches jobs.jsonl) ----
def _load_opened() -> set[str]:
    try:
        return set(json.loads(OPENED_STORE.read_text()))
    except (OSError, json.JSONDecodeError):
        return set()


def mark_opened(job_id: str) -> None:
    opened = _load_opened()
    opened.add(job_id)
    OPENED_STORE.parent.mkdir(parents=True, exist_ok=True)
    OPENED_STORE.write_text(json.dumps(sorted(opened)))


def needs_manual_with_opened() -> list[dict]:
    """jobs.needs_manual()'s items, each annotated with whether [OWNER] has
    already clicked into it (from the side-channel store, never jobs.jsonl).
    Joined on the job id needs_manual() now carries, not the apply_url."""
    opened = _load_opened()
    return [{**item, "opened": item.get("id") in opened} for item in jobs.needs_manual()]


# ---- D243 manual-apply companion (prefilled answers beside the form link) ----
# Submission-UNCERTAIN wall reasons: the operator may have already submitted before it
# capped/died, so the human must VERIFY in the ATS (or [OWNER]'s email) before re-submitting.
_UNCERTAIN_REASONS = ("attempt_cap", "inflight_timeout")


def _resume_path(job: dict) -> str:
    """Tailored resume for this job if one was rendered, else the base resume."""
    try:
        safe = resume_tailor.safe_name(job.get("id") or "")
        tailored = ROOT / "store" / "resume_tailored" / f"{safe}.pdf"
        if tailored.exists() and tailored.stat().st_size > 20000:
            return str(tailored)
    except Exception:  # noqa: BLE001
        pass
    return str(ROOT / "store" / "resume.pdf")


def _apply_email(job: dict) -> str:
    """Apply-by-email address if the posting offers one (mailto: apply_url or apply_email
    field), else "". A zero-CAPTCHA path when present; the human sends it, never auto-sent."""
    em = (job.get("apply_email") or "").strip()
    if em and "@" in em:
        return em
    url = job.get("apply_url") or ""
    if url.lower().startswith("mailto:"):
        addr = url[7:].split("?", 1)[0].strip()
        return addr if "@" in addr else ""
    return ""


def _applied_link(jid: str) -> str:
    """One-click localhost URL that marks THIS job applied, so a manual finish still records
    (Codex: the old manual flow never recorded completion, undercounting the daily cap and
    letting a finished job get reapproved). Same per-job callback token the apply operator
    uses (hmac over the id, scoped to /applied+/skipped on localhost only)."""
    cb = hmac.new(sign_secret().encode(), f"applycb:{jid}".encode(),
                  hashlib.sha256).hexdigest()[:24]
    return f"http://localhost:8765/api/jobs/{quote(jid, safe='')}/applied?cb={cb}"


def build_prefill_companion(job: dict) -> dict:
    """Everything a human needs beside the form link so a walled application takes ~30s
    instead of a full redo: standard profile fields, the answer bank's Q&A, this job's
    cover (job_cover.py's cover_override, else default_cover), the salary directive, the
    resume path, the detected ATS + wall reason, an apply-by-email path if one exists, a
    one-click 'mark applied' link, and a US-VPN reminder (the human ATS submit needs the
    same US exit IP the operator does)."""
    profile = jobs.load_profile()
    try:
        bank = json.loads((ROOT / "store" / "answer_bank.json").read_text()).get("qa", [])
    except (OSError, json.JSONDecodeError):
        bank = []
    cover = job.get("cover_override") or profile.get("default_cover", "")
    reason = (job.get("reason") or "").lower()
    uncertain = any(reason.startswith(r) for r in _UNCERTAIN_REASONS)
    jid = job.get("id") or ""
    return {
        "job_id": jid, "title": job.get("title"), "company": job.get("company"),
        "apply_url": job.get("apply_url"),
        "ats": ats_friction.detect_ats(job.get("apply_url") or "", job.get("source") or ""),
        "reason": job.get("reason"),
        "profile_fields": {k: v for k, v in profile.items() if not k.startswith("_")},
        "standard_answers": bank, "cover_letter": cover,
        "salary_directive": jobs.salary_target(job, 0)[1],
        "resume_path": _resume_path(job),
        "apply_email": _apply_email(job),
        "applied_link": _applied_link(jid) if jid else "",
        # if the bot may have already submitted, tell the human to check before re-sending
        "verify_before_submit": bool(uncertain),
        "geo_note": "Confirm you are on the US VPN (Mullvad) before you submit.",
        "generated": now_iso(),
    }


def write_prefill_companions(limit: int = 30, force: bool = False) -> int:
    """One prefill file per needs_manual() job. Filenames use resume_tailor.safe_name (an
    8-hex id hash), NOT a bare regex sanitize: the old `re.sub(...)` was many-to-one, so two
    job ids differing only in a stripped char ('role:a' vs 'role?a') collided on one file and
    the second job silently reused the first's prefill (the same bug class fixed for resume
    PDFs, CX-G2). Idempotent unless force=True."""
    PREFILL_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    manual_ids = {m.get("id") for m in jobs.needs_manual()}
    by_id = {j.get("id"): j for j in jobs.load_jobs()}
    for jid in manual_ids:
        j = by_id.get(jid)
        if not j:
            continue
        out_path = PREFILL_DIR / f"{resume_tailor.safe_name(jid)}.json"
        if out_path.exists() and not force:
            continue
        tmp = out_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(build_prefill_companion(j), indent=1))
        tmp.replace(out_path)
        n += 1
        if n >= limit:
            break
    return n


def notify_manual_pile(count: int | None = None) -> bool:
    """Push once the finish-by-hand pile reaches MANUAL_NOTIFY_AT, with hysteresis (re-arm
    only after it drops below MANUAL_REARM_AT) and a MANUAL_COOLDOWN_H floor, so a pile that
    hovers at the threshold doesn't nag every maintenance run. Returns whether it pushed."""
    if count is None:
        count = len(jobs.needs_manual())
    try:
        st = json.loads(NOTIFY_STATE.read_text())
    except (OSError, json.JSONDecodeError):
        st = {"armed": True, "last_ts": ""}
    if count < MANUAL_REARM_AT:
        if not st.get("armed"):
            st["armed"] = True
            _save_notify_state(st)
        return False
    if count < MANUAL_NOTIFY_AT or not st.get("armed"):
        return False
    last = st.get("last_ts") or ""
    if last:
        try:
            age_h = (datetime.now().astimezone() - datetime.fromisoformat(last)).total_seconds() / 3600
            if age_h < MANUAL_COOLDOWN_H:
                return False
        except (ValueError, TypeError):
            pass
    try:
        import planner
        planner.notify("Job apps to finish by hand",
                       f"{count} walled applications are staged and ready. Open the JOBS tab, "
                       "Finish by hand, each is prefilled.", tags="briefcase")
    except Exception:  # noqa: BLE001
        pass
    st["armed"] = False
    st["last_ts"] = now_iso()
    _save_notify_state(st)
    return True


def _save_notify_state(st: dict) -> None:
    try:
        NOTIFY_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = NOTIFY_STATE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(st))
        tmp.replace(NOTIFY_STATE)
    except OSError:
        pass


def run():
    refreshed = refresh_stale()
    dupes = 0
    all_jobs = jobs.load_jobs()
    approved = [j for j in all_jobs if j.get("status") == "approved"]
    for j in approved:
        if repost_variance_dupe_reason(j, all_jobs):
            dupes += 1
    prefilled = write_prefill_companions()
    pushed = notify_manual_pile()
    tax = error_taxonomy_report()
    print(f"job_pipeline_quality: stale-refresh checked {refreshed['checked']}, "
          f"{refreshed['dead']} dead listing(s) dropped")
    print(f"job_pipeline_quality: {dupes} repost-variance dupe(s) detectable in approved queue "
          "(report only, not auto-skipped by this run)")
    print(f"job_pipeline_quality: velocity cap {velocity_cap()}/day, "
          f"{velocity_remaining()} remaining today")
    print(f"job_pipeline_quality: {prefilled} new manual-apply prefill companion(s) written"
          + (" (pushed finish-by-hand nudge)" if pushed else ""))
    print(f"job_pipeline_quality: skip-reason taxonomy {tax}")
    return {"refreshed": refreshed, "repost_dupes": dupes, "prefilled": prefilled, "taxonomy": tax}


if __name__ == "__main__":
    run()
