# DR-RUNBOOK: the laptop dies, restore the second brain

> **Note for this copy.** This runbook was written against the original owner's
> live machine and reads as their operational history: specific dates, their
> backup situation, their launchd jobs. It is kept because the *restore
> sequence* and the dependency map are genuinely useful, and because it documents
> how the pieces fit together. Treat the specifics as an example, not as
> instructions about your install. Your setup starts from `install.sh`.


Written 2026-07-07 against the live machine. Every path and command below was
verified against this repo on that date. No em-dashes, no flattery: where the
system has a hole, this file says so.

**The one thing to do BEFORE disaster: step 0. As of 2026-07-07 this repo has
NO off-machine copy. If the laptop dies today, everything in section "What is
lost" PLUS the entire repo is gone.**

---

## 0. Do this NOW, before any disaster (10 minutes, [OWNER]'s hands)

`git remote -v` is empty and `agents/backup_verify.py` reports `no_remote`
(see `store/backup_verify.json`). The hourly autocommit commits locally and
never pushes, so until a remote exists this runbook has nothing to restore
from. The `gh` CLI is not installed; use the GitHub web UI:

1. github.com -> New repository -> name `this system`, **Private**, no README.
2. Then:

   ```bash
   cd ~/Claude/this system
   git remote add origin git@github.com:<your-user>/this system.git
   git push -u origin main
   ```

   (HTTPS with a fine-grained PAT works too if no SSH key is set up:
   `git remote add origin https://github.com/<your-user>/this system.git`.)

3. Prove it restores, not just pushes:

   ```bash
   .venv/bin/python agents/backup_verify.py
   ```

   It shallow-clones the remote into a temp dir and checks `app/server.py`
   and `store_lib.py` exist in the fresh clone. Want: `status: ok`.

4. Make pushing continuous. Today NOTHING pushes automatically; a remote that
   is 3 weeks stale is a 3-week data loss. Until a push step is added to
   automation, run `git push` by hand after real work sessions.

`store/` (contacts, proposals, replies, jobs, config) is tracked in git, so
the remote covers the data that matters, not just code.

---

## 1. Restore the repo (new Mac)

```bash
mkdir -p ~/Claude
cd ~/Claude
git clone git@github.com:<your-user>/this system.git this system
cd this system
```

The absolute path matters: every launchd plist, `serve.sh`, `watchdog.sh`,
and many agents hardcode `[APP_ROOT]`. If the
new Mac's username is not `[OWNER_HANDLE]`, fix the paths in
`agents/launchd/*.plist` before loading them, or create the same username.

Also reinstall the pre-commit secret guard (git does not clone hooks):

```bash
cp tools/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

## 2. Python env via uv (no cmake on this Mac; do not add a build-chain dependency)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh    # installs to ~/.local/bin/uv
cd ~/Claude/this system
uv venv --python 3.12 .venv
uv pip install fastapi "uvicorn[standard]" python-dotenv requests pytest \
    pyyaml numpy google-api-python-client google-auth-oauthlib \
    google-auth-httplib2 cryptography tqdm
uv pip install pywhispercpp   # optional: only coach/coach.py uses it (lazy
                              # import). It ships mac wheels; if it ever tries
                              # to compile instead, skip it, the server and all
                              # agents run without it.
```

There is no `requirements.txt` or `pyproject.toml` in this repo (a known gap);
the list above was read off the live `.venv` site-packages on 2026-07-07
(Python 3.12.13). `make doctor` at the end will tell you if an import is
missing; install what it names.

## 3. Secrets (none of these are in git, on purpose)

**`.env`** at repo root, four keys, values from your password manager or the
provider dashboards (OpenAI, ElevenLabs; the two tokens are self-chosen):

```
BRAIN_TOKEN=...
OPENAI_API_KEY=...
ELEVENLABS_API_KEY=...
GUEST_TOKEN=...
```

- `store/config.json` comes with the repo (it is tracked; its
  `openai_api_key` / `elevenlabs_api_key` fields are empty by design, real
  keys live in `.env`).
- **BRAIN_TOKEN caveat:** `store_lib.sign_secret()` uses `brain_token` as the
  HMAC key for every signed capability link (`/prop/ /mock/ /agree/`). If you
  restore with a DIFFERENT token, every previously sent proposal/mockup link
  goes 404 for the prospect. Restore the OLD value if any live deals have
  links out; rotate later with `tools/rotate_token.sh`.
- **Google Calendar OAuth** (`schedule/credentials/client_secret.json` +
  `token.json`) is gitignored and dies with the laptop. Re-do the one-time
  flow in `schedule/SETUP.md` (Google Cloud Console -> OAuth Desktop client ->
  download JSON -> first run re-consents and caches a fresh `token.json`).

## 4. Other tools the system calls

- **Claude Code CLI**, logged in. `agents/morning.sh` and `app/planner.py`
  shell out to the `claude` binary for the morning brief and chat; without it
  the server runs but briefs and LLM agents fail.
- **Tailscale** (Mac App Store or tailscale.com), logged into your tailnet.
  See step 6.
- Optional: `tools/install_whisper.sh` rebuilds `vendor/whisper.cpp` (local
  transcription; gitignored, regenerable).

## 5. launchd plists ([OWNER] loads these, by hand, never an agent)

Source of truth: `agents/launchd/` holds verbatim copies of all seven live
jobs taken 2026-07-07, with ONE deliberate difference: the watchdog plist
adds `KeepAlive = {Crashed: true}` (D9 #13, self-restart if the watchdog
process itself dies abnormally; see the XML comment in the file for why it is
not a literal `KeepAlive=true`).

| plist | what it runs | cadence |
|---|---|---|
| com.jarvis.brain-server | serve.sh (uvicorn on 127.0.0.1:8765) | always on, KeepAlive |
| com.jarvis.secondbrain | run.sh (Siri captures + dashboard) | every 10 min |
| com.jarvis.morning | agents/morning.sh | daily 06:30 |
| com.jarvis.watchdog | agents/watchdog.sh | every 5 min |
| com.jarvis.autocommit | agents/autocommit.sh | hourly |
| com.jarvis.replywatch | agents/reply_watch.py | every 30 min |
| com.jarvis.retro | agents/retro.py | Sundays 09:00 |

Load them (your hands):

```bash
cp ~/Claude/this system/agents/launchd/*.plist ~/Library/LaunchAgents/
for l in brain-server secondbrain morning watchdog autocommit replywatch retro; do
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jarvis.$l.plist
done
```

First `run.sh` cycle will pop a macOS Reminders permission prompt (the Siri
capture pull); approve it once. The server needs no prompt.

## 6. Tailscale for the public links

`store/config.json` `public_base_url` is `https://macbook-pro.your-machine.ts.net`.
A new Mac gets a NEW ts.net hostname; update `public_base_url` to match or
every link the system generates points at a dead host.

Live state as of 2026-07-07 (from `tailscale serve status`): the proxy is
**tailnet only**, not a public funnel:

```bash
tailscale serve --bg 8765          # what runs today: HTTPS for your own devices
```

For prospect-facing links (proposals/mockups reachable by people OUTSIDE the
tailnet), it must be a funnel instead, which is a deliberate exposure choice:

```bash
tailscale funnel --bg 8765         # public internet -> 127.0.0.1:8765
```

`app/server.py` already hardens funnel traffic (only `/prop/ /mock/ /agree/
/case/ /og/` exist for funnel requests; everything else 404s), but flipping
serve -> funnel is [OWNER]'s call, made knowingly.

## 7. Verify the restore

```bash
cd ~/Claude/this system
make doctor                        # ast sweep + selftest + config check + full pytest suite
curl -s 127.0.0.1:8765 | head -5   # server answers (dashboard HTML)
.venv/bin/python agents/backup_verify.py       # backup loop is closed again
.venv/bin/python tools/verify_payment_links.py # deposit links still alive
```

`make doctor` green + a 200 from the curl is the "system is back" signal.
Then wait one hour and check `git log` shows a fresh autosave (proves
launchd jobs actually run).

## What is NOT backed up and is LOST with the laptop (honest list)

From `.gitignore` plus a live sweep of the working tree:

- **`.env`** (all four secrets). Recoverable only from your password manager.
- **`schedule/credentials/`** (Google OAuth client + token). Re-do SETUP.md.
- **Every `*.log`** (server, agents, run history). Diagnostic history only.
- **`store/tts-cache/`, `store/.coach-ME/`, `store/.coach-THEM/`** (audio),
  **`store/*.lock`** (harmless), **`store/.sign_secret`** (only exists when
  BRAIN_TOKEN is absent; not present today).
- **`content/images/`, `content/samples/`, all `*.mp3 *.mp4 *.wav`**
  (generated media; regenerable at token cost).
- **`.browser-profile/`** (logged-in browser sessions; every site login is
  redone by hand, per the no-credentials rail).
- **`vendor/`** including whisper.cpp (rebuild via `tools/install_whisper.sh`),
  all `node_modules/`, and `.venv/` (rebuild, step 2).
- **`~/Backups/this system/`** store snapshots (tools/snapshot_store.sh
  rsyncs to the SAME disk; it protects against bad edits, not a dead laptop).
- **Up to 59 minutes of uncommitted work** (autocommit is hourly), plus
  **everything committed since your last manual `git push`**, because nothing
  pushes automatically today (step 0.4).

---

## Restore drill results (2026-07-07)

This runbook is no longer theory. `tools/restore_drill.sh` executed the restore
end to end on 2026-07-07 against commit `3f7a3f1`: file:// git clone into a temp
dir, fresh `uv venv --python 3.12` + `uv pip install -r requirements.txt`, then
the ast gate and the FULL pytest suite inside the clone. Run it again any time:
`bash tools/restore_drill.sh` (auto-deletes its temp dir on PASS, keeps it on
FAIL and prints the path).

| stage | result | time |
|---|---|---|
| git clone (file://) | PASS | 1s |
| uv venv create | PASS | 0s |
| deps install from requirements.txt | PASS | 0s (uv cache warm; expect 1-2 min of downloads on a truly new Mac) |
| fresh-install store/ (emptied) | PASS | 0s |
| ast gate (tools/ast_check.py) | PASS | 0s |
| full pytest suite | 5 failed / 1506 passed | 4s |
| **total machine time** | | **5s** |

**Two different restore paths, two different outcomes:**

- **Real DR path (what this runbook describes): GREEN.** store/ is tracked in
  git, so a real restore keeps the cloned store/. Verified in the same drill
  clone: after `git checkout -- store/`, the full suite ran **1511 passed, 0
  failed**. A bare `git clone` + `requirements.txt` restores a fully green
  system in seconds of machine time.
- **Strict fresh-install path (store/ emptied): 5 tests fail.** They pin
  against live store data instead of fixtures, i.e. fresh-install safety is
  broken in the TESTS, not the agents:
  - `tests/test_payment_links.py::TestTierExpectations::test_all_tiers_cover_every_config_payment_links_key` (reads `store/config.json` directly)
  - `tests/test_meeting_prep.py::TestMatchingContactExcludeJobOnly` (3 tests; pin against the real "829 Studios" entry in `store/contact_graph.json`)
  - `tests/test_agent_cadence_checker.py::TestRun::test_fixture_mode_has_expected_shape` (fixture mode still depends on store state)

**requirements.txt now exists** (the gap flagged in step 2 is closed): 50 pinned
packages generated from the live venv via `uv pip freeze`, committed. Step 2's
hand-typed install list stays as fallback documentation; prefer
`uv pip install -r requirements.txt`.

**Chain lint** (`tools/chain_lint.py`, run 2026-07-07 against `agents/morning.sh`):
101 `$RUN` invocations (80 daily, 21 Sunday/Monday-only), every target exists,
ast-parses, and every top-level import resolves in `.venv`. 0 hard failures,
7 warnings:

- **The 6-agent mail lane imports `gmail_api` from `~/Claude/gmail/`, OUTSIDE
  this repo** (job_replies, mail_brain, mail_drafts, mail_sender_scores,
  mail_signals, mail_threads). A this system-only restore brings the chain up
  with the whole Gmail lane crashing at import. Restoring `~/Claude/gmail/`
  (plus its OAuth token) is part of DR whether or not it is in this runbook's
  repo.
- No per-step timeout anywhere in the chain: one hung agent stalls the 6:30 run
  until the 90-minute stale-lock clears.
- Estimated chain runtime: ~5.8 min median across the 33/80 daily agents that
  have runlog data. That number is a FLOOR, not the truth: 47 of the 80 daily
  agents never adopted `runlog.track()` and have no duration history, and the
  uninstrumented set includes the LLM-heavy ones (daily_brief, content_gen,
  jobs). Reality check from the same day: a live chain was observed running
  ~5.7 HOURS with the full fleet active (see the 2026-07-07 lock-heartbeat
  comment in `agents/morning.sh`), which is why the flat 90-min stale-lock
  clear had to become a heartbeat. Until more agents adopt runlog, the 45-min
  budget cannot be verified from data. Slowest instrumented: organize 84s,
  interview_war_room 78s, mail_sender_scores 46s, mail_brain 33s,
  transcript_miner 24s.
- Crash-guard confirmed: `set -uo pipefail` without `-e` plus the
  `RUN=.venv/bin/python || python3` fallback means a crashing agent cannot
  abort the chain. No `$RUN` line is coupled to another with `&&`/`;`/pipes.

**Model independence** (`tests/test_model_independence.py`, 4 tests, green):
zero fable model ids in any runtime surface, every model token in code is a GA
`claude-(haiku|sonnet|opus)-*` id, the `store/config.json` models map resolves
all 12 roles to GA ids, and `app/planner.py`'s fallback `MODEL` constant is GA.
The system runs on any Claude CLI login; nothing depends on Fable existing.

**Still needs [OWNER]'s hands after a restore (unchanged, in order):**

1. **Git remote + push (step 0) — STILL NOT DONE as of this drill**
   (`git remote -v` empty, `store/backup_verify.json` says `no_remote`). The
   drill proves the repo restores itself; it cannot prove an off-machine copy
   exists, because none does.
2. `.env` secrets from the password manager (or Keychain via
   `tools/secrets_to_keychain.sh`) — BRAIN_TOKEN caveat in step 3 applies.
3. Google Calendar OAuth re-consent (`schedule/SETUP.md`).
4. launchd plist loads (step 5, never an agent's job).
5. Tailscale login + `public_base_url` update (step 6), then
   `tools/verify_payment_links.py` to confirm the Stripe links in
   `store/config.json` still resolve.
6. Claude Code CLI login (briefs and LLM agents need it).
7. Restore `~/Claude/gmail/` alongside this repo (see chain-lint finding above).
