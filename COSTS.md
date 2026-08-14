# What this costs, and how to run it for nothing

Short version: **Claude Pro at $20/mo is the only thing you need.** Everything
else has a free path or can stay off.

Run `setup.py` and pick the Pro profile and it configures itself to survive on
that plan. Nothing below is required reading unless you want to change something.

---

## The one real requirement

**Claude Code.** Every agent's thinking step shells out to `claude -p`. No API
key, it uses your existing subscription.

| Plan | What you get here |
|---|---|
| **Pro, $20** | Fine. Pick the Pro profile at setup: cheap model routing, a daily token budget, a trimmed morning chain, lower daily caps. |
| **Max** | Full chain, stronger models, no budget cap. |
| API key | Works, but you pay per token. The subscription is cheaper for this workload. |

### What the Pro profile actually changes

- **Models.** Haiku for every internal step (triage, planning, scoring, chat).
  Sonnet only for text a human will read (posts, DMs, replies, proposals,
  resume tailoring). **No Opus anywhere.**
- **Daily token budget: 400k.** Past it, `planner._cli` automatically drops
  internal features to the cheap model for the rest of the day. Public-facing
  writing keeps its model. You will not silently run out mid-morning.
- **Morning chain: lite.** The full chain touches 100+ agents. Lite runs the
  core and jobs lanes only. Flip `morning_profile` to `full` in
  `store/config.json` if you upgrade.
- **Lower caps.** 5 job applications a day, 8 connects, 4 comments.

Feeling it? Lower `daily_token_budget`, or set `morning_profile` to `lite` and
run the other lanes by hand when you want them.

---

## Optional integrations, and the free version of each

| Feature | Paid default | Free alternative |
|---|---|---|
| **Push notifications** | — | **[ntfy.sh](https://ntfy.sh) is already free.** No account. Pick an unguessable topic, install the app, subscribe. |
| **Images for posts** | OpenAI (~$0.04/image) | Skip it. Text posts do fine. Or generate elsewhere and drop files in `content/images/`. |
| **Voice brief** | ElevenLabs | macOS has `say` built in: `say -f store/brief.md`. Free, offline, good enough for a morning brief. |
| **Gmail + Calendar** | — | **Free.** Google's API quota is far above what this uses. Only cost is the OAuth setup in `schedule/SETUP.md`. |
| **Job boards** | — | **Free.** Public endpoints, no keys. |
| **LinkedIn** | — | **Free.** Drives your own logged-in browser. No LinkedIn API, no Sales Navigator. |
| **CRM sync** | GoHighLevel | Skip unless you already pay for GHL. Nothing depends on it. |

Everything above degrades cleanly. Missing key means that one feature is off,
with a note in the logs. Nothing crashes.

---

## Skipping setup entirely

The wizard is convenience, not a requirement.

```bash
python3 setup.py --quick            # name and email only, Pro defaults
python3 setup.py --plan=max         # preset the plan, still asks the rest
```

Or write `config/owner.json` yourself. Copy `config/owner.example.json` and fill
it in. The only field that matters is `name`; everything else has a fallback.

`connect.py` is entirely optional. Run `python3 connect.py --status` any time to
see what is wired up and what is not.

---

## Cheapest useful setup

If you want the value with none of the spend:

1. `bash install.sh`
2. `python3 setup.py --quick` (Pro profile)
3. `cp -r skills/yours/* ~/.claude/skills/`
4. Stop there.

That gives you 36 skills covering LinkedIn writing, job applications,
interviews, salary negotiation, and proposals. They cost nothing beyond your
Claude subscription and need no agents, no server, no keys, no scheduling.

Add the agent machinery later, when you know which parts you actually want.

---

## If the job hunt is the priority, run only that

The morning chain has ~112 steps. Most of them serve a business you may not be
running yet: content generation, cold outreach, weekly deep analytics, and a
Monday drift check that makes about twelve real LLM calls. On a $20 plan that is
the whole budget, spent before the job search runs.

```bash
python3 tools/tune_for_plan.py --jobhunt
```

That sets `morning_profile: jobs`, which runs ~29 steps: source jobs, score them,
tailor the resume, prep interviews, mine the answer bank, and produce the brief.
The brief always runs, under every profile. Move back up with `--pro` (adds job
analytics) or `--max` (everything) whenever you want.

| profile | morning steps | what it drops |
|---|---|---|
| `jobs` | ~29 | outreach, content, analytics, intel, golden set |
| `lite` | ~33 | same, but keeps job funnel analytics |
| `full` | ~112 | nothing |

## Make the scanner search YOUR field

`job_queries` in `store/config.json` is what gets searched. Unset, it falls back
to a generic list. For a broad marketing search, something like:

```json
"job_queries": ["Marketing Manager", "Marketing Operations Manager",
                "Growth Marketing Manager", "Digital Marketing Manager",
                "SEO Manager", "Demand Generation Manager",
                "Lifecycle Marketing Manager", "Marketing Automation Manager"]
```

Sourcing is free (plain HTTP, zero LLM calls), so a wider list costs nothing per
scan. It only changes what lands in the queue.

## Applying without spending tokens

One application through the LLM operator is a full agentic browser session: snapshot,
reason, act, snapshot, per field. That is why the daily cap has been single digits.

Most of that reasoning is wasted. A Greenhouse form asks for a first name in the same
box every time. `agents/apply_direct.py` fills the known boards from a table with
**zero LLM calls**, and leaves everything it does not recognise to the operator.

```bash
.venv/bin/pip install playwright && .venv/bin/playwright install chromium
.venv/bin/python agents/apply_direct.py --dry-run --ats greenhouse.io
```

Read what it says it would fill. Only then enable submitting, which needs **both**
an explicit flag and a config knob:

```json
"direct_apply": true,
"direct_apply_pace_s": [45, 90]
```

```bash
.venv/bin/python agents/apply_direct.py --submit --ats greenhouse.io --limit 5
```

Supported: Greenhouse (high confidence), Lever (medium), Ashby, Workable, Rippling
and SmartRecruiters (low, prove with `--dry-run` first). Measured against 266 real
jobs sourced from five broad-marketing queries, that covers **40%** of the queue.
Workday is deliberately unsupported: a multi-screen wizard behind mandatory account
creation, which deterministic filling cannot and should not attempt.

## Knowing which applications actually landed

The system used to count applications it could not prove it sent. `job_verify.py`
settles that against the employer's own confirmation email, which is external
evidence rather than the operator's self-report:

```bash
.venv/bin/python agents/job_verify.py --report   # the pile, no mailbox needed
.venv/bin/python agents/job_verify.py            # verify against mail, 0 LLM calls
```

It runs in the morning chain after `job_replies.py`. It only moves jobs toward what
the mailbox supports: an unconfirmed application becomes confirmed, and an
`inflight_timeout` with a confirmation is **recovered as a real submission** that
would otherwise have been re-applied to later. Absence of mail changes nothing, and
a human status (interview, rejected, replied) always outranks it.

Not every employer sends a confirmation, so what remains after a run is unproven
rather than unsent. That list is short enough to check by hand, which the whole pile
never was.

**On daily volume.** This removes the token ceiling, not every ceiling. Three remain,
and they are the ones that decide whether volume is worth anything:

1. **Queue composition.** Only jobs on a supported board take the cheap path. In one
   measured queue that was roughly a third; the rest still cost operator sessions.
2. **Velocity filters.** Greenhouse, Lever and Workday score submissions for speed
   and uniformity before a human reads them. Applications sent faster than a person
   could type are worth *less*, not more. That is what the pacing window is for, and
   why it is not a knob worth turning to zero.
3. **How many jobs actually fit.** Sourcing is free, so widening `job_queries` costs
   nothing. Applying to roles you do not fit costs your reply rate.

The honest framing: this makes fifty applications a day *affordable*. Whether fifty
poorly-matched applications beat ten good ones is a different question, and the
published numbers on mass applying (one documented run: 5,000 applications, 5
interviews) suggest they do not.

## If job applications are eating your limit

Sourcing is free (plain HTTP, zero LLM calls). Applying is where the cost is:
each application is a `claude -p` session driving a browser, and the cost is the
agentic loop, snapshot the page, decide, act, snapshot again.

Retune any time:

```bash
python3 tools/tune_for_plan.py --show    # current settings
python3 tools/tune_for_plan.py --pro     # cheapest settings that still work
python3 tools/tune_for_plan.py --max     # throughput over economy
```

The Pro profile switches form-filling to Haiku, runs one operator at a time in
batches of 5, and lets the ATS-friction router skip forms that have walled you
before, so a session is never spent on a CAPTCHA you cannot pass.

**Seed the answer bank. One minute, no LLM, pays back every day.**

```bash
python3 agents/answer_bank.py --seed
```

A fresh install ships an empty `store/answer_bank.json` and nothing warns you.
Every application then regenerates every standard screener answer (work
authorization, sponsorship, notice period, remote...) with an LLM call — the
largest recurring per-application cost after page navigation. Seeding pre-answers
the universal ones so the operator pastes instead of reasons. Only store answers
that are actually true for you; skip anything that is not.

**A note on what "applied" means.** The operator must quote the confirmation it
saw after submitting; an applied callback with no quote is recorded but tagged
`unconfirmed`, and `jobs.needs_verify()` lists every submission-uncertain job
(operator died mid-flight, hit its attempt cap, or reported success without
evidence):

```bash
python3 -c "import sys;sys.path[:0]=['.','app','agents'];import jobs;\
print(*[f'{x[\"status\"]:8} {x[\"company\"]} - {x[\"reason\"]}' for x in jobs.needs_verify()], sep='\n')"
```

Open those in the ATS and check before trusting the count. If no confirmation
email arrived either, assume it did not land.

---

## Two things worth checking on the job side

**Is the scanner searching for YOUR roles?**

```bash
python3 -c "import sys;sys.path[:0]=['.','app','agents'];import jobs;print(jobs.active_queries()[:6])"
```

If those titles are not what you want, set them:

```json
"job_queries": ["Your Target Title", "Another Title"]
```

in `store/config.json`, or re-run `setup.py` and answer the target-roles
question. Left unset it falls back to a generic list, and every scan then sources
jobs you do not want and spends fit-scoring on them. Sourcing itself is free, so
this costs accuracy rather than tokens, but a queue full of wrong roles is worse
than an empty one.

**Resume tailoring is one Sonnet call per job.** It is the largest LLM cost in
the pipeline, larger than applying on some days. `resume_tailor_limit` tracks the
apply cap so it stops tailoring resumes for jobs it will not submit today. The
PDFs are cached, so tomorrow's run picks up where this one stopped and nothing is
wasted.
