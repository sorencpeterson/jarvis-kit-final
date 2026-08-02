#!/usr/bin/env python3
"""Job reply detection: scan Gmail for responses to applications, match to jobs.jsonl,
flip status (applied -> confirmed/replied/interview/rejected), push on anything human.

The job pipeline's missing bottom half: 135 apps out, zero inbound signal until now.
Read-only on Gmail; only writes job statuses locally. Reuses gmail_api (OAuth already set up).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents", Path.home() / "Claude" / "gmail"):
    sys.path.insert(0, str(p))
import planner  # noqa: E402
import jobs  # noqa: E402
import gmail_api  # noqa: E402
import job_mail_patterns  # noqa: E402  (D224/225: regex fast-path, checked before the LLM call)

SEEN = ROOT / "store" / "job_mail_seen.json"
REJECT_BODIES = ROOT / "store" / "rejection_bodies.jsonl"

# ---- D-lane reject-intel: why a rejection happened, from a fixed taxonomy ----
# Motivation (2026-07-07 audit): 6 rejections all carry reason="gmail:rejection",
# a useless constant. But 3 of 6 are STATE-RESTRICTION auto-rejects triggered by
# [OWNER]'s South Dakota domicile (which he's chosen to keep), so those will keep
# coming and the system should LEARN them (and eventually skip / blocklist).
# Cheapest impl: a regex pass first (the state-restriction phrasings are highly
# templated), and only fall through to one capped LLM call on a regex miss.
REJECT_REASONS = ("state_restriction", "overqualified", "underqualified",
                  "position_filled", "competitive_no_reason", "ghost")

# Ordered most-specific-first: the first family that matches wins. state_restriction
# is checked FIRST because a geo-reject often ALSO carries generic "not moving
# forward" boilerplate, and the geo reason is the load-bearing one (it's structural,
# it recurs, it drives the blocklist). Patterns are deliberately broad on the geo
# family (that's the one we most want to catch) and tight on the rest.
_REJECT_PATTERNS: list = [
    ("state_restriction", re.compile(
        r"(only able to hire in|only hire in|hire (?:in|within) (?:the )?states|"
        r"states listed|not located in a region|registered to employ|"
        r"must reside in|unable to offer employment outside|"
        r"require(?:s|d)? candidates to be located|located in specific (?:locales|states|regions)|"
        r"can (?:only|only ever) (?:employ|hire)|"
        r"not (?:currently )?(?:able to |registered to )?(?:hire|employ) (?:in|outside)|"
        r"where (?:we|[a-z]+) (?:is|are) registered to employ)", re.I)),
    ("position_filled", re.compile(
        r"(position (?:has been |was )?filled|role (?:has been |was )?filled|"
        r"filled (?:this |the )?(?:position|role)|no longer accepting|"
        r"position (?:has been |is now )?closed)", re.I)),
    ("overqualified", re.compile(
        r"(over[\s-]?qualified|more senior than|beyond (?:the )?scope of (?:this )?role|"
        r"your (?:experience|background) exceeds)", re.I)),
    ("underqualified", re.compile(
        r"(do(?:es)?n['’]?t (?:quite )?(?:meet|match)|not (?:a )?(?:strong )?(?:enough )?match "
        r"for (?:the )?(?:required|specific)|lack(?:ing|s)? (?:the )?(?:required|specific)|"
        r"looking for (?:someone with|candidates with) more|"
        r"seeking (?:a )?candidate with (?:more|additional))", re.I)),
    # "competitive_no_reason" is the templated soft-no with no stated cause
    # ("decided not to move forward", "other candidates", "much consideration").
    ("competitive_no_reason", re.compile(
        r"(decided (?:not )?to (?:move forward|proceed)|not (?:be )?mov(?:e|ing) forward|"
        r"pursue other candidates|other (?:candidates|applicants)|"
        r"(?:after|following) (?:much |careful )?(?:consideration|review)|"
        r"will not be (?:moving|proceeding)|not selected|"
        r"decided to (?:go|move) (?:in another|a different) direction)", re.I)),
]


def classify_reject_reason(body: str) -> str:
    """Classify a rejection email body into REJECT_REASONS by regex only.
    Returns "" (empty) on no confident match so the caller can fall through to
    a single capped LLM call. Never guesses: an empty return is safe (means
    'ask the LLM or leave it unclassified'), a wrong guess pollutes the
    learning loop that blocklists on state_restriction."""
    text = body or ""
    for reason, pat in _REJECT_PATTERNS:
        if pat.search(text):
            return reason
    return ""


_LLM_REASON = ("Classify this job REJECTION email into EXACTLY one label and reply with ONLY "
               "that word, nothing else: state_restriction (they can only hire in certain "
               "states/regions and the applicant isn't in one) | overqualified | underqualified | "
               "position_filled | competitive_no_reason (a generic 'went with other candidates' "
               "no-reason no) | ghost. EMAIL:\n")


def _reason_llm(body: str) -> str:
    """Single capped LLM fallback, used ONLY on a regex miss. Returns a valid
    taxonomy label or "" if the model's answer isn't one of them (never trust a
    freeform answer into the blocklist loop)."""
    try:
        out = (planner._cli(_LLM_REASON + (body or "")[:2000], timeout=60, feature="default") or "").strip().lower()
    except Exception:  # noqa: BLE001
        return ""
    for r in REJECT_REASONS:
        if r in out:
            return r
    return ""


def _persist_reject_body(job_id: str, company: str, reason: str, body: str) -> None:
    """Append the full (capped) rejection body to store/rejection_bodies.jsonl,
    keyed by job_id, so the taxonomy can be retuned later WITHOUT re-hitting
    Gmail. flocked (same lock discipline as jobs._save). Capped at ~4KB so one
    fat HTML-derived body can't bloat the store."""
    from store_lib import _flock
    rec = {"job_id": job_id, "company": company, "reject_reason": reason,
           "at": jobs.now_iso(), "body": (body or "")[:4000]}
    REJECT_BODIES.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _flock(REJECT_BODIES), REJECT_BODIES.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


ATS_QUERY = ("newer_than:30d (from:ashbyhq.com OR from:hire.lever.co OR from:greenhouse.io OR "
             "from:workablemail.com OR from:rippling.com OR from:careerplug.com OR from:jazz.co OR "
             "from:breezy.hr OR from:applytojob.com OR from:myworkday.com OR subject:application OR "
             "subject:interview OR subject:\"next step\")")

CLASSIFY = """Classify each job-application email sent TO [OWNER] (a job applicant). For each return:
- company: the hiring company's name (best guess from the sender or subject)
- type: exactly one of
    confirmation  (automated "thanks for applying / we received your application")
    rejection     (not moving forward, position filled, decided to pursue others)
    interview     (they want to schedule a call/interview or move to next steps)
    human         (a real person wrote asking a question or for info, not automated)
    other         (unrelated / newsletter / job alert)
Return ONLY a JSON array in the same order: [{"company":"..","type":".."}]
EMAILS:
"""

_RANK = {"applied": 0, "confirmed": 1, "rejected": 1, "replied": 2, "interview": 3}


def _seen() -> set:
    try:
        return set(json.loads(SEEN.read_text()))
    except (OSError, json.JSONDecodeError):
        return set()


def _mark_seen(ids):
    keep = sorted(_seen() | set(ids))[-800:]
    SEEN.write_text(json.dumps(keep))


def _applied() -> list:
    return [j for j in jobs.load_jobs() if j.get("status") in ("applied", "confirmed", "replied", "interview")]


def _norm_co(s: str) -> str:
    # strip only true LEGAL suffixes so "Acme, Inc." == "Acme". Do NOT strip
    # group/technologies/labs/holdings: those distinguish real companies ("Meta Labs" vs
    # "Meta Technologies" must not both collapse to "meta" — red-team #5 collision).
    s = (s or "").lower()
    s = re.sub(r"\b(inc|llc|ltd|corp|incorporated|limited)\b", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def _match(company: str, applied: list):
    """Match an email's guessed company to an applied job. Tightened 2026-07-07 (audit S3):
    the old substring/first-word match let a crafted email flip the WRONG job (any two
    companies sharing a first word collided). Now: normalized-exact only, and AMBIGUOUS
    matches (2+ applied jobs match) return None rather than guessing — a wrong flip to
    'rejected'/'interview' is worse than a missed one."""
    c = _norm_co(company)
    if not c or len(c) < 3:
        return None
    hits = [j for j in applied if _norm_co(j.get("company")) == c]
    if len(hits) == 1:
        return hits[0]
    if hits:
        return None  # 2+ exact matches: ambiguous, never guess
    # Codex#8 (2026-07-15): second pass with jobs._conorm, the employer-dedupe key, which
    # also strips trailing co/company/group/holdings-style suffixes this module's _norm_co
    # deliberately keeps. A rejection signed "Acme" must still flip the job stored as
    # "Acme Co". Collision safety holds: the same only-if-unique rule applies, so "Meta
    # Labs" vs "Meta Group" both collapsing produces 2 hits and returns None, not a guess.
    c2 = jobs._conorm(company)
    if not c2 or len(c2) < 3:
        return None
    hits2 = [j for j in applied if jobs._conorm(j.get("company")) == c2]
    return hits2[0] if len(hits2) == 1 else None


def _current_status(job_id: str, fallback: str) -> str:
    """Fresh status for job_id straight from the store (R2-22): the pre-scan `applied` list is
    a snapshot taken once at the top of run(); comparing every message in a 30-message batch
    against that same snapshot let a LATER message downgrade a status an EARLIER message in
    the SAME run just wrote (interview -> confirmed), because the stale in-memory dict never
    saw its own write. Re-reading here is the fix -- one extra jobs.jsonl parse per message,
    bounded to this run's batch size, not a hot path."""
    for j in jobs.load_jobs():
        if j.get("id") == job_id:
            return j.get("status", fallback)
    return fallback


def _set_status_with_extra(job_id: str, status: str, reason: str, extra: dict,
                           expect: str | set[str] | None = None) -> dict | None:
    """Same locked read-modify-append jobs.set_status() does, but folds `extra` (rejected_at /
    rejection_snippet / reject_reason) into the SAME append instead of a second _save() call
    (R2-28): two separate appends meant a crash between them lost the reason/timing metadata,
    and a concurrent status write landing between them got silently reverted by the second
    append's stale base record. status is always "rejected" at every call site here, so
    jobs.set_status's own hardcoded blocklist (which only ever blocks "applied"/"skipped")
    never applied to this path -- but see `expect` below.

    `expect` (regression fix, post-17bf56c, R1#5): mirrors jobs.set_status's own CAS param.
    The classify-then-write in run() can sit behind a capped LLM call (_reason_llm, up to
    60s) between reading the job's current status and reaching this write -- a real gap a
    concurrent job_rescan/job_replies run can land a newer status in. Passing the status this
    caller OBSERVED as `expect` makes the whole check-then-write atomic (one lock): if the
    job has already moved on by write time, this becomes a safe no-op instead of clobbering
    whatever the other, more-recent writer set."""
    from store_lib import _flock
    with _flock(jobs.QUEUE):
        rec = next((x for x in jobs.load_jobs() if x.get("id") == job_id), None)
        if not rec:
            return None
        if expect is not None:
            allowed = {expect} if isinstance(expect, str) else set(expect)
            if rec.get("status") not in allowed:
                return rec
        rec["status"] = status
        if reason:
            rec["reason"] = reason
        rec.update(extra)
        jobs.QUEUE.parent.mkdir(parents=True, exist_ok=True)
        with jobs.QUEUE.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec


def run():
    applied = _applied()
    if not applied:
        print("no applied jobs to match against")
        return []
    ids = [m["id"] for m in gmail_api.search(ATS_QUERY, 50)]
    new_ids = [i for i in ids if i not in _seen()]
    if not new_ids:
        print("no new job mail")
        return []
    # CX13 + R2-27: fetch is per-message error-isolated -- one deleted/inaccessible Gmail id
    # must never abort the whole scan before any interview is processed. msgs/msg_ids stay
    # index-aligned (only successful fetches are kept, re-indexed from 0).
    msgs, msg_ids = [], []
    for mid in new_ids[:30]:
        try:
            msgs.append(gmail_api.get_message(mid))
            msg_ids.append(mid)
        except Exception:  # noqa: BLE001
            continue

    # D224/225: regex fast-path FIRST -- resolves the type (confirmation/
    # rejection/interview) for cleanly-templated ATS mail with zero LLM cost.
    # Company matching still needs the LLM's best-guess extraction (sender
    # display names are inconsistent across ATSes), so every message still
    # goes into the LLM call for company, but the ones the regex already
    # resolved skip re-deriving `type` from the LLM's classification --
    # if the two ever disagree, the regex wins for type (it's a direct
    # pattern match on the actual ATS's own boilerplate, not a guess) while
    # company still comes from the LLM either way.
    fast_type = {}
    for i, m in enumerate(msgs):
        try:
            t = job_mail_patterns.classify(m.get("from", ""), m.get("subject", ""), m.get("snippet", ""))
        except Exception:  # noqa: BLE001
            t = None
        if t:
            fast_type[i] = t

    listing = "\n".join(
        f"{i}. From {m.get('from', '')[:44]} | Subj: {m.get('subject', '')[:64]} | {m.get('snippet', '')[:90]}"
        for i, m in enumerate(msgs))
    try:
        data = planner._extract_json(planner._cli(CLASSIFY + listing, timeout=120)) or []
    except Exception:  # noqa: BLE001 -- a transient LLM failure must not crash the run; any
        # message without a regex fast-path hit simply stays unresolved below and retries
        # next pass instead of being silently marked seen (CX13).
        data = []
    flips, pushes = [], []
    resolved_ids = []  # CX13/R2-27: only ids that reached a confident outcome are marked
    # seen -- a failed classification or an unmatched company must retry next run, not vanish.
    reason_llm_budget = 4  # cap the LLM reason-fallback: only fires on a regex MISS,
    # and at most this many per run (a batch of all-novel rejections shouldn't fan out
    # 30 extra LLM calls; the regex resolves the templated geo family for free anyway).
    reason_tally = {}  # reject_reason -> count, for the run summary line
    for i, m in enumerate(msgs):
        mid = msg_ids[i]
        try:
            has_llm_entry = i < len(data)
            d = data[i] if has_llm_entry else {}
            typ = fast_type.get(i) or (d.get("type") if has_llm_entry else None)
            if not typ:
                # neither the regex fast-path nor this run's LLM batch produced a verdict for
                # this message (LLM call failed, or its array came back short) -- unresolved,
                # leave unseen so it retries (CX13).
                continue
            # INTERVIEW needs LLM corroboration (2026-07-12): the regex over-calls conditional/
            # future boilerplate ("if selected we'll schedule an interview") as interview, which
            # then fires the prep agents and inflates the metric. For interview ONLY, if the LLM
            # (which saw the same mail and is better at the nuance) disagrees, defer to it.
            # Confirmation/rejection stay regex-authoritative -- the regex is reliable there.
            if typ == "interview" and d.get("type") and d.get("type") != "interview":
                # Codex#6 (2026-07-15): the batch LLM above saw only ~90 snippet chars per
                # message; letting that sliver-informed verdict downgrade a regex interview
                # hit lost real invites whose scheduling language sat past the snippet.
                # Before deferring, re-check against the FULL body (fires only on the rare
                # regex-vs-LLM disagreement, so the extra call is ~free). If the full-body
                # check errors, KEEP the regex interview verdict: a false prep fire is
                # cheap, a silently dropped real invite is a lost job.
                try:
                    body = (m.get("body") or m.get("snippet") or "")[:4000]
                    v = planner._extract_json(planner._cli(
                        "A pattern-match flagged this email to a job applicant as an "
                        "interview invite. Read the FULL email. Return ONLY JSON "
                        '{"type":"interview"} if it invites him to schedule a call, '
                        "interview, assessment, or concrete next step; otherwise "
                        '{"type":"confirmation"|"rejection"|"human"|"other"}.\n'
                        f"From: {m.get('from', '')}\nSubject: {m.get('subject', '')}\n{body}",
                        timeout=90)) or {}
                    if isinstance(v, list):
                        v = v[0] if v and isinstance(v[0], dict) else {}
                    if v.get("type") and v.get("type") != "interview":
                        typ = v["type"]
                except Exception:  # noqa: BLE001
                    pass
            # D-lane reject-intel detection fix (the mis-marked Airship record class):
            # a geo-rejection ("we can only hire in the states listed", "not registered
            # to employ in your region") routinely carries a "thank you for applying"
            # footer, and both the regex fast-path and a terse LLM answer can score it
            # as a plain CONFIRMATION -- which is how grnhse___airship___4900516101
            # got stuck at status=confirmed despite being a geo-reject. State-restriction
            # language NEVER appears in a genuine confirmation, so if the body carries it
            # we upgrade confirmation/other -> rejection here (a general rule keyed on the
            # taxonomy regex, not a one-off), so the record is re-detected correctly on a
            # rescan and BUILD 1's classifier then tags reject_reason=state_restriction.
            _body_full = m.get("body") or m.get("snippet") or ""
            if typ in ("confirmation", "other") and classify_reject_reason(_body_full) == "state_restriction":
                typ = "rejection"
            newstat = {"rejection": "rejected", "interview": "interview",
                       "human": "replied", "confirmation": "confirmed"}.get(typ)
            if not newstat:
                resolved_ids.append(mid)  # confident "other"/irrelevant verdict -- nothing
                # left to retry, safe to mark seen
                continue
            job = _match(d.get("company"), applied)
            if not job:
                # classification succeeded but no applied job matched this company -- leave
                # unseen so a later run (better data, a fixed match) can still catch it (CX13)
                continue
            # R2-22: re-read the CURRENT status fresh, not the pre-scan snapshot -- a message
            # earlier in THIS SAME batch may have just promoted this job (e.g. to interview);
            # comparing against the stale snapshot let a later, weaker message downgrade it.
            cur = _current_status(job["id"], job.get("status", "applied"))
            # only move forward (interview>replied>confirmed>applied); rejection is always recorded
            if newstat != "rejected" and _RANK.get(newstat, 0) <= _RANK.get(cur, 0):
                # deliberate no-op: never downgrade. BUT a genuine interview/human follow-up on an
                # already-interview thread (a 2nd-round invite, a scheduling/availability request)
                # still needs to surface -- otherwise interview_followup later reports "no word" on
                # a thread that is actively moving (Codex end-to-end pass, 2026-07-14). Notify via
                # `pushes` (a distinct "follow-up" suffix so it flows into the generic nudge, not
                # the first-time "Interview request!" push, and does NOT re-trigger interview_prep).
                if typ in ("interview", "human"):
                    pushes.append(f"{job.get('company')} (follow-up)")
                resolved_ids.append(mid)  # a real, deliberate no-op -- resolved, not a failure
                continue
            src = "regex" if i in fast_type else "gmail"
            # D225: rejected_at timestamp + reason-keyword capture, so funnel
            # truth (D301 weekly analytics) can report real time-to-rejection
            # and rejection-language patterns, not just a bare status flip.
            extra = {}
            if newstat == "rejected":
                extra["rejected_at"] = jobs.now_iso()
                extra["rejection_snippet"] = (m.get("snippet") or "")[:200]
                # D-lane reject-intel: read the FULL body (already fetched by
                # get_message above; today only the [:200] snippet was kept and the
                # body discarded), classify a reason from the fixed taxonomy, and
                # persist the body so the taxonomy can be retuned without re-hitting
                # Gmail. Regex first (free), one capped LLM call only on a miss.
                body = _body_full
                reason = classify_reject_reason(body)
                if not reason and reason_llm_budget > 0:
                    reason = _reason_llm(body)
                    reason_llm_budget -= 1
                if reason:
                    extra["reject_reason"] = reason
                    reason_tally[reason] = reason_tally.get(reason, 0) + 1
                _persist_reject_body(job["id"], job.get("company"), reason, body)
            if extra:
                # R2-28: status + metadata written as ONE record, not two separate appends.
                # expect=cur (R1#5): atomic check-and-write against the status THIS message
                # observed, so a slow reject-reason LLM call can't clobber a fresher status.
                rec = _set_status_with_extra(job["id"], newstat, f"{src}:{typ}", extra, expect=cur)
            else:
                rec = jobs.set_status(job["id"], newstat, reason=f"{src}:{typ}", expect=cur)
            # The expect=cur CAS silently NO-OPs when the status changed under us between the
            # _current_status() read (outside the lock) and this locked write -- a concurrent
            # scan, an apply callback, or the 2h inflight sweep. It returns the UNCHANGED record.
            # If the write was dropped, DON'T mark the email seen or notify: leave it unseen so
            # the next scan retries with a fresh cur. Otherwise a real interview whose promotion
            # lost that race is consumed-as-seen and lost forever (Codex end-to-end pass +
            # Claude's own read, both flagged this, 2026-07-14).
            if not rec or rec.get("status") != newstat:
                continue
            flips.append(f"{job.get('company')}->{newstat}")
            if typ in ("interview", "human"):
                pushes.append(f"{job.get('company')} ({typ})")
            resolved_ids.append(mid)
        except Exception:  # noqa: BLE001 -- one message's processing error must never abort
            # the batch or the rest of the scan (R2-27); leave it unseen so it retries.
            continue
    _mark_seen(resolved_ids)  # only ids that reached a confident outcome (CX13/R2-27); the
    # D226: interview-invite fast path -- push IMMEDIATELY and distinctly for
    # interviews (not lumped into the generic "need you" line), since this is
    # the highest-value signal the whole pipeline produces.
    interview_flips = [f for f in flips if f.endswith("->interview")]
    if interview_flips:
        cos = [f.split("->")[0] for f in interview_flips]
        planner.notify("Interview request!", "Respond fast: " + ", ".join(cos[:4]), tags="tada,briefcase")
        # feed line is INDEPENDENT of the push: ntfy down must never mean an interview
        # goes silent (same class as the agreement-signed fix — 2026-07-07 audit H2)
        try:
            planner.feed_add("jobs", "INTERVIEW: " + ", ".join(cos[:4]) + " — respond fast")
        except Exception:  # noqa: BLE001
            pass
    other_pushes = [p for p in pushes if not p.endswith("(interview)")]
    if other_pushes:
        planner.notify("Job replies", f"{len(other_pushes)} need you: " + ", ".join(other_pushes[:4]),
                       tags="briefcase")
    if interview_flips:   # a job became an interview -> auto-build the prep pack
        try:
            import interview_prep
            interview_prep.run()
        except Exception:  # noqa: BLE001
            pass
    # Learning loop: a state_restriction rejection means this employer structurally
    # cannot hire an SD resident ([OWNER]'s chosen domicile), this month or next, so
    # rebuild the auto-blocklist NOW to block a repeat application immediately. The
    # rebuild reads jobs.jsonl (which now carries the fresh reject_reason we just
    # wrote), so it picks up the new geo-rejects. Best-effort: a blocklist hiccup
    # must never sink the reply scan.
    if reason_tally.get("state_restriction"):
        try:
            import job_fit_signals
            job_fit_signals.rebuild_auto_blocklist()
        except Exception:  # noqa: BLE001
            pass
    fast_n = sum(1 for i in range(len(msgs)) if i in fast_type)
    reason_str = (" reasons=" + ", ".join(f"{k}:{v}" for k, v in sorted(reason_tally.items()))) if reason_tally else ""
    print(f"scanned {len(msgs)} mails ({fast_n} regex fast-path, {len(msgs)-fast_n} LLM), "
          f"updated {len(flips)}: {flips}{reason_str}")
    return flips


if __name__ == "__main__":
    run()
