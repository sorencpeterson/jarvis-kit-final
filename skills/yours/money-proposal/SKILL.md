---
name: money-proposal
description: Write a client proposal in [OWNER]'s voice that closes — right offer, right price from the pricing tree, ROI framing, a real teardown detail. Use for any prospect proposal (white-label agency partner, or a direct web/marketing client). [OWNER_COMPANY].
---

# money-proposal

## When to use
Any time [OWNER] needs a proposal: an agency owner asking about white-label builds, a small
business that needs a site or a fix, an ops/AI install prospect. Output is the proposal
text itself, ready to paste. It must read in 2 minutes. One page. No deck.

## Inputs you need
1. **Who** they are (name, business, niche).
2. **Their situation**: what you know from the call, the reply, or their site. If you have
   their site, find 3-5 REAL faults (mobile speed, buried phone number, no click-to-call,
   invisible reviews, no booking path). The teardown detail is the moat. If you have no
   real detail, say so and ask for the site URL before writing. Never invent faults.
3. **Which lane**: white-label agency partner, or direct client.

## Pricing (the confirmed spine — never quote anything else as final)
| SKU | Price | When |
|---|---|---|
| Landing page | [LANDING_PAGE] | Single page, one goal |
| Standard site | [STANDARD_SITE] | 3-6 pages. The default. |
| E-com / Booking | [ECOM_PRICE] | Cart, checkout, or scheduling. Minimum, no exceptions. |
| White-Glove | [WHITE_GLOVE] | Copy + brand + photo direction + site |
| Agency first build | [FIRST_BUILD] flat | New white-label partner's first order only |
| Webfix bundle | [WEBFIX] | Site is salvageable: speed/mobile/SEO fixes |
| Care Basic / Growth | [CARE_BASIC]/mo · [CARE_GROWTH]/mo + $250 onboarding | Attach Care Growth to every direct build |
| Ops Partner Lite | [OPS_RETAINER]/mo | Fractional ops |
| AI Ops Install | [AI_OPS_PRICE] | GHL automation + AI drafting + dashboards |

Routing: local service → Standard [STANDARD_SITE] (booking-based business → [ECOM_PRICE]). Agency →
first build [FIRST_BUILD] flat, then rate card; 3+ orders/mo earns 10% partner pricing.
Salvageable site → [WEBFIX] webfix, BUT 4+ structural faults → recommend rebuild and say why
in one line. Full tree: `~/Claude/business-library/playbooks/pricing-tree.md`.

Hard terms in every proposal: **50% deposit books the slot, remainder on delivery. Two
revision rounds included, round 3+ is [CARE_GROWTH]. Price holds 14 days, then requote. Rush +50%.
Never hourly. Max discount 15% and only traded for a testimonial, 3 intros, or a case study.**

## The framework (7 beats, in order)
1. **The read.** Open with what you actually saw in THEIR business. For direct clients,
   the numbered fault list. For agencies, name their bottleneck (stuck projects, ghosting
   freelancer, turned-down work). First line carries it. Zero throat-clearing.
2. **The diagnosis flip.** One sentence reframing the real problem. Shape: "That's not a
   sales problem. That's a fulfillment bottleneck."
3. **The fix.** The offer, scoped in plain words. Bold the price line. State it once,
   confidently. Never apologize for it.
4. **The math.** ROI in their numbers. Direct client: "one missed {job} pays for the
   site." Agency: their markup ("you quote $3,000, my invoice is [STANDARD_SITE], the rest is
   margin you're turning away").
5. **The proof.** One sentence, one number. The [PRIOR_RESULT] COO story, or 35+ builds,
   or "handed off Monday, client-ready Thursday." Portfolio: [OWNER_SITE]/web
6. **Process + terms.** Day-3 working preview, approve before live, 7 days from deposit
   (48-72 hrs first draft for agency builds). Deposit terms. 14-day price expiry.
7. **The close.** Short imperative. "Send me the scope. I'll tell you if I can help." or
   "Tap the deposit link and send me your logo. Preview Thursday." Never "feel free to
   reach out whenever."

Bake in 2-3 objection pre-empts from `business-library/playbooks/objections.md` chosen by
niche (price anchor, nephew line, "you approve before anything goes live"). Pre-empt,
don't lecture.

## Voice (hard rules, violations = rewrite)
No em-dashes or en-dashes, ever. Sentences 9-13 words, shorter is fine. Contractions
always. No emojis. Numbers do the talking. Banned: unlock, leverage, seamless, elevate,
excited, thrilled, delve, cutting-edge, "I hope this finds you well." Operator talking to
operator. Litmus test: if it could come from a HubSpot template, kill it. Full spec:
`~/Claude/business-library/VOICE-SPEC.md`.

## True facts you may state (never go beyond these)
Fractional COO, scaled a marketing agency [PRIOR_RESULT]/yr. 35+ white-label builds,
48-72 hr turnaround. Unlimited revisions until the agency's client approves (white-label
lane). NDA standard. Taking 3 agency partners this quarter. Never claim: named clients,
review counts you haven't verified, guaranteed rankings or results.

---

## WORKED EXAMPLE 1 — white-label agency partner
*Input: Dana runs Northline Creative, 8-person brand agency, Austin. Replied to email 5
of the cold sequence: two site projects stuck in her pipeline, her freelancer ghosted.*

**Northline Creative | white-label build proposal**

Dana,

You've got two builds stuck and a freelancer who went quiet. That's not a sales problem.
That's a fulfillment bottleneck, and it's the exact thing I fix.

Here's the model. I build your clients' sites behind the scenes. Your brand on
everything, NDA standard, your client never knows I exist. You own the relationship and
the markup. I'm the build team in the background.

**First build: [FIRST_BUILD] flat.** That's the proving rate for a new partner. After that,
standard is [STANDARD_SITE] a site, landing pages [LANDING_PAGE], e-com or booking [ECOM_PRICE]. First draft in
48-72 hours. Unlimited revisions until your client signs off. Two stuck projects means
both could be client-ready by Friday.

The math on your side: quote your client $3,000 like you do now. My invoice is [STANDARD_SITE].
The rest is margin you're currently turning away.

Why me and not the next freelancer: I ran ops inside a marketing agency as fractional
COO and scaled it from [PRIOR_BASELINE] to over $1M a year. I know what a client breathing down
your neck feels like, so I build like your deadline is mine. 35+ builds delivered this
way: [OWNER_SITE]/web

Terms: 50% deposit books the slot, remainder on delivery. Build starts on deposit. I'm
taking 3 agency partners this quarter and this price holds 14 days.

Send me the scope on the first stuck project. I'll tell you if I can help.

[OWNER]

---

## WORKED EXAMPLE 2 — direct small business
*Input: Rick owns Example Plumbing, [OWNER_CITY]. Site is 9 years old. Teardown found: 11s
mobile load, phone number buried, no click-to-call, 40+ Google reviews invisible on site,
no emergency-service page. Average job ~[WEBFIX]. Routing: 4+ structural faults, so rebuild
at [STANDARD_SITE], not the [WEBFIX] webfix. Attach Care Growth.*

**Example Plumbing | website rebuild proposal**

Rick,

I went through your site the way a customer with a burst pipe would. Five things are
costing you calls:

1. On a phone, your number takes three taps to find. That customer gives you one.
2. No click-to-call button anywhere.
3. The site takes 11 seconds to load on mobile. Most people are gone at 3.
4. Your 40+ Google reviews don't appear on the site at all.
5. No emergency-service page, and that's your highest-value search.

I could patch this for [WEBFIX]. But four of those five are structural, and patching paint
on a cracked wall wastes your [WEBFIX]. I won't sell you that.

**The fix: a rebuilt site, [STANDARD_SITE] flat.** 3-6 pages built around one job, making your
phone ring. Click-to-call everywhere, reviews front and center, an emergency page that
catches your best searches. You see a working preview on day 3 and approve before
anything goes live. Live in 7 days from deposit.

One missed emergency job pays for most of this site. Two pay for all of it.

After launch, Care Growth at [CARE_GROWTH] a month: updates, backups, edits, and a monthly report
you'll actually read, so it never rots into the site you have now. Optional. Most owners
add it in the first month.

Terms: 50% deposit ($600) books the build, remainder on delivery. Two revision rounds
included. Price holds 14 days. If your nephew does websites, this proposal doubles as
his spec sheet. If he can hit it at this price, hire him.

Ready? Reply "go" and send your logo. Preview Thursday.

[OWNER]
