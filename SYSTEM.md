# Second Brain — Operator's Guide

Your always-on command center. FastAPI server (localhost:8765, kept alive by launchd), a
single-page dashboard, and a fleet of scheduled agents. Everything runs on the Max-plan Claude
CLI (no API dollars). This doc is the map.

---

## The four loops

1. **Money loop** — warm-call cockpit (58 booked calls, dial-ready) → reply-watch catches inbound
   GHL/Gmail replies → you approve a drafted response → booked call → GHL pipeline. The DBR draft
   feeds the 423 repliers into the top.
2. **Job loop** — source (hiring.cafe + jobicy/remotive/remoteok) → fit-ranked, deduped, fresh-only
   queue → chain applies in parallel (isolated browsers, Sonnet) → Gmail detects confirmations/
   rejections/interviews → interview-prep pack auto-builds → you interview.
3. **Networking loop** — source LinkedIn targets → on-voice drafts (tone-screened) → you approve →
   run through your real Chrome (human-paced, capped).
4. **Content loop** — daily generation (Sonnet, self-scored) → auto-approve at score bar or you
   review → publish via GoHighLevel.

Everything outward (sends, publishes, campaign activation) is **gated behind your click**.

---

## The dashboard (localhost:8765)

**Header pills** (click any):
- **⚡ needs you** — the one queue of everything waiting on you, deep-links to each item.
- **💰 pipeline** — real GHL open pipeline $ + warm-call burndown. Opens the warm cockpit.
- **⚙ usage** — today's Claude token burn (the allowance is the scarce fuel). Opens a breakdown.
- **🛂 visa** — Schengen 90/180 counter.

**Left-edge drawers**: 💰 Warm Calls · 💬 Replies · Today's Moves · ✍ Content · 🤝 Network · 💼 Jobs.

**Command bar** (⌘K) — talk to it in plain English. It knows real state ("how many jobs queued",
"what happened today"). Voice: 🎤 push-to-talk in, 🔊 replies in your cloned ElevenLabs voice.

---

## Scheduled agents (launchd)

| Agent | When | Does |
|---|---|---|
| `brain-server` | always | the FastAPI server + apply chain |
| `morning.sh` | 6:30 daily | captures, triage, organize, content, **jobs source**, **job-reply detect**, **interview prep**, brief, dashboard, **janitor** |
| `secondbrain` (run.sh) | every 10 min | pulls phone captures / reminders |
| `replywatch` | every 30 min | scans GHL for inbound replies, drafts responses, pushes |
| `watchdog.sh` | every 5 min | restarts a dead server; pushes on brief-error / stale-morning / low-disk |
| `autocommit.sh` | hourly | `git commit` snapshot (rollback safety) |
| `retro.py` | Sundays 9am | weekly retro + one proposed config change |

Manage: `launchctl list | grep jarvis`. Reload one: `launchctl kickstart -k gui/$(id -u)/<label>`.

---

## Key API endpoints

- `GET /api/state` `/api/needs` `/api/health` `/api/usage` `/api/money` — dashboard state
- Jobs: `/api/jobs`, `/api/jobs/{id}/applied|skipped` (operator callbacks, token-guarded),
  `/api/launch/job_apply` (starts the chain), `/api/jobs/stop`
- Warm: `/api/warm`, `/api/warm/{id}/dispo`
- Replies: `/api/replies`, `/api/replies/{id}/approve|skip` (approve **sends** via GHL)
- Retro: `/api/retro`, `/api/retro/apply` (whitelisted config keys only)
- Prep: `/api/prep` · TTS: `/api/tts?text=`

All `/api/*` require the `X-Brain-Token` header (or `?t=`), served injected into the page. Server
binds `127.0.0.1` only — use Tailscale for phone access.

---

## Config (`store/config.json`) — highlights

- `models` — per-feature routing: public writing (content/networking/replies) → Sonnet, internal
  (interpret/plan/tone/brief) → Haiku.
- Jobs: `job_min_yearly`, `job_scan_target`, `job_daily_apply_cap`, `job_apply_batch`,
  `job_apply_concurrency`, `job_apply_model`, `job_auto`, `job_blacklist` (retro-managed).
- `network` caps, `auto_approve_min`, `ntfy_topic`, `push_full`, `elevenlabs_voice_id`.
- Cold: `cold_daily_enroll` (0 = drip off), `cold_domains` (preflight checks these),
  `cold_workflow_live` (override when GHL reports a blank status after you publish).
- Hands-off: `public_base_url` (your Tailscale URL — turns on one-tap Approve/Skip
  buttons in pushes), `job_morning_chain` (0 = off; >0 runs one apply chain each
  morning). Dashboard: ⌘K opens the command palette; phone gets a bottom icon nav and
  installs as an app (Share → Add to Home Screen over Tailscale).
- Secrets (`OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, `BRAIN_TOKEN`) live in `.env` (chmod 600, gitignored).

---

## Day-to-day

- **Mornings**: glance at the phone push (brief + "N need you"). Dial a few warm calls from bed.
- **Anytime**: open the ⚡ queue, clear it in ~10 min (approve replies, finish CAPTCHA jobs, review posts).
- **Jobs**: hit ▶ Apply to run a chain; interviews build their own prep packs.
- **Weekly**: the Sunday retro proposes one tuning change; approve or skip it in the ⚡ queue.

## Cold outreach (🧊 COLD drawer)

The wl-webdev agency list, end to end. All machinery on, nothing sends by itself:

- **Enrich**: `agency-enrichment/hooks_cli.py` writes one verified "Saw X's work on ..."
  hook per agency into `out/wl-hooks.csv` — Haiku via `claude -p`, $0, resumable.
- **Stage** (morning, auto): `agents/cold_import.py` sets Greeting / Personalization /
  Breakup Detail on the existing GHL contacts. Never dupes; never touches DND, unsub,
  client, or booked contacts.
- **Drip** (morning, gated): `agents/cold_feeder.py` tags `cold_daily_enroll` staged
  contacts per day into the workflow. Refuses unless the deliverability preflight is
  green AND the workflow is published AND the knob is > 0 (it ships at 0 = off).
- **Preflight**: `agents/cold_preflight.py` digs SPF/DKIM/DMARC + checks the GHL
  from-address. The drawer shows the light; red = nothing sends.
- **To go live**: publish the draft `[2026-07] Cold Agencies - WL Sites (email only)`
  (build it with `gohighlevel-cli/build-wl-cold.sh`), then set `cold_daily_enroll` (30
  is sane). Replies land in 💬 REPLIES like everything else. Copy lives in
  `business-library/campaigns/wl-cold-email-7.md`.

## Capture + iPhone / Apple Watch / Health (recipes)

- **Mac, from anywhere**: `capture/quick-add.sh "the thought"` — or add the `capture/`
  folder as a Raycast Script Commands directory and give "Brain Capture" a hotkey.
- **iPhone + Watch capture** (Shortcuts app, works on the Watch and the Action button):
  new shortcut "Brain Capture" = *Dictate text* → *Get Contents of URL*:
  `https://<your-tailscale-host>:8765/api/todo`, Method POST, Header
  `X-Brain-Token: <token from .env>`, Request Body JSON `{"text": Dictated Text}`.
  Say "Hey Siri, Brain Capture" from the wrist; it lands in the inbox.
- **Apple Health -> wellness chip**: Shortcuts *Automation*, daily ~9:00: *Find Health
  Samples* (sleep analysis last night; steps yesterday) → same POST to
  `/api/wellness` with body `{"sleep_h": X, "steps": Y}`. The trend bar shows
  😴/🚶 when data is fresh (<40h).
- **Watch notifications**: the ntfy iPhone app mirrors every push to the Watch
  automatically — approve/skip buttons included once `public_base_url` is set.

## Your manual setup (once)

1. Subscribe the ntfy iPhone app to your topic (in `config.json` `ntfy_topic`) — turns on every push.
2. `sudo pmset repeat wakeorpoweron MTWRFSU 06:25:00` + re-enable the macOS firewall.
3. Install Tailscale → `tailscale serve 8765` for phone access.
4. Optional: a private GitHub remote for off-machine backup (git is local-only now).

## Troubleshooting

- **Something silently off?** `curl -s localhost:8765/api/health` (the watchdog polls this).
- **Bad edit broke it?** `git log`, `git revert` / `git reset` (hourly autosnapshots exist).
- **Logs/stores** self-compact nightly via the janitor; nothing grows unbounded.

## Iron Man extras (v3 buildout)

- **Recall**: ⌘K → "Recall" searches every brief, prep pack, insight, objection, and
  feed line the brain ever wrote (local FTS index, `/api/recall?q=`).
- **Futures**: ⌘K → "Set a future" — "when <name> replies, remind me to <x>". The
  10-min poller fires it into the inbox the moment their reply lands.
- **Proposals**: ⌘K → "Draft a proposal" → client + scope → one-page markdown from
  your real offer terms, saved to store/proposals/, print-to-PDF from the sheet.
- **Voice replies from the Watch (#13)**: pushes already carry Send/Skip buttons; to
  reply by voice, run the "Brain Capture" Shortcut from the wrist — dictate and it
  lands in the inbox for the machine to draft against.
- **iOS widget (#94)**: Shortcuts app → new Shortcut → "Get Contents of URL"
  `/api/money` (token header) → "Show Result"; add it as a Home-Screen Shortcut
  widget for a one-tap pipeline number.
- **NFC desk tag (#95)**: any NFC sticker → Shortcuts Automation → "When tag is
  tapped" → Open URL `https://<tailscale>/?t=<token>` — tap your desk, the bridge
  opens (add `&listen=1` and it opens LISTENING).
- **Local Whisper (#11)**: `bash tools/install_whisper.sh` once (~5 min, 150MB) —
  `/api/stt` activates automatically; private offline voice input.
- **Summon (#17)**: `capture/summon.sh` in Raycast — global hotkey opens the bridge
  with the mic hot.
- **Rehearse mode (#44)**: `cold_feeder.py --rehearse` / `morning_chain.py --rehearse`
  print exactly what they WOULD do, touching nothing.
- **Token rotation (#99)**: `tools/rotate_token.sh` · **Black box (#106)**:
  `tools/blackbox.sh` · **Replay (#48)**: `tools/replay.sh <agent>`.
