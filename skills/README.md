# Skills

Claude Code skills. Each folder is a `SKILL.md` that Claude loads on demand when
a task matches its description.

## Install

Copy the ones you want into your Claude skills folder:

```bash
cp -r yours/* ~/.claude/skills/
cp -r third-party/* ~/.claude/skills/     # optional, see note below
```

Then in Claude Code they trigger automatically, or call one by name:
`/linkedin-post-writer`, `/job-application`, etc.

---

## `yours/` — the good stuff (36 skills)

These were written from scratch for a solo agency + job search. They carry
`[OWNER]`-style tokens; **run `python3 setup.py` in the parent folder first**, or
just find-and-replace the tokens with your details.

**LinkedIn + content**
| Skill | What it does |
|---|---|
| `linkedin-post-writer` | A post in your voice. Kills the 5 AI-LinkedIn tells |
| `linkedin-content-arc` | Plans a themed week/month, hands each beat to the writer |
| `objection-column-writer` | Turns a sales objection into a public post |
| `portfolio-teardown-writeup` | Teardown-with-commentary on your own work |
| `case-study-writer` | A delivered project into 3 proof artifacts |
| `win-announcement` | Close-day post, proof line, SMS version |

**Job search**
| Skill | What it does |
|---|---|
| `job-application` | Tailored cover + answers to the usual custom questions |
| `interview-ace` | STAR stories mapped to likely questions, company brief |
| `recruiter-screen-call` | The 15-min screen: the 3 opening questions, salary deflection |
| `salary-negotiation` | Offer calls, counters, lowballs, exploding offers |
| `multi-offer-negotiation` | Two live offers, aligned timelines, walk-away math |
| `take-home-assignment` | Scope it, time-box it, signal-and-stop |

**Sales + client work**
`money-outreach`, `money-proposal`, `discovery-call-prep`,
`discovery-to-proposal-bridge`, `objection-handler`, `proposal-follow-up`,
`client-onboarding`, `contract-sow-drafter`, `scope-creep-response`,
`price-increase-letter`, `upsell-existing-client`, `care-plan-pitch`,
`referral-ask`, `referral-partner-agreement`, `testimonial-extraction`,
`agency-partner-pitch`, `webfix-teardown-to-quote`,
`speed-to-lead-installer-pitch`, `dead-lead-reactivation`,
`warm-reopen-call-script`, `niche-positioning-writer`, `cross-brand-router`,
`weekly-review`, `ghl-workflow-builder`

### Set your own numbers first
Prices are tokens, not real figures. Find and replace these with yours before
you quote anyone. They appear across the sales skills and the pricing tables:

| Token | What it is |
|---|---|
| `[FIRST_BUILD]` | Intro/first-project price for a new partner |
| `[LANDING_PAGE]` | Single-page site |
| `[STANDARD_SITE]` | Your default multi-page build |
| `[ECOM_PRICE]` | Cart, checkout, or booking build |
| `[WHITE_GLOVE]` | Full service: copy, brand, build |
| `[WEBFIX]` | Fix-list bundle for a salvageable site |
| `[SPEED_TO_LEAD]` | Lead-response install |
| `[AI_OPS_PRICE]` | Automation/stack install |
| `[CARE_BASIC]` `[CARE_GROWTH]` `[CARE_PREMIUM]` | Monthly care tiers |
| `[OPS_RETAINER]` | Fractional ops monthly |
| `[REFERRAL_FEE]` | Per closed referral |

Job-search skills use `[SALARY_ANCHOR]`, `[SALARY_RANGE]`, `[SALARY_TARGET]`
and `[PRIOR_RESULT]` (your headline career number). Same deal, make them yours.
Do not ship a proposal with a token still in it.

The worked examples assume a small web/marketing agency and a US context (work
authorization, job boards, state tax). Adjust for your situation.

---

## `third-party/` — publicly available skills (26)

Not written here. Bundled for convenience:

- **Cloudflare suite** (`cloudflare`, `workers-best-practices`, `wrangler`,
  `durable-objects`, `agents-sdk`, `sandbox-sdk`, `turnstile-spin`,
  `cloudflare-one*`, `cloudflare-email-service`, `web-perf`) — official
  Cloudflare developer skills.
- **Market suite** (`market`, `market-*`) — a marketing analysis toolkit.

These update independently of this package, so if you actually use them, grab
current versions from their sources rather than relying on this snapshot. Their
original licenses apply, not this package's.

---

## `../browser-agent/` — automation playbooks

Not Claude skills, these are operator briefs for driving a real logged-in
browser (LinkedIn sourcing, job applications, profile edits). They document the
exact selectors and gotchas that actually worked, including the ones that cost
an hour to discover. Read `subagent-brief.md` first.

Rule from that folder worth repeating: the browser operator **never handles
credentials**. You log in by hand once, it drives the already-authenticated
session, and it stops at anything irreversible.
