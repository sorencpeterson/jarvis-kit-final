# Past-Client Reactivation: 3 emails

Source: 250-IDEAS-BUSINESS.md B21. Trigger: the moment a contact lands in
`past-clients.csv` / gets tagged `past-client` in GHL with no active care plan.
Goal: "site still serving you?" check-in that opens the door to a care attach or a
rebuild, not a hard pitch. Any reply exits the sequence immediately.

**Status: built paused.** Sends are gated behind [OWNER]'s review of the actual
past-clients list before this goes live. Nothing fires until he flips it on.

Audience: everyone who's had a site delivered, no active care plan, tag
`past-client-reactivation`. Trigger tag removed on any reply (rule: reply pulls
them out of automation).

---

## EMAIL 1: Day 1 | Still serving you?
**Subject:** {{contact.first_name}}, quick check on {{custom_values.site_domain}}

Hey {{contact.first_name}},

[OWNER], the guy who built {{custom_values.site_domain}}. Been a minute, so I'm doing a
round of check-ins on every site I've shipped.

Quick question: is it still doing its job? Phone ringing, forms coming in, nothing
broken that you've noticed?

If yes, good, that's all I needed to hear. If something's off or it's been a while
since anyone touched it, reply here and I'll take a look.

[OWNER]

{{location.full_address}}
Don't want these check-ins? Reply STOP and I'll take you off the list.

---

## EMAIL 2: Day 5 | The annual physical
**Subject:** the 10-minute site check

Hey {{contact.first_name}},

Following up on my last note. Here's the offer straight: I'll run a full QA pass on
{{custom_values.site_domain}}, free, no pitch. Broken links, slow images, anything
that quietly costs you a call.

Most sites I check haven't been touched since delivery. Nothing wrong with that, it's
just worth knowing what's actually true a year or two in.

Reply YES and I'll send you the report this week. If you want, I'll walk you through
it live: {{custom_values.booking_link}}

[OWNER]

{{location.full_address}}
Reply STOP to opt out.

---

## EMAIL 3: Day 12 | Two doors, no pressure
**Subject:** last one on this, {{contact.first_name}}

Hey {{contact.first_name}},

Last check-in, then I'll leave your inbox alone.

If {{custom_values.site_domain}} is doing its job, there's a simple way to keep it
that way: Care Basic is [CARE_BASIC]/mo, hosting watched and backed up, updates handled,
nothing to think about. Care Growth is [CARE_GROWTH]/mo and adds a quarterly report plus
priority turnaround. Neither is required. Plenty of past clients run without it and
that's fine too.

If the site's actually behind now, outdated, slow, doesn't match where the business
is, that's a different conversation. Either way, reply and tell me which one this is.
I'll take it from there.

[OWNER]

{{location.full_address}}
Reply STOP to opt out.
