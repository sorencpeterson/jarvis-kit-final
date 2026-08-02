# second-brain in a container — portability insurance

This directory + the three files at the repo root (`Dockerfile`, `docker-compose.yml`,
`.dockerignore`) let you run the **server** part of the second brain off [OWNER]'s
specific Mac: another Mac, a Linux VPS, a container host. It exists so a dead Mac
doesn't take the always-on public surface (proposals, agreements, the webhook)
down with it.

**Read the verdict at the bottom before you get excited.** This is portability
*insurance*, not a migration. The brain — the LLM agents and the browser/audio
operators — stays on the Mac.

---

## What runs in the container

`app/server.py` (FastAPI on uvicorn). Concretely, the parts that work headless:

- the **read API** (`/api/state`, dashboards, the token-gated surface),
- the **signed capability links** a prospect actually clicks: `/prop/{id}`,
  `/mock/{id}`, `/agree/{id}`, `/case/...`,
- the **GHL webhook receiver** (`/api/ghl/webhook`),
- `/pub/health` — the tunnel liveness probe.

Plus the handful of **cloud-safe agents** (pure math over `store/`) that
`deploy/cloud/agent-runner.sh` can run on an interval. Everything else is Mac-only
(next section).

## What does NOT run in the container — the free-LLM gotcha, stated plainly

> **The daily LLM runs via `claude -p` — the Claude Code CLI on [OWNER]'s Max plan,
> which is Mac-login-bound. It is not in the image and will not work headless in
> the cloud without his Claude auth.**

Every "smart" thing (the chief-of-staff planner, JARVIS chat, proposal/email copy,
mail triage, all the daily briefs) calls `planner._cli` / `brain.cli_mode`, which
shells out to the `claude` binary. There is no `claude` binary in this image, so
`_find_claude_cli()` returns `None` and those code paths **no-op gracefully** — the
server still boots and serves, it just won't generate anything new. Verified: the
server module imports and answers `/pub/health` with no CLI present.

Also Mac-local and deliberately excluded: the **browser operators** (Playwright /
Claude-in-Chrome, under [OWNER]'s logged-in profile), **Call Coach** (BlackHole audio
+ whisper on the M-series GPU), **Reminders/HealthKit** capture, and anything
needing his **GHL or Gmail credentials**. See the classification block at the top of
`agent-runner.sh` for the per-agent reasoning.

---

## Build & run locally

From the **`second-brain/` repo root** (where `requirements.txt` lives):

```bash
# Build + start the server with the store/ volume and .env mounted.
docker compose up --build

# Server is now on the HOST at 127.0.0.1:8765 (loopback only — see security note).
curl -fsS http://127.0.0.1:8765/pub/health
# -> {"ok":true,"service":"proposals"}
```

Just the image, no compose:

```bash
docker build -t second-brain:latest .
docker run --rm -p 127.0.0.1:8765:8765 \
  --env-file .env \
  -v brain-store:/app/store \
  second-brain:latest
```

Stop / clean up:

```bash
docker compose down          # keeps the store/ volume (state + tokens preserved)
docker compose down -v       # DELETES the volume — wipes state + minted token. Don't.
```

### Running the cloud-safe agents (optional)

The image CMD is uvicorn; the agent chain is **not** started by default. To run the
few portable agents on a VPS, run the script as a sidecar or from cron:

```bash
# one-shot (put this line in crontab)
docker compose exec brain-server deploy/cloud/agent-runner.sh --once

# or loop it as its own long-running process
docker compose exec brain-server env INTERVAL=1800 deploy/cloud/agent-runner.sh
```

They talk only to `store/` and the local server, so they're safe in-cluster.

---

## What mounts where

| Host / volume            | Container path      | Why                                                                 |
|--------------------------|---------------------|---------------------------------------------------------------------|
| named volume `brain-store` | `/app/store`      | **All runtime state** (jsonl/json) AND secrets in `store/config.json`. Must persist across rebuilds; never baked into the image. |
| `.env` (via `env_file`)  | process env         | Runtime secrets/config (`BRAIN_TOKEN`, ntfy topic, etc.). Mounted at start, **never** in an image layer. |
| *(image, read-only)*     | `/app/app`, `/app/agents`, `/app/dashboard`, `/app/tools`, `/app/store_lib.py` | The code the server imports. |

Secret resolution order (from `store_lib.secret()`): **process env → `.env` →
`store/config.json`**. The container feeds the first two from `env_file`; the third
rides in on the `store/` volume. **No secret is ever copied into the image** — the
`.dockerignore` blocks `.env`, `store/`, `*.key`, `*.pem`, `*token.json`, and
`store/.sign_secret` as a backstop even if the copy set changes.

> If `.env` is absent the server still boots: it mints a fresh `BRAIN_TOKEN` and
> writes it into the mounted store, and `sign_secret()` persists a random per-install
> key to `store/.sign_secret`. A fresh clone works, just with new unguessable secrets.

---

## Security: never expose the raw API

The container listens on `0.0.0.0:8765` **inside** the container (the cloud can't
reach a container's loopback otherwise), but compose publishes it to the **host's
`127.0.0.1` only**. The token-gated `/api` surface must never face the internet
directly. To go public:

1. Put a reverse proxy / tunnel in front — **Caddy**, **nginx**, or a
   **Cloudflare Tunnel** — terminating TLS.
2. Forward only what you mean to. The app's own Host guard already restricts a
   *public* host to `/prop /mock /agree /case /og /pub`; the proxy is the belt to
   that suspenders.
3. Do **not** change the compose `ports` left side to `0.0.0.0` without that proxy.

This is the same posture as the Mac today: the live server binds `127.0.0.1` and is
reached over Tailscale/Cloudflare, not by opening a port to the world.

---

## Honest verdict — what containerizing actually buys [OWNER]

This is **portability insurance, not a migration.** What you get: if the Mac dies,
gets stolen, or is traveling with the lid shut, you can `docker compose up` on any
Linux box or spare Mac and the **public-facing surface stays alive** — prospects can
still open a proposal, sign an agreement, and hit the webhook, and the read API +
the pure-math rollups keep working against the mounted `store/`. That's the whole
win, and it's a real one for an always-on business surface. What you do **not** get:
the brain. The daily LLM planner, JARVIS, all copy generation, mail triage, the
browser operators, and Call Coach depend on the free Max-plan `claude` CLI and the
real logged-in Chrome/audio — every one of them is Mac-login-bound and stays where
that lives. So think of the container as a **hot standby for the storefront**, not a
clone of the operator. The storefront can run anywhere; the operator runs on [OWNER]'s
Mac.
