# Jobs MCP notes (D221)

_2026-07-03. Written by the D-lane build. Read this before touching MCP enrichment code —
it documents exact tool shapes plus a hard operational constraint discovered live._

## What this MCP actually is

Despite the mission brief calling it "the jobs MCP," this is **Indeed's public job-search
MCP** (server id `8d745385-0dfe-41b1-9d80-cfbb9b29f489`), not a bespoke tool built for this
system. It exposes three tools:

- `search_jobs(search, location, country_code, job_type?)` — Indeed job search.
- `get_job_details(job_id)` — full posting for one Indeed job id.
- `get_company_data(companyName, language, location, knowledgeCategories)` — Indeed's
  company knowledge base: metadata, ratings, salaries. (A 4th tool, `get_resume`, exists on
  this same server per JOBS-POSTMORTEM.md but isn't in this lane's loaded set — that's the
  resume-critique tool the postmortem used, not job search.)

This is a **separate job source from jobs.py's pipeline**. jobs.py sources from hiring.cafe
(`__NEXT_DATA__` scrape) plus Remotive/RemoteOK/Jobicy public JSON APIs. Indeed's MCP is not
a fetcher replacement — its role per D221 is **enrichment**: given a job jobs.py already
found (via hiring.cafe etc.), call `get_company_data` to attach salary/culture/rating
context, and optionally cross-check the same role via `search_jobs`/`get_job_details` if
Indeed independently lists it (a same-role-on-Indeed signal is also a referral/cross-check
path, related to D308).

## Rate limit — confirmed live, matches the postmortem exactly

Tested live 2026-07-03 per this lane's brief (sparing use, verify-then-document):

1. First `search_jobs` call: `Rate limit exceeded for account 367685673 on toolset claude.
   Try again in 33 seconds.`
2. Waited 35s (`perl -e 'select(undef,undef,undef,35)'`), retried once.
3. Second call: `Rate limit exceeded for account 367685673 on toolset claude. Try again in
   48 seconds.` — **the countdown went UP, not down**, after waiting longer than the first
   quoted window.

This is the exact "persistent account cap" / "retry countdown behaving inconsistently"
behavior JOBS-POSTMORTEM.md diagnosis #6 recorded for `get_resume` on this same server/account
(367685673). Conclusion: **the cap is account-wide across this MCP server's tools, not
per-tool, and not a simple decaying window.** Do not build any code path that assumes a
short backoff clears it. No live response body was obtainable this session — zero real
request/response JSON exists to fix in this file. Everything below the schemas is marked
**[E]** (estimated/unverified) and must be treated as a best-guess contract until a session
gets a real response through.

## Tool schemas (verified from the loaded MCP definitions, not guessed)

### `search_jobs`
Required: `search` (string, title/keywords), `location` (string — city+state, or the
literal string `"remote"`), `country_code` (ISO 3166 alpha-2, e.g. `"US"`).
Optional: `job_type` (one of `fulltime|parttime|contract|internship|temporary`).
Returns: **markdown**, not JSON — the tool description says "Returns: Formatted job search
results as markdown" and explicitly instructs the caller to preserve apply links embedded
in job titles. This means any programmatic consumer must **parse markdown**, not `json.loads`
a payload. [E] exact markdown structure (headers? bullet list? table?) — unverified.

### `get_job_details`
Required: `job_id` (string). Returns markdown (same "Returns: ... as markdown" pattern,
apply link embedded in the title). The `job_id` must come from a prior `search_jobs` call's
output — jobs.py's own job ids (e.g. `"rippling___genlogs-corporation___...`) are NOT Indeed
job ids and cannot be passed here directly. [E] no live example of an Indeed job_id format
seen this session.

### `get_company_data`
Required: `companyName` (string, one company only), `language` (2-letter ISO), `location`
(object: `country` required 2-letter ISO, `usState`/`usStateCode`/`usCity` optional/nullable),
`knowledgeCategories` (object of 3 required booleans: `metadata`, `ratings`, `salaries` — the
tool description says "if not sure, return true" for each). `jobTitle` is optional but
**required specifically to get salary data** ("Salary information can only be queried with a
job title"). Returns markdown company profile (culture/comp/CEO/size per the categories
requested). This is the one D221 actually needs most: company size + culture + comp-range
context per candidate job, keyed by `company` (already on every jobs.jsonl record) plus a
best-guess `jobTitle` (already on every record as `title`).

## D221 integration design (built blind, per the fallback instruction)

Given the rate limit made live verification impossible, `job_mcp_enrich.py` (new file, see
below) is written defensively:

1. **Never blocks the core pipeline.** Enrichment is a strictly-additive side-call, tried
   AFTER a job already passed all existing guards (dedupe/YOE/fit-floor). If the MCP call
   fails, times out, or comes back unparseable, the job keeps flowing with no enrichment
   fields set — exactly like every other `except Exception: continue` pattern already used
   throughout jobs.py's fetchers.
2. **Low frequency, cached.** Given the account-wide persistent cap observed above, this
   should run at most once per unique company per some multi-day TTL (not per-job, not
   per-run) — see `store/company_enrich_cache.json` (company-name-keyed, TTL 14d). This
   matches the brief's "document exact shapes so D221 enrichment can run at low frequency."
3. **Markdown parsing, not JSON parsing.** Since the tool returns markdown, the parser
   extracts headline signals with regex/keyword scan (company size mentions, rating number,
   salary range numbers) rather than assuming any JSON structure. This degrades gracefully:
   worst case it finds nothing and returns `{}`, which is the same as "MCP unreachable."
4. **[E] marked functions** are explicitly labeled in `job_mcp_enrich.py`'s docstring as
   built from schema + this rate-limit finding, NOT from a verified live response. The next
   session that gets a real response through should update this file with the actual shape
   and tighten the parser.

## Operational guidance for future sessions

- Before calling any tool on this server, check whether another lane/session hit the same
  cap recently (it's account-wide, per above) — a fresh session's "first" call may already
  be rate-limited from a totally unrelated task.
- If rate-limited: wait once (~35s), retry once. If still blocked, do NOT loop-retry — the
  countdown observed going UP rather than down means further waiting has no established
  floor. Fall back to schema-only / fixture-based work and mark output [E].
- `get_resume` (documented in JOBS-POSTMORTEM.md, same server) is out of scope for this
  lane's loaded toolset but shares the same account cap — don't expect it to be reachable
  either in a session where `search_jobs`/`get_company_data` are already capped.
