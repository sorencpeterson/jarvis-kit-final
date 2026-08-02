#!/usr/bin/env python3
"""Full-pipeline re-scan of every submitted job (2026-07-12, [OWNER]: "fix the classifier
for the whole pipeline and re-scan all applied jobs").

The cursor-based job_replies.py only ever sees NEW mail and can never DOWNGRADE a wrong
call (its _RANK only moves forward), so a mis-fired "interview" (the "next steps" bug) or a
rejection that landed after a confirmation stays wrong forever. This replays the truth: for
every job that reached the pipeline, pull its real ATS emails, classify each against the
FULL body (job_replies only saw a 90-char snippet), and set the terminal status a human
would: a rejection is final; else the strongest signal seen wins.

Read-only against Gmail; writes only job statuses (with reason "rescan 2026-07-12"). Idempotent.

  python agents/job_rescan.py            # apply corrections
  python agents/job_rescan.py --dry      # print the diff, change nothing
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents", Path.home() / "Claude" / "gmail"):
    sys.path.insert(0, str(p))
import gmail_api  # noqa: E402
import jobs  # noqa: E402
import job_mail_patterns as jmp  # noqa: E402
import planner  # noqa: E402
from job_replies import classify_reject_reason  # noqa: E402

_INT_LLM_BUDGET = [12]   # cap the interview LLM-verify calls per run (they only fire on a
#                          regex interview hit; the whole pipeline sees at most a handful)


def _real_interview(company: str, subject: str, body: str) -> bool:
    """The regex flagged this as an interview; the LLM makes the final call because the hard
    case is CONDITIONAL/FUTURE boilerplate ("if selected we'll schedule an interview" =
    confirmation) vs a real first-touch invite ("let's grab time", "open to a quick chat").
    Regex can't tell those apart without re-breaking real soft invites. Fails OPEN (keeps the
    regex's True) whenever the LLM doesn't come back with a clean, unambiguous CONFIRMATION:
    budget exhausted, a _cli exception, empty/None output, or a garbled answer all keep the
    interview verdict (finding D -- the old code fell through to False, i.e. downgraded to
    confirmation, on exactly those cases, silently erasing the single highest-value signal
    the pipeline produces on a transient LLM hiccup). Only a clean CONFIRMATION downgrades."""
    if _INT_LLM_BUDGET[0] <= 0:
        return True
    _INT_LLM_BUDGET[0] -= 1
    try:
        out = planner._cli(
            "Classify this ATS email to a job applicant. A REAL interview invite gives a concrete "
            "next step directed at the candidate NOW. A CONFIRMATION only acknowledges the "
            "application and describes a POSSIBLE future step. The word 'interview' in a conditional "
            "or future clause is a CONFIRMATION, not an invite.\n"
            "Examples:\n"
            "- 'If a member of the hiring team feels it is a good fit we will reach out to schedule an interview' => CONFIRMATION\n"
            "- 'We will be scheduling interviews soon; you will be contacted if we wish to interview you' => CONFIRMATION\n"
            "- 'Thank you for applying, we are reviewing your qualifications' => CONFIRMATION\n"
            "- 'If you are open to a quick chat about the role, here is my calendar' => INTERVIEW\n"
            "- 'We would like to schedule a call. Please book a time using the link' => INTERVIEW\n"
            "Answer exactly one word: INTERVIEW or CONFIRMATION.\n\n"
            f"Subject: {subject}\nBody: {body[:900]}", timeout=60, feature="reply")
    except Exception:  # noqa: BLE001 -- any _cli failure fails OPEN, never downgrades silently
        return True
    if not out:
        return True
    out = out.upper()
    if "CONFIRMATION" in out and "INTERVIEW" not in out:
        return False  # clean, unambiguous downgrade
    return True  # anything else (garbled, both words, neither word) keeps the interview verdict

SUBMITTED = ("applied", "confirmed", "replied", "interview", "rejected")
# rank of the final status a company's mail implies; higher wins EXCEPT rejection is terminal
_STAGE = {"applied": 0, "confirmed": 1, "replied": 2, "interview": 3}
# generic recruiter-noreply senders whose "company" we take from the subject, not the from
_ATS_HINT = re.compile(r"greenhouse|ashby|lever|dover|workday|workable|breezy|rippling|"
                       r"recruitee|jazzhr|bamboohr|paycom|gusto|ultipro|icims|jobvite", re.I)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _company_pattern(company: str):
    """Word-boundary phrase match for `company`, built once per lookup (finding W). Raw
    substring containment on the fully-stripped name let a short/partial company name match
    inside an unrelated longer word -- a namesake collision (normalized "meta" is a substring
    of "metadata"; sibling job_replies.py's _match already learned this lesson and went
    normalized-EXACT for its own field-vs-field comparison). Here we're checking phrase-in-a-
    sentence, not field-equality, so the fix is `\\b`-anchored words: the company's own word
    sequence must appear as whole words, so "Acme" no longer matches "AcmeCorpAnalytics"."""
    words = re.findall(r"[A-Za-z0-9]+", company or "")
    if not words:
        return None
    return re.compile(r"\b" + r"\s*".join(re.escape(w) for w in words) + r"\b", re.I)


_TITLE_STOP = {"senior", "junior", "remote", "hybrid", "onsite", "full", "time", "part",
               "lead", "manager", "director", "specialist", "associate", "principal",
               "staff", "level", "based", "team", "role", "position"}


def _title_words(title: str) -> set:
    """Significant (len>=4, non-generic) words from a job title, used ONLY to disambiguate
    which of a company's several open reqs a piece of ATS mail belongs to (finding C: a
    rejection on one req must not clobber a DIFFERENT req's interview at the same company).
    Common title filler is excluded so a one-word coincidental overlap ("Manager") can't
    misattribute mail to the wrong req."""
    words = re.findall(r"[a-z]{4,}", (title or "").lower())
    return {w for w in words if w not in _TITLE_STOP}


def _company_status(company: str, max_msgs: int = 6, title: str = "",
                    sibling_titles: tuple = ()) -> tuple[str, str] | None:
    """Return (true_status, evidence) from the company's real ATS mail, or None if no mail
    found (leave the job as-is). An interview seen in this fetch always outranks a rejection
    ALSO seen in this fetch (finding C: a rejection must never clobber an interview); absent
    an interview, rejection is terminal, else the furthest stage reached. `title`, when given
    (run() only passes one when the company has 2+ open reqs), requires evidence to also
    reference that req's own significant words so a different req's mail can't be misattributed
    onto this one -- a company-wide rejection must not force "rejected" onto every job there."""
    cn = _norm(company)
    if len(cn) < 3:
        return None
    pat = _company_pattern(company)
    if pat is None:
        return None
    # Codex#7 (2026-07-15): binding on ANY overlap with this req's words misattributed mail
    # when the company's own sibling reqs SHARE a word ("Operations Manager" vs "Director of
    # Operations Strategy" both carry "operations", so one req's rejection bound to both).
    # Disambiguation must use words DISTINCTIVE to this req vs its siblings; a message that
    # only carries shared words can't be safely attributed to any one req and is skipped.
    # If the reqs' significant words are identical, nothing can ever bind -- correct: leave
    # both as-is rather than coin-flip (finding C's own rule).
    title_words = _title_words(title) if title else set()
    if title_words and sibling_titles:
        sib_words = set()
        for st in sibling_titles:
            sib_words |= _title_words(st)
        title_words -= sib_words
        if not title_words:
            # every significant word is shared with a sibling req: an empty set would fall
            # through the `if title_words` binding guard below and bind EVERYTHING -- bail
            # out instead, no mail is attributable to this req.
            return None
    try:
        q = f'("{company}") newer_than:60d category:primary OR ("{company}") newer_than:60d'
        ids = [m["id"] for m in gmail_api.search(q, max_msgs)]
    except Exception:  # noqa: BLE001
        return None
    best_stage, best_type, best_evidence = -1, None, ""
    saw_reject, reject_evidence = False, ""
    for mid in ids:
        try:
            m = gmail_api.get_message(mid)
        except Exception:  # noqa: BLE001
            continue
        subj, frm = m.get("subject", ""), m.get("from", "")
        body = (m.get("body") or m.get("snippet") or "")
        # the email must actually be about THIS company (subject or sender), not a namesake
        # collision -- word-boundary match, not raw substring containment (finding W)
        if not (pat.search(subj) or pat.search(frm)):
            continue
        # finding C: when this company has multiple open reqs, only bind evidence to a
        # message that also references THIS req's significant title words -- a generic mail
        # with none of them can't be safely attributed to any one specific req.
        if title_words:
            haystack_words = set(re.findall(r"[a-z]{4,}", f"{subj} {body}".lower()))
            if not (title_words & haystack_words):
                continue
        t = jmp.classify(frm, subj, body[:1500])   # FULL body, not the 90-char snippet
        if not t and classify_reject_reason(body):  # a classified geo/other reject the regex missed
            t = "rejection"
        # interview is high-stakes (fires prep agents, it's the key metric) and the regex
        # over-calls conditional-future boilerplate -> LLM makes the final interview call
        if t == "interview" and not _real_interview(company, subj, body):
            t = "confirmation"
        if t == "rejection":
            saw_reject = True
            reject_evidence = f"reject: {subj[:60]}"
        elif t in ("interview", "human", "confirmation"):
            st = {"interview": "interview", "human": "replied", "confirmation": "confirmed"}[t]
            if _STAGE[st] > best_stage:
                best_stage, best_type = _STAGE[st], st
                best_evidence = f"{st}: {subj[:60]}"
    # finding C: a rejection never clobbers an interview seen in this SAME fetch -- only
    # terminal when no interview signal was also present among the same evidence.
    if saw_reject and best_type != "interview":
        return "rejected", reject_evidence
    if best_type:
        return best_type, best_evidence
    return None


# CX11: a rescan must never downgrade an authoritative human/terminal status back down to
# "confirmed" -- confirmed is the weakest signal this scan can produce (a bare ATS auto-ack),
# so replied/interview/rejected all outrank it and stay put even if a fresh (thinner) fetch
# only turns up a confirmation-shaped email for that company.
_AUTHORITATIVE = ("replied", "interview", "rejected")


def run(dry: bool = False) -> dict:
    all_jobs = jobs.load_jobs()
    submitted = [j for j in all_jobs if j.get("status") in SUBMITTED]
    # group by company for lookup planning; a company with 2+ open reqs gets each job its OWN
    # lookup below (finding C) instead of one company-wide verdict blindly applied to all --
    # a rejection on one req must never clobber a different req's interview at the same company.
    by_co: dict[str, list] = {}
    for j in submitted:
        by_co.setdefault(_norm(j.get("company")), []).append(j)
    changes, checked = [], 0
    for cn, group in by_co.items():
        company = group[0].get("company") or ""
        checked += 1
        multi_req = len(group) > 1
        for j in group:
            title = j.get("title", "") if multi_req else ""
            sibs = tuple(x.get("title", "") for x in group if x is not j) if multi_req else ()
            res = _company_status(company, title=title, sibling_titles=sibs)
            if not res:
                continue
            true_status, evidence = res
            cur = j.get("status")
            if cur == true_status:
                continue
            # CX11: never let a rescan downgrade an authoritative status to "confirmed"
            if cur in _AUTHORITATIVE and true_status == "confirmed":
                continue
            changes.append((company, j.get("title", "")[:40], cur, true_status, evidence))
            if not dry:
                # R1#4 (regression, post-17bf56c): `cur` is a snapshot from the SINGLE
                # jobs.load_jobs() call at the top of run(), taken before this whole
                # per-company scan (Gmail search + an LLM interview-verify call per company)
                # ran -- by the time we reach company #N's write, cur can be long stale. A
                # concurrent job_replies run can have advanced this exact job's status in the
                # meantime; expect=cur makes the write atomic against what THIS scan actually
                # observed, so a slow rescan can never clobber a status job_replies (or
                # another writer) already moved on to.
                jobs.set_status(j["id"], true_status, f"rescan 2026-07-12: {evidence}", expect=cur)
    # finding V: a promotion to interview gets the SAME treatment job_replies.py gives one --
    # push notify, feed line, and the prep pack -- never during --dry (no real change happened).
    interview_promos = [c for c in changes if c[3] == "interview"]
    if interview_promos and not dry:
        cos = [c[0] for c in interview_promos]
        planner.notify("Interview request!", "Respond fast: " + ", ".join(cos[:4]), tags="tada,briefcase")
        # feed line is INDEPENDENT of the push: ntfy down must never mean an interview
        # goes silent (same discipline job_replies.py uses)
        try:
            planner.feed_add("jobs", "INTERVIEW: " + ", ".join(cos[:4]) + " — respond fast")
        except Exception:  # noqa: BLE001
            pass
        try:
            import interview_prep
            interview_prep.run()
        except Exception:  # noqa: BLE001
            pass
    return {"companies_checked": checked, "changes": changes}


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    r = run(dry=dry)
    print(f"{'[DRY] ' if dry else ''}checked {r['companies_checked']} companies, "
          f"{len(r['changes'])} status correction(s):")
    from collections import Counter
    tally = Counter((c[2], c[3]) for c in r["changes"])
    for (frm, to), n in tally.most_common():
        print(f"  {frm} -> {to}: {n}")
    for co, title, frm, to, ev in r["changes"][:40]:
        print(f"    {co[:24]:24} {frm:9}->{to:9} | {ev[:50]}")
