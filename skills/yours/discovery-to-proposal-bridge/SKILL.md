---
name: discovery-to-proposal-bridge
description: Turn raw discovery-call notes into the right SKU from the pricing tree and a same-day proposal brief the proposal writer can run with. Extracts the anchor number, maps the stated need to one offer, and hands off a tight brief while the call is still warm. [OWNER_COMPANY].
---

# discovery-to-proposal-bridge

## When to use
A discovery call just wrapped and [OWNER] needs to move from messy notes to a proposal
today, while the call is warm. Input: the call notes (what they sell, the anchor number
they gave, where they leak, what they've tried, any timeline or budget signal). Output: a
one-page proposal brief that names the SKU, the price, the ROI anchor in their units, the
three objections to pre-empt, and the one real teardown detail. This is the bridge, not
the proposal itself. The proposal gets written from this brief (money-proposal skill).
Same-day matters: a proposal sent while the call is warm closes; one that rots for a week
requotes.

## Step 1: pull the anchor number
The single most important line in the notes is their number: what a patient, job, or
client is worth to them. If the notes have it, the whole brief prices against it. If they
don't, flag it: **[ASK BEFORE SENDING: their anchor number]** and fall back to the niche
default (medspa tox patient ~[PROJECT_EXAMPLE]/yr) only as a placeholder, never as their number.

## Step 2: map the need to one SKU
Route on what they actually said, not on what's easiest to sell. One SKU, not a menu.
| What the notes show | SKU | Price |
|---|---|---|
| Local service, no booking motion (plumber, roofer, landscaper) | Standard | [STANDARD_SITE] |
| Takes appointments (medspa, salon, gym, dental, clinic), even phone-only today | E-com / Booking | [ECOM_PRICE] |
| Medspa/men's-health with a dead site or Fresha/Vagaro/Linktree-only page | White-Glove | [WHITE_GLOVE] |
| Salvageable existing site, under 4 structural faults | Webfix bundle | [WEBFIX] |
| No site at all (non-medspa), needs to move fast | Landing, then Standard | [LANDING_PAGE] |
| Loses leads in the first few minutes / after hours | Speed-to-Lead Mini-Install | [SPEED_TO_LEAD] |
| Agency, white-label, first order | Agency first build | [FIRST_BUILD] flat |
| Delivered client ready for more | see upsell-existing-client | care / install |

4+ structural faults on an existing site flips Webfix to a Standard rebuild, say why in
one line. Speed-to-Lead ([SPEED_TO_LEAD]) and White-Glove-for-men's-health are the two lanes to
watch: quote them where the notes clearly point there, and White-Glove is confirmed at
[WHITE_GLOVE]. Full routing: `~/Claude/business-library/playbooks/pricing-tree.md`.

## Step 3: write the brief (the handoff, 6 lines)
The proposal writer needs these six things and nothing else:
1. **Who + niche + where they came from** (warm list, referral, GHL tag).
2. **The SKU and price**, one line, from step 2.
3. **The anchor number and the payback line** in their units. "A patient's worth $2K to
   her, the site pays for itself with two." Their number, never a generic ROI line.
4. **The one real teardown detail** observed on their actual site/page. No detail means
   go get one, a proposal with no evidence is a horoscope.
5. **Three objections to pre-empt** (pick by niche from the pricing tree + niche book):
   price anchored to one lost job, the 7-day/day-3-preview timeline, the nephew line.
6. **The close signal + terms**: 50% deposit books the slot, price holds 14 days, two
   revision rounds included. Any timeline/budget signal from the call goes here.

## Voice (hard rules)
The brief is for [OWNER] and the proposal writer, but every quoted client-facing line must
survive being said out loud. No em-dashes or en-dashes, ever. Short sentences, 9-13
words. Contractions always. No emojis. Numbers do the talking. Banned: unlock, leverage,
seamless, elevate, excited, circle back. Full spec:
`~/Claude/business-library/VOICE-SPEC.md`.

## True facts you may state
Six years doing this. Fractional COO who scaled a marketing agency [PRIOR_RESULT] per
year. Day-3 working preview, approve before live, 7 days from deposit. 50% deposit books
the slot, price holds 14 days, two revision rounds included, round 3+ is [CARE_GROWTH]. NDA
standard for agencies. A retained medspa tox patient is worth about [PROJECT_EXAMPLE] a year. Never
invent the anchor number, a teardown detail, client names, or guaranteed results.

---

## WORKED EXAMPLE 1 — medspa discovery call to a White-Glove [WHITE_GLOVE] brief
*Input notes: Lauren, owner-injector, Bloom Aesthetics. Warm list. Says a patient's worth
about [ECOM_PRICE] over the first year. Bookings come from Instagram, bio link goes to a Vagaro
page, no site of her own. Running a new GLP-1 program with nowhere to send people. Got
burned by a $2,000/mo agency that sent reports, not patients. Wants to move "pretty soon,
before summer."*

**PROPOSAL BRIEF | Lauren, Bloom Aesthetics**

1. **Who.** Owner-injector, medspa, warm list ([SECOND_BRAND] re-open).
2. **SKU.** White-Glove, [WHITE_GLOVE]. She has no site of her own, so this is copy + brand +
   photo direction + build, not a reskin. Vagaro embedded above the fold.
3. **Anchor + payback.** A patient's worth [ECOM_PRICE] to her. Say it back: "Two retained
   patients pay for the whole site." That's the price line.
4. **Real teardown detail.** Her Instagram bio link lands on a bare Vagaro page: no
   faces, no prices, no before/afters, and no mention of the GLP-1 program she's paying
   to promote. She's buying the click and dropping the catch.
5. **Pre-empt three.** Price: one retained patient covers most of it, two covers it all.
   Time: live in 7 days, working preview on day 3, she approves before anything ships.
   The burned-by-the-agency wound: fixed price, one countable outcome, not another
   retainer that mails reports.
6. **Close + terms.** She wants it before summer, so lead with the timeline. 50% deposit
   ([DEPOSIT_EXAMPLE]) books the slot, price holds 14 days, two revision rounds included. Care
   Growth+ [CARE_PREMIUM]/mo is the delivery attach, NOT in this proposal. Landmine for the writer:
   never propose replacing Vagaro, embed it.

Why: her number ([ECOM_PRICE]) prices the whole brief, the Vagaro-page detail is real and
observed, and the SKU is the textbook White-Glove signal (no site, needs program pages
and brand). The agency burn becomes the anti-retainer positioning. Care stays out of the
proposal per the pricing tree, it converts at delivery.

---

## WORKED EXAMPLE 2 — agency discovery call to a [FIRST_BUILD] first-build brief
*Input notes: Devin, founder of Tidewater Digital, 6-person marketing agency. Replied to
the cold sequence. Bills clients [WHITE_GLOVE] to [AI_OPS_PRICE] for a site, currently builds with an
offshore contractor who missed the last two deadlines. Passed on two web projects last
quarter because he couldn't staff them. Asked how the white-label thing works. Has a real
project stuck on his desk right now.*

**PROPOSAL BRIEF | Devin, Tidewater Digital**

1. **Who.** Founder, 6-person marketing agency, cold-sequence replier. White-label lane.
2. **SKU.** Agency first build, [FIRST_BUILD] flat. First order only, the proving rate. Rate
   card after: [STANDARD_SITE] standard, [LANDING_PAGE] landing, [ECOM_PRICE] booking. Never promise [FIRST_BUILD] past
   build one, the ongoing rate is [STANDARD_SITE] (offers.md TODO).
3. **Anchor + payback.** His number, not mine: he bills [WHITE_GLOVE] to [AI_OPS_PRICE], my invoice is
   [FIRST_BUILD] on the first one. He clears [ECOM_PRICE] to $4,000 and writes no code. The margin is
   the pitch.
4. **Real teardown detail.** He named it himself: the offshore contractor missed his last
   two deadlines. The receipt answers it directly: handed off Monday, client-ready
   Thursday, day-3 preview he can forward to his client under his own brand.
5. **Pre-empt three.** Reliability: the date is the product, day-3 preview proves it
   early. Invisibility: NDA standard, his brand on everything, I never touch his client,
   said before he asks. Why-not-Fiverr: ran ops inside an agency [PRIOR_RESULT], I know
   what a client breathing down his neck feels like.
6. **Close + terms.** He has a stuck project right now, so the close is "send me that
   scope, first one runs [FIRST_BUILD] flat." $500 books the slot. Unlimited revisions on the
   first build until his client signs off. Landmine for the writer: never sound like a
   threat to his client relationship, sell invisibility, not features.

Why: the whole brief prices off HIS margin ([WHITE_GLOVE]-5,000 bill vs [FIRST_BUILD] invoice), which
is the only number that sells an agency. The stuck project is the close, not a
hypothetical. The first-build rate is flagged clearly so the proposal never promises
[FIRST_BUILD] ongoing, and the invisibility rules lead because that's the fear that kills agency
deals.
