---
name: referral-ask
description: Write the +30-day post-delivery referral ask the pricing tree names, plus the follow-through message when a referral lands. One favor, one specific persona, zero groveling. Pure margin. [OWNER_COMPANY].
---

# referral-ask

## When to use
Thirty days after a delivered build, per the pricing tree's upsell path: delivery gets
the care attach, +14 days gets the testimonial ask, **+30 days gets the referral ask**.
Input is a delivered, happy client. Output is the ask message, plus the follow-through
message for when a referral actually lands. Nothing sends without [OWNER]'s click.

## Preconditions (skip the ask if any fail)
1. **Delivered and clean**: QA passed, site live, no open tickets or complaints.
2. **Happy on record**: they gave the testimonial, replied warmly, or their care
   report shows the site working. If the +14-day testimonial ask went unanswered,
   resolve that first. Never stack two asks on a silent client.
3. One ask per message, ever. The referral ask never rides along with an invoice, an
   upsell, or a problem.

## The ask (4 rules)
1. **Open with their result, not your need.** One real line about their site, with a
   number if care reports provide one. This is the reminder of why they'd vouch.
2. **One favor, one specific persona.** Never "anyone you know." Name the exact kind
   of person: for a medspa owner, another owner-injector from trainings or
   conferences. For an agency, another founder in their circle. Specific gets names,
   vague gets nothing.
3. **Make it effortless.** A two-line text or email intro to both parties. [OWNER] takes
   it from there. Protect their reputation explicitly: if it's not a fit, he'll say so
   kindly, their name stays safe.
4. **What's in it for them: usually nothing, and say nothing.** A happy client refers
   because the work was good. Zero groveling, no "it would mean the world," no bribes.
   **Do not promise referral payment**: a [REFERRAL_FEE]-per-closed-referral SKU exists only as
   PROPOSED-v2 in `offers.md` and is not confirmed. Until [OWNER] confirms it, money
   never enters the ask.
5. **Close for the same-day yes.** End on a next step so easy it happens now, not
   "someday." The ask names the effortless action ("a two-line text intro to us both")
   and the smallest possible commitment ("one name is plenty"). The goal is a reply
   today with a name or an intro, not a vague "I'll think about who." If they can act in
   the same thread, they usually do.

## The templates (both channels, fill and send)
Produce both; [OWNER] picks the channel the client already talks on.

**Email** (under 90 words):
> Subject: one favor, {first name}
>
> {First name},
>
> {One line on their result, with a real number from the care report if there is one.}
>
> One favor. {The specific persona, named: "You know other {peer type} from {where they
> meet them}."} Who's the one whose {work/business} is great but whose website hides it?
> A two-line text intro to us both is all I need, and I'll take it from there. If it's
> not a fit, I'll tell them kindly. Your name stays safe with me either way.
>
> One name is plenty.
>
> [OWNER]

**SMS** (1-3 sentences, clients are opted in):
> {First name}, {your result line with the number}. One favor: which {specific persona}
> do you know whose work deserves a better website? A two-line intro to us both and I'll
> handle the rest.

The same-day-yes close lives in the last line of each: name a person, send an intro, done
in one reply.

## The follow-through (when a referral lands)
1. Thank them in one line, immediately, same channel the intro came on.
2. Tell them how the referral will be treated: same day-3 preview, same rules they
   got, and a kind no if it's not a fit.
3. Close the loop exactly once, when the referral resolves either way. They should
   never have to wonder what happened to their name.

## Voice (hard rules)
No em-dashes or en-dashes, ever. Short sentences, 9-13 words. Contractions always. No
emojis. Numbers do the talking. One favor, stated once, no pressure language and no
apology for asking. Email under 90 words where possible; SMS 1-3 sentences (clients
are opted in, SMS is fine). Full spec: `~/Claude/business-library/VOICE-SPEC.md`.

## True facts you may state
Their build's real tier, timeline, and any number from their own care report. Day-3
preview and approve-before-live apply to whoever they refer. Never promise the
referral a discount, never promise the referrer payment, never invent capacity
pressure. If a real build-slot limit exists at send time, it may be stated.

---

## WORKED EXAMPLE — medspa owner, 30 days after launch
*Input: Kelsey, owner-injector, Glow Theory Aesthetics. White-Glove build at [WHITE_GLOVE],
live 30 days. Testimonial already given at +14 days. On Care Growth+ at [CARE_PREMIUM] a month;
her first care report showed 61 booking clicks through the new site.*

**The ask (email):**

*Subject:* one favor, Kelsey

Kelsey,

Your site turned 30 days old this week. First care report counted 61 booking clicks
through it. That's the thing doing its job.

One favor. You know other owner-injectors from trainings and conferences. Who's the
one whose work is great but whose website hides it? A two-line text intro to us both
is all I need, and I'll take it from there. If it's not a fit, I'll tell them kindly.
Your name stays safe with me either way.

One name is plenty.

[OWNER]

**The ask (SMS version):**
> Kelsey, your site's 30 days old and counted 61 booking clicks last month. One favor:
> which owner-injector do you know whose work deserves a better website? A two-line
> intro to us both and I'll handle the rest.

**The follow-through (her intro landed, she texted [OWNER] and Dana together):**
> Kelsey, thank you for the intro, that's a real favor and I don't take it lightly.
> Dana gets the same treatment you got: working preview on day 3, she approves
> everything before it goes live, and if it's not a fit I'll say so kindly. I'll let
> you know how it lands either way.

**When it resolves (won or lost, one message, once):**
> Kelsey, closing the loop on Dana: her build went live yesterday. Your name opened
> that door and the work honored it. Thank you.
