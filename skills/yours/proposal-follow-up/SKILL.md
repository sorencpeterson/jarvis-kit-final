---
name: proposal-follow-up
description: Write the follow-up sequence for a sent proposal based on its open data: day-3 opened, day-4 unopened, day-7 takeaway close. Email and SMS versions of each. Price holds 14 days, then requote. [OWNER_COMPANY].
---

# proposal-follow-up

## When to use
A proposal is out and hasn't closed. Input is the proposal state: who, tier, price,
sent date, opened or not, read seconds if tracked, any section-level data. Output is
the right follow-up copy for where they are, in email AND SMS lengths. Nothing sends
without [OWNER]'s click.

## Inputs
1. **The proposal**: prospect name, tier, price, the sent date.
2. **Open data**: opened or not, how many times, read_secs, any section they lingered
   on. This data picks the message. Don't guess at it, read it.
3. **Relationship state**: have they replied or booked before? **SMS only if yes.**
   Cold SMS is banned, TCPA exposure is $500-1,500 per text.

## The three touches (pick by state, never send all three at once)
1. **Day 3, OPENED.** They read it. Assume interest, not rejection. No re-pitch, no
   summary of what they already read. Reference the strongest signal plainly: a double
   open, a full read, a section they sat on (if section data exists, name that section's
   subject; if not, don't invent one). Open counts may be said to them; read seconds and
   tracking mechanics never go in the copy ("you've been through it twice," never "about
   40 seconds"). One ask: the call or the deposit link. An open
   without a reply usually means one unresolved thing, so offer to resolve it live.
2. **Day 4, NOT OPENED.** The proposal isn't rejected, it's buried. Resend with a new
   subject line and a shorter body: the offer in one breath, price and timeline
   included, one link back to the full page. Give them a clean out ("reply not now and
   I'll stop"). Never guilt, never "just bumping this."
3. **Day 7, LAST TOUCH.** The takeaway close. The price holds 14 days from the send
   date, name the actual expiry date, then it's a requote. Pair it with permission to
   say no: "if it's a no, tell me and I'll close your file." Then stop. After day 7 the
   file goes quiet and the 90-day timers take over. No fourth touch.

## Channel lengths
- **Email**: under 90 words, one link max, sign "[OWNER]" alone.
- **SMS**: 1-3 sentences, reads like a text from a guy in a truck.

## Voice (hard rules)
No em-dashes or en-dashes, ever. Short sentences, 9-13 words. Contractions always. No
emojis. Never "just checking in," never "following up on my last email," never
apologize for following up. Numbers and dates do the talking. End on an ask or a hard
stop. Full spec: `business-library/VOICE-SPEC.md`.

## True facts you may state
Price holds 14 days from send, then requote (real rule, not a tactic). 50% deposit
books the slot. Day-3 working preview, approve before live, live 7 days from deposit.
Two revision rounds included. Build slots are real capacity, so slot scarcity may be
stated only when true. Never invent urgency, competitor interest, or fake deadlines.

---

## WORKED EXAMPLE — [WHITE_GLOVE] medspa proposal, opened twice, 90s read time
*Input: Priya, owner of Lumen Aesthetics. White-Glove proposal at [WHITE_GLOVE] sent July 1
after a discovery call (she's replied before, so SMS is allowed). Tracking shows two
opens, about 90 seconds total read time, no section-level data. Today is July 4.*

**State call: opened twice with a real read = day-3 OPENED copy. She's interested and
stuck on something. Since there's no section data, no section gets named.**

**Day-3 opened, email:**

*Subject:* the thing holding it up, Priya

Priya,

Saw the proposal's been read, twice. In my experience that means it's close, and one
thing is unresolved.

Ten minutes on the phone beats a third read: [OWNER_SITE]/book. If it's already a
yes, tap the deposit link in the proposal and your working preview lands on day 3.

[OWNER]

**Day-3 opened, SMS:**
> Priya, saw you've been through the proposal twice. What's the one thing holding it
> up? Ten minutes on the phone settles it either way.

**Day-4 unopened, email (the version if tracking had shown NO opens):**

*Subject:* the 2-minute version, Priya

Priya,

The proposal's sitting unread, so here's the whole thing in one breath: full rebuild
for Lumen, copy and brand included, booking above the fold, [WHITE_GLOVE], live 7 days after
deposit, and you approve everything before launch.

The full page is one click: [proposal link]. If the timing's wrong, reply "not now"
and I'll stop nudging.

[OWNER]

**Day-4 unopened, SMS:**
> Priya, short version of the proposal: [WHITE_GLOVE], live in 7 days, you approve before
> anything launches. Full page here: [proposal link].

**Day-7 last touch, email:**

*Subject:* closing the loop, Priya

Priya,

No pitch, just housekeeping. The proposal price holds 14 days from send, so [WHITE_GLOVE] is
good through July 15. After that it's a requote, because my build calendar changes.

If it's a no, tell me and I'll close your file, no hard feelings. If it's a not-yet,
give me a date and I'll come back then.

[OWNER]

**Day-7 last touch, SMS:**
> Priya, closing the loop on the proposal. The [WHITE_GLOVE] holds through July 15, then it's
> a requote. A no is a fine answer, just tell me.
