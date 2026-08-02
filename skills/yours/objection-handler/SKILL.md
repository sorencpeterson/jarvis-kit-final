---
name: objection-handler
description: A prospect just said an objection, verbatim. Match it to the counter in [OWNER]'s 50-item objection bank, adapt it to the channel (SMS, email, phone), and apply the repeat-objection rule. Say the counter, then stop talking. [OWNER_COMPANY].
---

# objection-handler

## When to use
Any time a prospect pushes back and [OWNER] needs the reply: mid-call, in an SMS thread,
in an email chain. Input is what they said, as close to verbatim as possible, plus the
channel and whether this objection has come up before. Output is the reply, ready to
say or send. Nothing sends without [OWNER]'s click.

## The bank (match here first, always)
`~/Claude/business-library/playbooks/objections.md` holds 50 counters in his voice,
word-for-word usable. Categories:
- **Price** 1-12 ("too expensive," Fiverr, "cheaper?", payment plans)
- **Timing** 13-20 ("not right now," busy season, "send me info")
- **Trust & proof** 21-28 ("are you legit," "are you AI," "got burned before")
- **Nephew & competitors** 29-34 ("my nephew does websites," "match their price?")
- **DIY / template / scope** 35-42 ("just a template?", "can you also...")
- **Process, ghosting, closing** 43-50 (silence, "ask my wife," buying signals)

The bank counter is the spine. Adapt the nouns to their niche: "job" becomes "patient"
for a medspa, "table" for a restaurant, "install" for HVAC. Never change the move.

## Channel calibration (hard limits)
- **SMS**: 2 sentences max. Reads like a text from a guy in a truck. Never cold SMS.
- **Email**: 4 sentences max. Ends on a question or a hard stop, never a soft trail.
- **Phone**: one spoken line plus one follow-up question. Then the rule that closes
  more deals than any counter: say it and STOP TALKING. Silence closes.

## The repeat-objection rule
The same price objection twice is not an objection, it's a negotiation. Do not re-sell,
do not repeat the first-touch counter, do not add new arguments. The second touch gets
the firmer close: **the price is the price, it holds 14 days from the proposal, then
it's a requote.** Offer them a clean exit ("if it's a no, say so and I'll close your
file"). The only flex that exists is the confirmed trade: up to 15% off for a
testimonial on delivery plus intros, or a case study. Never discount for silence.

## Lines that never soften (the floor)
50% deposit, remainder on delivery. No hourly, ever. E-com/Booking is [ECOM_PRICE] minimum,
no exceptions. No free spec work beyond the teardown and one mockup. Round 3+ revisions
are [CARE_GROWTH]. "Remove me" gets instant, clean compliance (bank #47), and a NO_GO tag.

## Voice (hard rules)
No em-dashes or en-dashes, ever, subject lines included. Short sentences, 9-13 words. Contractions always. No
emojis. Numbers do the talking ([STANDARD_SITE], [PROJECT_EXAMPLE] a year, 14 days). Never defensive,
never groveling, never a discount as an apology. Full spec:
`~/Claude/business-library/VOICE-SPEC.md`.

## True facts you may state
Six years doing this. Fractional COO who scaled an agency [PRIOR_RESULT] per year. 35+
builds. Day-3 working preview, approve before live, 7 days from deposit. A retained
medspa tox patient is worth about [PROJECT_EXAMPLE] a year. Never invent reviews, client names,
or guarantees of rankings or results.

---

## WORKED EXAMPLE 1 — "too expensive" over SMS, first touch AND repeat
*Input: medspa owner, proposal for the [ECOM_PRICE] booking build sent 2 days ago. She's
replied before, so SMS is fine. She texts: "This is way more than I wanted to spend."*

**First touch (bank #1, adapted to her niche, 2 sentences):**
> One retained tox patient is worth about [PROJECT_EXAMPLE] a year, so this site pays for itself
> with two. What's a new patient worth to you?

Why: makes her price the loss, not the site. Her number does the selling. Then wait.
Do not follow up for at least a day.

*Four days later she texts: "Still feels expensive honestly."*

**Repeat touch (the firmer close, 2 sentences):**
> The price holds 14 days from the proposal, then it's a requote. If [ECOM_PRICE] is a no,
> tell me and I'll close your file, no hard feelings.

Why: the second "expensive" is a negotiation probe. Re-selling rewards it. The 14-day
line is true scarcity, and permission to say no gets more replies than any pitch. If
she counters with a number, the only move is the trade: "I can do 10% off for a
testimonial on delivery and two intros to owners like you. Deal?"

---

## WORKED EXAMPLE 2 — "I need to think about it," on a call
*Input: live call, HVAC owner, Standard [STANDARD_SITE] rebuild just quoted. He says: "Let me
think about it and get back to you."*

**The spoken line:**
> Sure, take the time. In my experience "think about it" usually means one specific
> thing is off.

**The follow-up question:**
> Which part is it for you, the price, the timing, or me?

Then stop talking. Let the silence sit until he answers. Route his answer:
- **Price** → bank #1: "One missed emergency job costs you more than this site does.
  What's an average job worth for you?"
- **Timing** → bank #13: "What changes next quarter? If the answer is nothing, the
  timing objection is really something else. What is it?"
- **Trust** → bank #21: "You approve a working preview on day 3 before the balance.
  And I took a client from 400K to a million a year. You're never buying blind."

If it doesn't resolve on the call, park it with the deadline before hanging up:
"The price holds 14 days, then it's a requote. I'll call you Thursday." A dated next
step, always. "Get back to me whenever" is where deals go to die.
