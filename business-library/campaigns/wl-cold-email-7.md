# [2026-07] Cold Agencies - WL Sites (email only)

Source: `playwright-project/automations/ghl/wl-cold-outreach-15.md` (15-touch draft),
adapted 2026-07-02:

- **Email-only.** The 7 cold SMS were dropped: texting scraped numbers without prior
  express written consent is TCPA exposure ($500-1,500 per text, statutory). SMS starts
  only AFTER a contact replies or books. The 4 original SMS live in the source doc.
- **Cadence compressed** 52 -> 30 days (7 emails: days 1, 4, 8, 12, 16, 22, 30) since
  the SMS interleave is gone.
- **Voice pass:** em-dashes stripped per [OWNER]'s voice rules.
- **Claims aligned to `business-library/offers.md`:** [FIRST_BUILD] first test build, [STANDARD_SITE]
  standard, 48-hour delivery, unlimited revisions, NDA standard, [PRIOR_BASELINE]->$1M/yr COO wedge.
- **Compliance:** every email ends with the physical-address merge tag and a working
  opt-out (reply UNSUB; the workflow's reply-exit pulls any replier out automatically).
- **Personalization:** email 1 opens with `{{contact.personalization}}` (the verified
  "Saw X's work on ..." hook from the enrichment run). Empty for un-hooked contacts,
  which renders as a blank line and reads fine.

- **Branding (2026-07-13):** personal / [OWNER_COMPANY]. Sign-off "[OWNER]", footer
  "[OWNER_COMPANY]", portfolio link [OWNER_SITE]/web, sending from
  send.[OWNER_SITE]. The old "[SECOND_BRAND]" footer was a cross-brand mismatch
  (No BS = local-business lane; agency white-label = [OWNER_COMPANY]).

Audience: cold marketing-agency owners (tag `wl-cold`). Offer: white-label sites.

---

## EMAIL 1 — Day 1 | Pattern interrupt + hook
**Subject:** web work you're turning down?

Hey {{contact.greeting}},

{{contact.personalization}}

Quick one: do you ever turn down website projects because building them is a headache?

We do white-label sites for agencies. Your brand, your client, 48-hour turnaround, [STANDARD_SITE] flat. You resell it, we stay invisible.

Worth a look? Portfolio's here: [OWNER_SITE]/web

No pressure. Just reply if it's a fit.

[OWNER]

[OWNER_COMPANY] · {{location.full_address}}
Not for you? Reply UNSUB and I'll take you off the list.

## EMAIL 2 — Day 4 | Proof, not pitch
**Subject:** a few of the sites we've built

Hey {{contact.greeting}},

Not sure web fulfillment is even on your radar, so here's proof instead of a pitch.

Portfolio: [OWNER_SITE]/web. A mix of niches. If any look like the work your clients ask for, that's the whole idea: you sell it, we build it in 48 hours, your brand on everything.

Reply and I'll walk you through the white-label side.

[OWNER]

[OWNER_COMPANY] · {{location.full_address}}
Not for you? Reply UNSUB and I'll take you off the list.

## EMAIL 3 — Day 8 | Objection killer
**Subject:** I never talk to your client

Hey {{contact.greeting}},

The thing most agencies worry about with a dev partner: will they go around me?

No. The NDA's standard and the whole model depends on me being invisible. Your client sees your brand, talks to you, pays you. I'm the build team in the background.

That's the point of white-label. You look great, I stay out of it.

Reply if you want to test it on one project.

[OWNER]

[OWNER_COMPANY] · {{location.full_address}}
Not for you? Reply UNSUB and I'll take you off the list.

## EMAIL 4 — Day 12 | The COO wedge
**Subject:** I ran the agency you're running

Hey {{contact.greeting}},

I'm not a dev who learned to talk shop. I ran ops for a marketing agency we grew from [PRIOR_BASELINE] to over $1M a year. I know what a client breathing down your neck on a deadline feels like.

That's why I built this fast and hands-off. I fix the part that used to keep me up at night. Fiverr can't say that. Neither can your offshore guy.

Want to run one project through us and see?

[OWNER]

[OWNER_COMPANY] · {{location.full_address}}
Not for you? Reply UNSUB and I'll take you off the list.

## EMAIL 5 — Day 16 | Test-build CTA
**Subject:** send me one project

Hey {{contact.greeting}},

Easiest way to know if we're a fit: give us one site.

Send a project you've got in the pipeline. First test build is [FIRST_BUILD] flat, zero risk. We turn it around in 48 hours, white-label, unlimited revisions until your client signs off. If it's not the smoothest build you've had all year, you don't send us another.

Reply with the project and I'll take it from there.

[OWNER]

[OWNER_COMPANY] · {{location.full_address}}
Not for you? Reply UNSUB and I'll take you off the list.

## EMAIL 6 — Day 22 | Risk reversal
**Subject:** unlimited revisions until they're happy

Hey {{contact.greeting}},

One thing that makes this easy: we don't stop until your client approves. Unlimited revisions, no nickel-and-diming. [STANDARD_SITE] flat, 48-hour first draft.

You carry zero delivery risk. You just mark it up and resell.

We're taking on 3 agency partners this quarter. Worth one test? Reply and I'll send next steps.

[OWNER]

[OWNER_COMPANY] · {{location.full_address}}
Not for you? Reply UNSUB and I'll take you off the list.

## EMAIL 7 — Day 30 | Breakup
**Subject:** should I close your file?

Hey {{contact.greeting}},

I've reached out a few times about white-label sites for your agency and haven't heard back. Totally fine, timing's everything.

I'll stop here so I'm not clutter in your inbox. If fast, white-label web builds ever help, you've got my info: [OWNER_SITE]/web

Either way, good luck with the agency.

[OWNER]

[OWNER_COMPANY] · {{location.full_address}}
Not for you? Reply UNSUB and I'll take you off the list.
