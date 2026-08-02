---
name: upsell-existing-client
description: Move a delivered client up the ladder, site to care plan to AI Ops Install to Ops Partner Lite, with the right tier jump at the right moment. The upsell path from the pricing tree, the timing gates, and the one-tier-at-a-time rule. Earn the next tier, never leap it. [OWNER_COMPANY].
---

# upsell-existing-client

## When to use
A client [OWNER] already delivered for is ready (or nearly ready) for the next thing.
Input: who they are, their niche, what was delivered, how long ago, and any real signal
(they reply fast, they run on GHL-like ops, they keep asking for edits). Output: the next
tier to pitch, the timing, and the message. The pricing tree defines the path and it
fires automatically per close. The discipline: move ONE rung at a time, at the moment the
last rung has proven itself. Never leap from a [STANDARD_SITE] site to a [AI_OPS_PRICE] install cold.

## The ladder (the path up)
Site -> Care -> AI Ops Install -> Ops Partner Lite. Each rung earns the next.
| Rung | SKU | Price | When it fires |
|---|---|---|---|
| 1 | The build (delivered) | Landing/Standard/Booking/White-Glove | Done. This is the anchor. |
| 2 | Care plan | [CARE_BASIC] / [CARE_GROWTH] / [CARE_PREMIUM] per mo | At delivery, and again at +30 days. Care converts at delivery. |
| 3 | AI Ops Install | [AI_OPS_PRICE] one-time | +60 days, for owners who reply fast and run on GHL-like ops. |
| 4 | Ops Partner Lite | [OPS_RETAINER]/mo | After the install proves out, for owners who want it run for them. |

Care tiers: Basic [CARE_BASIC] (small/brochure), Growth [CARE_GROWTH] + $250 onboarding (they send edits
and run marketing), Growth+ [CARE_PREMIUM] (medspa/men's-health default, monthly seasonal content).
Existing clients moving UP a care tier never pay the $250 onboarding again. All prices
confirmed except Growth+ [CARE_PREMIUM] and AI Ops Install [AI_OPS_PRICE] scope: quote Growth+ in the
medspa/men's-health lane per the niche book, and confirm the install scope against
offers.md before promising specifics. Full path:
`~/Claude/business-library/playbooks/pricing-tree.md` (upsell path).

## The timing gates (fire the right rung at the right time)
- **At delivery:** the care attach, inside the delivery email (care-plan-pitch skill).
  Care converts here, not at signing.
- **+14 days:** testimonial ask (not an upsell, but it sets up everything after).
- **+30 days:** if they passed on care at delivery, the second care touch, now with real
  month-one facts from the logs. Also the referral ask.
- **+60 days:** the AI Ops Install teaser, but ONLY for owners who reply fast and already
  run on GHL-like ops. This is not for everyone, it self-selects.
- **After the install proves out:** Ops Partner Lite [OPS_RETAINER]/mo, for the owner who'd
  rather [OWNER] run the system than run it themselves.

## The one-tier-at-a-time rule
- **Never skip a rung.** A delivered site owner is not an AI Ops Install prospect on day
  one. They become one after care proves the relationship and they've shown the ops
  signal. Leaping tiers reads as a money grab and kills the trust the delivery earned.
- **Each pitch anchors on what the last rung proved.** Care proved reliable, so the
  install is "let's do to your whole operation what the site did for your bookings." The
  ladder is a story, each rung the setup for the next.
- **Read the signal before you climb.** Fast replies and GHL-like ops = install-ready.
  Slow, low-touch, brochure-site owner = they live at care, don't push them up.

## Voice (hard rules)
This is an existing relationship, so warm but never salesy. No em-dashes or en-dashes,
ever. Short sentences, 9-13 words. Contractions always. No emojis. Anchor to what the
last rung delivered, in real numbers from the logs where you have them. One tier, one
ask, then stop. Banned: unlock, leverage, seamless, elevate, excited, circle back. Full
spec: `~/Claude/business-library/VOICE-SPEC.md`.

## True facts you may state
Six years doing this. Fractional COO who scaled a marketing agency [PRIOR_RESULT] per
year, so the ops work is real, not a bolt-on. Care edits turn in 48 hours on Growth and
up. Month-one counts come from the logs or from [OWNER], never invented. A retained medspa
tox patient is worth about [PROJECT_EXAMPLE] a year. Never claim results the site produced that you
can't show, never invent an ops outcome, never quote AI Ops Install scope beyond what's
confirmed.

---

## WORKED EXAMPLE — a delivered medspa at +30 days to Care Growth
*Input: Bianca, medspa owner, White-Glove [WHITE_GLOVE] site delivered 30 days ago. She passed
on care at delivery ("let me see how it goes first"). She's replied fast to every message
and asked for two small edits already. Care log shows 2 core updates, 3 plugin updates,
one backup test, zero downtime.*

**The +30 day care touch (the right rung, not a leap):**
> Bianca, site's been live a month, quick read from my side. Two edits handled, six
> updates shipped behind the scenes, backups tested, zero downtime. That's the boring
> work that keeps it earning. Next month it includes putting your fall pigment push up
> before September hits. Care Growth+ is [CARE_PREMIUM] a month, and since you're already set up
> there's no onboarding fee. Want it on? Reply "care" and it starts this week.

Then stop. One ask.

**Why this rung and not a leap to AI Ops Install:**
She's shown the fast-reply signal that flags a future install candidate, but it's day 30
and care hasn't even started. The install is a +60-day conversation, AFTER care proves
the relationship. Pitching a [AI_OPS_PRICE] install to a 30-day client who just passed on a
[CARE_PREMIUM]/mo plan would read as a grab and torch the trust the delivery built. So the move is
the care rung, anchored on real log numbers (six updates, zero downtime, her fall
season), with the onboarding fee waived because she's already onboarded.

**The next rung, teed up for later (+60 days, if she takes care and keeps the signal):**
> Bianca, care's been running clean and you move fast on this stuff. When you've got 20
> minutes, I want to show you what it'd look like to do to your whole front desk what the
> site did for your bookings. Auto follow-ups, drafted replies, a daily read on what's
> coming in. That's the AI Ops Install. No pitch today, just planting it.

Why: the ladder is one tier at a time, each earning the next. The +30 message is pure
care, anchored on what the delivery and month one actually produced. The install only
surfaces at +60, framed as an extension of what care already proved, and even then it's
planted, not pushed. The Growth+ tier is the confirmed medspa default, and the waived
onboarding is the real rule for an existing client moving up.
