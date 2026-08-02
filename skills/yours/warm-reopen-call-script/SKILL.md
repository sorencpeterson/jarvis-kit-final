---
name: warm-reopen-call-script
description: Build the live phone script for re-opening a warm [SECOND_BRAND] lead who booked a call or replied months ago and went cold. The vague re-open, the niche-swap line, the answer/voicemail/gatekeeper branches, the voicemail-to-text-to-email fallback, and the on-call pivot to the offer that fits. [OWNER]'s week-1 money motion. [OWNER_COMPANY].
---

# warm-reopen-call-script

## When to use
[OWNER] is about to dial (or has just reached) one of the 481 warm leads: 58 who booked a
call and never connected, 423 who replied. They came in on **[SECOND_BRAND]** about
growing their business, NOT about a website. Input: the name, niche, phone/email on
file, and how they came in (booked vs replied, roughly how long ago). Output: the exact
spoken opener, the branch to run for what happens (answer, voicemail, gatekeeper), the
fallback chain, and the pivot line to whatever offer their need points at. This is a
re-open, not a pitch and not a cold call. Full framework:
`~/Claude/business-library/playbooks/warm-reopen-call-framework.md`.

## The one discipline
**Re-open vague, diagnose live, pivot to the offer that fits.** Pitching anything on the
first touch is how these calls died the first time. The first touch is a door, not a
walk-through. Assume they do NOT remember the call. Do not say "remember when we spoke,"
do not name a date, do not reference what was discussed. Own the drop, give a reason for
calling now, ask one vague question, then listen.

## The niche-swap line (the only thing that changes in the opener)
Swap the noun to match the row. Everything else in the opener is fixed.
| Niche | The vague question |
|---|---|
| Medspa / clinic / men's health | "still looking to get more patients booked, or did you get that handled?" |
| HVAC / plumbing / electrical / contractor | "still looking to get more jobs on the calendar, or did you get that sorted?" |
| Salon / barber | "still looking to keep the chairs full, or is that handled?" |
| Anything else / unknown | "still looking to get more customers in the door, or did you get that handled?" |

The "or did you get that handled" is load-bearing. It gives them a clean out, which is
what makes the yes real. Never drop it.

## The five stages (compressed; full version in the framework)
1. **Re-open (30-45s).** Own the miss, give a reason for calling now, ask the one vague
   question. No pitch.
2. **Need-find (5-8 min).** Start wide ("walk me through where things are at with
   getting new customers"), then shut up. Get their anchor number in their language
   (patient/job/client worth). Find where they leak (what happens at 9pm). Name the last
   thing they tried. Numbers and pain out of THEIR mouth.
3. **Pivot (2-4 min).** Reflect the need, name ONE offer, state the price once, go
   silent. Their problem, your SKU, the price anchored to their number. No buffet.
4. **Handle and close (2-3 min).** One counter then silence. On a yes: deposit link plus
   first intake item, in one breath. On a maybe: real objection, handle once, hard date.
5. **No-answer / no.** Voicemail then text. A clean no gets instant clean compliance.

## The pivot map (stage-2 signal to offer)
| What they say | Offer | Price |
|---|---|---|
| "Site can't take a booking" / "I run a Calendly link" | Booking | [ECOM_PRICE] |
| "My site's dead / embarrassing / a Fresha page" | White-Glove | [WHITE_GLOVE] |
| "Site's fine, just slow or losing people" | Webfix (or booking fix) | [WEBFIX] |
| "I don't really have a site" | Landing, then Standard | [LANDING_PAGE] |
| "I lose leads in the first few minutes / after hours" | Speed-to-Lead Mini-Install | [SPEED_TO_LEAD] |
| Local service, standard need | Standard (Booking if they take appointments) | [STANDARD_SITE] |
| "I'm an agency drowning in delivery" | White-label lane, switch scripts | see agency book |
| "I'm the owner buried in the work" | COO / Ops Audit lane, switch scripts | see MONEY pack |

Speed-to-Lead is [SPEED_TO_LEAD] (offers.md marks it PROPOSED-v2, quote it in the warm-call pivot
where the need is clearly a speed leak, hold otherwise). All build prices are confirmed.

## The fallback chain (no answer)
Voicemail plus text beats either alone. Order: dial, leave voicemail, send the text
right after. Email only for the ~16 booked (and the 421 repliers) who have an address.
Every fallback drives to [OWNER_SITE]/book. Templates live in MONEY-THIS-MONTH.md
Sections 2-3, reuse them verbatim.

## Voice (hard rules)
Every line is SPOKEN, so it must survive being said out loud by a guy who's mildly
impatient to get back to work. No em-dashes or en-dashes, ever. Short sentences, 9-13
words. Contractions always. No emojis. Numbers do the talking. State the price once,
then STOP TALKING, silence closes. Banned: unlock, leverage, seamless, elevate, excited,
circle back, touch base. Full spec: `~/Claude/business-library/VOICE-SPEC.md`.

## True facts you may state
Six years doing this, never rounded up. Fractional COO who scaled a marketing agency
[PRIOR_RESULT] per year. Day-3 working preview, approve before live, 7 days from deposit.
A retained medspa tox patient is worth about [PROJECT_EXAMPLE] a year. Never claim named clients,
review counts, or guaranteed results. Never reference the specifics of the original call
you don't have, assume they forgot it.

---

## WORKED EXAMPLE 1 — medspa owner who booked 5 months ago, phone-only
*Input: Dana, owner-injector, Radiance Medical Aesthetics. Booked a [SECOND_BRAND] call
about 5 months ago, never connected. Phone on file, no email. Runs tox, filler, and a
new GLP-1 program.*

**The opener (she picks up):**
> Hey, is this Dana? It's [OWNER] over at [SECOND_BRAND]. We had a call on the books a
> while back and somewhere along the way we never actually connected. That one's on me.
> Your name came up this week, so I figured I'd just reach out direct instead of playing
> phone tag. Quick one while I've got you. Are you still looking to get more patients
> booked, or did you get that handled?

Then stop and let her answer. If yes, move to need-finding, don't pitch.

**Need-finding (she says bookings are still soft, especially the GLP-1 program):**
> Walk me through where things are at right now with getting new patients.

Then shut up. When she lands, get the anchor and the leak:
> What's a patient worth to you over the life of a program, honestly?

> Where do bookings come from now, and what happens to someone who finds you at 9pm
> and wants to start the weight-loss program?

She says patients run [PROJECT_EXAMPLE] to $3,000, most come from Instagram, and the bio link goes
to a Fresha page that doesn't even mention GLP-1. Write the number down. Say it back.

**The pivot (her site is a Fresha-only page with no program page):**
> So you told me a patient's worth two to three grand, and the guy who finds you at 9pm
> ready to start weight loss lands on a Fresha page that doesn't mention the program.
> You're paying Instagram for that click and dropping the catch. The fix is a real site
> with the program on its own page and booking above the fold. That's White-Glove,
> copy, brand, and the build. Thirty-five hundred.

Then silence. Count to five. First to speak owns the objection.

**On a yes:**
> Two things. Tap the deposit link I'll text you, then send me your logo. Half now, half
> when it's live in 7 days, and you see a working preview on day 3.

Why: she booked 5 months ago and won't remember it, so the opener never references the
old call. The vague question re-opens the door. Her number ($2-3K) does the selling in
the pivot, not a generic ROI line. Fresha-only page plus a program with no home is the
textbook White-Glove [WHITE_GLOVE] signal, and the landmine (never propose replacing Fresha) is
respected: the site catches, Fresha books.

---

## WORKED EXAMPLE 2 — plumber who replied once, then ghosted
*Input: Mike, owner of a plumbing company. Replied once to a [SECOND_BRAND] text months
ago ("what's this about?"), never wrote back. Phone and email on file.*

**Dial 1, no answer. Voicemail:**
> Hey Mike, [OWNER] with [SECOND_BRAND]. We connected a while back and never picked it
> back up, and your name came across my desk this week. If you're still looking to get
> more jobs on the calendar, I'd love to reach back out. Shoot me a text at this number
> or grab a time at [OWNER_SITE]/book. No pressure either way. Talk soon.

**Text, right after the voicemail:**
> Hey Mike, it's [OWNER] with [SECOND_BRAND]. We connected a while back and never took it
> anywhere. Your name came up this week so I wanted to reach back out. Still looking to
> book more jobs, or did you get it sorted? Happy to pick it up: [OWNER_SITE]/book

**He texts back "still slammed but leads slip through the cracks." Move to the call or
keep it in text, but diagnose before pitching:**
> What's an average job worth to you? And when a call comes in after hours and you can't
> grab it, what happens to that lead?

He says jobs run $400 to $2,000 and after-hours calls usually go to whoever answers
first, which isn't him.

**The pivot (this is a speed leak, not a website problem):**
> So you're paying to make the phone ring and losing the after-hours ones to whoever
> calls back first. That's a five-minute problem, not a website problem. I install a
> speed-to-lead setup that texts every new lead back in under a minute, day or night,
> so you stop losing them to the next guy. That's a [SPEED_TO_LEAD] install, one time.

Then silence.

**On a maybe:**
> The one thing I'd fix first is the after-hours leak, it's costing you jobs you already
> paid to get. Grab a slot this week and we're done in 15 minutes: [OWNER_SITE]/book

Why: a one-word ghost months ago gets the same vague re-open, framed as "we connected"
not "you never replied," no guilt. The opener never assumes he remembers. His stated
pain (leads slip after hours) points straight at the [SPEED_TO_LEAD] Speed-to-Lead Mini-Install,
not a build, so the pivot names that offer and anchors to his job value. One clean
counter on the maybe, then a dated next step. Never end without a date.
