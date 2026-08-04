#!/usr/bin/env python3
"""C214 reactivation lane — segments WARM-HITLIST.csv tier=2 repliers per
business-library/campaigns/segment-plans.md (PRICE / TIMING / INTEREST, by what they
actually replied about) and stages a demo batch of drafts into the ONE approve queue
(replies.jsonl), for the TOP 10 by recency ONLY.

Explicitly scoped per the mission brief: this does NOT touch the 423-scale reactivation
list. That volume lives in the GHL DBR workflow (business-library/campaigns/
warm-reactivation-423.md is its copy, built paused, another fleet's lane) and its
publish stays entirely under [OWNER]'s control. This module is a small, honest
DEMONSTRATION of the segmentation logic segment-plans.md describes, sized at exactly
10 contacts max, every draft landing in his normal one-click approve queue like
everything else reply_watch.py produces. It never enrolls anyone in a GHL workflow
and never sends anything.

Segmentation signal gap, documented honestly: segment-plans.md's tagging mechanics
(step 2) call for "the 423-repliers export with ORIGINAL REPLY TEXT intact" to keyword-
match price/timing/interest signal. WARM-HITLIST.csv's `tags` column is campaign/source
metadata (e.g. "inst reply", "men-clinic", "aoa-men"), not reply body text, so it can't
directly answer "what did they say." This module's real() classification pulls the
actual reply text three ways, in order, and only when none exist does a contact land in
the honest "unclassified" bucket segment-plans.md itself calls for on ambiguous cases
("flagged for [OWNER]'s manual call, not auto-assigned"):
  1. store/replies.jsonl -- a real reply_watch-drafted record already exists for them
     (matched by name, since WARM-HITLIST rows carry no contact_id)
  2. a live GHL conversation lookup + convo_context.fetch_context() (capped, see
     REACTIVATION_GHL_LOOKUP_CAP below -- this is a genuine per-contact API call, so
     it's bounded the same way reply_watch.py bounds its own context-window fetches)
  3. none found -> segment = "unclassified", no first-touch draft generated for them
     (counted honestly in the summary, never silently dropped)

Rails: DRAFTS ONLY. Every staged record has status="pending" like every other
reply_watch.py queue entry, so it needs [OWNER]'s own click through the normal approve
flow -- nothing here calls GHL's send endpoint. Suppress list is checked FIRST for
every candidate before it's even considered for the batch (see _not_suppressed()).

Usage:
  convo_reactivation.py             # segment + stage up to 10 demo drafts
  convo_reactivation.py --dry-run   # segment + print, stage nothing
  convo_reactivation.py --limit 3   # smaller demo batch
"""
from __future__ import annotations

import argparse
import csv
import os
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import owner  # noqa: E402
from store_lib import now_iso, new_id, humanize  # noqa: E402
import reply_watch  # noqa: E402  (ONLY for ._load()/._save() -- the approve-queue reader/writer,
                    # exactly as warm_followup.py and proposal_timers.py already do)
import proposal_factory  # noqa: E402  (find_contact -- resolves a GHL contact_id so the
                         # approve click can actually send; read-only)
import convo_context  # noqa: E402

WARM_CSV = Path(os.environ.get("WARM_CSV") or (ROOT / "store" / "warm-hitlist.csv"))
SUPPRESS = ROOT / "store" / "suppress.jsonl"
DEMO_LIMIT = 10
REACTIVATION_GHL_LOOKUP_CAP = 10  # never more live GHL lookups than the demo batch itself needs

# reply_watch._looks_spam() is deliberately not weakened/extended here (owner file,
# off-limits to touch beyond what the mission calls for) and its marker list is tuned
# for spam sent AT [OWNER]'s own inbox, not for filtering a business's own automated
# marketing blasts that happen to sit in the same GHL conversation thread as any real
# replies from them. Confirmed live: a real tier=2 contact's most recent inbound
# message was their OWN business's "SculpSure promo, SOLD OUT, $499" blast, which
# reply_watch._looks_spam() doesn't catch (no % sign, no "unsubscribe", etc) but is
# obviously not a reply about [OWNER]'s pricing. This second, narrower filter is scoped
# ONLY to this module's reply-text lookup -- it does not touch reply_watch.py at all.
_AUTOMATED_BLAST_MARKERS = ("sold out", "spots taken", "spots left", "book now",
                            "% off", "promo", "limited spots", "don't miss", "hurry",
                            "last chance", "expires soon", "% save", "save now")


def _looks_like_their_own_blast(text: str) -> bool:
    t = (text or "").lower()
    return any(m in t for m in _AUTOMATED_BLAST_MARKERS)

# ---- segment-plans.md's signal lists, verbatim from the doc ----
# NOTE: a bare "$" has no word-character neighbor for \b to anchor on, so it's matched
# as its own alternative outside the \b(...)\b word-list group, not inside it (a regex
# with \$ wrapped in \b\b never matches: was a real bug here, fixed after a fixture
# test caught "what's the $ on this" silently classifying as unclassified).
PRICE_SIGNALS = re.compile(
    r'\b(too expensive|price|budget|cost|cheaper|afford)\b|\$', re.IGNORECASE)
TIMING_SIGNALS = re.compile(
    r'\b(not right now|later|busy season|call me back|let me get through|next quarter|'
    r'not now|maybe later|too busy)\b', re.IGNORECASE)
INTEREST_SIGNALS = re.compile(
    r'\b(sounds good|let me think|send me more|interested|sure|okay|tell me more|'
    r'sounds interesting)\b', re.IGNORECASE)

# ---- segment-plans.md first-touch copy, exact text, per segment (voice already
# reviewed/approved in that doc -- this module reuses it verbatim, humanize()'d the
# same way every other draft in this codebase is before staging) ----
_COPY = {
    "segment-price": (
        "a cheaper way in, if that's what stalled us",
        "Last time we talked, price was the sticking point. Fair. Here's a smaller door: "
        "the $800 landing page, one page, one job (get the call, get the form, get the "
        "booking). Most people who start there upgrade once it's earning its keep.\n\n"
        "If your current site's actually fine and just needs fixing, not rebuilding, "
        "there's also the $450 webfix bundle, speed, mobile, and SEO cleanup on what "
        "you've got.\n\nEither way, one missed job a month usually costs more than either "
        "option. Reply and tell me which one fits."),
    "segment-timing": (
        "did the timing change?",
        "You mentioned the timing wasn't right when we last talked. Just checking back, "
        "genuinely, not a push.\n\nBuild takes 7 days from deposit, and I can hold a slot "
        "instead of you having to remember to reach back out. If now's still not it, tell "
        "me what needs to change first and I'll check back around then instead of "
        "guessing."),
    "segment-interest": (
        "picking this back up",
        "You seemed genuinely interested last time we talked, then it went quiet on both "
        "ends, that happens. Not re-explaining the offer, you already got it.\n\nWant to "
        "just grab 15 minutes and finish the conversation? {book}"),
}
BOOK_URL = f"{owner.get('site', 'example.com')}/book"


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _not_suppressed(email: str, name: str) -> bool:
    """C187: suppress-list check FIRST, before this contact is considered for
    ANYTHING (segmentation, drafting, staging). Same union logic cold_feeder.py's
    suppress read already uses (email OR contact match)."""
    email_l = (email or "").strip().lower()
    name_l = (name or "").strip().lower()
    for r in _load_jsonl(SUPPRESS):
        if email_l and (r.get("email") or "").strip().lower() == email_l:
            return False
        if name_l and (r.get("name") or "").strip().lower() == name_l:
            return False
    return True


def _tier2_rows() -> list[dict]:
    if not WARM_CSV.exists():
        return []
    out = []
    with WARM_CSV.open(newline="") as f:
        for r in csv.DictReader(f):
            if (r.get("tier") or "").strip() != "2":
                continue
            out.append(r)
    return out


def _by_recency(rows: list[dict]) -> list[dict]:
    """Lower deal_age_days = more recent contact. Rows with an unparseable/missing
    age sort last (they're not MORE recent than a dated row, and shouldn't crowd
    out ones we can actually verify are recent)."""
    def key(r):
        try:
            return int((r.get("deal_age_days") or "").strip())
        except ValueError:
            return 10**9
    return sorted(rows, key=key)


def classify_segment(reply_text: str) -> str:
    """Pure function: segment-plans.md's three signal lists, checked in the order
    the doc lists them (PRICE, then TIMING, then INTEREST). A message matching
    multiple lists' keywords is genuinely ambiguous per the doc's own rule ("Anything
    ambiguous or matching multiple segments gets flagged for [OWNER]'s manual call, not
    auto-assigned") -- so multi-match returns 'ambiguous', not the first list that
    happened to hit. No signal at all -> 'unclassified'."""
    if not (reply_text or "").strip():
        return "unclassified"
    hits = []
    if PRICE_SIGNALS.search(reply_text):
        hits.append("segment-price")
    if TIMING_SIGNALS.search(reply_text):
        hits.append("segment-timing")
    if INTEREST_SIGNALS.search(reply_text):
        hits.append("segment-interest")
    if len(hits) > 1:
        return "ambiguous"
    if len(hits) == 1:
        return hits[0]
    return "unclassified"


def _looks_real(text: str) -> bool:
    """Both spam filters must pass: reply_watch's own (unweakened, unmodified) plus
    this module's narrower automated-blast heuristic (see _looks_like_their_own_blast
    docstring above for why the second one exists)."""
    return bool(text) and not reply_watch._looks_spam(text) and not _looks_like_their_own_blast(text)


def _find_reply_text(name: str, email: str, contact_id: str = "") -> str:
    """Best-effort real reply text lookup, in the order documented at module top.
    Returns '' if nothing is found (honest 'unclassified', never a fabricated
    guess). Filters out marketing/automated noise via _looks_real(), AND requires
    at least one OUTBOUND message in the thread first (evidence [OWNER] actually
    said something for this to be a reply TO) -- confirmed live that a real tier=2
    CSV row's GHL contact_id can point at a conversation that's 100% inbound
    marketing from an unrelated business (a phone-number-only contact named
    "(555) 000-0000" whose entire thread was "Example Spa" promo blasts, zero
    outbound from [OWNER] anywhere in it -- not a [OWNER]-outreach conversation at
    all, so nothing in it can honestly be "their reply to [OWNER]'s pitch")."""
    name_l = (name or "").strip().lower()
    # 1. an existing replies.jsonl record (already-run reply_watch drafts) matched by name.
    # A replies.jsonl record existing at all is itself evidence this was a real
    # reply_watch-processed conversation, so no separate outbound-message check
    # is needed for this path.
    for r in _load_jsonl(ROOT / "store" / "replies.jsonl"):
        if (r.get("name") or "").strip().lower() == name_l and name_l:
            msg = r.get("their_msg") or ""
            if _looks_real(msg):
                return msg
    # 2. live GHL conversation history, if we can resolve a contact_id. Looks at
    # several recent inbound turns (not just the latest) since the latest one may
    # itself be spam while an earlier real reply exists in the same thread.
    if contact_id:
        try:
            out_raw = proposal_factory.ghl_social._api(
                ["GET", f"/conversations/search?contactId={contact_id}&limit=1"])
            data = json.loads(out_raw[out_raw.find("{"):])
            convos = data.get("conversations", [])
            if convos:
                msgs = convo_context.fetch_context(convos[0].get("id", ""), turns=8)
                has_outbound = any(m["dir"] == "outbound" for m in msgs)
                if not has_outbound:
                    return ""  # nothing [OWNER] said -> nothing here is "their reply to him"
                inbound = [m["body"] for m in msgs if m["dir"] == "inbound"]
                for body in reversed(inbound):  # most recent real message first
                    if _looks_real(body):
                        return body
        except Exception:  # noqa: BLE001 — a lookup failure just falls through to unclassified
            pass
    return ""


def build_batch(limit: int = DEMO_LIMIT, dry: bool = False) -> dict:
    rows = _by_recency(_tier2_rows())
    total_tier2 = len(rows)
    candidates = rows[:limit]
    already_queued_names = {(r.get("name") or "").strip().lower()
                            for r in _load_jsonl(ROOT / "store" / "replies.jsonl")
                            if r.get("src") == "reactivation_demo"}

    staged, skipped_suppressed, skipped_already, unclassified, ambiguous = [], 0, 0, 0, 0
    lookups_used = 0
    for row in candidates:
        name = (row.get("name") or "").strip() or (row.get("company") or "").strip()
        email = (row.get("email") or "").strip()
        if not _not_suppressed(email, name):
            skipped_suppressed += 1
            continue
        if name.strip().lower() in already_queued_names:
            skipped_already += 1
            continue
        contact = {}
        if lookups_used < REACTIVATION_GHL_LOOKUP_CAP and (email or name):
            contact = proposal_factory.find_contact(email=email, name=name)
            lookups_used += 1
        reply_text = _find_reply_text(name, email, contact.get("id", ""))
        segment = classify_segment(reply_text)
        if segment == "unclassified":
            unclassified += 1
            continue
        if segment == "ambiguous":
            ambiguous += 1
            continue
        subject, body_tpl = _COPY[segment]
        first = name.split()[0].title() if name else "there"
        body = humanize(body_tpl.format(book=BOOK_URL).replace("{{contact.first_name}}", first))
        rec = {
            "id": new_id("reactivation_" + (email or name) + segment),
            "convo": None, "contact_id": contact.get("id", ""),
            "name": name or "warm contact", "phone": row.get("phone", ""),
            "channel": "Email" if email else "SMS",
            "their_msg": f"[reactivation demo: tier=2, deal_age_days={row.get('deal_age_days')}, "
                        f"segment={segment}, matched from: {reply_text[:120] or '(no reply text found)'}]",
            "intent": "followup", "draft": body,
            "segment": segment, "email_subject": subject,
            "status": "pending", "created": now_iso(), "src": "reactivation_demo",
        }
        if not dry:
            reply_watch._save(rec)
        staged.append(rec)

    result = {
        "total_tier2_in_csv": total_tier2,
        "candidates_considered": len(candidates),
        "staged": len(staged),
        "skipped_suppressed": skipped_suppressed,
        "skipped_already_staged": skipped_already,
        "unclassified": unclassified,
        "ambiguous_multi_signal": ambiguous,
        "segments": {s: sum(1 for r in staged if r["segment"] == s) for s in _COPY},
        "note": ("Demo batch only, capped at " + str(limit) + ". The 423-scale reactivation "
                "volume stays entirely in the GHL DBR (business-library/campaigns/"
                "warm-reactivation-423.md) -- its publish is a separate fleet's lane and "
                "[OWNER]'s own go-ahead, this module never touches it."),
    }
    print(f"reactivation demo: {total_tier2} tier=2 row(s) in WARM-HITLIST.csv, "
          f"considered top {len(candidates)} by recency, staged {len(staged)} draft(s) "
          f"({result['segments']}), {skipped_suppressed} suppressed, "
          f"{unclassified} unclassified, {ambiguous} ambiguous"
          + ("" if dry else " -> replies.jsonl"))
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=DEMO_LIMIT)
    args = ap.parse_args()
    build_batch(limit=min(args.limit, DEMO_LIMIT), dry=args.dry_run)
