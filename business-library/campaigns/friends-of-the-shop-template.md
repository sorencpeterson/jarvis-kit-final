# Friends of the Shop: Monthly Email Skeleton

Source: 250-IDEAS-BUSINESS.md B23. Audience: everyone who ever replied to any
outreach, tag `friends-of-the-shop` (this overlaps heavily with the 423-repliers list
See `segment-plans.md` for how those get split for first touches; this monthly
send goes to the whole warm list regardless of segment). Cadence: monthly, one send.
Goal: stay top-of-mind with something useful, not a pitch dressed as a newsletter.

**Status: built paused.** [OWNER] fills the three slots each month (insight, win, slot
count) and approves before send. The skeleton is fixed; the content is never
templated filler.

---

## The skeleton (3 required slots, every month)
1. **One teardown insight**: a real, useful thing pulled from a recent audit.
   Anonymized. Something the reader can go check on their own site right now.
2. **One client win**: a real, honest result from a recent delivery. If nothing
   shipped that month, skip the slot rather than invent one, or use a testimonial
   line instead.
3. **One slot count**: the actual number of open build slots for the current month,
   pulled from the calendar. Never a made-up number. If the calendar's full, say so:
   "July's full, August has 2 open."

## Subject line pattern
Short, specific, no "Newsletter #4" framing. Examples: "the thing killing your
contact form," "what's actually slow on your site," "2 slots left this month."

## EMAIL TEMPLATE
**Subject:** {{custom_values.subject_line}}

Hey {{contact.first_name}},

{{custom_values.teardown_insight}}

{{custom_values.client_win}}

{{custom_values.slot_count_line}}

Reply if any of this hits close to home. I read every one.

[OWNER]

{{location.full_address}}
Reply STOP to opt out.

---

## Slot-by-slot writing guide

**Teardown insight**: 2-4 sentences. Shape: name the fault, explain why it costs
them calls, tell them how to check their own site for it. No client name, no
identifying detail.

**Client win**: 1-3 sentences. Shape: what shipped, how fast, one honest result if
the client shared a number. Never invent a stat.

**Slot count**: 1 sentence. Shape: "{{month}} has {{N}} build slots left" or "full
this month, {{next_month}} is open." Pulled from the real calendar every time.

---

## EXAMPLE 1 (fully written, illustrative)
**Subject:** the thing killing your contact form

Hey Marcus,

Ran a teardown on a local business site this week and found the contact form was
posting to an email address that bounced. Had been for months. Every lead that came
through it just vanished, no error, no notification, nothing. If you've never
actually tested your own contact form by submitting it yourself in the last 90 days,
do that today. Takes two minutes and it's the single most common fault I find.

Wrapped a build for a landscaping company last week. Live in 6 days, they approved
the first round, and the client said calls picked up within the first week of it
going live.

July has 2 build slots left.

Reply if any of this hits close to home. I read every one.

[OWNER]

---

## EXAMPLE 2 (fully written, illustrative)
**Subject:** what's actually slow on your site

Hey Priya,

Most "my site feels slow" complaints trace back to one thing: images. Ran a teardown
this week where three photos on the homepage were each over 2MB, uncompressed,
straight off someone's phone. That's the whole load time right there. If your site
takes more than 2-3 seconds to show the homepage on your phone with wifi off, that's
almost always the culprit, and it's a fast fix.

No new build to report this month, but a care client's quarterly report came back
clean across the board, zero downtime, all updates current. Boring is the goal.

August is full. September has 3 slots open.

Reply if any of this hits close to home. I read every one.

[OWNER]
