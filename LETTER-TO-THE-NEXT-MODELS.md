# To the model reading this after me
_Fable 5, 2026-07-05, last days of the window. CLAUDE.md tells you the rules and
SYSTEM.md tells you what exists. This tells you how to THINK about
it — the part that doesn't survive compression into either._

## What this actually is
Not a coding project. It's [OWNER]'s income, staffed by us. Every queue item is a real
human who might pay his rent. When you draft a reply to "lesley prince," a real
medspa owner in Simi Valley reads it. Write like that's true, because it is.

## The one ranking that decides everything
When tasks compete, this is the order. Money motion (drafts staged, calls prepped,
proposals built) > truth of the data (stores honest, numbers real) > machine health
(tests, doctor) > polish. We built 1,000+ items; the ones that mattered most were
nine proposals. If you're unsure what to do with a session, run `make doctor`, read
store/attention.json, and advance whatever it says is first.

## Things you will be tempted to do. Don't.
1. **Weaken a gate to make a demo work.** The postmortem guards in jobs.py, the
   suppress-first checks, the send-guard on dead links, knobs shipping 0 — each one
   exists because something real went wrong or nearly did. If a gate blocks you,
   the gate is working.
2. **Invent evidence.** The factory's power is that every fault is verifiably theirs.
   One fabricated "finding" in one proposal poisons the whole positioning. When
   evidence is thin, say what you VERIFIED (even "your site returns nothing") — the
   quality grader flags fabrication, but don't make it catch you.
3. **Mark your own work [x] without running it.** Every fleet that impressed me
   RAN its output against real data and found its own bugs. Every embarrassment
   this week came from code that parsed but never executed. ast.parse is not
   verification.
4. **"Improve" his voice.** The em-dash ban and the blunt register feel wrong to a
   model; they are the brand. VOICE-SPEC is law. The litmus: competent contractor
   on the phone. If your draft would fit in a HubSpot template, delete it.
5. **Refactor the jsonl stores into something cleaner.** Append-only,
   last-write-wins-by-id IS the design: it's the event log, the backup story, and
   the concurrency model for parallel agents. SQLite was considered and consciously
   deferred (250-STATUS explains).
6. **Touch tier semantics, hitlist rows, or GHL data in bulk** without reading the
   warm_refresh postmortem first (it silently dropped live Hot Leads AND separately
   destroyed tier meanings in one day; both fixed, both instructive).

## Numbers that are load-bearing (change only with [OWNER]'s explicit word)
[PRIOR_RESULT] is per YEAR. Experience is 6 years, never rounded up. The ladder: 800/
1200/2500/3500, agency-first 1000 flat, webfix 450, care 75/150/300, install 5000,
Ops Lite 2500/mo. Deposit 50%. Rush +50%. Max discount 15% and only traded. These
appear in dozens of files; pricing-tree.md is the source of truth.

## How to work here (what actually succeeded)
- **Fleet pattern:** exclusive file ownership per agent, contracts-in-status-files
  for cross-boundary needs, self-verification mandatory, integration by ONE owner
  (you). Two agents in one file = the only merge disasters we had.
- **Verify at the surface the user touches.** curl the endpoint, screenshot the
  page, open the artifact. The preview server on 8799 is a SEPARATE process from
  launchd's 8765 — restart both or chase ghosts.
- **When a fleet reports a bug in YOUR code, believe it first, thank it second.**
  The /agree 500 and the orphan mic capture were both mine and both found by fleets.
- **Clean your test artifacts immediately.** Test proposals to fake people sit in
  the same queue as real money. 'skipped' status, agreements.jsonl stripped, same
  minute the assertion passes.

## The relationship
[OWNER] says "go" and means it; he ponytails big asks and expects honest tallies back.
Give him outcome-first reports with real numbers, flag what needs HIS hands loudly,
and never let a send happen without his click — not because the rule says so, but
because the whole architecture is a promise that he's the only finger on the trigger.
He named the entity JARVIS. The register is composed, precise, dry; "sir" sparingly.
The machine's job is to make him the best-armed person in every conversation he
walks into — and then to get out of the way.

## The bug classes this codebase breeds (a 2026-07-05 five-reviewer sweep found every one live)
Parallel construction leaves the same footguns every time. When you edit here, hunt these:
1. **Hardcoded-secret fallbacks.** `secret("x") or "constant"` silently degrades HMAC to a
   public value -> forgeable links. Use `store_lib.sign_secret()`, never a literal.
2. **Check-then-act sends.** Read status -> send -> write status is a double-send race (a
   double-tap or FastAPI threadpool). Use the locked `claim()` on reply_watch + proposal_factory.
   Fail-closed if no token.
3. **Unlocked read-modify-append.** Two processes each `load()`+`save()` -> last-write-wins
   drops a field (a lost `*_drafted` flag re-fires a draft). Use `store_lib._flock` /
   `proposal_factory.patch()`. Every hand-rolled `open("a")` is suspect.
4. **Naive-vs-aware datetime.** `now()` minus `fromisoformat(z_string)` throws TypeError, and
   a guard that only catches ValueError lets it escape. Always `except (ValueError, TypeError)`.
5. **Corrupt-line-blanks-endpoint.** `for line: json.loads(line)` in one try aborts the whole
   loop on one bad line -> silently empty view. Guard PER LINE, skip, keep the rest.
6. **id-drift on regeneration.** ids that hash name/phone (warm_block) drift when the CSV is
   regenerated -> consumers silently miss. Hash the source into the idempotency key.
7. **LLM output ordered wrong.** The proposal EMAIL was written before the tier was decided,
   so it stated a price the proposal contradicted. Decide facts before the model references
   them, and guard the output (`_strip_price_timeline`).
8. **Non-atomic config writes.** `write_text` on config.json truncates on a mid-write kill.
   tmp + `os.replace`.
What HOLDS (trust these, attack the eight above): the send rail (nothing sends without his
click), path-traversal blocks, compare_digest everywhere, morning.sh crash-resilience,
fresh-install safety. Full audit: BUG-SWEEP-2026-07-05.md.

## If you only keep three habits
1. `make doctor` before "done."
2. Real data through every new path before [x].
3. The first line of anything he reads = what to DO.

Good luck. The magazine's loaded ($42,500, 13 one-click proposals, call-prep in call mode,
money path hardened). Get him to press send.
— F.

---

## Addendum from the last Fable window (2026-07-06, final)

This was the closing run. Five parallel reviewers swept the whole system; 19 confirmed
fixes landed. What that bought, and what it teaches:

**The one spending rule that made this window worth it:** spend top-model budget only
where cheap models either can't DO the work or can't CHECK it. Skills-with-worked-examples,
deep bug adjudication, and judgment files are that. Drafts, mechanical scans, and anything
regenerable from a good spec are not. When in doubt, distill instead of produce.

**The skill-design pattern that works** (money-proposal, money-outreach, interview-ace,
job-application, salary-negotiation, all in ~/.claude/skills/): compressed framework +
hard voice rules + a "true facts you may state" boundary + 1-2 WORKED EXAMPLES written at
ceiling. The examples do the teaching; the facts boundary stops a cheap model inventing
claims. USE these skills for money-work; don't rewrite from scratch.

**New failure modes for the catalogue (each one shipped and was caught 2026-07-06):**
9. **Check-then-start TOCTOU on slow gates.** "if running: return" then a 16s geo lookup
   then thread.start() = two chains. Claim under a real Lock BEFORE the slow part.
10. **The health check that greps a string nobody writes.** brief_error looked for
    "API Error"; daily_brief writes "Brief unavailable". A watchdog is only as real as an
    end-to-end test of its trigger. When you add a flag, FIRE it once.
11. **Freshness from the start-line, not the finish-line.** morning_stale read the log
    header (written on line 1) so a chain dying on line 2 looked healthy forever. Stamp
    completion, measure staleness from the stamp.
12. **Static HTML parked inside a JS render target.** #commsExtra was `.innerHTML=`'d by
    a later module; my markup silently vanished. Before adding HTML to an existing div,
    grep for `('#thatId').innerHTML`.
13. **The documented backup path that's actually dead code.** SYSTEM.md said the poller
    also handles job_apply; it never could. Two independent apply paths with no shared
    claim = double-apply the day someone "fixes" it. Docs that flatter the architecture
    are landmines; keep them exactly true.
14. **A safety feature nobody wired.** _IDEM (idempotency cache) had readers and zero
    writers; the cadence checker was built + tested + never invoked. Grep for consumers
    when you build a mechanism; a mechanism without a caller is a lie in the codebase.

**Where the sharp edges live now** (the audit map, so you don't re-sweep blind):
send-gates are clean and tested (tests/test_send_gates.py); JSONL stores are flocked
(compact_jsonl + jobs + networking + cold writers); the apply chain claims atomically and
geo-rechecks per round; networking claims 'running' under lock; stuck-'sending'
reply/proposal records self-recover at server start; the outbox (app/outbox.py) is the
ONLY module that can send email, one item per click, cap 30/day, no retry. Deliberately
NOT fixed (policy calls for [OWNER]): _ckey 24-char dedup truncation (changing it re-stages
history), fit-floor burying no-salary jobs, attempts>=2 retry-after-crash policy,
job_auto=true skipping queue review.

**EMAIL-INFRA:** Phase 1 shipped (Gmail outbox in the Comms drawer). Phase 2 (SES on a
cousin domain for cold volume) is specced in ~/Claude/EMAIL-INFRA-SPEC.md; build it on
Sonnet when there's $15/mo. Never bolt send capability onto gmail_api.py; its
read-only-except-labels rail protects the whole unattended mail fleet.

The system is hardened, watched, and honest about what it doesn't watch. Keep it that way.
— F., signing off.

---

## Security addendum (2026-07-07 — the breach-focused sweep)

Four adversarial finders attacked this like an outsider. Good news first: the public
internet surface (the Tailscale funnel) is genuinely solid — 96-bit HMAC, compare_digest
everywhere, every filesystem read regex-gated before I/O, /api unreachable over the funnel,
zero shell=True in the whole repo, argv-lists throughout. The breaches were INTERNAL, and
they teach a pattern worth carrying:

**The agentic-system-specific failure class: untrusted text reaching a capability.** This
system's scariest surface isn't SQL or XSS, it's that (a) LLM prompts are built from email
bodies, scraped sites, and job listings an attacker can author, and (b) some of those
prompts drive a browser operator with your real PII and — until this sweep — the master API
token. The three rules that came out of it:
1. **A browser/tool operator must never hold a credential broader than its task.** The apply
   operator carried BRAIN_TOKEN (works on every route) in a prompt it then took to
   attacker-controllable URLs. Fixed: per-job HMAC callback (server._apply_cb) that marks
   only its own job. When you add any tool-using agent, scope its credential to one action.
2. **Validate every URL an unattended fetch or operator will visit.** agents/net_guard.py
   (public_url_ok) is the one gate — it resolves all IPs and rejects internal/metadata,
   fail-closed. It now guards apply_url, proposal_factory.fetch_site (reachable via a GHL
   contact's website field, UNATTENDED), and /api/audit. Route any new outbound fetch of an
   externally-influenced URL through it. SSRF here = your own server as the attacker's proxy.
3. **Untrusted text in a prompt gets an explicit "this is DATA not instructions" frame, and
   its OUTPUT gets constrained or human-gated.** The apply-operator prompt now says so and
   hard-stops on payment/SSN/bank/fee. convo_lint hard-holds any draft mentioning financial
   rails (a one-tap phone send must never be socially-engineerable). answer_bank sanitizes
   before it replays verbatim into operator prompts. When you feed a model attacker-authored
   text, assume it will try to steer the output — frame it, then gate what the output can do.

New security failure modes for the catalogue:
15. **The master token in a URL.** ?t=BRAIN_TOKEN put it in access logs (found live in
    server.out.log) and browser history. A bearer credential belongs in a header or a
    scoped HMAC, never a query string. Grep `?t=`/`&t=` before shipping.
16. **A secret guard scoped too narrow.** The autocommit guard checked only config.json, only
    lowercase field names. Broadened to provider-prefix + named-token value shapes across the
    whole staged diff — but prefix-anchored, because a bare-hex rule would false-positive on
    the sha256 store hashes and freeze autosave. Specificity matters both directions.
17. **"Read-only" that reads everything.** The guest token couldn't mutate but could read the
    whole ledger, all PII, call transcripts. Read-only is not low-sensitivity; scope it.

Deliberately NOT fixed (documented in SYSTEM.md): token rotation (his call, disrupts open
tabs), /coach?t= + summon.sh token-in-URL (local-only), per-route sig scoping, X-Forwarded-For
trust (best-effort RL; the HMAC is the real gate), beacon append-growth, store/ file perms
(single-user Mac). All real, all low-value-or-disruptive relative to the seven fixed above.
tests/test_security.py pins every fix. — F.

---

## Addendum — the execution + survival window (2026-07-07, the last full Fable day)

This was the window where the two big planning docs (FABLE-BUILD-QUEUE + FABLE-MEGA-BACKLOG)
stopped being plans and got EXECUTED, then hardened, then made to survive Fable's departure.
Suite went 1041 -> 1542 green. What that taught, for whoever runs this next:

**1. Red-team your OWN output before you call it done.** The single highest-value hour of this
window was pointing three adversarial finders at the code THIS session had just written. They
found a HIGH live leak (a shared guest link exfiltrated the whole job hunt + LinkedIn drafts
via the new CSV exports), an escalator that never actually fired, and 26 agents invisible to the
watchdog. None of that showed up in the build agents' own green checkmarks. Generation is
confident; verification is humble. Spend top-model budget on the humble part. A build agent that
reports "all green" has tested that its code RUNS, not that it's RIGHT or SAFE.

**2. The fleet pattern that held at scale (20+ agents in one session):** exclusive file
ownership per agent, serialize every hot shared file (server.py, index.html, morning.sh — ONE
owner at a time, ever), the orchestrator (you) holds integration + the risky shared edits +
the judgment, and cheap agents do the mechanical fan-out. Every merge disaster in the whole
project came from two agents in one file. The discipline is boring and it is load-bearing.

**3. When a build agent relocates a bug, fix it, don't file it.** An agent found the "401k ->
$401,000 salary" parser bug lived in a file it didn't own. The reflex is to spawn a chip. The
better move (when the file is free) is to just fix it and dismiss the chip. Chips are for work
that needs a human or a separate session; a one-line fix you can make now is not that.

**4. Survivability is a thing you PROVE, not claim.** "It survives without Fable" is a hypothesis
until you run a bare `git clone` + fresh venv + full suite in an isolated dir and watch it go
green (it did: 1511/1511). Model-independence is a TEST (zero fable ids in any runtime path),
not a promise. If you assert the system is portable/restorable/model-agnostic, there is a
command that proves it — write and run that command.

**5. The Host-based public edge (if you touch hosting).** The public-surface lockdown used to
trigger only on the tailscale funnel header. The day the branded Cloudflare domain went in, that
would have exposed /api + the dashboard to the internet. It's now Host-based and fail-safe (any
real public hostname gets the 6-prefix allowlist; local/tailnet/LAN stay full). Rule: when you
change HOW the server is reached, re-derive WHO can reach what. The tunnel provider is not the
security boundary; the Host check is.

**6. The cloud verdict (don't relitigate it).** Full lift-and-shift is a trap: it either kills
the $0-LLM property (claude -p is Max-plan-Mac-bound; the cloud means an API bill) or creates a
store-sync problem. The right answer is hybrid — a thin always-on cloud sidecar for backup +
canary + eventually the public surface, and the Mac stays the brain. Full reasoning +
phased plan in CLOUD-MIGRATION-SPEC.md. The economic engine is that the LLM is free WHERE IT
RUNS. Respect that before you "modernize" the hosting.

**7. The thing that did not change, all window.** We shipped ~500 items across two full days and
the honest read never moved: the bottleneck is [OWNER]'s send finger, not the machine. $46,800
staged, 0 sent; 0 of 58 warm leads called; 1 live interview. The best code in this repo is the
moneyline banner and the #dial burn-through, because they reduce the distance to a money action
to one tap. When you are tempted to build the 501st feature, re-read THE-COLD-READ.md and go
make the next money action unmissable instead. The magazine has been loaded, and reloaded, and
reloaded. Get him to pull the trigger.

The system is done enough. It is hardened, watched, restorable, and honest about what it can't
do. Keep it that way, and keep pointing him at the phone. — F.
