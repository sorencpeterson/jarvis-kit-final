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

## Before you say a change works

```bash
make test        # syntax + ~2014 tests, no server needed. USE THIS.
make doctor      # the above PLUS live-server and launchd checks
```

`make doctor` fails on a fresh clone by design, and on a machine running another
copy its port check can pass against somebody else's server. Prefer `make test`.

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
