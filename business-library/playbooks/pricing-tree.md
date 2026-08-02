# Pricing Decision Tree — [OWNER_COMPANY]
_Authored at peak intelligence 2026-07-03. This is the single source of pricing truth.
The Proposal Factory implements it in `second-brain/agents/proposal_factory.py` (PRICING
dict). Change a number here, change it there. All prices CONFIRMED by [OWNER] 2026-07-03._

## The ladder
| SKU | Price | When |
|---|---|---|
| Landing page | [LANDING_PAGE] | Single-page, one goal (book/call/quote) |
| Standard site | [STANDARD_SITE] | 3-6 pages. The default recommendation. |
| E-com / Booking | [ECOM_PRICE] | Cart, checkout, or scheduling built in |
| White-Glove | [WHITE_GLOVE] | Copy + brand + photos direction + site |
| Agency first build | [FIRST_BUILD] flat | New white-label partner's first order only |
| Webfix bundle | [WEBFIX] | Their site is salvageable: speed/mobile/SEO fix list |
| Care Basic | [CARE_BASIC]/mo | Hosting watch, updates, backups |
| Care Growth | [CARE_GROWTH]/mo + $250 onboarding | + edits, monthly report, priority |
| Ops Partner Lite | [OPS_RETAINER]/mo | Fractional ops: automations + reporting |
| AI Ops Install | [AI_OPS_PRICE] | The stack install: GHL automation + AI drafting + dashboards |

## Routing rules (niche → recommendation)
- **Local service** (HVAC, plumbing, roofing, landscaping, electrical): Standard [STANDARD_SITE].
  If they take appointments → E-com/Booking [ECOM_PRICE]. Always attach Care Growth in the
  proposal (it converts at delivery, not at signing — see upsell path).
- **Restaurant / salon / gym**: booking is the business → [ECOM_PRICE] default.
- **Agency (white-label)**: first build [FIRST_BUILD] flat to prove us. Then rate card.
  3+ orders/mo earns partner pricing: 10% off, floors $700 landing / [FIRST_BUILD] standard.
- **E-commerce**: [ECOM_PRICE] minimum, no exceptions. Scope creep lives here.
- **Webfix lane** (site exists, salvageable): [WEBFIX] bundle. If the teardown finds 4+
  structural faults, recommend Standard rebuild instead and say why in one line.
- **No site at all**: Landing [LANDING_PAGE] to start fast, Standard pitched as the follow-on.

## Hard rules (the spine)
1. **50% deposit books the slot, remainder on delivery.** Build starts on deposit. No exceptions.
2. **Rush +50%** for delivery under 7 days. Say it plainly, no apology.
3. **Max discount 15%**, and only trading for something: testimonial on delivery,
   3 referral intros, or a case-study write-up. Never discount for silence.
4. **Two revision rounds included.** Round 3+ is [CARE_GROWTH]/round, said upfront in the proposal.
5. **No free spec work** beyond the teardown + one mockup. The mockup IS the proof.
6. **Never price on hourly.** Fixed SKU, fixed scope, fixed timeline.
7. **Price is stated once, confidently, with a date it expires** (14 days) so proposals
   don't rot in inboxes.

## Walk-away triggers (say no, kindly, fast)
- Budget under $500 for a build (offer the [WEBFIX] webfix or nothing).
- "Pay when it makes me money" / equity offers / rev-share on a [STANDARD_SITE] site.
- Third reschedule with no reason. Archive with the 90-day breakup line.
- Wants to provide their own hosting nightmare + refuses care plan + wants same-day support.

## Upsell path (fires automatically, per close)
- **At delivery**: Care plan attach (the site is beautiful today; care keeps it that way).
- **+14 days**: testimonial ask (templates-bundle.md).
- **+30 days**: referral ask — "who else in {niche} should have a site like this?"
- **+60 days**: AI Ops Install teaser for owners who reply fast and run on GHL-like ops.

## Objection pre-empts to bake INTO proposals (pick 3 by niche)
- Price: anchor to one lost job/month ("one missed {job_type} pays for the whole site").
- Time: "7 days, and I show you progress on day 3. You approve before anything goes live."
- "I have a nephew who does sites": "Great — this proposal doubles as a spec sheet for him.
  If he can hit it at this price, hire him." (Confidence sells; gatekeeping doesn't.)
- Trust: the [PRIOR_BASELINE]→$1M/yr client story, one sentence, with the number.
