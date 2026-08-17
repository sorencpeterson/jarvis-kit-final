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


_LABEL_JS = """(el) => {
  const id = el.getAttribute('id');
  if (id) { const l = document.querySelector(`label[for="${CSS.escape(id)}"]`);
            if (l && l.innerText.trim()) return l.innerText; }
  const wrap = el.closest('label');
  if (wrap && wrap.innerText.trim()) return wrap.innerText;
  const grp = el.closest('div,fieldset,li,section');
  if (grp) { const lab = grp.querySelector('label,legend');
             if (lab && lab.innerText.trim()) return lab.innerText; }
  return el.getAttribute('aria-label') || el.getAttribute('placeholder')
      || el.getAttribute('name') || '';
}"""


def _answer_bank() -> list:
    try:
        return json.loads((ROOT / "store" / "answer_bank.json").read_text()).get("qa", [])
    except (OSError, json.JSONDecodeError, AttributeError):
        return []


def _answer_screeners(page, profile: dict, out: dict) -> list:
    """Fill the form's OWN questions from answers the owner already gave.

    Only touches controls that are still EMPTY, so nothing the spec filled is
    overwritten. Anything ats_forms.answer_for() cannot match confidently is left
    blank on purpose: an unfilled required field fails validation and reaches a
    human, while a guessed one is submitted and believed.
    """
    bank = _answer_bank()
    done = []
    try:
        controls = page.query_selector_all(
            "input:not([type=hidden]):not([type=file]):not([type=submit]), textarea, select")
    except Exception:  # noqa: BLE001
        return done
    for el in controls[:60]:                       # bounded: no runaway on a huge form
        try:
            if not el.is_visible() or not el.is_enabled():
                continue
            kind = (el.get_attribute("type") or el.evaluate("e => e.tagName") or "").lower()
            if kind in ("checkbox", "radio"):
                continue                            # consent/EEO radios: never auto-click
            if (el.input_value() or "").strip():
                continue                            # already filled by the spec
            label = el.evaluate(_LABEL_JS) or ""
            ans = ats_forms.answer_for(label, profile, bank)
            if not ans:
                continue
            if kind == "select":
                # only choose an option that genuinely matches; never the first one
                picked = el.evaluate(
                    """(sel, want) => {
                        const w = String(want).toLowerCase().trim();
                        for (const o of sel.options) {
                          const t = (o.textContent||'').toLowerCase().trim();
                          if (t === w || (t && (t.startsWith(w) || w.startsWith(t)))) {
                            sel.value = o.value;
                            sel.dispatchEvent(new Event('change', {bubbles:true}));
                            return o.textContent;
                          }
                        }
                        return null;
                     }""", ans)
                if picked:
                    done.append(f"{label.strip()[:36]}={str(picked).strip()[:20]}")
                continue
            el.fill(ans)
            done.append(f"{label.strip()[:36]}={ans[:20]}")
        except Exception:  # noqa: BLE001 -- one awkward control must not stop the form
            continue
    return done


def new_result(job: dict, spec: dict) -> dict:
    """The result dict, created by the CALLER so its state survives an exception.

    submit_attempted in particular has to outlive a crash: it is the difference
    between safely returning a job to the queue and applying to the same employer
    twice.
    """
    return {"id": job.get("id"), "company": job.get("company"),
            "url": job.get("apply_url") or "", "ats": spec["host_match"][0],
            "filled": [], "action": "", "reason": "", "submit_attempted": False}


def apply_one(page, job: dict, spec: dict, profile: dict, submit: bool,
              out: dict | None = None) -> dict:
    """Fill one application, recording progress into `out` as it goes."""
    url = job.get("apply_url") or ""
    out = out if out is not None else new_result(job, spec)

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
    if not res:
        # Submitting an application with no resume attached is worse than not
        # applying: it burns the one-per-employer guard on something no recruiter
        # can act on. Hand it back rather than send an empty-handed application.
        out["action"] = "handoff"
        out["reason"] = "no resume file (expected store/resume.pdf)"
        return out
    uploaded = False
    for sel in spec["resume"]:
        try:
            el = page.query_selector(sel)
            if el:
                el.set_input_files(str(res))
                out["filled"].append(f"resume={res.name}")
                uploaded = True
                break
        except Exception:  # noqa: BLE001
            continue
    if not uploaded and submit:
        out["action"] = "handoff"
        out["reason"] = "resume field not found on the page"
        return out

    # Every REQUIRED field must actually have landed on the page. A selector that
    # matched nothing means this page is not the form we think it is, and a form we
    # do not understand gets handed over rather than submitted with whatever stuck.
    landed = set(out["filled"])
    not_landed = [k for k in spec["required"] if k not in landed]
    if not_landed:
        out["action"] = "handoff"
        out["reason"] = f"required field(s) not on page: {', '.join(not_landed)}"
        return out

    # Everything above fills name/email/phone/resume. Real forms mark more than that
    # required -- work authorization, sponsorship, location, notice period, a screener
    # or two -- and pressing submit with those blank produces a validation error, not
    # an application. That is the largest single reason a run does not land.
    out["answered"] = _answer_screeners(page, profile, out)

    if not submit:
        out["action"] = "dry-run"
        return out

    for sel in spec["submit"]:
        try:
            btn = page.query_selector(sel)
            if not (btn and btn.is_visible()):
                continue
            # Snapshot BEFORE the click. Everything below is a comparison against this,
            # because the post-click page ALONE cannot tell "the employer confirmed
            # receipt" from "the form we failed to submit contains the word
            # application". That conflation marked 17 applications confirmed with zero
            # confirmation emails behind them.
            before_url = page.url
            before = page.inner_text("body")
            # Recorded BEFORE the click: if the page then dies, the caller must know a
            # submit may already have gone through, or returning this job to the queue
            # applies to the same employer twice.
            out["submit_attempted"] = True
            btn.click()
            page.wait_for_timeout(4000)
            after_url, after = page.url, page.inner_text("body")

            hit = ats_forms.confirmation_delta(before, after)
            if hit:
                out["action"] = "submitted"
                out["reason"] = f"confirm: {hit}"
                return out

            bad = ats_forms.validation_error(before, after)
            if bad:
                # the form rejected us and is still on screen. Nothing was submitted,
                # so this is safe to hand back rather than leave submission-uncertain.
                out["action"] = "handoff"
                out["submit_attempted"] = False
                out["reason"] = f"form rejected the submission ({bad}); needs a human"
                return out

            if ats_forms.page_changed(before_url, after_url, before, after):
                # something happened, but the employer never said "received". Real for
                # ATSes that redirect to a bare status page; recorded honestly so
                # job_verify settles it against the confirmation email.
                out["action"] = "submitted"
                out["reason"] = ("unconfirmed (page advanced but showed no receipt; "
                                 "verify in ATS)")
                return out

            # The click changed NOTHING: no URL move, no new text, no complaint. It
            # did not submit. Marking this applied is what produced a 5% real rate
            # behind a much higher reported one.
            out["action"] = "uncertain"
            out["reason"] = ("submit clicked but the page did not change; "
                             "likely not submitted, verify in ATS")
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

    # GEO GATE, fail closed, same rule the LLM chain enforces: never apply to a
    # US-remote role from a non-US IP. It contradicts the profile's own address and
    # gets the application geo-filtered. Only gates SUBMITTING; a dry run reads
    # public pages and is harmless from anywhere.
    if submit:
        try:
            import geo_check
            g = geo_check.check()
        except Exception as e:  # noqa: BLE001 -- unavailable means unknown means stop
            g = {"ok": False, "error": f"{type(e).__name__}"}
        if not g.get("ok"):
            where = g.get("city") or g.get("country") or g.get("error") or "unknown"
            print(f"apply_direct: held, not on a US IP (currently {where}). "
                  "Connect the VPN and re-run.")
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

    # CLAIM the batch before touching it. Without this, this agent and the LLM apply
    # chain both read approved_to_apply() and can select the SAME job, then both
    # submit it: a duplicate application to a real employer, which is worse than a
    # missed one. mark_applying is a compare-and-swap (expect="approved"), so a job
    # the chain claimed a moment earlier simply does not come back as 'applying'
    # here and is dropped from this batch. bump_attempts is the poison-pill guard:
    # without it a job that fails every time is retried forever.
    if submit:
        ids = [j["id"] for j in queue]
        jobs.bump_attempts(ids)
        jobs.mark_applying(ids)
        now = {x["id"]: x.get("status") for x in jobs.load_jobs()}
        queue = [j for j in queue if now.get(j["id"]) == "applying"]
        if not queue:
            print("apply_direct: every candidate was claimed elsewhere; nothing to do")
            return 0

    print(f"apply_direct: {len(queue)} job(s), submit={submit}")
    done = handed = skipped = 0
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(locale="en-US",
                                      timezone_id=cfg.get("apply_tz") or "America/Chicago")
            for i, job in enumerate(queue):
                spec = ats_forms.detect(job["apply_url"])
                page = ctx.new_page()
                # created here so a crash inside apply_one still leaves us holding
                # whatever it managed to record, submit_attempted above all
                r = new_result(job, spec)
                try:
                    apply_one(page, job, spec, profile, submit, out=r)
                except Exception as e:  # noqa: BLE001 -- one bad page never stops the batch
                    r["action"] = "handoff"
                    r["reason"] = f"page error: {type(e).__name__}"
                finally:
                    page.close()

                tag = f"[{r['ats']}] {(r.get('company') or '?')[:28]:<28}"
                if r["action"] == "submitted":
                    jobs.set_status(r["id"], "applied", r["reason"], expect="applying")
                    done += 1
                    print(f"  {tag} SUBMITTED  {r['reason'][:52]}")
                elif r["action"] == "skip":
                    jobs.set_status(r["id"], "skipped", r["reason"], expect="applying")
                    skipped += 1
                    print(f"  {tag} walled     {r['reason']}")
                elif r["action"] == "dry-run":
                    ans = r.get("answered") or []
                    print(f"  {tag} would fill {', '.join(r['filled']) or '(nothing)'}"
                          + (f"  +{len(ans)} screener(s): {'; '.join(ans[:4])}" if ans else ""))
                elif r["action"] == "uncertain" or r.get("submit_attempted"):
                    # a submit may already have landed: never return this to the
                    # approved pool, or the LLM operator applies to the same
                    # employer a second time. job_verify settles it from the
                    # confirmation email.
                    jobs.set_status(r["id"], "skipped",
                                    "inflight_timeout (submit attempted, outcome unknown; "
                                    "verify in ATS before retrying)", expect="applying")
                    skipped += 1
                    print(f"  {tag} UNCERTAIN  {r['reason'][:44]}")
                else:
                    # nothing was submitted, so hand it straight back to the LLM
                    # operator by returning it to the approved pool
                    jobs.set_status(r["id"], "approved", expect="applying")
                    handed += 1
                    print(f"  {tag} -> operator {r['reason'][:52]}")

                if submit and i < len(queue) - 1:
                    time.sleep(_pace(cfg))
            ctx.close()
            browser.close()
    finally:
        # Release anything from THIS batch still held, whatever the exit path
        # (browser crash, interrupt, exception). A job stranded in 'applying' is
        # invisible to needs_manual() and needs_verify() and can never be retried,
        # which is strictly worse than a skipped one.
        if submit:
            try:
                held = {x["id"] for x in jobs.load_jobs() if x.get("status") == "applying"}
                for j in queue:
                    if j["id"] in held:
                        jobs.set_status(j["id"], "skipped",
                                        "inflight_timeout (direct apply exited with this "
                                        "job in flight; verify in ATS before retrying)",
                                        expect="applying")
            except Exception:  # noqa: BLE001 -- cleanup must never mask the real exit
                pass

    print(f"apply_direct: {done} submitted, {handed} handed to the operator, "
          f"{skipped} walled or uncertain, 0 LLM calls")
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
