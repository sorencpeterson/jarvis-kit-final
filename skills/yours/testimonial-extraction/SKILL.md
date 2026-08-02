---
name: testimonial-extraction
description: Run the +14-day testimonial ask, then turn a raw client reply into a clean, VERIFIED testimonial line — facts the client actually said, nothing added. The ask that gets a real quote and the edit that keeps it honest. [OWNER_COMPANY].
---

# testimonial-extraction

## When to use
Two jobs, often in sequence. (1) It's ~14 days after a delivered build and the pricing tree's
upsell path calls for the testimonial ask. (2) A client replied with praise (from that ask, an
email, a text) and it needs turning into a usable testimonial line. Input: a delivered client
and their real words if you have them. Output: the ask message, or a verified quote plus a
permission-to-use line. Nothing gets published without the client's OK and [OWNER]'s click. The
referral ask is a separate, later touch (`referral-ask`, +30 days); never stack them.

## The two moments
1. **The +14-day ask.** Short, warm, one favor. Frame it around their result, make it
   effortless to answer, and ask a specific question so you get a specific answer. "How's the
   site working?" gets "good." "What's changed since it went live, and would you say a line I
   can quote?" gets a testimonial.
2. **The extraction.** They replied. Now cut their words into one or two clean lines that read
   like a real person, keep every fact they actually stated, and confirm you may use it with
   their name. This is where honesty gets enforced.

## The ask (4 rules)
1. **Open with their result, not your need.** One real line about their site, a number from
   the care report if there is one. Reminds them why they'd vouch.
2. **Make it a 30-second reply.** Ask one or two specific questions they can answer in a
   sentence. Offer to draft a line from their answer for their approval, that removes the
   blank-page friction that kills most testimonial asks.
3. **Ask for permission in the same message.** "If you're happy, can I use a line from this
   with your name and business?" Get the yes before anything gets published.
4. **No groveling, no bribe.** A happy client vouches because the work was good. Zero "it would
   mean the world," no discount-for-a-review (that's the 15%-trade rule's lane, and even there
   it's testimonials in exchange for a discount agreed up front, never begging after).

## The extraction (turn a raw reply into a verified line)
- **Facts only, theirs only.** Every claim in the final quote must be something the client
  actually said or a documented fact. If they wrote "the booking thing is way easier now," the
  quote can say that. It cannot become "bookings doubled" unless they said a number.
- **Tighten, don't inflate.** Cut filler, keep meaning. "Yeah so honestly it's been great, the
  site looks so much more professional and people can actually book now" becomes "The site
  looks far more professional, and people can actually book now." Same facts, cleaner line.
- **Preserve qualifiers.** "Seems like more calls" stays "seems like," never hardens to "more
  calls." A softened claim is honest; a hardened one is a liability.
- **Keep their voice.** A testimonial that sounds like [OWNER] wrote it is worthless. Leave their
  phrasing mostly intact; the client should read it and think "yeah, I said that."
- **Attribution, verified.** Name + business + role only if they OK'd it. Default without
  explicit permission: first name + niche + region ("Kelsey, medspa owner, Southeast"). Never
  invent a fuller attribution than they granted.

## The verified-line gate (all must pass before it's usable)
1. The client said it, or it's a documented fact.
2. The client OK'd being quoted, and OK'd the level of attribution used.
3. No number appears that the client didn't state.
4. Qualifiers are intact, no claim is stronger than the source.

If any fail, the line is a draft, not a testimonial, and it doesn't go on a proposal or a
site.

## Voice (hard rules)
No em-dashes or en-dashes, ever, in [OWNER]'s ask. (The client's own quote keeps their
punctuation, lightly cleaned.) Short sentences, 9-13 words. Contractions always. The ask is
email under 90 words or SMS 1-3 sentences. Full spec:
`~/Claude/business-library/VOICE-SPEC.md`.

## Output structure (produce this)
1. **The +14-day ask** (email and SMS versions), ready to send.
2. **When a reply exists:** the extracted verified line(s), plus the attribution as granted.
3. **A flag:** anything in their reply that can't be used yet (a number they implied but didn't
   state, permission not yet given), and the one-line follow-up that would get it.

---

## WORKED EXAMPLE — a happy medspa client
*Input: Kelsey, owner-injector, Glow Theory Aesthetics. White-Glove build, live 14 days. Care
report shows 61 booking clicks in the first two weeks. No testimonial or permission yet.*

**1. The +14-day ask (email):**

> Subject: quick one, Kelsey
>
> Kelsey, your site's two weeks in and the first report counted 61 booking clicks through it.
> Good sign.
>
> One small favor. What's actually changed for you since it went live, even one sentence? If
> you're happy with it, can I quote a line from your reply, with your name and the practice? I'll
> draft it from whatever you send so it's zero work for you.
>
> [OWNER]

**The SMS version:**

> Kelsey, site's 2 weeks in, 61 booking clicks already. Quick favor: one line on what's changed
> since launch? And ok to quote you, with your name and the practice? I'll write it up from your
> reply.

**2. The extraction (her reply comes back):**
*Raw reply: "Honestly it's been such a relief, the old Fresha page was embarrassing and now it
actually looks like our practice. Booking is way smoother and I think we're getting more of the
after-hours ones. You can totally use my name, Glow Theory."*

**Verified line:**

> "The old page was embarrassing. Now it actually looks like our practice, and booking is way
> smoother." — Kelsey R., owner-injector, Glow Theory Aesthetics

**Attribution:** full, she granted name + practice ("you can totally use my name, Glow
Theory").

**3. Flag:** she said "I think we're getting more of the after-hours ones." That's an implied
result but she qualified it with "I think" and gave no number, so it stays OUT of the quoted
line as a hard claim. If [OWNER] wants it, the one-line follow-up is: "Want me to add the
after-hours booking bump? If so, roughly how many, so I quote it right?" Only after she gives a
number, or agrees to keep the soft phrasing, does it become usable, and even then only as "seems
like more after-hours bookings," never a hard count.

*Why this works: the ask is result-first and offers to do the writing, so it actually gets
answered. The extraction keeps her voice and every real fact, drops the one claim she couldn't
back, and uses exactly the attribution she granted. The line is true, hers, and safe to put on a
proposal.*
