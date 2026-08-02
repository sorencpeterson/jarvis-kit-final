# Evergreen Sequence Library: H127 + H131

Source: 250-IDEAS-BUSINESS.md H127 (post-teardown 3-touch, post-delivery care pitch
2-touch, written once, triggered by tags) and H131 (the dead-lead sunset email,
full copy). All sequences here are written once and fire on tag triggers, not on a
calendar.

**Status: built paused.** Every sequence below is ready to wire to its trigger tag
in GHL. None fire until [OWNER] reviews and flips them on.

---

## SEQUENCE 1: Post-Teardown (3-touch)
**Trigger:** teardown delivered (the $97 audit report sent), tag
`post-teardown-3touch`. Goal: convert the paid-audit relationship into a build,
without being pushy about it, since they already paid once and proved real intent.

### Touch 1: Day 2 | Did you get through it?
**Subject:** did the teardown make sense?

Hey {{contact.first_name}},

Wanted to check the teardown report landed clearly, not just dropped in your inbox
unread. {{custom_values.fault_count}} things flagged, all real, all fixable.

Any questions on any of it, reply here. If you want to talk through what fixing the
top issue would actually look like, grab 15 minutes: {{custom_values.booking_link}}

[OWNER]

### Touch 2: Day 6 | The credit reminder
**Subject:** the $97 counts toward the fix

Hey {{contact.first_name}},

Quick reminder on how this works: the $97 you paid for the teardown is fully
credited toward any build, standard site or a smaller fix, whichever the report
actually calls for.

No pressure on timing. Just didn't want the credit to slip your mind if you decide
to move on it.

[OWNER]

### Touch 3: Day 14 | Last check on this
**Subject:** closing the loop on your teardown

Hey {{contact.first_name}},

Last note on this specific report, then I'll leave it be.

If the findings are still sitting on the to-do list, the $97 credit's still good
whenever you're ready. If the site's already fixed or it's not a priority right
now, no worries at all, just reply and let me know either way.

[OWNER]

{{location.full_address}}
Reply STOP to opt out.

---

## SEQUENCE 2: Post-Delivery Care Pitch (2-touch)
**Trigger:** build marked delivered in build-log.csv, no care plan attached, tag
`post-delivery-care-pitch`. Goal: the natural care-plan upsell at the moment the
site is freshest and best-looking.

### Touch 1: Day 3 post-delivery | The site is beautiful today
**Subject:** keeping {{custom_values.site_domain}} this sharp

Hey {{contact.first_name}},

{{custom_values.site_domain}} looks great right now. Worth saying plainly: "right
now" is the easy part. Sites drift, plugins go stale, nobody checks broken links
until a customer mentions one.

Care Basic is [CARE_BASIC]/mo: hosting watched, backups, updates handled. Care Growth is
[CARE_GROWTH]/mo and adds a quarterly report plus priority turnaround. Neither's required,
plenty of clients run without it just fine.

Reply YES if you want it set up, no long form to fill out.

[OWNER]

### Touch 2: Day 10 post-delivery | Last word on this
**Subject:** no pressure on the care plan

Hey {{contact.first_name}},

Not pushing this again after today. If care's not something you want right now,
totally fine, the site's yours and it's solid.

If you change your mind down the road, the offer doesn't expire, just reply
whenever.

[OWNER]

{{location.full_address}}
Reply STOP to opt out.

---

## SEQUENCE 3: Dead-Lead Sunset Email (H131, single send)
**Trigger:** contact has been unresponsive 90+ days across any active sequence
(silence-handling ladder day 30 final step, per I145 in the master list). Tag
`sunset-send`. This is the "closing your file" message. Per playbook energy
(objection #48's walk-away-kindly tone): revives roughly 5% and cleans the rest off
active lists honestly.

### THE SUNSET EMAIL
**Subject:** closing your file

Hey {{contact.first_name}},

Haven't heard back in a while, so I'm closing your file on my end. Not a guilt
trip, just honest bookkeeping, I'd rather stop emailing someone who's moved on than
keep landing in your inbox for no reason.

If the timing was just off and this is still something you want to look at, reply
and I'll pick it back up right where we left it. If not, no response needed at all,
this is the last one either way.

[OWNER]

{{location.full_address}}
Reply STOP to opt out.
