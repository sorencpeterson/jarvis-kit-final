# Weekly Teardown Post Template: C41

Source: 250-IDEAS-BUSINESS.md C41. One anonymized real teardown per week on
LinkedIn. Screenshot + 3 faults + fix. This is a byproduct of the factory, not extra
work: every teardown run through qa.py is a candidate post. Fault patterns below are
real categories qa.py checks for (broken links, missing alt text, missing meta
description, missing viewport/mobile meta, heavy images, missing favicon, duplicate
titles, mixed content, soft 404s). No client names, no identifying URLs, no
screenshots that reveal the business ever, without written OK.

**Status: build-in-public content. Publish is human-gated**: [OWNER] reviews every
post before it goes up, same as any send-adjacent asset.

---

## The skeleton
1. **Hook line**: one sentence, the fault stated as a surprising fact, not a
   complaint.
2. **The setup**: one sentence, what kind of business, no identifying detail
   ("a local plumber," "a salon," never the name or city).
3. **3 faults**: short, numbered, concrete. Pull straight from the qa.py finding
   category.
4. **The fix**: one sentence per fault or one combined fix line.
5. **The takeaway**: one sentence, something the reader can go check on their own
   site right now.
6. **No hashtag pile.** Zero or one hashtag max, per VOICE-SPEC LinkedIn calibration.

## Anonymization rule
Never the business name, never a screenshot with the URL visible, never enough
detail that someone could identify the client by process of elimination (city +
trade + size is often enough to de-anonymize; strip at least one of those three).

---

## SAMPLE POST 1: Broken contact form
Ran a teardown on a local business site this week. The contact form had been posting
to a dead email address for at least two months.

No error message. No bounce notice. Every lead that came through it just vanished.

Three things wrong:
1. Form endpoint pointed at an email that no longer existed
2. No confirmation message on submit, so the owner had zero way to know it was broken
3. No backup notification (SMS, second email) if the primary channel failed

Fix: point the form at a live address, add a visible "thanks, I got it" confirmation,
and wire a second notification channel so one dead email can't silently kill your
leads.

If you've never tested your own contact form by submitting it yourself in the last
90 days, do that today. Two minutes, and it's the single most common fault I find.

## SAMPLE POST 2: Mobile viewport missing
Teardown this week on a trade business site. Looked fine on a laptop. Looked broken
on a phone.

The site was missing the mobile viewport tag entirely, so phones were rendering it
at desktop width and shrinking it down. Text unreadable without pinch-zooming. Every
button too small to tap accurately.

Three things wrong:
1. No viewport meta tag, so mobile browsers defaulted to desktop rendering
2. Contact buttons overlapped on smaller screens, un-tappable
3. Nobody at the business had checked the site on their own phone in over a year

Fix: one line of code (viewport meta) fixes the rendering. Testing on an actual phone
catches the rest.

Most local searches happen on a phone, not a desktop. If your site's last "check"
was on a desktop monitor, you haven't actually checked it.

## SAMPLE POST 3: Heavy images, slow load
Teardown on a service business site this week. Three homepage photos, each over 2MB,
straight off a phone camera, uncompressed.

That's the whole load-time complaint right there.

Three things wrong:
1. Hero image was 2.4MB, no compression, no resizing for web
2. No lazy-loading, so every image loaded on page open even below the fold
3. No modern image format (still all JPG at full resolution, no WebP)

Fix: compress and resize before upload, load below-the-fold images lazily, serve
modern formats where the browser supports them.

If your homepage takes more than 2-3 seconds to show on your phone with wifi off,
check your image sizes first. It's almost always the culprit.

## SAMPLE POST 4: Missing meta description and duplicate titles
Teardown this week turned up something sneaky: the same page title on four different
pages, and no meta description anywhere on the site.

Doesn't look broken. Doesn't feel broken. Quietly kills your search visibility
anyway.

Three things wrong:
1. Homepage, services page, and two blog posts all shared the identical <title> tag
2. Zero meta descriptions site-wide, so search engines wrote their own snippets from
   random page text
3. No unique page framed around what that page is actually for

Fix: unique title per page, a real one-to-two sentence meta description per page
written for a human reading the search results.

Search engines can't tell your service page from your blog post if the title tag
doesn't either. Worth a five-minute check on your own site.
