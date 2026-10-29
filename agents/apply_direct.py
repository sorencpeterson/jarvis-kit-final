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
import re
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
    """The resume this job should upload: the role-matched variant when one exists,
    then any stamped per-job file, then the static resume.

    resume_for_mode() is the ONE place that decides which resume goes out, honouring
    config resume_mode. Without asking it, this path uploaded the generic resume even
    when a variant matched to the role was sitting rendered on disk -- the same class
    of miss as pasting a generic cover letter over a tailored one.
    """
    try:
        import resume_library
        kind, path = resume_library.resume_for_mode(job)
        if kind != "static" and path:
            p = Path(path)
            if p.is_file() and p.resolve().is_relative_to((ROOT / "store").resolve()):
                return p
    except Exception:  # noqa: BLE001 -- resume choice must never break an apply round
        pass
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


def _cover_for(job: dict, profile: dict) -> str:
    """This job's OWN cover letter if one was written for it, else the generic one.

    job_cover.py generates and caches cover_override per job during the morning batch,
    so the customisation is already paid for; pasting it here costs nothing. Without
    this the zero-token path submitted a generic application when a tailored one was
    sitting on the record.
    """
    return ((job.get("cover_override") or "").strip()
            or (profile.get("default_cover") or "").strip())


def _consent_ok() -> bool:
    """Owner knob for ticking a required DATA-PRIVACY notice. Ships False.
    Scope is enforced in ats_forms.is_privacy_ack(), not here: arbitration,
    background checks and employment terms are refused even with this on."""
    return bool(_cfg().get("accept_privacy_notice"))



def _opts_in(page, el):
    """The option elements belonging to THIS combobox, via aria-controls.

    Scoping matters: an Arcesium posting pre-renders 244 [role=option] nodes (every
    country, plus other selects), so a global query returns other fields' options.
    """
    owns = el.get_attribute("aria-controls") or el.get_attribute("aria-owns") or ""
    if owns:
        box = page.query_selector(f"#{owns}")
        if box:
            got = box.query_selector_all("[role=option], [class*=option]")
            if got:
                return got
    return page.query_selector_all(
        "[role=listbox]:not([hidden]) [role=option], .select__menu [class*=option]")


# Yes/No comboboxes, matched by POLARITY rather than text overlap. "Authorized to
# work in the US for any employer" contains neither "yes" nor "no", so a literal match
# leaves a required authorization question blank. Deliberately narrow: only an answer
# that OPENS with an unambiguous marker gets a polarity, because getting this backwards
# would put a false statement about work authorization on a real application. Anything
# else falls through to the handoff.
_AFFIRM_OPEN = ("yes", "authorized", "i am authorized", "i am", "i do", "i have",
                "eligible", "us citizen", "permanent resident")
_DENY_OPEN = ("no", "none", "not ", "n/a", "i do not", "i am not", "never")
_YES_OPT = ("yes", "y")
_NO_OPT = ("no", "n")


def _polarity(want: str) -> str:
    w = " ".join((want or "").lower().split())
    for d in _DENY_OPEN:            # checked FIRST: "not authorized" must not read affirmative
        if w.startswith(d):
            return "no"
    for a in _AFFIRM_OPEN:
        if w.startswith(a):
            return "yes"
    return ""


def _match_option(opts, want: str):
    """Best option for `want`, or None. Never settles for a weak match.

    The answer may be a sentence ("No. Nothing restricts me from working") while the
    option is one word ("No"), so the answer's leading clause is tried as well as the
    whole string. Longest genuine match wins, so "No" cannot beat "No, I do not
    require sponsorship".
    """
    w = " ".join((want or "").lower().split())
    lead = re.split(r"[.,;:]", w)[0].strip()
    cands = [c for c in (w, lead) if c]
    best, best_len = None, 0
    for o in opts[:250]:
        try:
            if not o.is_visible():
                continue
            t = " ".join((o.inner_text() or "").lower().split())
        except Exception:  # noqa: BLE001
            continue
        if not t:
            continue
        for c in cands:
            if t == c or t.startswith(c) or c.startswith(t) or (len(t) > 2 and t in c):
                if len(t) > best_len:
                    best, best_len = o, len(t)
                break
    if best is not None:
        return best

    # A decline answer has to match the wording the FORM uses. Greenhouse EEO offers
    # "Decline To Self Identify"; our sentinel says "I do not wish to answer", and a
    # literal comparison finds nothing, leaving a required race field blank.
    if any(k in w for k in ("do not wish", "decline", "prefer not", "not disclose")):
        for o in opts[:250]:
            try:
                if not o.is_visible():
                    continue
                t = " ".join((o.inner_text() or "").lower().split())
            except Exception:  # noqa: BLE001
                continue
            if any(k in t for k in ("decline", "prefer not", "do not wish",
                                    "not disclose", "not to answer", "not specified")):
                return o

    # no literal overlap: try polarity against a yes/no style option set
    pol = _polarity(want)
    if not pol:
        return None
    wanted = _YES_OPT if pol == "yes" else _NO_OPT
    for o in opts[:250]:
        try:
            if not o.is_visible():
                continue
            t = " ".join((o.inner_text() or "").lower().split()).strip(" .")
        except Exception:  # noqa: BLE001
            continue
        if t in wanted or any(t.startswith(x + ",") or t.startswith(x + " ") for x in wanted):
            return o
    return None


def _pick_option(page, el, want: str) -> str:
    """Open a React combobox and choose the option matching `want`. Returns its text.

    Greenhouse renders Country, Location, work authorization, the non-compete question
    and the privacy notice as role=combobox. el.fill() writes text into the box, the
    component's state never updates, and the form reports the field required-and-blank
    while visibly showing the right text. (Its search input also reads as empty AFTER
    a successful pick, because the selection lives in a sibling element -- so never
    verify a combobox by reading el.value.)

    READ THE OPTIONS BEFORE TYPING. Typing filters the list, and typing a sentence
    into a Yes/No dropdown filters it to nothing: that is why the non-compete and
    sponsorship questions stayed blank while Country, whose answer happened to be a
    literal option, worked. Typing is only a fallback for genuinely long lists
    (country, city) where the wanted option is not rendered until filtered.
    """
    try:
        el.click()
        page.wait_for_timeout(350)

        opts = _opts_in(page, el)
        hit = _match_option(opts, want)

        if hit is None and len(opts) > 12:
            # long list: filter it, then look again
            try:
                # A location box wants the CITY. Typing "Sioux Falls, SD" filters a
                # Greenhouse city list (which offers "Sioux Falls, South Dakota,
                # United States") down to nothing, so try the bare city too.
                typed = want[:40]
                el.fill("")
                el.type(typed, delay=15)
                page.wait_for_timeout(500)
                hit = _match_option(_opts_in(page, el), want)
                if hit is None and "," in want:
                    head = want.split(",", 1)[0].strip()
                    el.fill("")
                    el.type(head[:40], delay=15)
                    page.wait_for_timeout(600)
                    o2 = _opts_in(page, el)
                    hit = _match_option(o2, head) or (o2[0] if len(o2) == 1 else None)
            except Exception:  # noqa: BLE001 -- read-only combobox, list only
                pass

        if hit is not None:
            txt = " ".join((hit.inner_text() or "").split())
            hit.click()
            page.wait_for_timeout(250)
            return txt[:60]
        el.press("Escape")
    except Exception:  # noqa: BLE001 -- a stubborn combobox falls to the handoff
        pass
    return ""


def _answer_screeners(page, job: dict, profile: dict, out: dict) -> list:
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
                # A required data-privacy acknowledgment is the one tick allowed here,
                # and only with the owner's knob on. Everything else (arbitration,
                # background checks, EEO, generic terms) stays the human's act.
                lab = el.evaluate(_LABEL_JS) or ""
                if (_consent_ok() and ats_forms.is_privacy_ack(lab)
                        and not el.is_checked()):
                    el.check()
                    done.append(f"ACK privacy notice: {lab.strip()[:60]}")
                continue
            if (el.input_value() or "").strip():
                continue                            # already filled by the spec
            label = el.evaluate(_LABEL_JS) or ""
            # The privacy notice arrives as a COMBOBOX on Greenhouse, not a checkbox,
            # so the authorized ack has to be handled here too. Scope is still
            # ats_forms.is_privacy_ack(): arbitration, background checks and
            # employment terms are refused whatever the knob says.
            if ats_forms.is_privacy_ack(label):
                if _consent_ok():
                    for aff in ("I accept", "Accept", "Yes", "I agree", "Acknowledged"):
                        got = _pick_option(page, el, aff)
                        if got:
                            done.append(f"ACK privacy notice=[{got[:24]}]")
                            break
                continue
            # a cover-letter box gets THIS job's cover, not a screener answer
            if ats_forms.is_cover_field(label):
                cov = _cover_for(job, profile)
                if cov:
                    el.fill(cov)
                    done.append(f"cover letter ({len(cov)} chars)")
                continue
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
            is_combo = bool(el.get_attribute("role") == "combobox"
                            or el.get_attribute("aria-autocomplete"))
            if is_combo:
                got = _pick_option(page, el, ans)
                if got:
                    done.append(f"{label.strip()[:30]}=[{got[:24]}]")
                continue
            el.fill(ans)
            # Greenhouse's Country and Location fields are comboboxes: fill() types the
            # text but the component's value stays EMPTY until an option is chosen, so
            # the form rejected "Country*" and "Location (City)*" as required-and-blank
            # while the box visibly showed the right text. Pick the first option if one
            # appears; otherwise leave it, and the normal required-field handoff catches
            # it rather than submitting a form that looks full and is not.
            try:
                if (el.get_attribute("role") == "combobox"
                        or el.get_attribute("aria-autocomplete")
                        or el.get_attribute("aria-controls")):
                    page.wait_for_timeout(400)
                    opt = page.query_selector(
                        "[role=option]:visible, li[id*=option]:visible,"
                        " .select__option:visible")
                    if opt:
                        opt.click()
                        page.wait_for_timeout(200)
            except Exception:  # noqa: BLE001 -- a stubborn combobox falls to the handoff
                pass
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

    suffix = spec.get("apply_path_suffix") or ""
    if suffix and not url.rstrip("/").endswith(suffix.strip("/")):
        url = url.rstrip("/") + suffix

    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(1500)

    # 2026-08-14: a cookie-consent banner sits ON TOP of the page on some ATS platforms
    # (verified live: Workable) and physically intercepts clicks on anything underneath
    # it, including the Apply trigger below -- a real TimeoutError, not a missing
    # selector. Tried on EVERY platform, not just Workable, since the failure mode is
    # generic and costs nothing when no banner exists. Decline, not accept: nothing here
    # needs marketing cookies to fill a form, and declining is the more private default.
    for sel in ats_forms.COOKIE_DECLINE_SELECTORS:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                page.wait_for_timeout(300)
                break
        except Exception:  # noqa: BLE001 -- a banner that won't dismiss must not block the run
            continue

    # Some ATS platforms (Workable) don't render the field inputs at all until an
    # "Apply" trigger is clicked -- verified live: zero fields present before the click,
    # every required field present and visible after, same URL (an in-page reveal, not
    # a navigation, unlike Lever's separate /apply page above).
    #
    # 2026-08-14: the fixed page.wait_for_timeout(1500) after the click was flaky --
    # reproduced BOTH ways live against the identical URL: sometimes every field was
    # there right after, sometimes NONE were (not just the resume field; first_name and
    # last_name came up missing too on a slower render), which is what actually caused
    # the earlier "no resume file"/"resume field not found" misreads. A scroll happened
    # to fix it once, which looked like a lazy-render issue, but a clean re-run proved
    # that was a coincidence -- fields were still absent both before AND after
    # scrolling. The real cause is JS hydration time varying run to run, so the fix is
    # to wait for a REAL field to actually exist (bounded, so a genuinely broken page
    # still falls through to the normal missing-fields handoff) rather than guess a
    # fixed delay. Verified reliable across 3 repeated live runs against the same page.
    trigger = spec.get("apply_trigger") or ()
    for sel in trigger:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                first_field_sel = next(iter(spec["fields"].get(spec["required"][0], [])), None)
                if first_field_sel:
                    try:
                        page.wait_for_selector(first_field_sel, state="visible", timeout=8000)
                    except Exception:  # noqa: BLE001 -- times out into the normal missing-fields handoff
                        pass
                else:
                    page.wait_for_timeout(1500)
                break
        except Exception:  # noqa: BLE001 -- no trigger found/clickable, fields may already be present
            continue

    # 2026-08-14: was ats_forms.wall_reason(page.content()) -- the RAW HTML, including
    # every <script src> tag on the page. Verified live against 13 real Greenhouse/Lever
    # postings that all came back "captcha": every one was a false positive from
    # Google's invisible reCAPTCHA Enterprise script tag (loaded silently on nearly
    # every modern Greenhouse form as baseline anti-spam, never shown to a real user)
    # matching the bare "recaptcha" marker. Screenshotted one live: a completely normal
    # job page, no visible challenge anywhere, required fields present and fillable.
    # inner_text('body') is what a human actually SEES -- script tags render nothing,
    # so an invisible background check can no longer trip this, while a genuine visible
    # challenge ("I'm not a robot", "verify you are human") still reads straight through,
    # since that text exists specifically to be read by a human.
    try:
        visible_text = page.inner_text("body")
    except Exception:  # noqa: BLE001 -- a body-text read failing is itself a bad sign
        visible_text = page.content()  # fall back to the old, over-cautious behavior
    wall = ats_forms.wall_reason(visible_text)
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
    # VERIFY the upload, do not assume it. set_input_files() succeeds silently on a
    # DETACHED node, and this page re-renders twice before we get here (the cookie
    # decline, then the combobox interactions), so a reference captured earlier writes
    # the file into an orphaned element. The form then reports "Resume/CV* is required"
    # while our own log says resume=... uploaded. Re-query immediately before use, then
    # confirm the page actually shows the filename, and retry once if it does not.
    uploaded = False
    stem = res.stem[:18]
    for sel in spec["resume"]:
        for attempt in (1, 2):
            try:
                el = page.query_selector(sel)       # re-queried every attempt
                if not el:
                    break
                el.set_input_files(str(res))
                page.wait_for_timeout(900)
                try:
                    shown = stem.lower() in (page.inner_text("body") or "").lower()
                except Exception:  # noqa: BLE001
                    shown = True                    # cannot read: do not loop forever
                if shown:
                    out["filled"].append(f"resume={res.name}")
                    uploaded = True
                    break
            except Exception:  # noqa: BLE001
                pass
        if uploaded:
            break
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
    out["answered"] = _answer_screeners(page, job, profile, out)

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
                # Name the fields the FORM objects to, rather than guessing from the
                # DOM. A react-select search input reads as empty even when a value is
                # chosen (the selection lives in a sibling element), so inspecting
                # el.value to find "missing" fields is actively misleading. The form's
                # own aria-invalid / error text is the only honest source.
                try:
                    flagged = page.evaluate(r"""() => {
                      const out=[];
                      for (const e of document.querySelectorAll(
                            '[aria-invalid="true"],[class*=error],[role=alert]')) {
                        let lab='';
                        const id=e.getAttribute && e.getAttribute('id');
                        if (id){const l=document.querySelector(`label[for="${CSS.escape(id)}"]`);
                                if(l) lab=l.innerText;}
                        if(!lab && e.closest){const w=e.closest('label,div,fieldset');
                                if(w){const l2=w.querySelector('label,legend'); if(l2) lab=l2.innerText;}}
                        const t=(lab||e.innerText||'').trim().replace(/\s+/g,' ').slice(0,64);
                        if (t && !out.includes(t)) out.push(t);
                      }
                      return out.slice(0,10); }""")
                    if flagged:
                        out["flagged"] = flagged
                except Exception:  # noqa: BLE001
                    pass
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
            #
            # A form can also reject with NO new text at all (a red border, or just
            # aria-invalid), which is indistinguishable from a dead button by text
            # alone. Capture the flagged fields here too, so "nothing happened" says
            # WHY rather than leaving it to a one-off script.
            try:
                still = page.evaluate(r"""() => {
                  const out=[];
                  for (const e of document.querySelectorAll('[aria-invalid="true"]')) {
                    let lab=''; const id=e.getAttribute('id');
                    if (id){const l=document.querySelector(`label[for="${CSS.escape(id)}"]`);
                            if(l) lab=l.innerText;}
                    if(!lab && e.closest){const w=e.closest('label,div,fieldset');
                            if(w){const l2=w.querySelector('label,legend'); if(l2) lab=l2.innerText;}}
                    const t=(lab||'').trim().replace(/\s+/g,' ').slice(0,64);
                    if (t && !out.includes(t)) out.push(t);
                  }
                  return {invalid: out.slice(0,8), url: location.href}; }""")
                if still.get("invalid"):
                    out["flagged"] = still["invalid"]
                    out["action"] = "handoff"
                    out["submit_attempted"] = False
                    out["reason"] = ("form rejected silently (aria-invalid, no message); "
                                     "needs a human")
                    return out
            except Exception:  # noqa: BLE001
                pass
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
                    for f in (r.get("flagged") or [])[:6]:
                        print(f"        still invalid: {f}")
                else:
                    # nothing was submitted, so hand it straight back to the LLM
                    # operator by returning it to the approved pool
                    jobs.set_status(r["id"], "approved", expect="applying")
                    handed += 1
                    print(f"  {tag} -> operator {r['reason'][:52]}")
                    for f in (r.get("flagged") or [])[:6]:
                        print(f"        form objects to: {f}")

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
