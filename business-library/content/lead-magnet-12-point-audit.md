# Lead Magnet: The 12-Point Website Self-Audit | C60

Source: 250-IDEAS-BUSINESS.md C60. The full PDF content, humanized from qa.py's
actual check categories, plus the email-gate landing page copy. Every check below
maps to a real thing qa.py mechanically checks (see elementor-recoder/qa.py) ,
nothing invented, nothing padded to hit a round number. qa.py checks fewer than 12
distinct fault categories per page; this expands each into a self-check a
non-technical owner can run by hand, plus adds the standing favicon/404 site-level
checks, to reach 12 genuinely useful, real items.

**Status: gated lead magnet. Delivery is automated (email → PDF), the automation
itself is built paused** pending [OWNER]'s review of the actual gate flow before go-
live. No auto-follow-up sequence fires without separate sign-off, see
evergreen-sequences.md for what happens after someone downloads this.

---

## LANDING PAGE COPY (email gate)

**Headline:** The 12-Point Website Self-Audit

**Subhead:** The exact checklist I run on every site before I recommend a fix.
Twelve things, ten minutes, no technical background needed.

**Body:**
Most website problems aren't design problems. They're small, specific, mechanical
things that quietly cost calls: a broken form, a slow image, a missing tag search
engines actually read.

This is the real checklist. The same one behind every teardown I run. Work through
it on your own site in about ten minutes, in plain English, no dev knowledge
required.

**Form:** Email address only. One field.

**CTA button:** Send me the checklist

**Below the form (trust line):** No spam, no sales sequence buried in the PDF. Just
the checklist. If you want a second set of eyes after you run it, that's a
conversation for later, not part of this.

---

## THE PDF: "The 12-Point Website Self-Audit"

**By [OWNER], [OWNER_COMPANY]**

I run this exact list on every site before I recommend anything. It's mechanical on
purpose, no opinions, no "does it look nice." Twelve things, pass or fail. Work
through your own site and mark each one.

### 1. Does your contact form actually deliver?
Submit your own form right now, with a real email you check. Time how long the
confirmation takes. If nothing arrives in a few minutes, or the form shows no
"got it" message at all, you may be losing every lead through it silently.

### 2. Does your site load fast on a phone with average signal?
Open your site on your phone with wifi off, just cell signal. If the homepage takes
more than 2-3 seconds to show up, something's too heavy, almost always images.

### 3. Are your photos sized for the web, or straight off a camera?
Right-click (or long-press) any photo on your site and check the file size. Photos
over 300KB are usually unnecessarily heavy and slowing your whole site down.

### 4. Does every photo have alt text?
Alt text is the behind-the-scenes description search engines and screen readers use
for images. If your site was built without it, search engines can't tell what your
photos show, and neither can visitors using accessibility tools.

### 5. Does your site render correctly on a phone, not just shrink down?
Load your site on your phone. If text is too small to read without zooming, or
buttons are too close together to tap accurately, the site is missing proper mobile
handling, not just "small."

### 6. Do all your internal links actually go somewhere?
Click every link in your main menu and footer. A single broken link looks like a
small thing. A few of them tell a visitor the site isn't maintained.

### 7. Does every page have its own unique title?
Look at the browser tab text on 3-4 different pages of your site. If they're
identical, or generic ("Home," "Page 2"), search engines can't tell your pages
apart either.

### 8. Does every page have a meta description?
This is the snippet of text that shows under your link in Google search results.
If it's missing, Google writes its own, usually a random sentence pulled from the
page, not your best pitch.

### 9. Does your site have a favicon?
That's the small icon in the browser tab. Small detail, but a missing one is a
quiet signal that nobody's checked the basics in a while.

### 10. Is your entire site actually secure (no mixed content)?
If your site uses https (the lock icon in the address bar), every image and script
on it needs to load over https too. A padlock that shows a warning when clicked
usually means something on the page is loading insecurely.

### 11. Does a broken link on your site show a real error, or does it fake a working page?
Type a random page onto the end of your URL (like yoursite.com/xyz123) and hit
enter. It should show a clear "page not found." If it shows a normal-looking page
instead, that confuses search engines about what's actually on your site.

### 12. Do your important pages have social preview images set up?
Paste your homepage link into a text message to yourself. If no image and title
preview shows up, visitors sharing your site (or you posting it anywhere) get a
blank, unprofessional-looking link instead of a preview.

---

**Scoring:** Most sites fail 3 to 5 of these without anyone knowing. If you found
more than that, it's not unusual, it's actually the norm.

**If you want a second pass:** I run this exact check as an automated tool, plus a
few things that are hard to check by hand (crawling every page, checking every
image size, testing every link automatically). The $97 teardown gets you the full
report plus a 15-minute video walkthrough, credited toward any build if you decide
to fix what it finds.

Book here if you want that: [OWNER_SITE]/book

[OWNER]
