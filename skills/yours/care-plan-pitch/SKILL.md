---
name: care-plan-pitch
description: Write the post-delivery care plan attach for a just-delivered site, the [CARE_BASIC]/[CARE_GROWTH]/[CARE_PREMIUM] monthly tiers. Pitched inside the delivery email and again at +30 days, anchored on what breaks without it, tier steered by client size. Care converts at delivery, not at signing. [OWNER_COMPANY].
---

# care-plan-pitch

## When to use
A build is delivering today, or delivered ~30 days ago with no care plan attached.
Input: the client, their tier and niche, what the delivery included, and (for the +30d
touch) any real month-one facts from the logs. Output: the care section written into
the delivery email, or the +30d follow-up message. The pricing tree rule is absolute:
care converts AT DELIVERY, not at signing. Never pitch care on the sales call beyond
one mention.

## The two moments
1. **At delivery.** The pitch rides inside the delivery email, right after the QA
   report. The QA report is the setup: it proves the boring work is real. Frame as the
   two honest paths (objection bank #46): own it fully and call when needed, or care
   keeps it earning. Seed the observed pattern: "most owners try a month alone, then
   hand it over."
2. **At +30 days** (only if they passed at delivery). Short. Recap what month one
   actually required, with REAL numbers from the logs if [OWNER] supplies them (updates
   shipped, backups run, downtime). No log data means no counts, say the categories
   instead. Never invent a number.

### Parsing the +30d log into the recap (examples)
The care log is raw events; the recap is one honest sentence of counts. Roll events up,
keep qualifiers, drop anything the log doesn't show.
- Log: `2 core updates, 4 plugin updates, 1 backup restore test, 0 downtime` -> "Six
  updates shipped behind the scenes, backups tested, zero downtime." (2+4 rolls to six;
  the restore test becomes "backups tested"; zero downtime is a real count.)
- Log: `3 edit requests, all handled, avg 2 days` -> "Three edits turned around, each
  inside a couple days." Never tighten "avg 2 days" to "all within 24 hours."
- Log: `uptime monitor: 1 blip, 4 min, auto-recovered` -> "One four-minute blip, caught
  and recovered." Don't inflate a blip into "an outage we saved you from."
- Log empty or missing -> no counts at all: "The boring work ran, updates, backups,
  uptime watch." Categories only. A number that isn't in the log never appears.

## Anchor on what breaks without it (pick 2-3, their stack)
Updates pile up until one breaks the site. No backups means one bad update from a
blank homepage. Edits sit undone for months. Downtime goes unnoticed for days. The
seasonal page never goes up (medspa: gift cards in November, laser season in
January). Anti-anchor: care is not a marketing retainer. No reports instead of work.

## Tier steer (by client size and motion)
| Tier | Price | Who |
|---|---|---|
| Care Basic | [CARE_BASIC]/mo | Small local, brochure site, rarely changes. Updates, backups, hosting watch. |
| Care Growth | [CARE_GROWTH]/mo + $250 onboarding | Businesses that send edits and run marketing. Adds edits, monthly report, priority. |
| Care Growth+ | [CARE_PREMIUM]/mo | Medspa default per the niche book: adds a monthly content push timed to their season. |

Pitch ONE tier, the right one. Name the tier below it only if they balk.

**The routing fork (medspa-first vs generic), decide before writing a word:**
- **Medspa / aesthetics lane:** default to **Care Growth+ [CARE_PREMIUM]**. The seasonal content
  push is the justification (laser in January, bridal in May, gift cards in November),
  and Growth+ is the confirmed medspa attach in `sops/niche-books/medspa.md`. If they
  balk, **Growth [CARE_GROWTH] is the floor**, never drop a medspa to Basic [CARE_BASIC]. Growth+ is still
  PROPOSED-v2 in `offers.md`, so quote the [CARE_PREMIUM] in the medspa lane ONLY.
- **Generic lane (every other niche):** the ladder tops at **Care Growth [CARE_GROWTH]** until
  [OWNER] confirms Growth+ more broadly. A local service business that sends edits and runs
  marketing gets Growth [CARE_GROWTH]; a brochure site that rarely changes gets Basic [CARE_BASIC]. Never
  quote [CARE_PREMIUM] outside medspa.
- **Which lane is this?** If the niche is medspa/aesthetics (owner-injector, booking
  platform, before/afters), it's the medspa lane. Everything else is generic. When in
  doubt, it's generic, and the ceiling is [CARE_GROWTH].

Existing clients moving up never pay the $250 onboarding, they're already onboarded.

## Voice (hard rules)
No em-dashes or en-dashes, ever. Short sentences, 9-13 words. Contractions always. No
emojis. The pitch is 3-5 sentences inside the delivery email, never a brochure. Two
paths offered plainly, one clear next step, then stop. Full spec:
`~/Claude/business-library/VOICE-SPEC.md`.

## True facts you may state
The QA report is real and attached (deliver-a-site SOP). Edits turn around in 48
hours on Growth and up. Month-one counts come from logs or from [OWNER], never from
imagination. Never claim uptime stats or results you can't show.

---

## WORKED EXAMPLE: [WHITE_GLOVE] medspa build, delivery day, then +30d
*Input: Kelsey, Glow Theory Aesthetics, White-Glove [WHITE_GLOVE], delivering today. Fresha
embedded above the fold, GLP-1 program page, consent-gated gallery. Medspa lane, so
the steer is Growth+ [CARE_PREMIUM].*

**1. The delivery email (care attach inside):**

> Kelsey, glowtheoryaesthetics.com is live.
>
> QA report attached. Every button tapped on a phone, the Fresha flow tested end to
> end, forms submitted for real. Booking sits above the fold, like we said it would.
>
> Two ways to run it from here. You own it fully and call me when something's needed.
> Or Care Growth+ at [CARE_PREMIUM] a month: updates and backups handled, uptime watched, your
> edits turned in 48 hours, and one content push a month timed to your calendar.
> Laser season in January, bridal in May, gift cards in November. The site stays as
> sharp as it is today.
>
> Most owners try the first month alone and then hand it over. Skip the month: reply
> "care" and it's running by Friday.
>
> [OWNER]

Why: the QA report earns the pitch before it starts. The seasonal calendar is the
Growth+ justification in her units. Two honest paths, one word to say yes.

**2. The +30 day touch (she passed at delivery). Input adds: care log shows 2 core
updates, 4 plugin updates, one backup restore test, zero downtime.**

> Kelsey, the site's been live a month, quick read from my side. Six updates shipped
> behind the scenes, backups tested, zero downtime. That's the boring work Care
> covers, and next month it includes putting your fall pigment-correction push up
> before September. Growth+ is [CARE_PREMIUM] a month. Want it on? Reply "care" and it starts
> this week.

Why: month one is now evidence instead of a promise, and every count came from the
log. The next seasonal deadline (September, from the niche book calendar) gives the
yes a reason to happen now. One ask, then silence.
