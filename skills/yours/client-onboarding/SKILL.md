---
name: client-onboarding
description: Write the post-deposit intake message for a just-closed build. One congratulations line, the exact asset checklist from the delivery SOP, plain deadline framing (their delay = timeline slip), and what happens next with dates. [OWNER_COMPANY].
---

# client-onboarding

## When to use
The moment a deposit clears on any build tier: Landing [LANDING_PAGE], Standard [STANDARD_SITE],
E-com/Booking [ECOM_PRICE], White-Glove [WHITE_GLOVE], Agency first build [FIRST_BUILD], Webfix [WEBFIX].
Output is the intake message, ready to send. The rule behind it comes from the delivery
SOP: **missing intake items = the build does not start.** This message exists to clear
Stage 0 in one pass. Source: `~/Claude/business-library/sops/deliver-a-site.md`.

## Inputs
1. **The deal**: client name, tier, price, what they bought, deposit date.
2. **The niche**: it changes the checklist (medspa adds consent and licenses).
3. **What's already in hand** from the sales process (logo? domain? copy?). Never ask
   for something they already gave.

## The message (4 beats, in order)
1. **One congratulations line.** Exactly one, not gushing. "Deposit landed. You're on
   the build calendar" is the energy. No "so excited to work together."
2. **The asset checklist**, numbered, from the SOP's Stage 0 gate:
   - Logo files (vector preferred, biggest PNG otherwise) and brand colors
   - Copy source or copy points (White-Glove: [OWNER] writes it, they approve it)
   - Photos of the business, team, space (missing ones get pro stock until swapped)
   - Testimonials with real full names, never initials-only
   - Form destination: which email or number inquiries should land at
   - Booking or calendar embed URL if their business books
   - Domain/DNS access: they create a temp admin at the registrar. Credentials never
     move through chat, text, or email. Say this rule in the message.
   - Hosting decision, GA4/GTM id if one exists
   - Image licenses: where each photo came from, so the licensing log stays clean
   - **Medspa extras**: before/afters WITH signed patient consent only, practitioner
     license and credentials as they should display, service menu with from-prices,
     financing partners (Cherry, CareCredit)
3. **Deadline framing, said plainly.** Their delay is timeline slip and it gets named
   as such, without apology: "every late item moves your launch a day. The clock runs
   on assets, not on me." Give the checklist a specific due day.
4. **What happens next, with dates.** Day-3 working preview, they approve before
   anything goes live, two revision rounds included (round 3+ is [CARE_GROWTH]), live 7 days
   from deposit, balance due on delivery. Weekdays make it real: "preview Thursday."

## Voice (hard rules)
No em-dashes or en-dashes, ever. Short sentences, 9-13 words. Contractions always. No
emojis. One congratulations, zero exclamation points. Plain deadline talk beats polite
deadline talk. Sign "[OWNER]" alone. Full spec:
`~/Claude/business-library/VOICE-SPEC.md`.

## True facts you may state
Day-3 working preview. Live 7 days from deposit (agency builds: 48-72 hr first draft).
50% deposit paid, remainder on delivery. Two revision rounds included, [CARE_GROWTH] per round
after. Care plans exist ([CARE_BASIC]/[CARE_GROWTH]/[CARE_PREMIUM] a month) but get pitched at delivery, not in
this message. Never promise a date the asset list doesn't support.

---

## WORKED EXAMPLE — [WHITE_GLOVE] medspa White-Glove build
*Input: Kelsey, owner-injector, Glow Theory Aesthetics. Closed Monday on White-Glove at
[WHITE_GLOVE], deposit of [DEPOSIT_EXAMPLE] cleared today (Monday). She books through Fresha. Logo
already received during the sales call.*

**Subject:** Glow Theory build starts now. One list to clear.

Kelsey,

Deposit landed. You're on the build calendar and the clock is running.

I have your logo already. Here's everything else I need by Wednesday:

1. Brand colors, or tell me to pull them from the logo.
2. Your Fresha booking link. The site embeds it front and center. Nothing about how
   you book changes.
3. Before and after photos, only ones with signed patient consent. I build the
   gallery, you supply consented images. That rule protects you.
4. Your license and credentials exactly as you want them displayed. Patients vet
   injectors, so we show them.
5. Your service menu with prices. Even "from" pricing works. Hidden prices send
   patients to whoever shows them.
6. Photos of you, the team, and the space. Anything missing gets pro stock until you
   swap it in.
7. Where inquiries should land: which email or phone number.
8. Domain access: create a temporary admin at your registrar and send me the username.
   Never send passwords by text or email, to me or anyone.
9. GA4 or Google tag ID if you have one. If you don't know what that is, say so and
   I'll check.
10. Financing partners you take, like Cherry or CareCredit, so the logos go up.

Plain truth about the timeline: the build doesn't start until this list is done, and
every late item moves your launch a day. The clock runs on assets, not on me.

Get it in by Wednesday and here's your week:

- **Thursday:** working preview link. Takes you 20 minutes to review.
- **Two revision rounds included.** Round 3 and up is [CARE_GROWTH] a round.
- **Next Monday:** live. Tested on a phone, booking checked with a real submission.
- Balance of [DEPOSIT_EXAMPLE] is due on delivery.

White-Glove means I write the copy and direct the photo choices. You approve every
word and image before anything goes live.

Send items as they're ready, don't wait for a complete set. Fresha link first.

[OWNER]
