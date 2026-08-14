#!/usr/bin/env python3
"""Settle which applications actually landed, using the employer's own confirmation mail.

    .venv/bin/python agents/job_verify.py            verify against the mailbox
    .venv/bin/python agents/job_verify.py --report   list the pile, no mail needed
    .venv/bin/python agents/job_verify.py --days 30  how far back to search

THE PROBLEM THIS CLOSES. The system counts applications it cannot prove it sent. A
job reaches `needs_verify()` three ways: the operator died mid-flight
(inflight_timeout), it capped out after possibly submitting (attempt_cap), or it
reported success without quoting any confirmation (unconfirmed). All three mean the
same thing, which is that nobody knows. That pile only grows, and it poisons two
decisions at once: treating those as not-applied risks applying to the same employer
twice, and treating them as applied risks abandoning a live opportunity.

The operator's own report cannot settle it, because the operator is exactly what is
in doubt. The employer's confirmation email can: it is external, it is authoritative,
and it costs nothing to read.

This runs on pure pattern matching over subject lines and senders. Zero LLM calls.

WHAT IT WILL AND WILL NOT DO. It only ever moves a job toward the truth the mailbox
supports:

  unconfirmed applied  + confirmation found -> applied, with the subject quoted
  inflight_timeout     + confirmation found -> applied  (a REAL submission recovered)
  attempt_cap          + confirmation found -> applied
  anything             + nothing found      -> left exactly as it is, and listed

It never marks a job applied on absence of evidence, and it never touches a job that
has already moved on to interview, rejected, replied or confirmed: those came from a
human and outrank anything found here.

NOT ALL EMPLOYERS SEND CONFIRMATIONS. A job with no confirmation email is not proven
unsent; it is unproven either way, which is what the report at the end says. Check
those in the ATS by hand. That is a much shorter list than the whole pile.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents",
          Path(os.environ.get("GMAIL_LIB") or (ROOT / "gmail"))):
    sys.path.insert(0, str(p))

import jobs  # noqa: E402

# Subject lines an ATS sends on receipt. Deliberately narrow: a false positive here
# marks an unsent application as sent, which is the one error this tool must not
# make. Rejections and interview invitations are excluded on purpose -- they are
# real signals but job_replies.py owns them, and both already imply receipt anyway.
_CONFIRM_SUBJECT = re.compile(
    r"(thank you for (your )?(applying|application|interest)"
    r"|thanks for applying"
    r"|application (received|submitted|confirmation)"
    r"|we('ve| have) received your application"
    # "your application ... received" only with EXPLICIT receipt language. A bare
    # "your application to X" also fits "Update on your application to X", which is
    # a rejection or a status change: job_replies.py owns those, and reading one as
    # a receipt would be this tool making the exact error it exists to prevent.
    r"|your application\b.{0,40}\b(has been |was |is )?received"
    r"|application to .{1,60} received)", re.I)

# Senders that only ever mail about applications. Used to widen matching slightly
# when a subject is generic ("Your application").
_ATS_SENDERS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
                "myworkday.com", "icims.com", "smartrecruiters.com", "breezy.hr",
                "rippling.com", "bamboohr.com", "jazzhr.com", "recruitee.com",
                "paylocity.com", "successfactors.com", "taleo.net")

_NOISE = re.compile(r"\b(job alert|jobs? for you|recommended|new jobs|"
                    r"unsubscribe from job|weekly digest|newsletter)\b", re.I)


def is_confirmation(subject: str, sender: str = "") -> bool:
    """True only for mail that says an application was RECEIVED.

    A job-board digest advertising roles is the common false positive and is
    excluded first: those subjects often contain 'your application' verbatim.
    """
    s = (subject or "").strip()
    if not s or _NOISE.search(s):
        return False
    if _CONFIRM_SUBJECT.search(s):
        return True
    # generic subject, but from a system that only mails about applications
    low = (sender or "").lower()
    if any(d in low for d in _ATS_SENDERS):
        return bool(re.search(r"\bapplication\b|\bapplied\b", s, re.I))
    return False


def _norm_co(s: str) -> str:
    """Company key for matching mail against a job record. Mirrors jobs._conorm so a
    match here means the same thing a dedupe there means."""
    return jobs._conorm(s or "")


def match_company(text: str, companies: list[str]) -> str:
    """Which of these companies this mail is about, or ''. Longest name first so
    'Acme Health' wins over 'Acme' when both are in the pile."""
    hay = _norm_co(text)
    if not hay:
        return ""
    best = ""
    for co in sorted(companies, key=len, reverse=True):
        k = _norm_co(co)
        if k and len(k) >= 4 and k in hay:
            if len(co) > len(best):
                best = co
    return best


# Reasons that mean "we do not know whether this was submitted".
_UNCERTAIN_SKIP = ("inflight_timeout", "attempt_cap")


def _pile() -> list[dict]:
    """Every job whose submission state is unknown, computed HERE rather than taken
    from jobs.needs_verify().

    That function exists in more than one shape across installs: some return only
    'skipped' rows, some omit the status field this module needs for its
    compare-and-swap. Depending on it made this agent silently verify nothing on a
    tree whose version differed. Scanning load_jobs() directly is a few lines and
    works everywhere, so the coupling is not worth it.
    """
    out = []
    for x in jobs.load_jobs():
        st, r = x.get("status"), (x.get("reason") or "").lower()
        uncertain = (
            (st == "skipped" and any(r.startswith(w) for w in _UNCERTAIN_SKIP))
            or (st == "applied" and r.startswith("unconfirmed"))
        )
        if uncertain:
            out.append({"id": x.get("id"), "title": x.get("title"),
                        "company": x.get("company"), "status": st,
                        "apply_url": x.get("apply_url"), "source": x.get("source"),
                        "reason": x.get("reason"),
                        "applying_at": x.get("applying_at")})
    out.sort(key=lambda j: j.get("applying_at") or "", reverse=True)
    return out


def _fetch(days: int) -> list[dict]:
    """[{subject, sender, snippet}] of recent mail. Empty when Gmail is unavailable."""
    try:
        import gmail_api
    except ImportError:
        print("job_verify: gmail library not importable; --report still works")
        return []
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y/%m/%d")
    try:
        ids = [m["id"] for m in gmail_api.search(f"after:{since}", 400)]
    except Exception as e:  # noqa: BLE001
        print(f"job_verify: mailbox unreachable ({type(e).__name__}). "
              "Re-auth Gmail, then re-run. --report works without it.")
        return []
    out = []
    try:
        for m in gmail_api.get_messages_metadata(ids, fields=("From", "Subject")):
            h = {k.lower(): v for k, v in (m.get("headers") or {}).items()}
            out.append({"subject": h.get("subject", ""), "sender": h.get("from", ""),
                        "snippet": m.get("snippet", "")})
    except Exception as e:  # noqa: BLE001
        print(f"job_verify: could not read message metadata ({type(e).__name__})")
    return out


def run(days: int = 30, report_only: bool = False) -> int:
    pile = _pile()
    if not pile:
        print("job_verify: nothing unverified. Every application is accounted for.")
        return 0

    print(f"job_verify: {len(pile)} application(s) whose fate is unknown")
    if report_only:
        for j in pile:
            print(f"  {(j.get('company') or '?')[:30]:<30} {j.get('status'):<8} "
                  f"{(j.get('reason') or '')[:44]}")
            print(f"      {j.get('apply_url') or ''}")
        print("\n  Check these in the ATS, or re-run without --report to search mail.")
        return 0

    mail = _fetch(days)
    if not mail:
        print("  No mail available. Falling back to a report; nothing was changed.")
        return run(days, report_only=True)

    confirms = [m for m in mail if is_confirmation(m["subject"], m["sender"])]
    print(f"  searched {len(mail)} message(s), {len(confirms)} look like confirmations")

    companies = [j.get("company") or "" for j in pile]
    by_co: dict[str, str] = {}
    for m in confirms:
        co = match_company(f"{m['subject']} {m['sender']} {m['snippet']}", companies)
        if co and co not in by_co:
            by_co[co] = m["subject"][:90]

    recovered = confirmed = 0
    for j in pile:
        subj = by_co.get(j.get("company") or "")
        if not subj:
            continue
        was = j.get("status")
        # expect= keeps this a compare-and-swap: a status that moved since _pile()
        # read it (a human marking an interview, say) always wins over this write.
        res = jobs.set_status(j["id"], "applied", f"confirm: {subj}", expect=was)
        if not res or res.get("status") != "applied":
            continue
        if was == "skipped":
            recovered += 1
            print(f"  RECOVERED  {(j.get('company') or '?')[:28]:<28} was {j.get('reason','')[:22]}")
        else:
            confirmed += 1
            print(f"  confirmed  {(j.get('company') or '?')[:28]:<28} {subj[:40]}")

    left = [j for j in _pile()]
    print(f"\njob_verify: {confirmed} confirmed, {recovered} recovered from skipped, "
          f"{len(left)} still unproven")
    if left:
        print("  Unproven is not the same as unsent: not every employer sends a")
        print("  confirmation. Check these by hand:")
        for j in left[:12]:
            print(f"    {(j.get('company') or '?')[:30]:<30} {j.get('apply_url') or ''}")
        if len(left) > 12:
            print(f"    ... {len(left) - 12} more")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify applications against employer mail")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--report", action="store_true", help="list the pile, no mail")
    a = ap.parse_args()
    return run(days=a.days, report_only=a.report)


if __name__ == "__main__":
    raise SystemExit(main())
