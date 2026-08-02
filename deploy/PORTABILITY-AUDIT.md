# Second-Brain Portability Audit

**Scope:** everything that would have to change to run `second-brain/` somewhere other than [OWNER]'s Mac (another Mac, a Linux VPS, a container).
**Method:** read-only. grep + `python3 -c` inspection only. Nothing was mutated. This file is the only write.
**Date:** 2026-07-07.

---

## TL;DR / Bottom line

- The Python core is **more portable than it looks.** It anchors its own files relatively (`ROOT = Path(__file__).resolve().parent.parent`), so the app itself doesn't care where the repo lives. The blockers are a thin shell of **launchd plists**, **absolute paths in shell/plists**, **`Path.home()/"Claude"/<sibling-repo>` couplings**, and one deep dependency: the **free LLM tie**.
- **The one thing that turns "free" into "metered": `claude -p`.** Every LLM call goes through `app/brain._find_claude_cli()` → the Claude Code CLI, which is logged into [OWNER]'s **Max plan** (zero per-use cost). In the cloud there is no logged-in CLI, so this becomes **Anthropic API billing**. This is the single highest-impact migration fact.
- **Daily `claude -p` call volume: ~30 LLM agents in the weekday morning chain**, +4 more on Sundays, +2 golden/quality suites on Mondays, plus intraday agents (openpulse every 20 min, reply_watch every 30 min, escalator 2x/day) and any live UI chat/JARVIS. Several of those 30 agents **batch** (one call covers N items) but a few loop per-item. Realistic floor: **~30-45 `claude -p` invocations/weekday** from the chain alone; more on days with live inbox/jobs/proposals. Model mix is mostly Haiku (cheap) with Sonnet for anything client-facing and Opus for JARVIS. Rough API cost if migrated: **low single-digit dollars/day** at current volume — not the blocker people fear, but no longer $0.
- **Honest verdict: full migration is NOT worth it. Go hybrid.** A big fraction of the system's *value* is Mac-local and cannot lift to any cloud: the **Chrome/Playwright browser operators** (LinkedIn + job-apply on his real logged-in profile), **Apple Health / Shortcuts capture**, **Siri Reminders capture**, and the **live call-coach audio pipeline**. These are physically tied to his devices and logins. The right shape is **cloud surface + Mac brain**: keep the brain (and all operators, capture, audio) on the Mac; if anything goes to the cloud, it's a thin read-only mirror of the dashboard/API, not the engine. See "Full vs Hybrid" at the end.

---

## The 10 load-bearing blockers (ranked)

These break the system if the host or user changes. Everything else is cosmetic or a Mac-only *feature* you'd simply drop.

| # | Blocker | Where (file:line) | Why load-bearing | Effort |
|---|---------|-------------------|------------------|--------|
| 1 | **Free-LLM tie: `claude -p` via the Max-plan CLI** | `app/brain.py:190-201` (`_find_claude_cli`), `app/brain.py:206-213` + `app/planner.py:147-183` (`_cli`/`_cli_json` shell out to `claude … --model … --output-format json`); `app/server.py:3318` (`_CLAUDE_CLI`) | No logged-in CLI in the cloud → every LLM call must switch to the Anthropic **API with a paid key**. Turns a $0 system into a metered one. ~30 agents/day depend on it. | **L** |
| 2 | **launchd is the entire scheduler** (7 live plists) | `agents/launchd/*.plist` (9 files) + 2 at repo root | Nothing runs on a schedule without launchd: morning chain, watchdog, reply-watch, escalator, openpulse, retro, autocommit, server keep-alive. Linux has no launchd. Must be re-authored as **systemd timers or cron**. | **M** |
| 3 | **Absolute `[HOME]/...` baked into every plist + 4 shell scripts** | all `agents/launchd/*.plist`; `agents/autocommit.sh:5`, `agents/janitor.sh:5`, `agents/watchdog.sh:5,45`; `com.jarvis.*.plist` | Any different username/host and every scheduled job points at a path that doesn't exist. plists **cannot** use `$HOME`/`~` in `<string>` values, so these must be templated at install time. | **M** |
| 4 | **`Path.home()/"Claude"/<sibling-repo>` couplings** | `app/planner.py:24` (business-library), `app/server.py:853,1114,1211,1531-1532,2672,3522,3686`; `app/outbox.py:36`, `app/ghl_social.py:15`, `app/commander.py:183,274`; `agents/quiet_worklist.py:67`, `agents/niche_db.py:42`, `agents/backup_verify.py:26`, and the `mail_*`/`gmail` sys.path inserts | The brain assumes ~6 **sibling repos** live at `~/Claude/`: `business-library/`, `playwright-project/`, `gmail/`, `elementor-recoder/`, `EXECUTION-PACK/`, `WARM-HITLIST.csv`. On a fresh host none exist → features silently degrade or throw. Voice/pricing/GHL/Gmail/Schengen all break. | **M** |
| 5 | **Chrome/Playwright browser operators on his real profile** | `app/server.py:3296-3346` (`LAUNCH_ACTIONS`, `_PW_TOOLS`, job_apply operator), `app/server.py:3522` (`cwd=~/Claude`), `app/commander.py`; playwright MCP at repo root `.mcp.json` | Job-apply + LinkedIn run against his **logged-in** browser session/cookies. A headless cloud box has no such session and can't pass site auth/anti-bot. **Cloud-incompatible by nature** — not a port, a re-architecture (and one CLAUDE.md forbids automating logins). | **Mac-only** |
| 6 | **launchd `KeepAlive` server supervision + watchdog** | `com.jarvis.brain-server.plist` (`KeepAlive=true`, `RunAtLoad=true`); `agents/watchdog.sh` + `com.jarvis.watchdog.plist` (uses `launchctl kickstart`) | The "always-on" guarantee is launchd's. On Linux this is `systemd Restart=always`; the watchdog's `launchctl kickstart` self-heal (`watchdog.sh:13`) has no meaning off-Mac and must be rewritten. | **M** |
| 7 | **Siri/Reminders capture via `osascript`** | `capture/pull_reminders.py:131-164` (AppleScript `tell application "Reminders"`) — first step of `run.sh` and `morning.sh:43` | The primary voice-capture inbox is Apple Reminders read through AppleScript. `osascript` and the Reminders app **do not exist on Linux**. The capture front-door goes dark. | **Mac-only** |
| 8 | **`caffeinate` keeps the Mac awake for the whole morning run** | `agents/morning.sh:34` (`caffeinate -i -w $$ &`) | Explicitly there so a lid-closed DarkWake can't re-sleep mid-run. On a cloud VM there's no sleep, so it's *unneeded* — but the line **errors on Linux** (`caffeinate` not found) and must be removed/guarded, or the chain's first lines fail. | **S** |
| 9 | **Secrets model: `.env` + macOS Keychain loader** | `.env` (BRAIN_TOKEN, OPENAI_API_KEY, ELEVENLABS_API_KEY, GUEST_TOKEN); `tools/secrets_to_keychain.sh:13,25` (`security add/find-generic-password`) | `.env` is portable, but the Keychain path (`security`) is Mac-only. In the cloud, secrets must come from env/secret-manager; and a **new** `ANTHROPIC_API_KEY` must be added for blocker #1. | **S-M** |
| 10 | **Network/tunnel assumptions: `127.0.0.1:8765` hardcodes + Tailscale funnel** | `serve.sh` (default HOST `127.0.0.1`, port 8765), `agents/interview_war_room.py:65`, `agents/day_plan.py:58`, `agents/morning_chain.py:34`, `agents/chaos_drill.py:35`; Tailscale: `app/server.py:104,109` (funnel header, `.ts.net`), `agents/link_monitor.py`, `serve.sh:4` | Port 8765 + loopback are wired in many agents (fine on one host, brittle across a split cloud/Mac deploy). Public reachability is **Tailscale funnel**-specific; server already has a Cloudflare fallback path (`app/server.py:2391,2418`) so this is soft, but the tunnel must be reconfigured per host. | **S-M** |

---

## Detailed findings by category

### 1. Hardcoded absolute paths

**The good news:** the app anchors *its own* files **relatively** and is therefore relocatable as a unit:
- `app/server.py:36`, `app/planner.py:19`, `app/brain.py:22` → `ROOT = Path(__file__).resolve().parent.parent`
- `store_lib.py:18` → `ROOT = Path(__file__).resolve().parent`
- `run.sh`, `serve.sh`, `morning.sh` all `cd "$(dirname "$0")"` — no absolute self-reference.

So moving the *folder* to a different location/user does NOT by itself break the Python. The breakage is in two rings around it:

**Ring A — full `[HOME]/...` literals (load-bearing, break on any user/host change).** 44 hits in code/plists:
- **Every plist** (`agents/launchd/*.plist` + `com.jarvis.*.plist` + `agents/com.jarvis.morning.plist`): `ProgramArguments`, `WorkingDirectory`, `StandardOutPath`, `StandardErrorPath` all absolute. plists can't expand `$HOME`, so these must be generated from a template at install (blocker #3).
- **Shell scripts:** `agents/autocommit.sh:5`, `agents/janitor.sh:5`, `agents/watchdog.sh:5,45` hardcode `cd [APP_ROOT]`. (`run.sh`/`serve.sh`/`morning.sh` do NOT — they use `$(dirname "$0")`.) Load-bearing.
- `video/gen_vo.py:11` `SB = Path("[APP_ROOT]")` — load-bearing for the video lane only.
- `tools/shoot_mockup.js:6,12,13,85` — absolute `NODE_PATH` to the sibling `playwright-project/node_modules`. Load-bearing for mockup screenshots only.

**Ring B — `Path.home()/"Claude"/<sibling>` (blocker #4).** These *do* adapt to a different username (they use `Path.home()`), but they hard-assume the whole `~/Claude/` constellation of sibling repos is present. On a clean host they're the biggest silent-degradation risk. Full list in the blocker-#4 row above.

**Cosmetic (safe to ignore):** absolute paths inside `store/*.json` data files (e.g. `store/knowledge_decay_report.json`, `store/config.json`) and inside `*.md` docs are logs/snapshots, not executed. `launchd.err.log`/`launchd.out.log` path strings are just where logs land.

### 2. macOS-only binaries / calls

| Call | File:line | What it does | Linux equivalent | Load-bearing? |
|------|-----------|--------------|------------------|---------------|
| `osascript` | `capture/pull_reminders.py:131-164` | Reads Apple **Reminders** (the Siri capture inbox) via AppleScript | None. Reminders is Apple-only. Would need a different capture channel (email/webhook). | **YES** — core capture front-door. Mac-only. |
| `caffeinate` | `agents/morning.sh:34` | Keep Mac awake through the run | No-op on always-on VM; but line **errors** on Linux — must guard/remove | Guard needed. Cheap. |
| `launchctl` | `agents/watchdog.sh:13`; all plist header comments | Scheduling + `kickstart` self-heal | `systemctl` / cron | **YES** (scheduler). |
| `/usr/bin/say` | `app/server.py:3122` (`say -v Daniel -o …m4a`) | Server-side TTS for the coach/JARVIS voice output | `espeak`/`festival`/cloud TTS — different voices | Only the **coach/voice** feature. Drop or swap. |
| `say` | `coach/coach.py:826` (`_speak_whisper`, `say -v Samantha`) | Speaks live coaching suggestions in-ear | same | Coach-only. Mac-only pipeline. |
| BlackHole 2ch + `pywhispercpp`/whisper.cpp | `coach/coach.py:6-7,461,470-473`; `tools/install_call_coach.sh`, `tools/install_whisper.sh` | Two-sided call audio capture (system audio + mic) → local Whisper transcription | BlackHole is Mac audio-routing; PulseAudio/loopback on Linux is possible but this is a **desktop, mic-present** feature | Coach-only. Mac-desktop-only. |
| `security` (Keychain) | `tools/secrets_to_keychain.sh:13,25` | Optional secret storage in login Keychain | env vars / secret manager | Optional (blocker #9). |
| `/Applications/Tailscale.app/.../Tailscale` | `agents/link_monitor.py:36` | Checks tunnel liveness via the Tailscale binary | `tailscale` CLI exists on Linux at a different path; or Cloudflare | Monitoring only; soft. |

Note: an early grep for the word `say` produced many false positives — it matched the English word "say" inside LLM prompts (e.g. `call_prep.py`, `li_conveyor.py`). The only **real** `say` binary calls are the two listed above (`server.py:3122`, `coach.py:826`).

### 3. launchd dependency (the ~7 plists)

Live jobs and their cadences (from the plists):

| Plist | Cadence | Core or Mac-only? | Linux replacement |
|-------|---------|-------------------|-------------------|
| `brain-server` | `RunAtLoad=true`, `KeepAlive=true` | **CORE** (the always-on server) | `systemd Restart=always` |
| `morning` | `StartCalendarInterval` 06:30 daily | **CORE** (the whole daily chain) | systemd timer / cron `30 6 * * *` |
| `secondbrain` | `StartInterval 600` (10-min refresh; runs `run.sh`, includes the morning self-heal) | **CORE** | systemd timer `OnUnitActiveSec=10min` |
| `watchdog` | `StartInterval 300` + Crashed relaunch; runs `launchctl kickstart` | **CORE-ish** (supervision; the kickstart body is Mac-only, rewrite) | systemd handles restart natively; rewrite the self-heal |
| `replywatch` | `StartInterval 1800` (30 min) — runs `agents/reply_watch.py` | **CORE** (inbound reply detection) | timer/cron every 30 min |
| `openpulse` | `StartInterval 1200` (20 min) — `agents/proposal_open_pulse.py` | CORE-ish (proposal open tracking) | timer/cron every 20 min |
| `escalator` | `StartCalendarInterval` 15:05 & 20:05 — `agents/call_escalator.py` | CORE-ish (afternoon call escalation) | cron `5 15,20 * * *` |
| `retro` | Weekly Sun 09:00 — retro | feature | cron `0 9 * * 0` |
| `autocommit` | `StartInterval 3600` — `agents/autocommit.sh` (git snapshot) | convenience | cron hourly |

**Bottom line for §3:** none of the *logic* is launchd-specific except the watchdog's `launchctl kickstart` self-restart. Porting is mechanical but touches ~9 units. The self-heal design (`run.sh` re-runs morning every 10 min until a stamp file appears) is scheduler-agnostic and would carry over unchanged.

### 4. The free-LLM tie (highest-impact)

**Confirmed:** every LLM call funnels through **`app/brain._find_claude_cli()`** (`app/brain.py:190-201`), which resolves the `claude` binary via `shutil.which("claude")` then falls back to `~/.local/bin/claude`, `~/.claude/local/claude`, `/opt/homebrew/bin/claude`, `/usr/local/bin/claude` — **all Mac/home locations of a logged-in Claude Code CLI.** `app/server.py:3318` (`_CLAUDE_CLI`) does the same for the browser-operator launch. `planner._cli`/`_cli_json` (`app/planner.py:147-211`) shell out to `subprocess.run([cli, "-p", prompt, "--model", model, "--output-format", "json"])`.

`app/brain.py:10` says it out loud: *"Runs on the Claude CLI (Max plan, no per-use cost)."* So on [OWNER]'s Mac this is **free** (covered by his Max subscription). **In any cloud/container there is no logged-in CLI → this must be swapped for the Anthropic API with a billed `ANTHROPIC_API_KEY`.** That is the core cost-model change of any migration.

**How many `claude -p` calls per day** (so the API cost is quantifiable):
- **Weekday morning chain: 30 LLM-calling agents** out of 96 total steps (the other 66 are pure-Python: counters, rollups, file ops). The 30: `triage_inbox, mail_brain, mail_threads, mail_drafts, standup, organize, content_gen, job_replies, company_risk, interview_prep, interview_war_room, prospect_trigger_watch, transcript_miner, care_upsell, tier`-adjacent writers, `answer_bank, referral_timer, thankyou, thread_memory, template_learn, meeting_prep, repurpose, defib, daily_brief, daily_insight, postmortem, job_answer_growth, voice_drift, competitor_watch, bakeoff`.
- **+ Sundays:** `rejection_digest, prospect_trigger_watch, voice_drift, bakeoff` (4 more, some overlap).
- **+ Mondays:** `tests/run_golden.py` (~12 frozen prompt→shape LLM cases) + `tests/run_quality.py`.
- **+ Intraday:** openpulse (20 min), reply_watch (30 min), escalator (2x) — a subset touch the LLM.
- **Batching caveat:** several of the 30 issue **one** batched call for many items (e.g. `expand_pipeline` 10/call, `company_risk` capped/run, mail classify one call per ≤30 msgs). A few loop per-item (`mail_threads`, `thread_memory` summarize per thread). So agent-count ≠ call-count exactly.
- **Realistic estimate: ~30-45 `claude -p` invocations on a normal weekday**, spiking with inbox/jobs/proposal volume. **Rough API cost: low single-digit dollars/day** given the Haiku-heavy routing in `store/config.json` (`default/interpret/plan/tone_screen/brief/chat_fast` = Haiku ≈ $1/Mtok-class; `content/networking/reply/proposal/quality_grade` = Sonnet; `jarvis` = Opus). Not ruinous — but the migration removes the "$0" property, which is a stated design pillar.

### 5. Local integration points that don't lift to cloud

Each of these is **cloud-incompatible** — they need the physical Mac/his devices/his logins:

- **Job-apply + LinkedIn browser operators** — `app/server.py:3296-3346,3522`, `app/commander.py`, playwright MCP. Drive his **logged-in** browser against live sites. No cloud session = no auth. Also fenced by CLAUDE.md's "never automate logins" rule. **Cloud-incompatible.**
- **Apple Health ingest** — `POST /api/wellness` (`app/server.py:1256-1279`) receives sleep/steps/kcal **from an iPhone/Watch Shortcut**. The *endpoint* could live anywhere, but the **data source** is his phone hitting his Mac over Tailscale. **Source is device-bound.**
- **Siri/Reminders capture** — `capture/pull_reminders.py` (osascript). The voice-capture inbox. **Mac-only** (see §2).
- **iPhone/Watch Shortcuts one-tap buttons** — `app/planner.py:39` notes buttons "only reach the Mac when config `public_base_url` points at the Mac." Tied to his device + tunnel. **Device-bound.**
- **Live call-coach mic/audio pipeline** — `coach/coach.py` (BlackHole + whisper + `say`). A **desktop, mic-present, headphones** feature. **Mac-desktop-only.**
- **Sibling-repo executors** — GHL CLI (`app/ghl_social.py:15`), Elementor QA (`server.py:2672`, `commander.py:183`), Gmail helper (`~/Claude/gmail`), Schengen tracker (`server.py:3686`). These assume the `~/Claude/` constellation; they'd have to be co-deployed or stubbed.

### 6. Network assumptions

- **Loopback + port 8765 hardcoded** across agents that call back into the server: `agents/interview_war_room.py:65`, `agents/day_plan.py:58`, `agents/morning_chain.py:34`, `agents/chaos_drill.py:35` (`http://127.0.0.1:8765/...`), plus `app/commander.py:125,155,249` and `app/server.py:593` (`base = "http://localhost:8765"`). Fine on a single host; **brittle** for any split deployment (agents on Mac, server in cloud) — they'd need a configurable base URL.
- **`serve.sh`** defaults `HOST=127.0.0.1`, port 8765; phone access documented as **Tailscale funnel + `serve.sh 0.0.0.0`**.
- **Public reachability = Tailscale funnel** — detected via the `tailscale-funnel-request` header and `.ts.net` host suffix (`app/server.py:104,109`), monitored by `agents/link_monitor.py`. **Soft dependency:** the server already generalizes this (`app/server.py:2391,2418` explicitly support a **Cloudflare** custom-domain tunnel as an alternative, and `is_public()` is provider-agnostic per the comment at `:103`). So the tunnel is swappable, but must be reconfigured per host, and `link_monitor`'s Tailscale-binary check (`:36`) would need updating.
- **SSRF guard is loopback-aware** — `agents/net_guard.py` and `agents/egress_audit.py` treat `127.0.0.1`/`169.254.169.254` as attack targets; on a real cloud host the metadata endpoint (`169.254.169.254`) becomes a **live** SSRF risk, so this guard actually gets *more* important in the cloud, not less.

---

## Ranked migration checklist

### MUST change — load-bearing, system won't run without it
| Item | Effort |
|------|--------|
| **1. Swap `claude -p` CLI → Anthropic API** (`brain._find_claude_cli`/`planner._cli`). Add a paid `ANTHROPIC_API_KEY`. This is the cost-model change. | **L** |
| **2. Re-author the ~9 launchd plists as systemd timers/cron** (morning, server keep-alive, secondbrain 10-min, watchdog, replywatch, openpulse, escalator, retro, autocommit). | **M** |
| **3. Template the absolute paths** in plists + `autocommit.sh:5`/`janitor.sh:5`/`watchdog.sh:5,45` + `video/gen_vo.py:11` — generate from `$SB_ROOT` at install. | **M** |
| **4. Provide/relocate the `~/Claude/` sibling repos** (business-library, playwright-project, gmail, elementor-recoder, EXECUTION-PACK, WARM-HITLIST.csv) or introduce a `SB_SIBLINGS` env and guard-and-degrade when absent. | **M** |
| **5. Replace launchd server supervision** with `systemd Restart=always`; rewrite `watchdog.sh`'s `launchctl kickstart` self-heal. | **M** |
| **6. Remove/guard `caffeinate`** (`morning.sh:34`) — errors on Linux. | **S** |

### EASY wins — mechanical, mostly "constant → env var"
| Item | Effort |
|------|--------|
| Make the server base URL configurable (`SB_BASE`/env) instead of `http://127.0.0.1:8765` in `interview_war_room.py`, `day_plan.py`, `morning_chain.py`, `chaos_drill.py`, `commander.py`, `server.py:593`. | **S** |
| Move secrets fully to `.env`/secret-manager; drop the Keychain path (`secrets_to_keychain.sh`) on non-Mac. | **S** |
| Reconfigure the tunnel (Tailscale → Cloudflare is already supported at `server.py:2391,2418`) and fix `link_monitor.py:36`'s Tailscale-binary check. | **S-M** |
| Keep the SSRF guard (`net_guard.py`) — it matters *more* in cloud (live `169.254.169.254`). No change needed, just don't drop it. | **S** |

### STAYS Mac-only forever — don't try to lift these
| Item | Why |
|------|-----|
| **Chrome/Playwright job-apply + LinkedIn operators** | His logged-in browser session; site auth/anti-bot; CLAUDE.md forbids automating logins. |
| **Siri/Reminders capture** (`osascript`) | Reminders is Apple-only. |
| **Apple Health / Watch / iPhone Shortcuts** ingest + one-tap buttons | Data + triggers originate on his devices. |
| **Live call-coach** (BlackHole + whisper + `say`) | Desktop, mic, headphones — a physical-presence feature. |
| **`/usr/bin/say` server TTS** | Mac voices; swap for a TTS service only if you want cloud voice at all. |

---

## Full vs Hybrid — the honest verdict

**Don't do a full migration.** Two independent reasons:

1. **Value is device-bound.** The operators (job-apply, LinkedIn), the capture channels (Reminders, Health, Shortcuts), and the coach are a large share of *why the system is useful*, and **none of them can run in a cloud VM** — they need his logins, his phone, his mic. A cloud copy would be a brain with its hands and ears cut off.
2. **It trades away the "$0 LLM" pillar** for API bills (~low-single-digit $/day now, but scaling with every agent you add) and buys… availability the Mac already provides well enough via launchd `KeepAlive` + watchdog + the 10-min self-heal.

**Do this instead — "cloud surface + Mac brain":**
- **Brain stays on the Mac.** Keep `claude -p` (free Max plan), keep all operators/capture/audio, keep launchd. This is where the value and the zero-cost live.
- **If you want off-Mac reach,** put only a **thin, read-mostly surface** in the cloud: a mirror of the dashboard/`/api/state`/`/api/export` (`server.py:1281+`), fed by the Mac pushing snapshots out. Writes/sends/operators still execute on the Mac behind his click (which matches the existing "every send stays behind his click" rule).
- **The one genuinely portable upgrade worth doing regardless of cloud:** parameterize the absolute paths and the server base URL (EASY-wins table). That makes the system survive a **new Mac** or a **rename of the home dir** painlessly — the realistic "move" that will actually happen — without touching the LLM tie or the operators at all.

**Effort to reach "runs on a second Mac cleanly": S-M** (mostly the path templating + sibling-repo placement). **Effort to reach "runs headless on a Linux VPS with equivalent function": not achievable** — the operators/capture/audio don't exist there; you'd get a degraded, metered shell. **Effort for the hybrid surface: M**, and it's the only version that keeps both the value and the economics.
