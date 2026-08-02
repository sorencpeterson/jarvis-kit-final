# Win Announcement Template: to the warm list

Source: 250-IDEAS-BUSINESS.md B30. Trigger: any build marked delivered in
build-log.csv where 2+ other contacts in the same niche/trade previously replied to
an outreach but didn't close. Send only to those same-trade repliers, not the
full list. Goal: "just shipped one in your trade, want the same look?" Proof over
pitch.

**Status: built paused.** Each send is a one-off drafted from the actual delivered
build. [OWNER] reviews and approves before it goes out. No batch automation fires
without a human picking the trigger build.

---

## The mechanism
1. Build delivers, gets logged in build-log.csv with a niche tag (plumber, HVAC,
   salon, etc).
2. Pull every contact tagged with that same niche who replied to a past touch but
   never became a client, tag `win-announcement-{{niche}}`.
3. Draft is auto-generated from the build's actual details (client type, one real
   result if available, live URL if the client's OK sharing it publicly).
4. [OWNER] reviews, confirms the client is fine being referenced (even anonymized),
   sends.
5. Any reply exits the tag immediately.

## EMAIL: Just shipped one in your trade
**Subject:** just finished a {{custom_values.niche}} site, thought of you

Hey {{contact.first_name}},

You and I talked a while back about your site. Didn't end up working together, no
hard feelings, just wanted to flag something.

I just finished a build for another {{custom_values.niche}} business.
{{custom_values.win_detail}}

Not pitching you again out of nowhere, just: if you ever want the same treatment,
the door's still open. Reply and I'll send you what it looked like before and after.

[OWNER]

{{location.full_address}}
Reply STOP to opt out.

---

## Fill-in variables ([OWNER] completes per send)
- `{{custom_values.niche}}`, the trade (plumber, HVAC, salon, roofer, etc).
- `{{custom_values.win_detail}}`, one honest sentence. Real detail only, no invented
  numbers. Examples of the shape (not verbatim claims, swap in what's actually true):
  - "Live in 6 days, they approved the first round."
  - "Same five faults your site had, all fixed. Happy to show you the before/after."
  - "They were losing calls to a broken contact form. Fixed, and the phone's ringing
    more since."

## Sample filled version (illustrative, not a real client)
**Subject:** just finished a plumbing site, thought of you

Hey Mike,

You and I talked a while back about your site. Didn't end up working together, no
hard feelings, just wanted to flag something.

I just finished a build for another plumbing business. Live in 6 days, they approved
the first round, and the same broken-contact-form issue your site had is exactly
what I fixed on theirs.

Not pitching you again out of nowhere, just: if you ever want the same treatment,
the door's still open. Reply and I'll send you what it looked like before and after.

[OWNER]
