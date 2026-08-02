# Deliver a Site

_Distilled from `elementor-recoder/SITE-FACTORY.md`, the full runbook. Read
that file for complete detail on any stage. This is the SOP-format summary,
not a replacement._

## Trigger
A deposit clears (50%) on any build tier (Landing/Standard/E-com/Booking/
White-Glove/Webfix/Agency-first), see `business-library/playbooks/
pricing-tree.md` for pricing.

## Steps

**Stage 0 (Intake gate).** Copy the checklist block into
`elementor-recoder/clients/<name>-intake.md` and fill every box: deposit
date, tier + page list, logo, brand colors, copy source, testimonials (real
names, no initials-only), photos, form destination + fields, booking/calendar
embed URL, domain/DNS access path (client creates temp admin, credentials
never through chat), hosting decision, GA4/GTM id, care plan pitched.
**Missing items = build does not start.**

**Stage 1 (Build, Lovable).** Landing ~1h / Standard ~2h. Use
`EXECUTION-PACK/LOVABLE-PROMPTS.md`, one block at a time, review each. Every
line of site copy follows `business-library/VOICE-SPEC.md`.

**Stage 2 (Convert).** ~10 min, scripted:
```
cd ~/Claude/elementor-recoder/lovable2elementor
python3 convert.py <lovable-url> -o out/<client>
```
(CloneWebX source instead: `python3 recode.py export.zip -o clean.zip --report r.md`)

**Stage 3 (Import).** Scripted, ~2 min + review:
```
./import-site.sh out/<client> --path=/var/www/clientsite     # or --ssh=user@host/path
```
Sanity-check first in Playground: `./import-site.sh out/<client> --playground`.
Re-running updates in place.

**Stage 4 (Known v1 cleanups).** Budget 25 min: rebuild inert forms with
Elementor Pro form widget (test with a REAL submission to the REAL
destination), batch-upload images off Lovable URLs to Media Library, remap
carousels/tabs to Pro widgets where interaction matters. Log image sources
per `EXECUTION-PACK/legal/image-licensing-log-convention.md` while doing this
step. Same session, not "later."

**Stage 5 (Go live).** DNS per intake (A/CNAME), SSL check (host-issued),
GA4 snippet, favicon.

**Stage 6 (QA).** Scripted + human pass, ~15 min:
```
~/Claude/second-brain/.venv/bin/python ~/Claude/elementor-recoder/qa.py https://client-site.com --out qa-<client>.md
```
Exit 1 = FAILs exist = not delivered yet. Human pass: phone in hand, tap
every CTA, submit the form, read the copy aloud (VOICE-SPEC litmus), check
the one thing the client said mattered. See
`EXECUTION-PACK/hiring/quality-gate-policy.md` for the full gate discipline
if a contractor did any of this build.

**Stage 7 (Deliver + attach).**
1. Delivery email per `EXECUTION-PACK/templates-bundle.md` §delivery. Attach
   the QA report. This is a differentiator most $500 vendors never show,
   and it closes care plans.
2. Care plan attach AT delivery, not before (the playbook rule: care
   converts at delivery, not signing).
3. Log the build: append one line to `elementor-recoder/clients/build-log.csv`
   (`date,client,tier,hours_build,hours_import,hours_cleanup,hours_qa,total`).
   This is the data that proves or kills the 2-hour target.
4. Timers take over from here: +14 days testimonial ask, +30 days referral
   ask, once the client is tagged `client-delivered` in GHL.

**White-label variant:** same stages, three changes. Agency's brand on
everything, agency owns all client comms (never contact their client
directly, per `EXECUTION-PACK/legal/white-label-confidentiality-policy.md`),
delivery email goes to the agency with a forwardable client-ready version
inside.

## Owner
[OWNER] today. First delegation target once the hiring trigger fires. See
`EXECUTION-PACK/hiring/first-hire-spec.md`: Stages 1-4 are the first hire's
actual job.

## Last-verified
2026-07-03 (SITE-FACTORY.md itself carries a 2026-07-03 authored date;
cross-checked against `build-log.csv`'s live header row).
