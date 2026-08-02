# Teardown Alumni Quarterly: B36

Source: 250-IDEAS-BUSINESS.md B36. Audience: everyone who bought the $97 teardown
audit but didn't build. Warm, paid-once, proved-interest list. Goal: quarterly
"prices held, findings still true" note, not a re-pitch every time. Tag
`teardown-alumni`. Any reply exits.

**Status: built paused.** Runs once per quarter, [OWNER] approves the specific findings
line before each send (it has to be true for that recipient, not templated fluff).

---

## The cadence
One email per quarter. Not a drip, a standing quarterly touch. Content changes each
time: the specific faults referenced have to still be true (re-check with qa.py
before sending, don't reference a finding they may have already fixed).

## EMAIL: Quarterly check-in
**Subject:** the {{custom_values.fault_count}} things from your teardown, still true

Hey {{contact.first_name}},

Quick quarterly note, not a pitch email.

Back when I ran the teardown on {{custom_values.site_domain}}, I flagged
{{custom_values.fault_summary}}. Just rechecked. {{custom_values.status_line}}

Price on the [STANDARD_SITE] standard build hasn't moved. If this is the quarter it's worth
fixing for real instead of patching around it, reply and I'll pick up where the
teardown left off. If not, I'll check back next quarter.

[OWNER]

{{location.full_address}}
Reply STOP to opt out.

---

## Fill-in variables
- `{{custom_values.fault_count}}`, number of findings from their original teardown.
- `{{custom_values.fault_summary}}`, one-line summary of the top 1-2 faults (broken
  links, no mobile viewport, slow images, missing meta description, pull straight
  from the qa.py report on file).
- `{{custom_values.status_line}}`, honest update. One of:
  - "Still there. Nothing's changed since the audit."
  - "Two of the three are fixed, the third's still open."
  - "All clear now, actually, whoever touched it since did the job. Ignore this one."
    (If this is the case, send it anyway. Honesty here is the whole point of the list.)

## Sample filled version (illustrative)
**Subject:** the 4 things from your teardown, still true

Hey Danielle,

Quick quarterly note, not a pitch email.

Back when I ran the teardown on danisalon.com, I flagged a missing mobile viewport
tag and three broken links in your booking menu. Just rechecked. Still there.
Nothing's changed since the audit.

Price on the [STANDARD_SITE] standard build hasn't moved. If this is the quarter it's worth
fixing for real instead of patching around it, reply and I'll pick up where the
teardown left off. If not, I'll check back next quarter.

[OWNER]
