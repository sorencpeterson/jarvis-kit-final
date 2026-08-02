# Second-Number Strategy — when call/text volume justifies a second line

_Doc-only for now (#169). No config change made to the live store/config.json — this
documents the plan and the exact config shape to add THE DAY the volume actually
justifies it. Today's real volume (58 booked warm calls, cold knobs at 0) doesn't."_

## The problem this solves

One phone number doing everything — warm calls, cold SMS (once that lane exists),
inbound replies — has two failure modes as volume grows:

1. **Carrier throttling.** A2P/10DLC registration caps daily send volume per
   registered number (see `EXECUTION-PACK/a2p-compliance.md`, #178). Push past it on
   one number and messages queue or get rate-limited, not instantly, but noticeably
   once daily volume gets real.
2. **Reputation bleed.** If a cold-outreach number ever gets flagged (too many STOPs,
   a spam-complaint spike), every OTHER use of that same number inherits the hit,
   including warm calls with people who already said yes. Booked-call contacts
   shouldn't be collateral damage from a cold lane's bad week.

## The strategy

Split by INTENT, not by volume alone:

- **Number A (warm/existing relationships):** booked calls, warm follow-ups,
  anything to someone who already said yes to talking. Protect this number's
  reputation above all else, this is where the actual pipeline lives.
- **Number B (cold outreach):** cold SMS once that lane exists (currently email-only
  per `wl-webfix-email-3.md`'s note: "SMS touches dropped per TCPA stance"). Cold is
  inherently higher-risk for STOPs/complaints; isolating it means a bad cold-lane day
  never touches Number A.

This mirrors the existing DOMAIN split for email (`get.thenobsmarketing.com` for cold,
`[OWNER_SITE]` informational for nurture, per `cold_preflight.py`'s
`DEFAULT_DOMAINS`) — same logic, different channel.

## When to actually do this

Not yet. Add Number B when EITHER:
- Cold SMS volume becomes real (an actual SMS lane gets built and turned on — today
  it doesn't exist, webfix is email-only by explicit prior decision), OR
- Warm call/text volume alone approaches a registered number's daily A2P ceiling
  (unlikely at current scale — 58 booked contacts total, nowhere near a 10DLC cap).

## Config scaffold (add THIS, not before it's needed)

When the day comes, this is the shape to add to `second-brain/store/config.json`
(NOT added now — this mission is read-only against config beyond the H161/162
knob-to-0 pause, and this isn't that; a human or a future mission adds this):

```json
"phone_numbers": {
  "warm": {"number": "+1XXXXXXXXXX", "a2p_registered": true, "use": "booked calls, warm follow-ups"},
  "cold": {"number": "+1XXXXXXXXXX", "a2p_registered": true, "use": "cold SMS lane (not live yet)"}
}
```

And the corresponding routing note for whichever agent ends up sending SMS
(warm_followup.py for warm, a future cold-SMS agent for cold): read `phone_numbers`
by intent, never hardcode a single "the" number once this exists.

## Status

Doc-only, as scoped. No config written. No number purchased. Revisit when either
trigger condition above is actually true.
