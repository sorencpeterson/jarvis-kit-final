#!/usr/bin/env python3
"""Why aren't applications landing? Check every precondition in one pass.

    python3 tools/job_doctor.py

Ten checks, in the order they actually bite. Each prints PASS, WARN or FAIL with the
exact command to fix it. No LLM calls, no network, nothing written.

Written because the failure modes here are all SILENT. A thin profile, an empty answer
bank, a missing resume and an unrendered variant library all present the same way: the
run reports numbers and almost nothing lands. Rather than debug that from the outside
each time, check the preconditions directly.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

STORE = ROOT / "store"
G, Y, R, B, X = "\033[32m", "\033[33m", "\033[31m", "\033[1m", "\033[0m"
_rows: list = []


def ok(name, detail=""):
    _rows.append(("PASS", name, detail, ""))


def warn(name, detail, fix=""):
    _rows.append(("WARN", name, detail, fix))


def bad(name, detail, fix=""):
    _rows.append(("FAIL", name, detail, fix))


def _cfg() -> dict:
    try:
        return json.loads((STORE / "config.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _profile() -> dict:
    try:
        return json.loads((STORE / "application_profile.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}


# --------------------------------------------------------------- the checks

def check_profile():
    p = _profile()
    if not p:
        return bad("application profile", "store/application_profile.json missing",
                   "python3 setup.py")
    # what the deterministic specs mark required, plus what real forms ask for
    hard = [k for k in ("first_name", "last_name", "email") if not str(p.get(k) or "").strip()]
    soft = [k for k in ("phone", "city_state", "state_abbrev", "zip5", "street_address",
                        "work_authorization", "requires_sponsorship", "availability",
                        "linkedin") if not str(p.get(k) or "").strip()]
    if hard:
        return bad("application profile", f"missing required: {', '.join(hard)}",
                   "edit store/application_profile.json")
    if soft:
        return warn("application profile", f"{len(soft)} field(s) blank: {', '.join(soft[:6])}",
                    "edit store/application_profile.json. Every blank one is a form "
                    "question that stays unanswered and fails validation.")
    ok("application profile", f"{len(p)} fields, all key ones present")


def check_answer_bank():
    try:
        qa = json.loads((STORE / "answer_bank.json").read_text()).get("qa", [])
    except (OSError, json.JSONDecodeError):
        qa = []
    if not qa:
        return bad("answer bank", "empty: every screener question goes unanswered",
                   "python3 agents/answer_bank.py --seed   (one minute, no LLM)")
    if len(qa) < 8:
        return warn("answer bank", f"only {len(qa)} answer(s)",
                    "python3 agents/answer_bank.py --seed")
    ok("answer bank", f"{len(qa)} answers available to screener matching")


def check_resume():
    static = STORE / "resume.pdf"
    if not static.is_file():
        return bad("resume PDF", "store/resume.pdf missing: applications are refused "
                                 "rather than sent without one",
                   "export your resume to store/resume.pdf")
    kb = static.stat().st_size // 1024
    variants = list((STORE / "resume_tailored" / "variants").glob("*.pdf"))
    if not variants:
        return warn("resume PDF", f"static resume present ({kb}KB), no variants rendered",
                    "python3 agents/resume_library.py --build && "
                    "python3 agents/resume_library.py --render")
    ok("resume PDF", f"static ({kb}KB) + {len(variants)} rendered variant(s)")


def check_covers():
    try:
        import jobs
        js = jobs.load_jobs()
    except Exception:  # noqa: BLE001
        return warn("cover letters", "could not read the job queue")
    appr = [j for j in js if j.get("status") == "approved"]
    if not appr:
        return warn("cover letters", "no approved jobs to check")
    with_cov = sum(1 for j in appr if (j.get("cover_override") or "").strip())
    if not with_cov:
        return warn("cover letters", f"0 of {len(appr)} approved jobs have one",
                    "python3 agents/job_cover.py   (generic default_cover is used "
                    "until then, which is the difference between a tailored "
                    "application and a form letter)")
    ok("cover letters", f"{with_cov} of {len(appr)} approved jobs have a tailored cover")


def check_playwright():
    try:
        import playwright  # noqa: F401
    except ImportError:
        return bad("playwright", "not installed: the zero-token path cannot run",
                   ".venv/bin/pip install playwright && "
                   ".venv/bin/playwright install chromium")
    ok("playwright", "installed")


def check_auth():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ok("LLM auth", "ANTHROPIC_API_KEY set: operators bill to the API, "
                              "not a subscription session")
    warn("LLM auth", "no API key: LLM operators ride the subscription session limit",
         "the deterministic path is unaffected. For the jobs it cannot handle, an "
         "ANTHROPIC_API_KEY removes the session ceiling that kills operators mid-form.")


def check_queue():
    try:
        import ats_forms
        import jobs
        js = jobs.load_jobs()
    except Exception as e:  # noqa: BLE001
        return bad("job queue", f"could not read: {type(e).__name__}")
    appr = [j for j in js if j.get("status") == "approved"]
    if not appr:
        return warn("job queue", "no approved jobs waiting",
                    "python3 agents/jobs.py   then approve some in the UI")
    det = sum(1 for j in appr if ats_forms.detect(j.get("apply_url") or ""))
    pct = det * 100 // max(1, len(appr))
    (ok if det else warn)(
        "job queue",
        f"{len(appr)} approved, {det} ({pct}%) on a board the zero-token path handles",
        "" if det else "the rest need the LLM operator")


def check_uncertainty():
    try:
        import job_verify
        pile = job_verify._pile()
    except Exception:  # noqa: BLE001
        return
    if len(pile) > 20:
        return warn("unverified applications", f"{len(pile)} whose fate is unknown",
                    "python3 agents/job_verify.py --report   then check those in the "
                    "ATS. A large pile also blocks retries: those employers are "
                    "reserved so a sibling role is not double-applied to.")
    ok("unverified applications", f"{len(pile)} pending verification")


def check_config():
    c = _cfg()
    if not c:
        return bad("config", "store/config.json missing", "python3 setup.py")
    bits = []
    if not c.get("direct_apply"):
        bits.append('direct_apply is off (dry-run only)')
    cap = c.get("job_daily_apply_cap")
    if cap:
        bits.append(f"daily cap {cap}")
    pace = c.get("direct_apply_pace_s") or [45, 90]
    bits.append(f"pace {pace[0]}-{pace[1]}s")
    if not c.get("direct_apply"):
        return warn("config", "; ".join(bits),
                    'set "direct_apply": true once a --dry-run looks right')
    ok("config", "; ".join(bits))


def check_geo():
    try:
        import geo_check
        g = geo_check.check()
    except Exception:  # noqa: BLE001
        return warn("exit IP", "geo check unavailable")
    if g.get("ok"):
        return ok("exit IP", f"US ({g.get('city') or g.get('country') or 'ok'})")
    warn("exit IP", f"not US ({g.get('city') or g.get('country') or g.get('error')})",
         "submitting is held until this is US: a US-remote application from a "
         "foreign IP contradicts the profile and gets geo-filtered")


def check_server_fresh():
    try:
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_server_fresh.py")],
                           capture_output=True, text=True, timeout=20)
        if r.returncode == 0:
            return ok("running server", (r.stdout or "").strip()[:60] or "current")
        warn("running server", "STALE: the process predates app/server.py",
             "restart it, then re-run tools/check_server_fresh.py")
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    print(f"\n{B}Job pipeline check{X}   (no LLM calls, nothing written)\n")
    for fn in (check_profile, check_answer_bank, check_resume, check_covers,
               check_playwright, check_auth, check_config, check_geo,
               check_queue, check_uncertainty, check_server_fresh):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 -- one broken check must not hide the rest
            warn(fn.__name__, f"check itself failed: {type(e).__name__}")

    colour = {"PASS": G, "WARN": Y, "FAIL": R}
    for state, name, detail, fix in _rows:
        print(f"  {colour[state]}{state:<4}{X} {B}{name:<24}{X} {detail}")
        if fix:
            for line in fix.split(". "):
                if line.strip():
                    print(f"       {line.strip().rstrip('.')}")
    fails = sum(1 for r in _rows if r[0] == "FAIL")
    warns = sum(1 for r in _rows if r[0] == "WARN")
    print()
    if fails:
        print(f"  {R}{fails} blocking problem(s){X}. Applications will not land until "
              f"those are fixed.\n")
        return 1
    if warns:
        print(f"  {Y}{warns} thing(s) worth fixing{X}, none blocking. Each blank profile "
              f"field\n  and missing cover letter costs you a form question.\n")
        return 0
    print(f"  {G}All clear.{X} Prove one platform before scaling:\n"
          f"    .venv/bin/python agents/apply_direct.py --dry-run --ats greenhouse.io --limit 5\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
