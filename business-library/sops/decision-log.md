# Decision Log (N210)

_Every pricing/strategy call with >$500 impact, logged with reasoning.
Institutional memory for a company of one. The point isn't accountability
theater, it's that six months from now, "why did we do it that way" has a
real answer instead of a guess._

## Format (one entry per decision, newest on top)

```
## YYYY-MM-DD: [short title]
**Decision:** [what was decided, one or two sentences, concrete]
**Impact:** [$ estimate or scope, why this crossed the >$500 threshold]
**Reasoning:** [why this and not the alternative, the actual thinking,
  not just the conclusion]
**Alternative considered:** [what else was on the table, briefly]
**Revisit:** [a condition or date that should trigger re-checking this
  decision, if applicable: "revisit if X" or "revisit at Q4 review" or "n/a"]
```

## Seed entries (from confirmed decisions already on record, 2026-07-03)

## 2026-07-03: Ladder v2 SKUs authored, priced PROPOSED-v2
**Decision:** 13 new full-SKU offer sheets + 7 shorter entries authored
across the ladder (teardown, speed-to-lead, GBP rescue, care Growth+, partner
pack, bundle, seasonal tune-up, rescue, booking-only, franchise sheet, annual
prepay, lease option, second-brain install, plus misc SKUs). All flagged
PROPOSED-v2 in `business-library/offers.md`'s ladder table pending [OWNER]'s
price confirmation.
**Impact:** Adds up to 20 new SKUs to the sellable ladder. The single
largest one-day expansion of the offer stack to date.
**Reasoning:** `250-IDEAS-BUSINESS.md` §A identified these as the highest
money-per-effort additions (marked ★ on the top items) building on the
existing engine rather than requiring new capability.
**Alternative considered:** Building fewer SKUs deeper vs. broad coverage
now, priced-but-unconfirmed. Chose broad coverage since pricing confirmation
is cheap ([OWNER]'s review) and having the asset ready beats rebuilding it
later when a client conversation calls for one of these SKUs.
**Revisit:** When [OWNER] confirms or edits any PROPOSED-v2 price, flip that
row to CONFIRMED in `offers.md` and log the confirmed number as its own
decision-log entry if the price changed materially from the proposed number.

## 2026-07-03: AI Ops Install priced at [AI_OPS_PRICE]
**Decision:** The AI Ops Install SKU is confirmed at [AI_OPS_PRICE] one-time.
**Impact:** Core recurring-adjacent revenue line in the operating model
(4/mo × [AI_OPS_PRICE] = $20k/mo at maturity per `business-library/operating-
model.md`).
**Reasoning:** Near-zero COGS (productizes [OWNER]'s own operating stack);
one sale nets roughly a month of site-build margin.
**Alternative considered:** n/a. Recorded here as a seed entry since this
was already CONFIRMED before this log existed; back-filled for continuity.
**Revisit:** n/a unless scope or delivery cost changes materially.

## 2026-07-03: Ops Partner Lite priced at [OPS_RETAINER]/mo
**Decision:** Confirmed as the rung between the AI Ops Install and a full
retainer. One operational system installed per month.
**Impact:** $30k/mo at maturity (12 retainers × [ECOM_PRICE], per the operating
model).
**Reasoning:** Fills the gap between a one-time install and open-ended
fractional-ops work; roughly 1-in-3 installs should convert here per
`EXECUTION-PACK/ai-ops-kit/README.md`.
**Alternative considered:** n/a. Seed entry, back-filled for continuity.
**Revisit:** Track actual install-to-retainer conversion rate against the
1-in-3 assumption once enough installs have run; revisit pricing if the
conversion rate is materially off that estimate.

---
_Add new entries at the top, newest first. This is a scaffold. The seed
entries above capture what was already confirmed as of 2026-07-03 so the log
starts populated rather than empty. Every future pricing/strategy call
crossing the $500 threshold gets its own entry here, logged the same day the
decision is made, not reconstructed later from memory._
