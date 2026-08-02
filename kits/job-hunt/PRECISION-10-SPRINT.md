# The precision-10 sprint — how to actually run it
_2026-07-05. The postmortem's verdict: 135 apps in one 55-hour burst, 0 resolved
outcomes, was volume without aim. This is the opposite: fewer, tailored, tracked. The
materials (resume, LinkedIn, covers, answers) are built. This is the execution loop._

## The one-hour-a-day loop (beats another 135-spray)
1. **Fix the profile once (20 min, do first, blocks everything).** Paste MASTER-RESUME.md
   into Indeed + your resume doc. Paste LINKEDIN-REWRITE.md into LinkedIn. Set preferred
   titles to Marketing Operations Manager / Growth Marketing Manager / RevOps Manager.
   Until this is done, every application uses the old profile that got 0 interviews.
2. **10 tailored applications, not 100 sprayed.** From live-openings.md (sourced) + the
   captcha list in JOBS-SPRINT-10.md, pick 10 that fit. For each: Version A or B cover
   (COVER-LETTERS.md), swap the archetype hook, add one line from the JD. 5 min each.
3. **Log the A/B.** Half Version A, half Version B. The jobs system tracks outcomes; after
   ~20 apps, standardize on whichever got replies.
4. **Referral pass.** Before applying cold, check LinkedIn for a 1st/2nd-degree connection
   at the company. A warm intro beats 50 cold applications. The contact-graph can surface
   these.

## What "winnable" means (from the postmortem, enforced by the machine now)
The jobs pipeline now auto-rejects: 8+ YOE roles (you have 6), duplicate employers, and
sub-62 fit. So the queue you see is already filtered to winnable. Trust it; don't
re-add Director/VP-with-10-YOE roles by hand.

## The 10 archetypes (why each is winnable + search query)
_Live current openings for these are in live-openings.md. The archetypes are the filter._
1. **Agency ops/growth seat** — you've RUN the motion. `"marketing operations" OR "head of growth" agency remote`
2. **GHL-ecosystem SaaS (Solutions/CS/Implementation)** — you speak both agency + automation. `GoHighLevel OR "high level" careers remote`
3. **AI automation agency (Head of Ops/Delivery)** — it's literally what you do. `"AI automation agency" "head of operations" OR delivery remote`
4. **SMB SaaS growth generalist** — they want one full-funnel owner. `"growth marketing" SaaS "series a" remote`
5. **Vertical SaaS selling to agencies/local** — you ARE their buyer. `"head of marketing" vertical SaaS local-business remote`
6. **Marketing-automation consultancy** — built > studied. `"marketing automation" consultancy director remote`
7. **RevOps at B2B → SMB/agencies** — operator P&L beats Salesforce-admin. `"revenue operations manager" B2B remote`
8. **Local-service/healthcare performance** — direct FMM continuity. `"performance marketing" healthcare OR "home services" remote`
9. **Fractional-exec platform** — they staff your exact story. `"fractional CMO" OR "fractional marketing" platform hiring`
10. **First marketing hire (seed/Series A)** — generalist, no YOE wall. `"first marketing hire" OR "head of marketing" seed startup remote`

## The captcha-walled shortlist (your hands — the machine can't solve captchas)
From JOBS-SPRINT-10.md, these were the strongest and need manual apply:
SEO Team Lead @ Seer Interactive · Growth Marketing Manager Conversion @ Greenlight ·
Sr Performance Marketing Manager @ Jobscan · Performance Marketing @ GiddyUp / Automatiq ·
Marketing Manager @ Strategic Risk Solutions · SEO & GEO Manager @ Seer Interactive.

## Re-measure (don't guess)
Re-run the funnel postmortem at 2 weeks with real reply/reject data:
`cd ~/Claude/second-brain && .venv/bin/python agents/postmortem.py` (or ask JARVIS
"jobs postmortem round 2"). The first cohort is too young to judge; the second, with
tailored materials, is the real test.

## The honest frame
A [SALARY_ANCHOR] remote role is real money the agency hasn't produced yet, and it's "minimal
work" in the sense that the materials now do the heavy lifting. But interviews come from
FIT + a warm angle, not volume. Ten aimed shots with these materials will out-convert
the 135 that came before. The materials are Fable's; the sends and the interviews are yours.
