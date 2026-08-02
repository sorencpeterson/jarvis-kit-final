# Automation Preferences

Defaults I apply when building sequences unless told otherwise.

## Channels
- **No fixed default — confirm per task.** [OWNER] specifies the channel mix per
  campaign based on the goal.

## Cadence
- **No fixed default — decide per campaign** (launch vs. nurture vs. reactivation).
  Propose and confirm.
- **Send windows:** SMS **inside business hours, by recipient timezone**. Stagger
  sends across the list — never blast the whole list at once.

## Deliverability / best practices (apply to every outbound build)
- **Cap daily send volume** so the sending domain doesn't get flagged.
- **Stagger** sends across the list; no firehose.
- **Reply-detection:** the moment a contact replies, pull them OUT of the
  automation so they stop getting touched after they engage.

## Structure
- **Email length:** short, punchy (confirm per campaign).
- **CTA style:** one clear CTA per message.
- **Subject line style:** short, operator tone, curiosity/benefit — confirm per campaign.

## Compliance
- **SMS:** include opt-out on **every** send — "Reply STOP to opt out."
- **Email:** compliant footer on **every** send — **physical address + unsubscribe**.
  Physical address: use GHL location merge tag `{{location.full_address}}`.

## Naming conventions
- **Sequences / campaigns:** TODO — confirm. Suggested: `[YYYY-MM] DBR – Agencies – White-label Sites`.
- **Tags:** TODO.

## Strategic note (per [OWNER] — important)
Warm channels (law firm, Andrew's referrals, own network) are where deals actually
move — weight them higher. Cold multi-touch campaigns like the agency DB-reactivation
sequence are a **supplement/test**, not the main engine. Build them well, but don't
over-invest effort relative to warm work unless [OWNER] asks. A 10-touch cold blast to
agency owners is the hardest version of this and historically his lowest-traction channel.
