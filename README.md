# JARVIS Starter

A personal operations system: LinkedIn content + outreach, job search automation,
and a daily brief. Built on Claude Code. Runs entirely on your machine.

Everything outward-facing ships **off**. Nothing posts, sends, or applies until
you turn it on and approve each item.

---

## Setup

```bash
bash install.sh          # finds Python 3.11+, builds the venv, installs, verifies
.venv/bin/python setup.py    # who you are
.venv/bin/python connect.py  # hook up accounts (all optional)
```

`install.sh` does the fiddly part. macOS ships Python 3.9 and this needs 3.11+,
so it hunts for a usable interpreter and tells you exactly what to install if
there is not one.

`setup.py` writes `config/owner.json`. That file replaces the `[OWNER]`-style
tokens baked into every prompt with your name, site, and voice, so the whole
system speaks as you. Re-run it any time.

`connect.py` is optional. **The only hard requirement is Claude Code** — the
agents call it for every thinking step. Everything else (push notifications,
Gmail, Calendar, image generation, CRM) turns on one more lane and can wait.
`python3 connect.py --status` shows what is wired up.

**On a $20 Claude Pro plan?** Setup asks and configures for it: cheap model
routing, a daily token budget, a trimmed morning chain. See **[COSTS.md](COSTS.md)**
for what everything costs and the free alternative to each paid piece.

Skip the wizard entirely if you prefer:

```bash
python3 setup.py --quick      # name and email only
python3 setup.py --plan=max   # preset the plan
```

Or just copy `config/owner.example.json` to `config/owner.json` and edit it.

To check it took:

```bash
.venv/bin/python owner.py     # prints your config
make test                     # 2014 tests, no server needed
```

`make doctor` is the fuller sweep, but it also checks that the server is up and
launchd jobs are loaded, so it fails on a fresh clone by design. Use `make test`
until you are actually running the server.

---

## What is actually in here

### LinkedIn (the strongest part)
- **Sourcing** finds people worth talking to (commenter mining beats cold search)
- **Scoring** ranks them against your ICP so you engage the right ones
- **Drafting** writes comments, replies, and opener DMs in your voice
- **Budget/pacing** caps daily actions so you never trip platform limits
- **Conveyor** follows up with people who accept your connect

Key agents: `agents/networking.py`, `agents/li_*.py`

### Job search
- Sources roles from public boards, scores them for fit
- Tailors a resume per posting, drafts covers and answers
- Tracks replies, detects interviews, builds prep packs
- Routes CAPTCHA/login-walled applications to a prefilled "finish by hand" pile

Key agents: `agents/jobs.py`, `agents/job_*.py`, `agents/resume_tailor.py`,
`agents/interview_*.py`

### Content
- Post drafting in your voice, arcs (a themed week/month), objection posts

Key agents: `agents/content_gen.py`, plus the writing skills

---

## Turning things on

Everything is a knob in `store/config.json`. The defaults are conservative:

| Knob | Ships as | What it does |
|---|---|---|
| `job_auto` | `false` | Auto-approve sourced jobs into the apply queue |
| `job_daily_apply_cap` | `10` | Max applications a day |
| `cold_daily_enroll` | `0` | Cold email enrollment. `0` = fully off |
| `network.daily` | `10/6/20/5` | connect / comment / like / dm per day |

Start with everything low. Watch what it produces for a week. Raise slowly.

**LinkedIn limits are real.** Keep connects at or under 100/week. Getting
restricted is much more expensive than going slow.

---

## The rules this system was built on

1. **Nothing sends without a human click.** Agents draft and queue. You approve.
2. **Never fabricate.** No invented numbers, results, or credentials. Ever.
3. **Say what is true, then stop.** The voice rules exist because honest and
   specific outperforms polished and vague.
4. **Cheapest rung that works.** API before browser automation, browser
   automation before vision.

Break rule 1 and you will eventually send something you regret at 3am. It is
the whole reason the approve step exists.

---

## Skills (start here, honestly)

`skills/yours/` holds 36 Claude Code skills: LinkedIn writing, job applications,
interviews, salary negotiation, proposals, objection handling. They work on their
own without any of the agent machinery, so they are the fastest thing to get value
from on day one.

```bash
cp -r skills/yours/* ~/.claude/skills/
```

Then `/linkedin-post-writer`, `/job-application`, `/salary-negotiation` and the
rest are available in Claude Code. See `skills/README.md` for the full list.

`skills/third-party/` holds 26 public skills (Cloudflare dev, marketing suite),
bundled for convenience. Get fresh copies from their sources if you use them.

`browser-agent/` holds the operator playbooks for driving a real logged-in
browser: LinkedIn sourcing, job applications, profile edits, with the selectors
and gotchas that actually worked.

---

## Kits (filled-in working docs)

`kits/job-hunt/` is the get-hired material: an ATS-safe resume structure, LinkedIn
rewrite, cover letters to A/B, an answer bank, and **PRECISION-10-SPRINT.md**, the
execution loop learned the hard way after 135 sprayed applications returned zero
interviews. Ten tailored beats a hundred sprayed.

`kits/client-work/` is the get-paid material: care-plan and follow-up templates
with merge fields, a productized pricing page, and a real 14-page lead magnet in
editable HTML.

Both are tokenized. Replace `[OWNER]`, `[SALARY_ANCHOR]`, `[STANDARD_SITE]` and
friends before anything goes to a real person. See `kits/README.md`.

---

## Business library, capture, cadences

`business-library/` is the reference layer the marketing skills read from:
playbooks, a 50-item objection bank, pricing structure, ICP and persona docs,
SOPs, and campaign templates. Prices and identity are tokenized. **Rewrite the
positioning, ICP, and offers to match your business** — the frameworks transfer,
the specifics do not.

`capture/` is the phone/quick-add front door (`quick-add.sh`, `summon.sh`,
`pull_reminders.py`).

`store-templates/agent_cadences.json` is the agent schedule table. Copy it to
`store/agent_cadences.json` after running setup if you want the default cadences.

---

## Layout

```
agents/        the workers (LinkedIn, jobs, content, mail)
app/           server, planner (LLM interface), dashboard
skills/        Claude Code skills: yours/ and third-party/
browser-agent/ browser automation playbooks
kits/          filled-in job-hunt and client-work documents
business-library/ playbooks, objection bank, pricing structure, SOPs
capture/       quick-add and phone capture scripts
config/        owner.json, your identity (gitignored)
store/         your data. never commit this
content/       voice.md and drafts
tests/         ~2000 tests. run: make doctor
tools/         maintenance scripts
```

`store/` and `config/owner.json` hold your personal data. Both are gitignored.
Keep it that way.

---

## Verifying

```bash
make test          # tests + syntax. Works on a fresh clone.
make doctor        # the above plus live-server and launchd checks
```

Run `make test` before you trust a change.

---

## Notes

- Needs Python 3.11+, Node (for PDF rendering), and Claude Code on your PATH.
- API keys go in `store/config.json`. Only fill in what you use.
- The browser automation drives *your* real logged-in browser. It never handles
  your credentials, you log in by hand once.
- Some agents assume a US context (job boards, work authorization). Adjust as needed.

Built by a friend. Make it yours.
