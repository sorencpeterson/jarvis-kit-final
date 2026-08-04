#!/usr/bin/env python3
"""Reply-Watch: find inbound GHL replies that need a response, draft on-voice replies,
queue them for one-tap approval on the dashboard.

Reading + drafting ONLY. Sending is gated behind [OWNER]'s approve click (see /api/replies/*
in server.py). Spam and automated promos texted AT him are filtered out before drafting.

C161-220 additions (500-IDEAS-AGGREGATORS.md section C), layered onto the original
concierge without weakening any of it -- every original behavior below (playbook digest,
voice_spec injection, interested->proposal-build inline, suppress-on-remove, objection
logging, spam filter, signed one-tap ntfy actions) is unchanged in substance:
  - C161 conversation state (convo_state.py) picked up per candidate to steer register.
  - C163 SLA aging: age_hours + escalation flag written onto every queued record.
  - C165 re-classification: a candidate whose convo already has a pending/prior record
    gets reconsidered instead of being skipped outright (interest can flip to not_now).
  - C166 context window: last 5 messages both directions via convo_context.fetch_context,
    capped at ONE GHL fetch per candidate per run.
  - C167-169/178-179/202-204: drafting-time lint gates (convo_lint.py) run on every
    draft before it's queued; hard-gate failures hold the draft with the reason attached
    instead of shipping a bad one silently.
  - C173 hot-lead fast path: interested + booking language -> ntfy immediately.
  - C174/180: same-company dedupe + multi-party detection (convo_dedupe.py) noted on
    the record for [OWNER]'s eyes, never auto-merged/auto-decided.
  - C181 webhook-first: store/webhook_replies_seen.jsonl (webhook_processor.py's output)
    checked before polling; polling still always runs (fallback, per the mission's
    explicit "poll stays as fallback" instruction -- this never REPLACES polling).
  - C186 per-campaign context: the same niche tags _niche_for() already reads
    (webfix/agency/local service) steer a per-niche addendum in the CLASSIFY prompt.
  - C210 competitor-mention counter-brief attached when they name a competitor.
  - C217/219: harassment flag (his-eyes-only, no auto-draft) + language-match routing.
"""
from __future__ import annotations

import os
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso, new_id, humanize, voice_spec, sign_secret  # noqa: E402
import planner  # noqa: E402
import ghl_social  # noqa: E402  (reuse the GHL api.sh caller + path)

import proposal_factory  # noqa: E402
import convo_state  # noqa: E402
import convo_lint  # noqa: E402
import convo_context  # noqa: E402
import convo_dedupe  # noqa: E402
import convo_meeting  # noqa: E402

REPLIES = ROOT / "store" / "replies.jsonl"
SUPPRESS = ROOT / "store" / "suppress.jsonl"
WEBHOOK_SEEN = ROOT / "store" / "webhook_replies_seen.jsonl"
PLAYBOOKS = Path(os.environ.get("BIZLIB") or (ROOT / "business-library")) / "playbooks"
GHL = ghl_social.GHL

# C163: SLA escalation thresholds (hours) for a pending queue record's age.
SLA_ESCALATE_HOURS = (4, 24)


def _playbook_digest(max_chars: int = 3200) -> str:
    """Condense objections.md into 'they say -> you say' lines for the classifier."""
    try:
        txt = (PLAYBOOKS / "objections.md").read_text()
    except OSError:
        return ""
    import re as _re
    pairs = _re.findall(r'\*\*\d+\. "?(.*?)"?\*\*\nSay: "(.*?)"', txt)
    out = "\n".join(f'- "{t[:80]}" -> "{s[:220]}"' for t, s in pairs)
    return out[:max_chars]


def _suppress(contact_id: str, email: str, why: str):
    """Local do-not-target list. No GHL write; cold_feeder checks this before enrolling."""
    with SUPPRESS.open("a") as f:
        f.write(json.dumps({"ts": now_iso(), "contact_id": contact_id,
                            "email": email, "why": why[:200]}) + "\n")


def _is_suppressed(contact_id: str, email: str) -> bool:
    """C187 audit surface: the check every new draft path in this file runs FIRST,
    before any drafting work happens for a candidate. Same union logic (contact_id
    OR email) cold_feeder.py's existing suppress read already uses."""
    if not SUPPRESS.exists():
        return False
    cid = (contact_id or "").strip()
    em = (email or "").strip().lower()
    for line in SUPPRESS.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if cid and (r.get("contact_id") or "").strip() == cid:
            return True
        if em and (r.get("email") or "").strip().lower() == em:
            return True
    return False


def _niche_for(contact: dict) -> str:
    tags = [t.lower() for t in (contact.get("tags") or [])]
    if any("wl-webdev" in t or "agency" in t for t in tags):
        return "agency"
    if any("webfix" in t for t in tags):
        return "webfix"
    return "local service"


# C186: per-campaign classify-prompt addenda, keyed by the same niche vocabulary
# _niche_for() already produces (webfix/agency/local service) -- reuses the existing
# tag taxonomy rather than inventing a new campaign concept the rest of the codebase
# doesn't have (cold_pipeline.jsonl's own `campaign` field is unpopulated in practice,
# confirmed by reading campaign_guard.py's _cold_pipeline_index(), which defaults every
# untagged contact to "wl" -- niche tags are the real, populated signal here).
_CAMPAIGN_CONTEXT = {
    "webfix": ("This contact came in through the WEBFIX lane (their site exists and is "
              "salvageable, not a from-scratch build). Lead with the $450 fix bundle "
              "unless the teardown found 4+ structural faults, in which case say so and "
              "recommend the Standard rebuild instead, per the routing rule."),
    "agency": ("This contact is an AGENCY OWNER (white-label). Speak operator to "
              "operator: pipeline, fulfillment, margin, white-label, delivery. The offer "
              "is the [FIRST_BUILD] flat first build, then rate card. Never pitch them like a "
              "local-service owner."),
    "local service": ("This contact is a LOCAL SERVICE owner (trades/clinic/etc). Price "
                      "in jobs, not just dollars, per the ICP A playbook: what's an "
                      "average job worth, one missed job pays for the site."),
}

# Promo/automated markers — inbound texts carrying these are marketing spam AT [OWNER], not real replies.
SPAM_MARKERS = ("text stop", "to stop", "offers.",
                "% off", "buy 3", "free!", "limited time", "flash sale", "deal ends",
                "click here", "act now", "sale ends", "www.", "http")

# B/CX1/CX2: explicit opt-out language from a real contact. These used to sit INSIDE
# SPAM_MARKERS, which meant a genuine "unsubscribe"/"opt out"/"reply stop" reply got
# silently dropped by the promo-spam filter before it ever reached _suppress() (a
# CAN-SPAM/reputation risk on the cold campaign). Checked and suppressed BEFORE the
# spam filter runs, and regardless of whether the convo already has a "seen_final"
# (non-pending) record from an earlier reply -- see run()'s opt-out pass.
OPT_OUT_MARKERS = ("unsubscribe", "opt out", "reply stop", "take me off", "remove me")

# C173: booking language that, combined with intent=interested, means "don't wait for
# the next poll, tell him now."
_BOOKING_LANGUAGE = ("book", "schedule", "calendar", "available", "call me", "ring me",
                     "what time", "when can", "this week", "tomorrow")

# C210: competitor names worth attaching a counter-brief for, drawn from
# playbooks/objections.md's THE NEPHEW & COMPETITORS section (#29-34) — Wix/Fiverr/
# Facebook-marketplace/nephew-does-sites are the recurring named competitors in that
# playbook, so a mention of any of these gets that section's counter attached verbatim
# instead of asking the classifier to reinvent one.
_COMPETITOR_MARKERS = {
    "wix": '#31 ("Wix says I can do it myself free"): "You can, and you\'ll do it at 11pm '
          'after work, for months. Your hourly rate says that\'s the most expensive site '
          'you could build."',
    "fiverr": '#2 ("$200 on Fiverr"): "You can. You\'ll get a template with your name on '
             'it. This is built off what\'s actually costing you customers, you saw the '
             'five faults. Templates can\'t see."',
    "squarespace": '#35 ("Is this just a template?"): "The tech is standard, the build is '
                   'yours. Nobody else gets your diagnosis, your copy, your call flow."',
    "godaddy": '#35 ("Is this just a template?"): "The tech is standard, the build is '
              'yours. Nobody else gets your diagnosis, your copy, your call flow."',
    "facebook": '#33 ("someone on Facebook for half"): "Screenshot their proposal and '
               'mine side by side. If theirs names one specific fault on your site, take it."',
    "nephew": '#29 ("My nephew/friend does websites"): "Great, this proposal doubles as '
             'his spec sheet. If he can hit it at this price, hire him. If he\'s still '
             '\'getting to it\' in two weeks, call me."',
}

CLASSIFY = """You screen inbound messages sent to [OWNER] ([OWNER_COMPANY] / [OWNER_COMPANY], a
web + marketing operator). For EACH message decide:
- real: true only if it's a genuine human reply from a prospect or contact worth a personal
  response. false for marketing spam, automated notifications, opt-outs, delivery receipts.
  A wrong-number reply (a real human saying they don't know who this is, or this isn't the
  business/person [OWNER] is trying to reach) is real=true with intent=wrong_person, NOT false --
  a real person deserves one polite closing line, not silence.
- intent: one of interested | question | objection | not_now | remove | wrong_person | other
- reply: a SHORT reply written the way [OWNER] actually types, plain, direct, human, a little
  blunt. HARD RULES: no em-dashes or en-dashes, no cliches (excited, thrilled, hope this finds
  you, circle back, touch base), no emojis, use contractions. When they seem interested, nudge
  toward a quick call at [OWNER_SITE]/book. If real is false, reply must be "".
  ANSWER THEIR ACTUAL QUESTION FIRST if they asked one, pitch second, never pitch-only when a
  direct question is sitting there unanswered.
  When their message matches an objection below, adapt that exact counter, do not invent one.
  If intent is remove, reply must be "" (we suppress them, no goodbye message needed).
  If intent is wrong_person, reply must be a short, gracious close: apologize for the mix-up,
  no explanation needed, no pitch, wish them well. Example shape: "My mistake, sorry for the
  bother. Have a good one."
  Keep the reply's LENGTH roughly proportional to their message length (a one-line "ok" does
  not need a paragraph back). Match their FORMALITY (a formal message gets a fuller-sentence
  reply, a casual one-liner gets a casual one back) without ever becoming stiffer than a
  direct, human voice. ONE question per reply, maximum. AT MOST one link in the reply.
- reply_en: ONLY when a message is tagged [LANGUAGE: SPANISH] below, also include an
  "reply_en" field: a plain English translation of your Spanish "reply" so [OWNER] (who
  doesn't read Spanish) can verify what he's about to send before approving it. Omit
  this field entirely for non-Spanish messages.

OBJECTION COUNTERS ([OWNER]'s playbook):
{PLAYBOOK}
{EXTRA_CONTEXT}

Return ONLY a JSON array, one object per input in the same order:
[{"real":true,"intent":"interested","reply":"..."}]

MESSAGES:
"""


def _load() -> list[dict]:
    if not REPLIES.exists():
        return []
    by_id, order = {}, []
    for line in REPLIES.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("id"):
            if r["id"] not in by_id:
                order.append(r["id"])
            by_id[r["id"]] = r
    return [by_id[i] for i in order]


def _save(rec: dict):
    REPLIES.parent.mkdir(parents=True, exist_ok=True)
    from store_lib import _flock
    with _flock(REPLIES), REPLIES.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def claim(rid: str, from_status: str = "pending", to_status: str = "sending") -> dict | None:
    """Locked compare-and-swap so a reply can't be double-sent (2026-07-05 audit #1):
    under the replies lock, flip to `to_status` ONLY if still `from_status`. A concurrent
    approve (double-tap / threadpool race / phone-tap + dashboard) sees the flipped status
    and gets None -> it must not send."""
    from store_lib import _flock
    REPLIES.parent.mkdir(parents=True, exist_ok=True)
    with _flock(REPLIES):
        cur = {x["id"]: x for x in _load()}.get(rid)
        if cur is None or cur.get("status") != from_status:
            return None
        claimed = {**cur, "status": to_status}
        with REPLIES.open("a") as f:
            f.write(json.dumps(claimed, ensure_ascii=False) + "\n")
        return claimed


def ghl_send_ok(out: str) -> bool:
    """D6: did a GHL /conversations/messages send actually succeed?

    Replaces the naive substring check in the send handlers ('"id"' in out /
    "messageId" in out), which could read an ERROR payload that merely echoes an
    id-ish field (e.g. a 422 quoting the request, or '"message":"messageId is
    required"') as a successful send. Parses the JSON and requires a real
    message/conversation id (or an explicit success flag). Fails CLOSED: no JSON,
    unparseable JSON, statusCode >= 400, or no id key -> False (send_failed).
    Success payloads the old check accepted still pass: {"messageId": ...},
    {"conversationId": ...}, {"success": true}, {"id": ...}."""
    s = out or ""
    i = s.find("{")
    if i < 0:
        return False
    try:
        data = json.loads(s[i:], strict=False)
    except (ValueError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    status = data.get("statusCode")
    try:
        if status is not None and int(status) >= 400:
            return False
    except (ValueError, TypeError):
        pass
    if data.get("success") is True:
        return True
    # flat first, then one nested level (GHL sometimes wraps the send result),
    # never inside an error/errors envelope
    scan = [data] + [v for k, v in data.items()
                     if isinstance(v, dict) and k.lower() not in ("error", "errors")]
    for d in scan:
        for k in ("messageId", "conversationId", "emailMessageId", "msgId", "id"):
            v = d.get(k)
            if isinstance(v, (str, int)) and str(v).strip():
                return True
    return False


def _loc() -> str:
    try:
        for line in (GHL / ".env").read_text().splitlines():
            if line.startswith("GHL_LOCATION_ID="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _looks_spam(body: str) -> bool:
    b = (body or "").lower()
    return any(m in b for m in SPAM_MARKERS)


# R2#10 (2026-07-14): a plain unqualified substring match suppressed NEGATED
# mentions -- "I don't want to unsubscribe, just curious about pricing" contains
# "unsubscribe" as a literal substring and read as a real opt-out, silently
# suppressing a still-interested prospect. This looks for a negation/continuation
# cue in a short window immediately BEFORE the matched phrase ("don't unsubscribe",
# "please don't remove me", "no need to opt out", "not asking you to take me off").
_OPTOUT_NEGATION = re.compile(
    r"\b(?:don'?t|do\s+not|won'?t|never|not|no\s+need\s+to|not\s+trying\s+to|"
    r"not\s+asking\s+(?:you\s+)?to|please\s+don'?t)\s+(?:\w+\s+){0,5}$"
)


def _looks_optout(body: str) -> bool:
    """B/CX1/R2#10: an explicit, word-boundary-matched opt-out phrase that is NOT
    negated (see _OPTOUT_NEGATION) -- "unsubscribe" inside "don't want to
    unsubscribe" no longer counts. Checked separately from (and before)
    _looks_spam so a real opt-out can never be silently dropped as promo spam.

    Only the LEADING edge is boundary-anchored (not a full \\bphrase\\b): that's
    enough to stop a marker matching mid-word inside an unrelated compound (e.g.
    "opt out" inside "adopt out"), which is what a real word is glued onto the
    FRONT of, while still catching an ordinary inflection glued onto the BACK
    ("unsubscribe" -> "unsubscribed"/"unsubscribes") -- a real opt-out signal a
    strict trailing boundary would otherwise silently stop matching."""
    b = (body or "").lower()
    for m in OPT_OUT_MARKERS:
        for match in re.finditer(r"\b" + re.escape(m), b):
            prefix = b[:match.start()]
            if _OPTOUT_NEGATION.search(prefix):
                continue  # "don't unsubscribe me" etc -- the opposite of an opt-out
            return True
    return False


# ---- C163: SLA aging helpers ----
def _age_hours(created_iso: str) -> float:
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(created_iso)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(dt.tzinfo)
        return max(0.0, (now - dt).total_seconds() / 3600)
    except (ValueError, TypeError):
        return 0.0


def _escalation_for(age_h: float) -> str:
    """C163: '' (fresh) -> 'watch' (past the first threshold) -> 'urgent' (past the
    second). Pure step function, easy to test, easy for the dashboard to color."""
    lo, hi = SLA_ESCALATE_HOURS
    if age_h >= hi:
        return "urgent"
    if age_h >= lo:
        return "watch"
    return ""


def refresh_sla_fields() -> int:
    """C163: rewrite age_hours + escalation on every PENDING record currently in the
    queue (not just newly-created ones -- a record can sit pending across many runs,
    and its age should reflect 'now', not 'when it was drafted'). Returns count
    updated. Called at the end of run() so the queue's aging is always current after
    a poll, independent of whether any NEW replies came in this cycle."""
    updated = 0
    for r in _load():
        if r.get("status") != "pending":
            continue
        age_h = _age_hours(r.get("created", ""))
        esc = _escalation_for(age_h)
        if r.get("age_hours") != round(age_h, 2) or r.get("escalation") != esc:
            _save({**r, "age_hours": round(age_h, 2), "escalation": esc})
            updated += 1
    return updated


# ---- C181: webhook-first signal ----
def _webhook_priority_contact_ids() -> set[str]:
    """C181: contact_ids webhook_processor.py has already flagged as having a new
    inbound event (store/webhook_replies_seen.jsonl), read BEFORE polling. This is a
    priority/visibility signal only, exactly per webhook_processor.py's own documented
    contract ("means reply_watch's next run should prioritize this contact, not draft
    one now") -- polling ALWAYS still runs after this; nothing here skips the poll."""
    if not WEBHOOK_SEEN.exists():
        return set()
    out = set()
    for line in WEBHOOK_SEEN.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("contact_id"):
            out.add(r["contact_id"])
    return out


def run() -> list[dict]:
    loc = _loc()
    if not loc:
        print("no GHL location")
        return []

    # C181 webhook-first: read the signal before the poll (visibility only, per the
    # documented contract -- it never substitutes for the poll below).
    webhook_priority = _webhook_priority_contact_ids()
    if webhook_priority:
        print(f"  webhook signal: {len(webhook_priority)} contact(s) flagged for priority "
              f"by webhook_processor.py this run")

    out = ghl_social._api(["GET", f"/conversations/search?locationId={loc}&limit=40&sortBy=last_message_date"])
    try:
        convos = json.loads(out[out.find("{"):]).get("conversations", [])
    except (ValueError, json.JSONDecodeError):
        print("could not read conversations")
        return []
    # C165 re-classification: a convo already queued gets reconsidered (not silently
    # skipped) ONLY if its existing record is still "pending" (unactioned) -- once
    # [OWNER] has sent/skipped/approved something for a convo, re-drafting on top of his
    # decision would be a real regression, so those stay excluded exactly as before.
    existing_by_convo = {r.get("convo"): r for r in _load() if r.get("convo")}
    seen_final = {cid for cid, r in existing_by_convo.items() if r.get("status") != "pending"}

    inbound = [c for c in convos if c.get("lastMessageDirection") == "inbound"]

    # B/CX1: an opt-out phrase must reach _suppress() even though it would also match
    # the promo-spam filter below, and even on a convo already in seen_final (a LATER
    # opt-out on an already-replied thread must still suppress) -- checked over every
    # inbound convo, before spam-filtering, before the seen_final exclusion.
    for c in inbound:
        body = c.get("lastMessageBody") or ""
        if not _looks_optout(body):
            continue
        cid, email = c.get("contactId") or "", c.get("email") or ""
        if _is_suppressed(cid, email):
            continue
        _suppress(cid, email, body)
        print(f"  suppressed {c.get('contactName') or cid}: opt-out phrase in message")

    cands = [c for c in inbound
             if not _looks_optout(c.get("lastMessageBody"))
             and not _looks_spam(c.get("lastMessageBody"))
             and c.get("id") not in seen_final]
    if not cands:
        print("no new inbound replies needing a response")
        return []

    # C174 same-company dedupe: computed once over this run's candidate batch (using
    # whatever contact name/company GHL's conversation-search response carries).
    company_flags = convo_dedupe.same_company_flags(
        [{"contact_id": c.get("contactId"), "name": c.get("contactName") or c.get("fullName"),
         "company": c.get("companyName") or ""} for c in cands])

    # C219: language-match drafting, computed per-message and embedded as an inline tag
    # rather than a separate LLM call per Spanish candidate (stays at ONE batch call
    # total, matching the C182-186 efficiency rail).
    langs = [convo_lint.detect_language(c.get("lastMessageBody") or "") for c in cands]
    msgs = "\n".join(
        f"{i}. From {c.get('contactName') or c.get('fullName') or 'unknown'} "
        f"({c.get('lastMessageType')}){' [LANGUAGE: SPANISH -- draft your reply in Spanish, still following every hard rule above]' if lang == 'es' else ''}: "
        f"{(c.get('lastMessageBody') or '')[:280]}"
        for i, (c, lang) in enumerate(zip(cands, langs)))

    # C186 per-campaign context: majority niche across this batch steers ONE addendum
    # for the whole classify call (classifying per-candidate niche would mean N calls
    # instead of 1 -- the efficiency rail C182-186 asks for -- so this picks the
    # dominant niche in the batch; a minority-niche candidate still gets correctly
    # niched context on ITS OWN follow-up path when proposal_factory.build() runs,
    # which already calls _niche_for() per-contact independently of this prompt addendum).
    from collections import Counter
    contact_lookups = {}
    for c in cands[:20]:  # cap contact lookups for the campaign-context vote (efficiency, C182-186)
        cid = c.get("contactId")
        if cid:
            contact_lookups[cid] = proposal_factory.find_contact(cid=cid)
    niche_votes = Counter(_niche_for(contact_lookups.get(c.get("contactId"), {})) for c in cands
                          if c.get("contactId") in contact_lookups)
    dominant_niche = niche_votes.most_common(1)[0][0] if niche_votes else "local service"
    campaign_ctx = ""
    if _CAMPAIGN_CONTEXT.get(dominant_niche):
        campaign_ctx = "CAMPAIGN CONTEXT (majority of this batch): " + _CAMPAIGN_CONTEXT[dominant_niche]

    # C210 competitor counter-brief: scan the batch's raw text for any named
    # competitor, attach the matching playbook counter(s) to the prompt.
    all_text = " ".join((c.get("lastMessageBody") or "").lower() for c in cands)
    competitor_hits = [brief for name, brief in _COMPETITOR_MARKERS.items() if name in all_text]
    competitor_ctx = ("COMPETITOR MENTIONED -- use this exact counter if relevant:\n" +
                      "\n".join(competitor_hits)) if competitor_hits else ""

    # C161 conversation state: majority-vote addendum, same efficiency reasoning as
    # the campaign context above (one classify call for the whole batch).
    state_lookup = convo_state.load_states()
    state_votes = Counter(state_lookup.get(c.get("contactId"), {}).get("state", "new")
                          for c in cands if c.get("contactId"))
    dominant_state = state_votes.most_common(1)[0][0] if state_votes else "new"
    state_ctx = (f"CONVERSATION STATE (majority of this batch, from convo_state.py): "
                f"{dominant_state}. 'negotiating' means a priced proposal is already on the "
                f"table for them -- don't re-pitch from scratch. 'dormant' means it's been "
                f"quiet a while -- a lighter re-open beats a hard pitch.") if dominant_state != "new" else ""

    # join non-empty context pieces with blank-line separation; an all-empty batch
    # (no niche vote, no competitor mention, brand-new contacts) leaves this "" so the
    # prompt template's {EXTRA_CONTEXT} slot collapses cleanly instead of leaving
    # dangling blank lines from unused placeholders.
    extra_context = "\n\n".join(x for x in (campaign_ctx, competitor_ctx, state_ctx) if x)

    prompt = CLASSIFY.replace("{PLAYBOOK}", _playbook_digest() or "(playbook unavailable)")
    prompt = prompt.replace("{EXTRA_CONTEXT}", extra_context)
    prompt = "VOICE SPEC for every reply you draft:\n" + voice_spec(1600) + "\n\n" + prompt
    data = planner._extract_json(planner._cli(prompt + msgs, timeout=150, feature="reply") or "") or []

    added = []
    hot_leads = []
    for i, c in enumerate(cands):
        d = data[i] if i < len(data) else {}
        contact_id = c.get("contactId") or ""
        their_msg = c.get("lastMessageBody") or ""

        # C187 (audited): suppress-list check FIRST, before ANY drafting work for this
        # candidate, on every path below -- remove/suppress, real draft, everything.
        # Checks BOTH contact_id and email (GHL's conversation-search rows carry an
        # email field for email-channel convos) so a suppression recorded by email
        # only (e.g. campaign_guard.py's unsub-tag scan, which may not always have a
        # contact_id) still catches a match here.
        if _is_suppressed(contact_id, c.get("email") or ""):
            continue

        # CX2: check intent=="remove" BEFORE the real-gate below. The CLASSIFY
        # prompt's own instructions list opt-outs under real=false, so a genuine
        # "take me off your list" can legitimately come back real=false +
        # intent=remove -- discarding it at the real-gate first would drop the
        # opt-out. remove must suppress regardless of the real verdict.
        intent = d.get("intent", "other")
        if intent == "remove":
            _suppress(contact_id, c.get("email") or "", their_msg)
            print(f"  suppressed {c.get('contactName') or contact_id}: asked to be removed")
            continue

        if not d.get("real"):
            continue

        # C165: intent CAN flip on a re-classified convo (e.g. was "interested" last
        # run, this run's fresh message reads "not_now") -- the LLM call above always
        # re-derives intent fresh from the CURRENT message, so this is automatic; no
        # special-casing needed here beyond not having skipped the candidate above.

        # C217: harassment/abuse routes to his-eyes-only, no auto-draft, logged
        # distinctly so it's visible but never auto-replied-to.
        if convo_lint.detect_harassment(their_msg):
            rec = {"id": new_id("rw_harassment_" + (c.get("id") or "")), "convo": c.get("id"),
                  "contact_id": contact_id, "name": c.get("contactName") or c.get("fullName") or "there",
                  "phone": c.get("phone", ""),
                  "channel": "Email" if "EMAIL" in (c.get("lastMessageType") or "") else "SMS",
                  "their_msg": their_msg[:400], "intent": "harassment", "draft": "",
                  "flag": "harassment_his_eyes_only",
                  "status": "flagged", "created": now_iso()}
            _save(rec)
            print(f"  FLAGGED (harassment/abuse, no auto-draft): {c.get('contactName') or contact_id}")
            planner.notify("Flagged message", "A message needs your eyes only, not auto-drafted.",
                           tags="warning")
            continue

        draft = humanize((d.get("reply") or "").strip())
        if not draft:
            continue

        # C218: a real wrong-number/wrong-person reply still gets its one graceful
        # closing draft queued for his click (unlike 'remove', which needs no reply
        # at all) -- but the contact is ALSO suppressed immediately, same as 'remove',
        # so nothing ever auto-drafts at this number/person again. Never contacting
        # someone again after "wrong number" is the obviously-correct move, and
        # suppressing here (not just on the next 'remove') closes that gap proactively.
        if intent == "wrong_person":
            _suppress(contact_id, c.get("email") or "", "wrong number/wrong person: " + their_msg[:150])

        # objection heard -> the library learns it (verbatim), counter = what we drafted
        objection_seq = 0
        if intent == "objection":
            name_for_log = c.get("contactName") or c.get("fullName") or ""
            objection_seq = convo_context.objection_sequence_count(contact_id, name_for_log)
            convo_context.log_objection(their_msg, draft, contact_id=contact_id,
                                        name=name_for_log, niche=dominant_niche)

        # interested -> build the proposal NOW (blocking is fine, this is a cron) and
        # put the live link inside the reply draft. [OWNER] still approves the send.
        prop_link = ""
        contact = contact_lookups.get(contact_id) or {}
        if intent == "interested" and contact_id:
            try:
                if not contact:
                    contact = proposal_factory.find_contact(cid=contact_id)
                prop = proposal_factory.build(cid=contact_id,
                                              niche=_niche_for(contact),
                                              url=contact.get("website", ""))
                prop_link = prop.get("link", "")
            except Exception as e:  # noqa: BLE001
                print(f"  proposal build failed for {contact_id}: {e}")
        if prop_link and prop_link not in draft:
            draft = draft.rstrip() + ("\n\nPut the whole plan in writing for you here: " + prop_link)

        # C206: when the exchange is heading toward a call (booking language present,
        # matches the same _BOOKING_LANGUAGE list C173's hot-lead fast path already
        # uses) and the draft doesn't already name a specific time itself, append 2
        # concrete calendar-aware slots so [OWNER] isn't leaving "whenever works" on
        # the table. One /api/gcal-backed call per qualifying candidate (never for
        # every candidate -- gated the same way the proposal-build branch above only
        # fires for qualifying intents, not the whole batch).
        if intent == "interested" and any(m in their_msg.lower() for m in _BOOKING_LANGUAGE) \
                and not any(day_name in draft.lower() for day_name in
                           ("monday", "tuesday", "wednesday", "thursday", "friday")):
            # NOTE: deliberately named `day_name`, not `d` -- this function already
            # uses `d` for the LLM's per-candidate classify response (d.get("reply_en")
            # etc a few lines below). A generator expression's loop variable doesn't
            # actually leak into the enclosing scope in Python 3 (confirmed), so reusing
            # `d` here was never a live bug, but it reads as one and would become a REAL
            # bug the moment anyone refactors this generator into an ordinary for-loop.
            try:
                slots_sentence = convo_meeting.slots_line(2)
            except Exception:  # noqa: BLE001 — a calendar-fetch failure should degrade
                slots_sentence = ""  # to generic booking language, never break drafting
            if slots_sentence:
                draft = draft.rstrip() + " " + slots_sentence

        # C166 context window: last 5 messages both directions, ONE fetch per
        # candidate per run (only for candidates that made it this far -- real,
        # not-suppressed, not-remove, not-harassment -- so spam/suppressed convos
        # never cost an extra API call).
        context_msgs = convo_context.fetch_context(c.get("id", ""), turns=5)
        context_block = convo_context.format_context(context_msgs)

        # C168-169/178/202/204 drafting-time gates on the FINAL draft (after the
        # proposal-link append above, since that's what actually ships).
        exact_name = c.get("contactName") or c.get("fullName") or ""
        expected_tier = None
        if prop_link:
            # the tier we just quoted them, so price_consistency checks against
            # exactly what's in the proposal we just built, not just "any SKU"
            try:
                expected_tier = proposal_factory.route(_niche_for(contact))
            except Exception:  # noqa: BLE001
                expected_tier = None
        gate = convo_lint.run_all_gates(draft, their_msg, exact_name, channel=(
            "Email" if "EMAIL" in (c.get("lastMessageType") or "") else "SMS"),
            expected_tier=expected_tier)
        if not gate["ok"]:
            # link_hygiene has a safe mechanical fix; double-question/price/name do not
            # (fixing those would mean silently rewriting [OWNER]'s counter or a price
            # figure, which is exactly the kind of thing that must stay visible, not
            # auto-patched) -- so link_hygiene alone gets auto-fixed, everything else
            # holds the draft with the reason attached for his review.
            link_only = all(f["gate"] == "link_hygiene" for f in gate["failures"])
            if link_only:
                draft = convo_lint.strip_extra_links(draft, keep=prop_link)
                gate = convo_lint.run_all_gates(draft, their_msg, exact_name, expected_tier=expected_tier)
            if not gate["ok"]:
                print(f"  draft HELD for {exact_name or contact_id}: "
                     f"{[f['gate'] for f in gate['failures']]}")

        # C219 language match (reuses the same detection computed once above for the
        # batch prompt's inline Spanish tags, rather than re-running it here)
        lang = langs[i]

        # C180 multi-party detection
        multi_party, multi_party_signal = convo_dedupe.detect_multi_party(
            their_msg, known_email=c.get("email") or "")

        age_h = 0.0  # brand new record -- age starts at 0, refresh_sla_fields() ages it on later runs
        # #6 day-boundary ghost fix: new_id embeds the calendar day, so re-drafting a
        # still-pending convo on a LATER day would mint a second id -> two pending records
        # for one contact. Reuse the existing pending record's id so the re-draft
        # SUPERSEDES it (load_queue is last-write-wins by id), never duplicates.
        _prior = existing_by_convo.get(c.get("id"))
        if _prior and _prior.get("status") == "pending" and _prior.get("edited"):
            # [OWNER] hand-edited this draft in the dashboard and hasn't sent it yet.
            # A re-draft here would silently discard his words (2026-07-06 audit #8).
            continue
        _rid = _prior["id"] if (_prior and _prior.get("status") == "pending" and _prior.get("id")) \
            else new_id("rw_" + (c.get("id") or ""))
        rec = {"id": _rid, "convo": c.get("id"),
               "contact_id": contact_id,
               "name": c.get("contactName") or c.get("fullName") or "there",
               "phone": c.get("phone", ""),
               # persist email so the send-gate suppression re-check can catch an email-only
               # unsub that lands after this draft is staged (red-team F1 HIGH: the gate
               # passed email="" because the record never carried it)
               "email": c.get("email") or "",
               "channel": "Email" if "EMAIL" in (c.get("lastMessageType") or "") else "SMS",
               "their_msg": their_msg[:400],
               "intent": intent, "draft": draft,
               "proposal": prop_link,
               "status": "held" if not gate["ok"] else "pending",
               "created": now_iso(),
               # ---- C163 SLA aging ----
               "age_hours": age_h, "escalation": _escalation_for(age_h),
               # ---- C166 context window ----
               "context": context_block,
               # ---- C161 conversation state ----
               "convo_state": state_lookup.get(contact_id, {}).get("state", "new"),
               # ---- C167 objection sequence ----
               "objection_sequence": objection_seq,
               # ---- C169 formality mirroring (informational, drafting already applied it) ----
               "their_formality": convo_lint.detect_formality(their_msg),
               # ---- C174 same-company dedupe ----
               "company_dedupe_note": company_flags.get(contact_id, ""),
               # ---- C180 multi-party ----
               "multi_party": multi_party, "multi_party_signal": multi_party_signal,
               # ---- C219 language match + English gloss so [OWNER] can verify a
               # Spanish draft before approving it (he doesn't read Spanish) ----
               "language": lang,
               "draft_en": (d.get("reply_en") or "").strip() if lang == "es" else "",
               # ---- lint gate result, always attached (even when ok, so his UI can
               # show a quality score per F439's "draft-quality score visible" ask,
               # even though F439 itself is a different fleet's item -- attaching the
               # data here costs nothing and doesn't require touching server.py) ----
               "lint": gate,
               # ---- C209 refusal-template suggestion: informational only, never
               # auto-substituted for whatever the LLM actually drafted -- surfaced
               # so [OWNER] can swap it in with one edit if their message asked for
               # something outside scope (SEO guarantees, equity deals, etc) and the
               # LLM's draft didn't already handle it well ----
               "refusal_suggestion": convo_lint.suggest_refusal(their_msg),
               }
        if not gate["ok"]:
            rec["held_reason"] = "; ".join(f"{f['gate']}: {f['detail']}" for f in gate["failures"])
        _save(rec)
        added.append(rec)

        # C173 hot-lead fast path: interested + booking language -> notify NOW, don't
        # wait for the batched notify-block below (drafting above already happened at
        # normal speed; this only affects HOW FAST [OWNER] hears about it).
        if intent == "interested" and any(m in their_msg.lower() for m in _BOOKING_LANGUAGE):
            hot_leads.append(rec)

    print(f"queued {len(added)} reply draft(s) from {len(cands)} inbound candidate(s)")

    if hot_leads:
        base = (planner._config().get("public_base_url") or "").rstrip("/")
        for r in hot_leads:
            planner.notify("HOT LEAD: booking language",
                           f"{r['name']} sounds ready to book. Reply drafted, check it now.",
                           tags="fire")
        print(f"  {len(hot_leads)} hot lead(s) pushed immediately (C173 fast path)")

    if added:
        base = (planner._config().get("public_base_url") or "").rstrip("/")
        if base:
            # One push per draft (max 3) with one-tap Send/Skip buttons. Body stays
            # content-free; buttons carry only the reply id, signed per-action per-day.
            import hashlib
            import hmac
            from store_lib import secret
            day = now_iso()[:10]

            def act(a):
                sig = hmac.new(sign_secret().encode(),
                               f"act:{a}:{day}".encode(), hashlib.sha256).hexdigest()[:20]
                return f"{base}/api/act/{a}?sig={sig}"
            # hot leads already got their own push above; don't double-notify them here
            hot_ids = {r["id"] for r in hot_leads}
            remaining = [r for r in added if r["id"] not in hot_ids]
            for r in remaining[:3]:
                planner.notify("Reply drafted", "A reply draft is ready. Send it or skip it.",
                               tags="speech_balloon",
                               actions=[{"action": "view", "label": "Send draft",
                                         "url": act("reply_send~" + r["id"])},
                                        {"action": "view", "label": "Skip",
                                         "url": act("reply_skip~" + r["id"])}])
            if len(remaining) > 3:
                planner.notify("Warm replies waiting",
                               f"{len(remaining) - 3} more drafts in the dashboard", tags="speech_balloon")
        elif len(added) > len(hot_leads):  # content-free push (just the count) so nothing private leaves the Mac
            planner.notify("Warm replies waiting", f"{len(added)} reply draft(s) ready to approve",
                           tags="speech_balloon")

    # C163: age every PENDING record in the queue (including ones from prior runs,
    # not just this batch) so the SLA fields never go stale between polls.
    aged = refresh_sla_fields()
    if aged:
        print(f"  SLA fields refreshed on {aged} pending record(s)")

    return added


def _run_timers():
    try:
        import proposal_timers
        proposal_timers.run()
    except Exception as e:  # noqa: BLE001
        print(f"proposal timers skipped: {e}")
    try:
        import link_monitor
        link_monitor.run()
    except Exception as e:  # noqa: BLE001
        print(f"link monitor skipped: {e}")
    # C176/212/213: dormancy re-engage + review/referral asks, driven by
    # convo_state.py's snapshot (already refreshed this cycle by _run_convo_state()
    # above). Its OWN try/except with its OWN import (not nested inside the block
    # above) so a failure in either .run() itself OR the import above never blocks
    # this second lane from at least attempting to run.
    try:
        import proposal_timers
        proposal_timers.run_lifecycle()
    except Exception as e:  # noqa: BLE001
        print(f"lifecycle timers skipped: {e}")


def _run_convo_state():
    """C161: recompute conversation states before the poll below reads them, so
    run()'s state_lookup is always fresh, not a stale snapshot from a prior cycle."""
    try:
        convo_state.run(dry=False)
    except Exception as e:  # noqa: BLE001
        print(f"convo_state refresh skipped: {e}")


if __name__ == "__main__":
    from runlog import track
    with track("reply_watch"):
        _run_convo_state()
        run()
        _run_timers()
