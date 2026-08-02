---
name: job-application
description: Build a copy-paste job-application packet for any posting — a tailored cover with one real detail from the role, answers to the common custom application questions, and which resume summary line to swap in. Two minutes to apply. Built from [OWNER]'s real [PRIOR_BASELINE]→$1M operator background.
---

# job-application

## When to use
[OWNER] wants to apply to a job. Input: the posting (title, company, JD text or its key
lines, the application's custom questions if visible). Output: one packet he pastes in.
The lesson from his postmortem: he applies in bursts and stops when tailoring gets
heavy. This skill's job is to make every application 2 minutes, aimed, and true.

## Posting-prioritization filter (when there's a stack, apply in this order)
When [OWNER] has several postings open and finite energy, don't apply top-to-bottom. Rank
them, hit the top ones while attention is fresh, and let the weak ones wait.
1. **Fit to the record first.** A posting his story lands on cleanly (agency ops, growth
   from zero, GHL-ecosystem, RevOps, retention, AI-native) beats a stretch role at a
   flashier company. One real JD detail he can connect is the whole tailoring, and it's
   easy when the fit is real.
2. **Comp clears the floor.** If the band tops out under $110K with no equity story,
   deprioritize or skip. Anchoring math lives in `salary-negotiation`; the floor gates
   which postings are even worth two minutes.
3. **Freshness and reachability.** A posting live under a week, or one with a warm path
   (a recruiter who reached out, a mutual, a company already in his orbit) ranks above a
   month-old listing shouting into an ATS void.
4. **Signal it's real.** Concrete mandate, named team, a number the role owns, beats a
   vague "wear many hats" post that reads like a perpetual req. Real roles convert.
5. **Effort-to-apply.** Among ties, the fast applications (cover + a couple screeners)
   go before the ones with a take-home or an essay wall, so momentum isn't spent on the
   heaviest gate first. When a take-home does land, `take-home-assignment` scopes it.
Rule: apply to the top 3-5 while sharp, then stop. His postmortem says he stalls when
tailoring gets heavy, so front-load the high-fit ones and don't grind the tail.

## The packet (produce exactly these parts)
1. **Resume summary line** to swap into MASTER-RESUME.md's summary.
2. **Cover letter**, under 150 words.
3. **Screener answers** for this application's questions.
4. **Flags**: anything [OWNER] must answer himself.

## Part 1 — the resume summary line
Formula: mirror THEIR exact job title (ATS ranks title-match heavily) + the [PRIOR_BASELINE]→$1M
number + the 2-3 skills their JD leads with. Shape: "{Their title} operator who {their
core mandate}: scaled a marketing agency [PRIOR_BASELINE]→$1M/yr, {their top 2-3 JD skills} end to
end." Rest of the resume stays as `~/Claude/JOBS-KIT/MASTER-RESUME.md`. Never re-add
retail history or the 200-item skill dump; they dragged the old profile down.

## Part 2 — the cover (six rules, every time)
1. First line: the [PRIOR_BASELINE]→$1M number, stated plainly.
2. Second line: what he builds (systems/automation), not what he "wants."
3. One line proving you read THEIR posting: quote or paraphrase a REAL detail from the
   JD and connect it. This line is the whole tailoring. Never fake it; if you don't have
   the JD text, get it.
4. One line on fit for THIS role's core problem.
5. Close confident and low-pressure, one next step, end with [OWNER_SITE]. Sign
   "[OWNER]."
6. Under 150 words. No em-dashes. No "I hope this finds you well," no "excited/thrilled."

Pick the chassis: **Version A story-led** (default for ops/growth/RevOps roles) or
**Version B skills-led** (for checklist-style JDs), from
`~/Claude/JOBS-KIT/COVER-LETTERS.md`, and swap in the matching archetype opener (agency
ops, GHL-ecosystem, AI-native, first marketing hire, RevOps, retention...).

## Part 3 — screener answers (the canonical bank, all true)
Work auth US: Yes. Sponsorship: No. Location: [OWNER_CITY]. Remote: Yes.
Relocation: No. Notice: 2 weeks. Salary: [SALARY_ANCHOR] (range asked: $115-140K; floor $110K;
band tops [CARE_GROWTH]K+ → anchor higher per the posting). Years in marketing/ops: 6. Years
leading: ~4. GoHighLevel: expert. HubSpot/Salesforce/Marketo: "Deep in GoHighLevel; the
concepts transfer directly and I ramp fast." Never claim years on a tool he hasn't used.
SEO/paid/email-SMS/GA4/WordPress/CRO/AI automation: Yes, genuinely.
Free-text stock answers (why this role, biggest accomplishment, automation experience,
why leaving) live in `~/Claude/JOBS-KIT/ANSWER-BANK.md`; tailor the first sentence to
their JD, keep the number.

## Part 4 — flags (never auto-answer, route to [OWNER])
EEO/demographic questions (leave default or decline). Any specific number he hasn't
verified. Anything that would claim experience he lacks. Cover claims must stay inside:
[PRIOR_BASELINE]→$1M as fractional COO, 6 years, 35+ builds, churn fix to 2+ yr retention, ~70%
internal quality improvement, GHL expert, Google/Meta certified.

---

## WORKED EXAMPLE — OpenHands, Growth Marketing Manager ($105-150K, Series A dev-tools)
*JD: first marketing hire, build the function from zero, test channels, own reporting.*

**Resume summary line:**
"Growth Marketing operator who builds the whole engine from scratch: scaled a marketing
agency [PRIOR_BASELINE]→$1M/yr, full-funnel across email, content, paid, lifecycle, and the
automation underneath."

**Cover (Version A chassis, archetype: first marketing hire):**

Hi,

Two things up front. As fractional COO I scaled a marketing agency from [PRIOR_BASELINE] to over
$1M a year by building the growth engine from scratch, not with headcount, with
systems. And I run the whole funnel myself: email, content, paid, lifecycle, and the
marketing automation underneath.

Your role is exactly that: one operator building OpenHands' marketing function from
zero, testing channels and owning reporting. That's the work I do best. A Series A
building the engine is where a full-stack operator beats three specialists.

I use AI automation in my own delivery daily, which for a dev-tools company is the same
muscle your users buy. Happy to show you how I'd approach it. [OWNER_SITE]

[OWNER]

**Screener answers:** auth Yes, sponsorship No, remote Yes, 6 yrs, notice 2 weeks.
Salary: band tops at [CARE_GROWTH]K, so answer [SALARY_RANGE], not the default [SALARY_ANCHOR] flat.

**Flags:** none. EEO section: decline/default, [OWNER]'s call.

*Why this packet works: the title is mirrored for ATS, the number leads the cover, the
JD detail ("from zero, testing channels, owning reporting") is real and named, and the
AI line converts his indie work into buyer-credibility for a dev-tools audience. Total
paste time: 2 minutes.*
