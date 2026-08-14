# Working on this repo

Instructions for Claude Code. Read this before changing anything here.

## What this is

A personal operations system: LinkedIn outreach and content, job search
automation, mail triage, and a daily brief. ~185 agents in `agents/`, a FastAPI
server in `app/`, ~2000 tests in `tests/`.

Every agent's thinking step shells out to `claude -p`. There is no API key for
the LLM; it rides the owner's Claude subscription. That means **token usage is a
real constraint**, not an abstraction. See `COSTS.md`.

## The identity layer, and why prompts look odd

Prompts across this codebase contain tokens like `[OWNER]`, `[OWNER_SITE]`,
`[SALARY_ANCHOR]`, `[STANDARD_SITE]`. **Those are not bugs.** `owner.py` swaps
them for the configured owner's real values at runtime, hooked into
`planner._cli`, so every agent and skill speaks as whoever owns this copy.

Two rules that follow from that:

1. **A token in a prompt string is correct. Leave it.**
2. **A token in executable code is a bug.** Config values, URLs, regexes and
   filenames must resolve through `owner.get("site")` etc. at runtime. One of
   these once landed inside a regex, where `[OWNER_HANDLE]` silently became a
   character class matching any single letter of the token name. If you see a
   token outside a prompt, fix it to resolve properly.

Owner config lives in `config/owner.json` (gitignored). `python3 owner.py`
prints the current one.

### What the entry context teaches

The root documents are read before any work happens, by you and by every agent
that opens this repo. They set a prior. When they are mostly incident logs, leak
post-mortems, `UNVERIFIED` tags on things later settled, and sentences like
"assume more remains", the prior is *this system is compromised and its own
claims are unreliable* — and the reasonable response to that is to hedge, refuse,
and over-check. A sibling install reached ~84,000 tokens of entry context across
29 root documents and its agent grew visibly reluctant to act.

```bash
python3 tools/context_hygiene.py --detail       # volume + what it teaches
python3 tools/archive_changelog.py REMINDERS.md # move history out, lossless
```

Two rules that follow:

- **Settled facts get stated flat.** Hedge language in an always-read document
  does not stay put; it propagates into generated copy and into willingness to
  act. If something is settled, say so and delete the reasoning that made it look
  uncertain. A "closed, but with residual doubt" entry re-teaches the doubt every
  time it is read. Genuinely open questions belong in one labelled open list.
- **Keep security guidance, archive security history.** "Red-team your own output
  before calling it done" earns its tokens. A catalogue of past credential leaks
  does not, and reads as evidence the environment is unsafe.

### Inheriting a copy from someone else

If this install came from another person, two things need clearing before the
system is really yours, and an agent working here will (correctly) hedge until
they are:

```bash
python3 tools/depersonalize.py --from "Previous Owner" --scan
python3 tools/retarget_audit.py
```

The first finds their literal name, handle, and domains in the source and can
rewrite them to yours. The second finds their *business model*. Neither touches
`store/`. Private-life mentions and named clients are reported but never
auto-edited: those are judgment calls, not substitutions.

This matters beyond tidiness. A tree full of a different real person's name,
clients, and relationships is genuinely ambiguous about whose identity is being
acted on, and the reasonable response to that ambiguity is caution. Clearing it
is what makes the system unambiguously yours.

### What the identity layer does NOT fix

`owner.py` retargets *identity*: name, site, company, email. It does not
retarget the *business model* — what this system assumes you sell, to whom, at
what price. That is baked into agent prompts and `business-library/` as ordinary
prose, so a fresh install signs its output with the new owner's name while still
reasoning from the original owner's business. Nothing errors; the output is
fluent and aimed at the wrong market, which is why it survives for weeks.

```bash
python3 tools/retarget_audit.py       # ranked by runtime impact, read-only
```

Tier 1 (agent prompts) and tier 2 (`business-library/`) are what a human
actually reads. Work one tier at a time; changing business assumptions is the
owner's call, not a cleanup you do unasked.

## Before you say a change works

```bash
make test        # syntax + ~2040 tests, no server needed. USE THIS.
make doctor      # the above PLUS live-server and launchd checks
```

`make doctor` fails on a fresh clone by design, and on a machine running another
copy its port check can pass against somebody else's server. Prefer `make test`.

**A green suite proves the file, not the process.** The server is a long-lived
process; after any `app/server.py` change it keeps running the OLD code from
memory until restarted, and no test can tell the difference. A sibling install
shipped fixes into a file a ten-day-old process never re-read and burned 80
queued jobs on a guard that was correct on disk and inert in memory. After any
server change: restart it, then

```bash
python3 tools/check_server_fresh.py
```

which fails if the process on :8765 predates the file. A change to `app/` is
not "done" until that passes.

For agents specifically:

```bash
python3 -c "import ast;ast.parse(open('agents/<file>.py').read())"
.venv/bin/python -c "import sys;sys.path[:0]=['.','app','agents'];import <module>"
```

## Hard rules

1. **Nothing sends without a human click.** Agents draft and queue; the owner
   approves. Every outward path (email, LinkedIn, applications) is gated behind
   an explicit action or a config knob shipped at zero. Do not add an auto-send
   path, and do not raise a cap without being asked.
2. **Never fabricate.** No invented numbers, results, clients, or credentials in
   anything a human will read. If a fact is not in the owner's config, resume,
   or store, it does not go in the output.
3. **Never handle credentials.** The browser automation drives an
   already-logged-in browser. It does not type passwords. If a flow hits a login
   wall, stop and hand it back.
4. **No em-dashes in generated copy.** Commas or periods. `store_lib.humanize()`
   enforces it; do not route around it.
5. **Store data is the owner's.** `store/` and `config/owner.json` are
   gitignored. Never commit them, never paste their contents into a PR or an
   issue, never send them anywhere.

## Layout

```
agents/       the workers. One concern each, mostly standalone scripts
app/          server.py (FastAPI), planner.py (the LLM interface), brain.py
owner.py      identity resolution. Read this before touching prompts
setup.py      first-run wizard (--quick for defaults)
connect.py    optional integrations, --status to see what is wired
install.sh    bootstrap: finds Python 3.11+, venv, deps, tests
skills/       Claude Code skills. yours/ = original, third-party/ = bundled
kits/         filled-in job-hunt and client-work documents
business-library/  playbooks, objection bank, ICP frameworks, SOPs
store/        the owner's data. Never commit
tests/        ~2014 tests. conftest.py pins a test identity
```

## Conventions

- **Agents are standalone.** Each has a `run()` and works from the CLI. They
  insert `ROOT`, `app`, `agents` on `sys.path` at the top; follow that pattern.
- **Writes are atomic.** Temp file then `os.replace()`. Concurrent writers use
  `store_lib._flock`. Do not write straight to a live store file.
- **Failures are swallowed at the edges.** One bad record must not abort a batch
  of 40. Wrap per-item work in try/except and continue.
- **Comments explain WHY, especially for non-obvious constraints.** Several
  comments in here document a specific bug that a change caused. They are load
  bearing; do not delete them as noise.
- **New agent?** Add it to `store-templates/agent_cadences.json` so the cadence
  checker notices when it stops running.

## Cost awareness

If the owner is on Claude Pro ($20), the config will show `morning_profile:
lite` and a `daily_token_budget`. Respect that. Do not add LLM calls to a hot
path, do not add another agent to the morning chain without saying what it
costs, and prefer a regex or a lookup where one will do. `planner._cli` already
degrades to the cheap model past the daily budget.

## Where this came from

Built by one person for their own use, then de-personalized to share. If you
find something that still looks specific to the original owner (a hardcoded
name, a real client, a baked path), that is a bug worth fixing, not a pattern to
copy. `LETTER-TO-THE-NEXT-MODELS.md` has the design philosophy; `SYSTEM.md` has
the architecture.
