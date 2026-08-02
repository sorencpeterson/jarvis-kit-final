# Segment Plans: the 423 repliers, split by what they actually said

Source: 250-IDEAS-BUSINESS.md B34. Problem: the 423-contact replier list
(`warm-crm-booked-calls` memory / warm-reactivation-423.md) currently gets one blast.
Fix: segment by WHAT they replied about, re-approach each segment with matching
copy instead of one generic re-open.

**Status: built paused.** Segmentation logic below is ready to apply in GHL. Actual
tagging pass and any first-touch sends need [OWNER]'s go-ahead before they fire.

---

## The three segments

### 1. PRICE: objected or asked about cost
**Signal:** reply contains price pushback, budget mention, "too expensive," "what's
the cost," "can you do it cheaper," or similar. Tag `segment-price`.

**Why they stalled:** money was the stated blocker, real or a polite exit. Some are
real budget cases, some used price as the easy no.

**Re-approach angle:** lead with the cheapest real entry point ([LANDING_PAGE] landing page or
the [WEBFIX] webfix bundle if their site is salvageable, not the full [STANDARD_SITE] rebuild) and
the missed-job math from the objection playbook (#1: "one missed job a month costs
you more than this site does").

**First-touch copy:**

**Subject:** a cheaper way in, if that's what stalled us

Hey {{contact.first_name}},

Last time we talked, price was the sticking point. Fair. Here's a smaller door: the
[LANDING_PAGE] landing page, one page, one job (get the call, get the form, get the booking).
Most people who start there upgrade once it's earning its keep.

If your current site's actually fine and just needs fixing, not rebuilding, there's
also the [WEBFIX] webfix bundle, speed, mobile, and SEO cleanup on what you've got.

Either way, one missed job a month usually costs more than either option. Reply and
tell me which one fits.

[OWNER]

{{location.full_address}}
Reply STOP to opt out.

---

### 2. TIMING: said not now, later, busy season, etc
**Signal:** reply contains "not right now," "later," "busy season," "call me back
in," "let me get through," or similar. Tag `segment-timing`.

**Why they stalled:** genuinely not the moment, or timing used as a soft no. Either
way, the honest move is asking what actually changes.

**Re-approach angle:** name the dodge gently (objection #13: "what changes next
quarter?"), offer to hold a build slot instead of re-pitching cold.

**First-touch copy:**

**Subject:** did the timing change?

Hey {{contact.first_name}},

You mentioned the timing wasn't right when we last talked. Just checking back,
genuinely, not a push.

Build takes 7 days from deposit, and I can hold a slot instead of you having to
remember to reach back out. If now's still not it, tell me what needs to change first
and I'll check back around then instead of guessing.

[OWNER]

{{location.full_address}}
Reply STOP to opt out.

---

### 3. INTEREST: showed real interest, no clear blocker (went quiet)
**Signal:** reply was positive or curious (asked questions, said "sounds good,"
"let me think," "send me more info") but never booked or closed. No price or timing
objection stated. Tag `segment-interest`.

**Why they stalled:** usually life got busy, or "send me info" became the place
deals go to die (objection #20). No real objection surfaced, which means the
original interest is probably still real.

**Re-approach angle:** compress straight back to a concrete next step, skip the
re-pitch entirely since they already showed interest once.

**First-touch copy:**

**Subject:** picking this back up

Hey {{contact.first_name}},

You seemed genuinely interested last time we talked, then it went quiet on both
ends, that happens. Not re-explaining the offer, you already got it.

Want to just grab 15 minutes and finish the conversation? {{custom_values.booking_link}}

[OWNER]

{{location.full_address}}
Reply STOP to opt out.

---

## Tagging mechanics (for the build)
1. Pull the 423-repliers export with original reply text intact.
2. Keyword-match pass against the signal lists above (price / timing / interest
   terms) to pre-tag. Anything ambiguous or matching multiple segments gets flagged
   for [OWNER]'s manual call, not auto-assigned.
3. Apply exactly one segment tag per contact. No overlap.
4. First-touch sends go out staggered, capped per day per the deliverability
   defaults in `automation-preferences.md`.
5. Any reply pulls the contact out of all segment automations immediately.
