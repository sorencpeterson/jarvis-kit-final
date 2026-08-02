# [2026-07] Webfix - Agencies With Broken Sites — EMAIL 3 v2 (the breakup, with receipts)

Source: v2 of `wl-webfix-email-3.md`'s EMAIL 3. Original breakup email was written
before real per-site QA data existed to reference, so it stayed generic ("that's
leads quietly bouncing every day"). Now qa.py runs a genuine audit at import AND
again on re-touch (agents/webfix_refresh.py, H174, this mission — cold_pipeline
webfix rows enrolled 85+ days with no reply get a fresh "90 days later" site_note
drafted from a real re-crawl). This version swaps the generic claim for
`{{contact.site_note}}` again, same as EMAIL 1, but now carrying the SECOND finding
(the re-audit), so the breakup reads as "I checked twice, here's proof, still your
call" instead of a guilt-trip with no evidence behind it.

Use this instead of the original EMAIL 3 once webfix_refresh.py has actually staged
a fresh site_note for a given contact (see store/webfix_refresh_staging.jsonl —
applied_to_ghl:false rows are drafts, apply the site_note to the contact's GHL custom
field by hand before this email goes out, this mission does not write to GHL). For
contacts with no re-audit on file yet, keep using the original EMAIL 3.

Same rails as the rest of the sequence: email-only, compliance footer every send,
UNSUB honored, no fabricated urgency.

---

## EMAIL 3 v2 — Day 10 | Breakup, with a second look

**Subject:** checked again, closing your file

Hey {{contact.greeting}},

Last note from me. Went back and looked at {{contact.company_name}}'s site again
before I did.

{{contact.site_note}}

Same story as the first time I wrote. Two-day job on my end, whenever you want it
handled.

If timing's wrong, all good. You've got my info: [OWNER_SITE]/web

Either way, good luck with the agency.

[OWNER]

[SECOND_BRAND] · {{location.full_address}}
Not for you? Reply UNSUB and I'll take you off the list.
