#!/usr/bin/env python3
"""Proposal lifecycle timers: proposals stop rotting on autopilot.

Runs after every reply_watch cycle (30 min). Rules, per SENT proposal:
- opened but silent 3+ days  -> draft the permission-to-say-no loop-close (playbook #43)
- never opened after 4+ days -> draft a short resend nudge with a different angle
Each fires ONCE (flag on the record). Drafts land in the ONE approve queue
(replies.jsonl) — [OWNER]'s click still sends everything.

C176/212/213 additions (500-IDEAS-AGGREGATORS.md section C), layered on without
touching any of the original logic above:
  - C176 dormant re-engage: contacts convo_state.py marks 'won' or 'negotiating' that
    have gone quiet 30/60/90 days get a playbook-cadence re-engage draft, once per
    rung (30d fires once, 60d fires once, 90d fires once -- a contact can pass through
    all three over time, never re-fires the SAME rung twice).
  - C212 review-ask: a 'won' contact, 14+ days past their last signal (delivery-ish
    proxy -- see docstring on dormancy_and_lifecycle_drafts() for the honest caveat
    about what "delivered" actually means here), gets a review-ask draft once.
  - C213 referral-ask: a 'won' contact, 30+ days past their last signal, gets a
    referral-ask draft once. This is DISTINCT from agents/referral_timer.py, which
    fires on warm_dispo.jsonl's dispo=='booked' (a call got booked, +30d) -- a
    completely different trigger (call-booked warmth) from this file's won-CONVERSATION
    trigger (a deal actually closed, per convo_state.py). Both can fire independently
    for the same contact if both signals exist; neither file reads the other's state,
    so there's no double-write risk, only occasionally two SEPARATE genuine asks
    (acceptable -- a contact who both booked a call AND became a won GHL conversation
    is exactly the kind of contact worth two honest touches over time, not a bug).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso, new_id, humanize  # noqa: E402
import planner  # noqa: E402
import reply_watch  # noqa: E402
import proposal_factory  # noqa: E402
import convo_state  # noqa: E402


def _pay_link_for(tier_key: str) -> str:
    """C211: same config.json 'payment_links' lookup proposal_factory._pay_link()
    does internally, reimplemented here (2 lines) rather than reaching into that
    module's underscore-prefixed (module-private, by this codebase's own convention
    -- confirmed no other file calls proposal_factory._* functions externally)
    namespace from outside."""
    return str((planner._config().get("payment_links") or {}).get(tier_key) or "")


LOOP_CLOSE = ("No pitch, just closing the loop on the plan I sent for {biz}. "
              "If it's a no, tell me and I'll stop. If it's a not-yet, when should I check back? "
              "The plan's still live here: {link}")
# C205: name-drop variant of LOOP_CLOSE, used when opens >= 2 (a single open is normal
# behavior, worth nothing to name; 2+ opens is a real engagement signal worth
# reflecting back to them -- "you opened it 3 times" reads as attentive, not creepy,
# specifically because it's true and specific).
LOOP_CLOSE_ENGAGED = ("No pitch, just closing the loop on the plan I sent for {biz}. Noticed "
                      "you've opened it {opens} times, so it's clearly on your mind. If it's a "
                      "no, tell me and I'll stop. If it's a not-yet, when should I check back? "
                      "Still live here: {link}")
RESEND = ("Resending this in case it got buried, {first}. I put together a specific plan for "
          "{biz}: five things costing you customers and what I'd do about them. Two-minute read: {link}")
# C211: payment-link inclusion when convo_state.py already shows this contact as WON --
# a proposal follow-up firing for a contact who's already said yes elsewhere (the
# ledger/won-language signal) shouldn't re-pitch, it should hand them the deposit link.
WON_PAYMENT_NUDGE = ("Hey {first}, since we're moving forward on {biz}: here's the link to lock "
                     "in the deposit and get the build started: {pay_link}")
# C211 fallback: a won contact with no configured payment link for their tier still
# gets SOMETHING (never silently drops the follow-up for a won contact), just without
# a dead/missing link -- says the deposit is coming rather than pretending nothing
# changed and reusing the original ambiguous "closing the loop, is it a no?" text,
# which would read strangely to someone who's already said yes.
WON_NO_PAYLINK_FALLBACK = ("Hey {first}, since we're moving forward on {biz}, I'll get the "
                           "deposit link over to you directly, want it by text or email?")


def _days_since(ts: str) -> float:
    try:
        dt = datetime.fromisoformat(ts)
        now = datetime.now(dt.tzinfo or timezone.utc)
        return (now - dt).total_seconds() / 86400
    except (ValueError, TypeError):
        return 0.0


def run() -> int:
    fired = 0
    for r in proposal_factory.load_queue():
        if r.get("status") != "sent" or not r.get("contact_id"):
            continue
        # C187 (audited): a contact suppressed AFTER their proposal was sent (e.g.
        # they later replied "remove me" through a different channel, or
        # campaign_guard.py's unsub-tag scan caught them later) must not still get a
        # loop-close/resend/won-nudge drafted here -- checked FIRST, before any of
        # the age/opens/won logic below even runs. Reuses reply_watch._is_suppressed()
        # rather than duplicating the check a 4th time in this fleet: this file
        # already imports reply_watch specifically to call its OTHER
        # underscore-prefixed function (._save()), an established precedent (also
        # used by warm_followup.py and autopsy.py) that reply_watch.py's helpers are
        # this fleet's shared internal API, not truly module-private.
        if reply_watch._is_suppressed(r["contact_id"], r.get("email") or ""):
            continue
        age = _days_since(r.get("sent_at") or "")
        first = (r.get("name") or "there").split()[0].title()
        biz = r.get("company") or r.get("name") or "your business"
        opens = int(r.get("opens") or 0)
        draft = ""
        kind = ""

        # C211: a contact convo_state.py already shows as WON never gets the
        # loop-close/resend logic below at all -- re-pitching or "closing the loop"
        # on someone who already said yes elsewhere reads as not paying attention.
        # This check is OUTSIDE the won_nudge_drafted once-only gate deliberately: a
        # won contact stays exempt from loop-close/resend for the rest of this
        # record's life, not just until the one-time nudge fires (a real bug here
        # originally -- the SECOND run after the nudge already fired fell through to
        # loop_drafted instead of staying exempt, caught by a fixture test before it
        # shipped). The nudge draft itself still only fires once (won_nudge_drafted flag),
        # and a won contact with no configured payment link gets the honest
        # WON_NO_PAYLINK_FALLBACK once too (still once-only, still never silently
        # dropped) rather than falling through to the ambiguous original loop-close
        # text, which would read strangely to someone who's already said yes.
        is_won = False
        try:
            is_won = convo_state.state_for(r["contact_id"]) == "won"
        except Exception:  # noqa: BLE001 — a convo_state lookup failure should fail
            pass           # OPEN (treat as not-won) so the original logic still runs
        if is_won:
            if not r.get("won_nudge_drafted"):
                pay_link = _pay_link_for(r.get("tier", ""))
                if pay_link:
                    draft = WON_PAYMENT_NUDGE.format(first=first, biz=biz, pay_link=pay_link)
                else:
                    draft = WON_NO_PAYLINK_FALLBACK.format(first=first, biz=biz)
                kind = "won_nudge_drafted"
            # already nudged (either variant) this record's life -- draft stays ""
            # here, falls through to `if not draft: continue` below, permanently
            # exempt from loop-close/resend now that it's won.
        else:
            # C205: 2+ opens is real engagement worth naming; a single open uses the
            # original, unembellished LOOP_CLOSE text unchanged.
            if opens and age >= 3 and not r.get("loop_drafted"):
                if opens >= 2:
                    draft = LOOP_CLOSE_ENGAGED.format(biz=biz, opens=opens, link=r.get("link", ""))
                else:
                    draft = LOOP_CLOSE.format(biz=biz, link=r.get("link", ""))
                kind = "loop_drafted"
            elif not opens and age >= 4 and not r.get("resend_drafted"):
                draft = RESEND.format(first=first, biz=biz, link=r.get("link", ""))
                kind = "resend_drafted"
        if not draft:
            continue
        reply_watch._save({
            "id": new_id("pt_" + r["id"] + kind), "convo": None,
            "contact_id": r["contact_id"], "name": r.get("name") or biz,
            "phone": "", "channel": "Email",
            "their_msg": f"[proposal {'won, payment nudge' if kind == 'won_nudge_drafted' else ('opened, silent ' + str(int(age)) + 'd') if kind == 'loop_drafted' else 'never opened, ' + str(int(age)) + 'd'}]",
            "intent": "followup", "draft": humanize(draft),
            "status": "pending", "created": now_iso(), "src": "proposal_timer"})
        proposal_factory.save({**r, kind: now_iso()})
        fired += 1
        print(f"  {kind} for {biz} ({int(age)}d)")
    if fired:
        print(f"proposal timers: {fired} follow-up draft(s) staged")
    return fired


# ---- C176/212/213: convo_state-driven lifecycle timers ----

# C176: playbook-cadence dormancy rungs, in days. A contact fires AT MOST ONCE per
# rung, ever (see _lifecycle_log()). Deliberately reuses the SAME "closing the loop"
# permission-to-say-no register objections.md #43 already established for proposal
# silence, since it's the same honest, low-pressure move applied to a longer gap.
DORMANCY_RUNGS = (30, 60, 90)

# C216: two opener variants per rung (A = permission-to-say-no register, matching
# objections.md #43's proven move; B = direct question register, a different shape
# to actually generate an A/B signal instead of two paraphrases of the same idea).
# Variant choice is DETERMINISTIC per contact_id (hash-based, not random) so the same
# contact always gets the same variant across a session/re-run -- reply-rate tracking
# needs consistency per contact, not a coin flip that could reassign mid-cadence.
DORMANT_REENGAGE = {
    30: {
        "A": ("Hey {first}, it's been about a month since we last talked about {biz}. "
             "No pitch, just checking: still on your radar, or should I close the file? "
             "Either answer is fine, I just don't want to keep taking up space in your inbox."),
        "B": ("Hey {first}, quick one on {biz}: still looking to get this handled, or did "
             "it get taken care of another way? Just want to know where to file this."),
    },
    60: {
        "A": ("Hey {first}, following up again on {biz}, it's been a couple months now. "
             "If the timing's better now than it was, happy to pick it back up. If not, "
             "just say the word and I'll stop reaching out."),
        "B": ("Hey {first}, checking in on {biz} again, been a couple months. What would "
             "need to change for this to make sense right now?"),
    },
    90: {
        "A": ("Hey {first}, last one from me on {biz}. It's been three months of quiet, "
             "which usually means the timing just isn't right, and that's fine. If "
             "anything changes, my line's open. Good luck out there."),
        "B": ("Hey {first}, closing the file on {biz} after three months of quiet, unless "
             "you tell me not to. Just reply if you want to keep this open."),
    },
}


def _ab_variant_for(contact_id: str) -> str:
    """C216: deterministic A/B pick per contact_id, roughly 50/50 across the contact
    population (not per-message-random). Pure function, easy to test for determinism."""
    import hashlib
    h = hashlib.sha1((contact_id or "").encode()).hexdigest()
    return "A" if int(h[0], 16) % 2 == 0 else "B"


REVIEW_ASK = ("Hey {first}, hope {biz} is treating you well since the build shipped. "
              "If you've got two minutes, a quick review would genuinely help me out, "
              "here's the link: {review_link}. No pressure either way, and thanks either way.")

REFERRAL_ASK = ("Hey {first}, quick one: know anyone else in {niche} who could use a site "
                "like yours? Always happy to take care of people you send my way. No worries "
                "if nobody comes to mind.")

LIFECYCLE_LOG = ROOT / "store" / "lifecycle_timers_log.json"


def _load_lifecycle_log() -> dict:
    import json
    try:
        return json.loads(LIFECYCLE_LOG.read_text())
    except (OSError, ValueError):
        return {}


def _save_lifecycle_log(log: dict) -> None:
    import json
    LIFECYCLE_LOG.parent.mkdir(parents=True, exist_ok=True)
    LIFECYCLE_LOG.write_text(json.dumps(log, indent=2))


def _already_fired(log: dict, contact_id: str, rung: str) -> bool:
    return rung in (log.get(contact_id) or {}).get("fired", [])


def _mark_fired(log: dict, contact_id: str, rung: str) -> None:
    entry = log.setdefault(contact_id, {"fired": []})
    if rung not in entry["fired"]:
        entry["fired"].append(rung)


def dormancy_and_lifecycle_drafts(states: dict | None = None, log: dict | None = None) -> list[dict]:
    """Pure-ish core (takes states/log as params for fixture-testability; run_lifecycle()
    below wires the real files): for every contact convo_state.py knows about, decide
    whether a dormancy re-engage (C176), review-ask (C212), or referral-ask (C213)
    draft is due, using ONLY convo_state.py's last_signal_days -- never a hard file
    read of its own, so it always reflects whatever convo_state.py's most recent run
    computed.

    Honest caveat on C212 "review-ask post-delivery": convo_state.py's 'won' state
    is derived from a ledger entry, a proposal marked won, or won-language in a
    reply -- none of which is literally "the site shipped and [OWNER] confirmed
    delivery." Using won+14d as the review-ask trigger is the best available proxy
    given what's actually on disk today (no separate "delivered_at" timestamp exists
    anywhere in this codebase's stores, confirmed by reading proposal_factory.py's
    queue record shape, warm_dispo.jsonl, and ledger.jsonl -- delivery isn't tracked
    as a distinct event from winning the deal). If a real delivery-date field gets
    added to some store later, this trigger should switch to reading THAT instead of
    the won-state proxy; documented here so that's an easy, deliberate future swap,
    not a silent behavior change.

    Returns a list of {"contact_id", "name", "kind" ("dormant_30"/"dormant_60"/
    "dormant_90"/"review_ask"/"referral_ask"), "draft"} dicts. Doesn't write
    anything -- run_lifecycle() does the actual _save()/log update."""
    states = states if states is not None else convo_state.load_states()
    log = log if log is not None else _load_lifecycle_log()
    out = []
    for contact_id, rec in states.items():
        state = rec.get("state")
        days = rec.get("last_signal_days")
        name = rec.get("name") or ""
        first = (name.split()[0].title() if name else "there")
        biz = name.title() if name else "your business"

        # C176: dormancy re-engage, only for contacts that WERE active (won or
        # negotiating) and have since gone quiet past a rung threshold. A brand
        # 'new'/'dormant'-forever contact never fires this (matches convo_state.py's
        # own definition: 'dormant' already means "was active, now quiet" -- see its
        # docstring -- so gating on state=='dormant' here is correct and sufficient,
        # not an approximation).
        if state == "dormant" and days is not None:
            # iterate HIGHEST rung first (DORMANCY_RUNGS is ascending, so reversed()
            # walks 90 -> 60 -> 30) and take the first-encountered qualifying,
            # not-yet-fired one -- a contact dormant 65 days should get the 60d
            # message, not the 30d one it also technically qualifies for. (A real
            # bug here originally: forward iteration + break always fired the
            # LOWEST rung first, caught by a fixture test before this shipped.)
            for rung in reversed(DORMANCY_RUNGS):
                rung_key = f"dormant_{rung}"
                if days >= rung and not _already_fired(log, contact_id, rung_key):
                    variant = _ab_variant_for(contact_id)
                    draft = DORMANT_REENGAGE[rung][variant].format(first=first, biz=biz)
                    out.append({"contact_id": contact_id, "name": name,
                              "kind": rung_key, "draft": humanize(draft),
                              "ab_variant": variant})
                    break  # only the HIGHEST qualifying rung fires per run, never
                           # multiple rungs stacked in one pass for the same contact

        # C212 review-ask: won + 14d+, proxy for "post-delivery," see docstring above.
        if state == "won" and days is not None and days >= 14 \
                and not _already_fired(log, contact_id, "review_ask"):
            review_link = "https://g.page/r/review"  # placeholder Google review link;
            # the real per-location review link isn't stored anywhere in this codebase
            # today (confirmed: no 'review_link'/'gbp'/'google_review' key in
            # store/config.json or business-library) -- flagged inline in the draft
            # itself via the placeholder text below rather than silently shipping a
            # dead link, so this is visibly a TODO for [OWNER] on his approve click, not
            # hidden.
            draft = REVIEW_ASK.format(first=first, biz=biz, review_link=review_link)
            out.append({"contact_id": contact_id, "name": name, "kind": "review_ask",
                      "draft": humanize(draft) + "\n\n[reply_watch note: review_link is a "
                      "placeholder, swap in the real Google review link before sending]"})

        # C213 referral-ask: won + 30d+.
        if state == "won" and days is not None and days >= 30 \
                and not _already_fired(log, contact_id, "referral_ask"):
            niche = "your line of work"  # convo_state.py's snapshot doesn't carry niche;
            # a generic-but-honest phrase beats guessing a specific niche wrong
            draft = REFERRAL_ASK.format(first=first, niche=niche)
            out.append({"contact_id": contact_id, "name": name, "kind": "referral_ask",
                      "draft": humanize(draft)})
    return out


def run_lifecycle() -> int:
    """Wires dormancy_and_lifecycle_drafts() to the real files: reads convo_state.py's
    live snapshot + the lifecycle log, stages each due draft into replies.jsonl
    (status=pending, exactly like every other queue record -- [OWNER]'s click still
    sends everything), marks the rung fired so it never re-fires.

    C187 (audited): suppress check happens HERE, in the file-I/O wrapper, not inside
    dormancy_and_lifecycle_drafts() -- that function is deliberately pure (no file
    reads) for fixture-testability, so filtering suppressed contacts belongs at this
    layer instead, same principle as convo_reactivation.py's build_batch() checking
    suppress before staging, never inside its own pure classify_segment()."""
    states = convo_state.load_states()
    log = _load_lifecycle_log()
    drafts = dormancy_and_lifecycle_drafts(states, log)
    staged = 0  # actual count of records saved -- NOT len(drafts), which would also
               # count suppressed contacts that got skipped below (a real bug here
               # originally: the return value silently over-reported by exactly the
               # suppressed count, caught by a fixture test asserting a suppressed
               # contact's draft doesn't count toward the return value at all).
    for d in drafts:
        contact_id = d["contact_id"]
        if reply_watch._is_suppressed(contact_id, ""):
            continue
        rec = {
            "id": new_id("lc_" + contact_id + d["kind"]), "convo": None,
            "contact_id": contact_id, "name": d["name"] or "warm contact", "phone": "",
            "channel": "Email",
            "their_msg": f"[lifecycle timer: {d['kind']}]",
            "intent": "followup", "draft": d["draft"],
            "status": "pending", "created": now_iso(), "src": "lifecycle_timer",
        }
        if d.get("ab_variant"):  # C216: only dormancy re-engage drafts carry a variant
            # (same field name proposal_factory.py's own headline A/B test already
            # uses, so any future cross-lane reply-rate rollup reads one consistent
            # field name instead of two different conventions).
            rec["ab_variant"] = d["ab_variant"]
        reply_watch._save(rec)
        _mark_fired(log, contact_id, d["kind"])
        staged += 1
        print(f"  {d['kind']} drafted for {d['name'] or contact_id}")
    if staged:
        _save_lifecycle_log(log)
        print(f"proposal timers (lifecycle): {staged} draft(s) staged")
    return staged


if __name__ == "__main__":
    run()
    run_lifecycle()
