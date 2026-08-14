#!/usr/bin/env python3
"""Apply to known-ATS jobs with a browser and no LLM. Zero tokens per application.

    .venv/bin/python agents/apply_direct.py --dry-run          fill, report, never submit
    .venv/bin/python agents/apply_direct.py --dry-run --limit 3
    .venv/bin/python agents/apply_direct.py --submit           actually submit

WHAT THIS CHANGES. The LLM operator costs roughly a full agentic browser session per
application, which is why the daily cap has been single digits. A Greenhouse form does
not need reasoning: the first-name box is the first-name box every time. This fills it
from a table (agents/ats_forms.py) and submits. No tokens, no session limit, no
operator dying halfway through a form with nobody able to say whether it landed.

WHAT IT DOES NOT CHANGE. Jobs on an unrecognised ATS are left exactly where they were,
for the LLM operator. This only ever adds a cheap path.

THE THREE RAILS, none of which are optional:

1. --dry-run is the default. --submit must be passed explicitly AND
   "direct_apply": true must be set in store/config.json. Both, every time.
2. A page showing a CAPTCHA, an account wall, or a verification code is never
   pushed through. It is skipped with the reason word that routes it to the
   finish-by-hand pile, same as the LLM path.
3. A form whose required fields the profile cannot fill is abandoned, not
   half-submitted. A blank application is worse than no application.

PACING. Submissions are spaced by a randomised interval (default 45-90s). This is not
politeness: ATS platforms run velocity heuristics that flag uniform, rapid, identical
submissions before a human ever reads them, so applications sent faster than a person
could type are worth less, not more. Tune with "direct_apply_pace_s": [min, max].

HONESTY ABOUT VERIFICATION. The selectors in ats_forms.py are written against each
platform's published structure and have NOT been proven against a live submission,
because proving them means sending real applications to real employers. Run --dry-run
first, read what it says it would fill, and only then enable submitting. Start with
Greenhouse: it is the one marked high confidence and the only platform with a
demonstrated success record.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import ats_forms  # noqa: E402
import jobs  # noqa: E402

CONFIG = ROOT / "store" / "config.json"
RESUME = ROOT / "store" / "resume.pdf"


def _cfg() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _pace(cfg: dict) -> float:
    lo, hi = 45, 90
    v = cfg.get("direct_apply_pace_s")
    if isinstance(v, (list, tuple)) and len(v) == 2:
        try:
            lo, hi = max(5, int(v[0])), max(6, int(v[1]))
        except (TypeError, ValueError):
            pass
    return random.uniform(min(lo, hi), max(lo, hi))


def _resume_path(job: dict) -> Path | None:
    """The resume this job should upload: its own tailored/variant file if one was
    stamped, else the static resume."""
    rf = (job.get("resume_file") or "").strip()
    if rf:
        p = Path(rf) if Path(rf).is_absolute() else ROOT / rf
        try:                                   # never upload outside store/
            if p.is_file() and p.resolve().is_relative_to((ROOT / "store").resolve()):
                return p
        except (OSError, ValueError):
            pass
    return RESUME if RESUME.is_file() else None


def apply_one(page, job: dict, spec: dict, profile: dict, submit: bool) -> dict:
    """Fill one application. Returns a result dict; never raises for a page problem."""
    url = job.get("apply_url") or ""
    out = {"id": job.get("id"), "company": job.get("company"), "url": url,
           "ats": spec["host_match"][0], "filled": [], "action": "", "reason": ""}

    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(1500)

    wall = ats_forms.wall_reason(page.content())
    if wall:
        out["action"], out["reason"] = "skip", wall
        return out

    values = ats_forms.values_for(spec, profile)
    missing = ats_forms.missing_required(spec, values)
    if missing:
        out["action"] = "handoff"
        out["reason"] = f"profile missing {', '.join(missing)}"
        return out

    for key, val in values.items():
        for sel in spec["fields"][key]:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.fill(val)
                    out["filled"].append(key)
                    break
            except Exception:  # noqa: BLE001 -- one bad selector must not kill the run
                continue

    res = _resume_path(job)
    if res:
        for sel in spec["resume"]:
            try:
                el = page.query_selector(sel)
                if el:
                    el.set_input_files(str(res))
                    out["filled"].append(f"resume={res.name}")
                    break
            except Exception:  # noqa: BLE001
                continue

    # Every REQUIRED field must actually have landed on the page. A selector that
    # matched nothing means this page is not the form we think it is, and a form we
    # do not understand gets handed over rather than submitted with whatever stuck.
    landed = set(out["filled"])
    not_landed = [k for k in spec["required"] if k not in landed]
    if not_landed:
        out["action"] = "handoff"
        out["reason"] = f"required field(s) not on page: {', '.join(not_landed)}"
        return out

    if not submit:
        out["action"] = "dry-run"
        return out

    for sel in spec["submit"]:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(4000)
                body = (page.content() or "").lower()
                ok = any(w in body for w in
                         ("thank you", "application received", "we have received",
                          "successfully submitted", "your application"))
                out["action"] = "submitted"
                out["reason"] = ("confirm: page showed a confirmation"
                                 if ok else
                                 "unconfirmed (no confirmation text found; verify in ATS)")
                return out
        except Exception:  # noqa: BLE001
            continue
    out["action"], out["reason"] = "handoff", "no submit control found"
    return out


def run(limit: int = 10, submit: bool = False, only_ats: str = "") -> int:
    cfg = _cfg()
    if submit and not cfg.get("direct_apply"):
        print("apply_direct: --submit requires \"direct_apply\": true in "
              "store/config.json. Refusing.")
        return 2
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("apply_direct: playwright is not installed.\n"
              "  .venv/bin/pip install playwright && .venv/bin/playwright install chromium")
        return 2

    profile = jobs.load_profile()
    queue = [j for j in jobs.approved_to_apply()
             if ats_forms.detect(j.get("apply_url") or "")]
    if only_ats:
        queue = [j for j in queue
                 if only_ats in (ats_forms.detect(j["apply_url"]) or {}).get("host_match", ())]
    queue = queue[:limit]
    if not queue:
        print("apply_direct: nothing in the queue on a supported ATS "
              "(everything else stays with the LLM operator)")
        return 0

    print(f"apply_direct: {len(queue)} job(s), submit={submit}")
    done = handed = skipped = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(locale="en-US",
                                  timezone_id=cfg.get("apply_tz") or "America/Chicago")
        for i, job in enumerate(queue):
            spec = ats_forms.detect(job["apply_url"])
            page = ctx.new_page()
            try:
                r = apply_one(page, job, spec, profile, submit)
            except Exception as e:  # noqa: BLE001 -- one bad page never stops the batch
                r = {"id": job.get("id"), "company": job.get("company"),
                     "action": "handoff", "reason": f"page error: {type(e).__name__}",
                     "filled": [], "ats": spec["host_match"][0]}
            finally:
                page.close()

            tag = f"[{r['ats']}] {(r.get('company') or '?')[:28]:<28}"
            if r["action"] == "submitted":
                jobs.set_status(r["id"], "applied", r["reason"])
                done += 1
                print(f"  {tag} SUBMITTED  {r['reason'][:52]}")
            elif r["action"] == "skip":
                jobs.set_status(r["id"], "skipped", r["reason"])
                skipped += 1
                print(f"  {tag} walled     {r['reason']}")
            elif r["action"] == "dry-run":
                print(f"  {tag} would fill {', '.join(r['filled']) or '(nothing)'}")
            else:
                handed += 1
                print(f"  {tag} -> operator {r['reason'][:52]}")

            if submit and i < len(queue) - 1:
                time.sleep(_pace(cfg))
        ctx.close()
        browser.close()

    print(f"apply_direct: {done} submitted, {handed} handed to the operator, "
          f"{skipped} walled, 0 LLM calls")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic ATS applications, no LLM")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--submit", action="store_true",
                    help="actually submit (also needs direct_apply:true in config)")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--ats", default="", help="only this ATS, e.g. greenhouse.io")
    a = ap.parse_args()
    return run(limit=a.limit, submit=a.submit, only_ats=a.ats)


if __name__ == "__main__":
    raise SystemExit(main())
