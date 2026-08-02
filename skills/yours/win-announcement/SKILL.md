---
name: win-announcement
description: A deal just closed and money is confirmed. Turn the win into three artifacts in [OWNER]'s voice, a LinkedIn post, a one-line proof addition for proposals, and an SMS-length version for warm threads. Verified facts only, ledger numbers only. [OWNER_COMPANY].
---

# win-announcement

## When to use
The same day money confirms (deposit cleared or paid in full, logged per
`~/Claude/business-library/sops/log-a-win.md`). Input is the win: who, what SKU, the
dollar amount, and any real numbers around it. Output is three ready artifacts.
Nothing posts or sends without [OWNER]'s click. Not for delivered-build proof pieces,
that's `case-study-writer`. This skill fires at the CLOSE, while the win is fresh and
the story is one sentence long.

## Inputs
1. **The ledger line**: amount, client, SKU. Not logged as won yet means not a win yet.
2. **The story facts**: channel (cold sequence, warm list, referral), days from open to
   cash, anything unusual (proposal link never opened, closed on the first call).
3. **Naming clearance**: has the client OK'd being named publicly? Default is no.

## The three artifacts
1. **LinkedIn post** (60-130 words). Open with the receipt or the diagnosis flip, never
   "announcing" anything. The number and the timeline are the story. Land on the offer
   plus a short imperative, or a hard stop. Passes the full `linkedin-post-writer` bar,
   including the five banned patterns (see below), because it publishes on the same feed.
2. **Proposal proof line** (one sentence). Slots into the proof section of the next
   proposal next to "35+ builds." Concrete: amount, timeline, or channel. No adjectives.
3. **Warm-thread SMS** (2 sentences max). Warm contacts only, never cold SMS. The win
   plus one clear ask. Capacity lines must match the live campaign in `offers.md`.

## The five banned patterns (the LinkedIn post rewrites on sight)
Inherited from `linkedin-post-writer`; a win post breaks these more than any other,
because "we won" begs to be dressed up. Kill all five:
1. **Cliche hooks:** "Excited to announce," "Thrilled to share," "Big news," "Humbled
   to." The receipt or the number opens, never a feeling.
2. **Rule-of-three crutch:** no "faster, cheaper, better" triads. One real detail beats
   three parallel ones. Scan for a comma triad and cut two.
3. **"Here's what I learned":** no lesson-listicle, no "3 takeaways from closing this."
   The close is the story, not a lecture.
4. **Broetry line breaks:** no one-sentence-per-line whitespace theater. Real
   paragraphs.
5. **Emoji bullets / emojis anywhere.** None. Also banned: "milestone," "journey,"
   "humbled," gratitude-as-humblebrag, "Agree?", "Thoughts?"

## Which artifact when (don't fire all three every time)
- **LinkedIn post:** only when the win is public-safe (naming cleared, or it anonymizes
  cleanly) AND there's a real audience reason to post. A win a week is noise; a notable
  close, a first in a niche, or an unusual story earns the post. Otherwise skip it.
- **Proposal proof line:** always produce it. It costs nothing and quietly sharpens the
  next proposal. This is the one artifact that fires on every logged win.
- **Warm-thread SMS:** only when the win maps to a live capacity line in `offers.md`
  (an open partner slot, room on the bench) or to a specific warm contact it's relevant
  to. No live scarcity fact means no SMS, silence beats a manufactured urgency text.

## Naming rules (hard)
- **White-label agency clients: anonymize by default** ("a software agency," "a 6-person
  brand shop"). The model sells invisibility. Naming them needs their explicit OK.
- **Their end clients: never.** NDA. Not even details that could identify them.
- **Direct clients**: name only with a recorded OK (the +14 day testimonial ask is where
  that permission usually lands). Until then: niche plus region.
- **Numbers**: only what the ledger shows or what [OWNER] typed in. Days-to-close comes
  from real dates. A gap stays a gap. Never round a story upward.

## Voice (hard rules)
No em-dashes or en-dashes, ever. Short sentences, 9-13 words. Contractions always. No
emojis, no hashtag piles (0-2 max on LinkedIn). First line carries the number. Banned:
excited, thrilled, milestone, humbled, journey, unlock, leverage, seamless. Read it
aloud: HubSpot template means kill it, contractor on the phone means ship it. Full
spec: `~/Claude/business-library/VOICE-SPEC.md`.

## True facts you may state
Six years doing this. Fractional COO who scaled an agency [PRIOR_RESULT] per year. 35+
white-label builds, 48-72 hr first drafts, day-3 preview, NDA standard. Taking on 3
agency partners this quarter (verify against offers.md before using). Never invent
testimonials, end-client details, or results the client didn't report.

---

## WORKED EXAMPLE: the Acme Co Soft win
*Input: ledger 2026-07-04, won, [STANDARD_SITE], "Acme Co Soft - WL Webdev." Came in through
the cold white-label outreach sequence. Proposal link never opened, closed inside the
email thread. Roughly 3 days from first reply to cash. No naming clearance yet.*

**1. LinkedIn post** (anonymized, no clearance on file):

> Cold email to [STANDARD_SITE] closed in 3 days.
>
> A software agency came through my white-label outreach last week. The proposal link
> was never opened. The deal closed inside the thread itself.
>
> That's not unusual. Most agency owners don't need a deck. They need one answer: can
> you build this under my brand, on time, without me managing it?
>
> I can.
>
> White-label web development for agencies with a fulfillment bottleneck. You sell the
> work, I build it, your client never knows I exist.
>
> Send me the scope.

Why: the receipt opens, the diagnosis flip carries the middle, the offer closes. Every
number is from the ledger. If Acme Co later OKs their name, swap "A software agency"
for "Acme Co Soft" and change nothing else.

**2. Proposal proof line:**

> Latest close: a white-label agency build, cold email to signed in 3 days, before the
> proposal link was ever opened.

**3. Warm-thread SMS** (agency contacts on the warm list):

> Closed another white-label build last week, cold email to cash in 3 days. That fills
> one of the 3 partner slots I opened this quarter. Want the one-pager?

Why: the slot math is real only because Acme Co is an agency client landed this
quarter and offers.md says 3 partners this quarter. If either fact moves, the line
becomes "If your bench is backing up this month, I have room. Want the one-pager?"
