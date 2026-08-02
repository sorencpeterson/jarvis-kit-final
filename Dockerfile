# syntax=docker/dockerfile:1
# =============================================================================
# second-brain — SERVER image (portability insurance, NOT a full migration)
# =============================================================================
# What this image runs: app/server.py (FastAPI/uvicorn) — the always-on public
# surface: the read API, the signed capability links (/prop /mock /agree /case),
# the /pub/health tunnel probe, and the GHL webhook receiver.
#
# What this image does NOT run, and CANNOT:
#   * the daily LLM brain. Every planner._cli / brain.cli_mode call shells out to
#     the `claude` CLI (Max plan, Mac-login-bound). There is no `claude` binary in
#     this image, so _find_claude_cli() returns None and those features no-op
#     gracefully (verified: the server imports and serves fine with no CLI).
#   * the browser operators, Call Coach audio, Health/HealthKit — all Mac-local.
# See deploy/cloud/README.md for the honest verdict and the free-LLM gotcha.
#
# Build (from the second-brain/ repo root, where requirements.txt lives):
#   docker build -t second-brain:latest .
# Run: prefer `docker compose up` so the store/ volume + .env are mounted right.
# =============================================================================

# ---- Stage 1: builder — compile wheels into an isolated venv --------------------
# Multi-stage so the compilers + build caches (cffi/cryptography need a C toolchain)
# never ship in the final image. Only the finished /opt/venv is copied forward.
FROM python:3.12-slim AS builder

# Pin the interpreter to the version the .venv was frozen against (3.12.13 locally;
# python:3.12-slim tracks the 3.12 series). requirements.txt is fully pinned.
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build deps for the wheels that have C extensions (cryptography, cffi, uvloop,
# httptools). Kept in the builder stage ONLY. build-essential + libffi for cffi,
# cargo is pulled by cryptography's pyo3 build on some arches.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        cargo \
    && rm -rf /var/lib/apt/lists/*

# Isolated venv so the copy-forward to the runtime stage is a single self-contained dir.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install deps first (own layer) so app-code edits don't bust the wheel cache.
# pywhispercpp is optional (lazy import in coach/coach.py, which we don't ship);
# if its wheel ever fails to build on a given arch, drop that one line from a
# fork of requirements.txt — the SERVER never imports it.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /tmp/requirements.txt

# ---- Stage 2: runtime — slim, non-root, code only -------------------------------
FROM python:3.12-slim AS runtime

# Faster, quieter Python in a container; unbuffered so logs stream to `docker logs`.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# curl is only here so the compose HEALTHCHECK can hit /pub/health from inside the
# container. Nothing else in the runtime needs a package. tini reaps the perl-alarm
# / subprocess children the agents spawn, so nothing turns into a zombie under PID 1.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Bring the finished venv over from the builder (no compilers in this stage).
COPY --from=builder /opt/venv /opt/venv

# Non-root: create an unprivileged user and give it ONLY the app dir.
# The store/ volume is chowned to this uid in docker-compose (or `chown -R 10001`
# on the host bind mount) so file-based writes succeed without running as root.
RUN groupadd --gid 10001 brain \
    && useradd --uid 10001 --gid brain --create-home --home-dir /home/brain brain

WORKDIR /app

# ---- Copy ONLY the code the server + cloud-safe agents import -------------------
# Deliberately NOT copied (see .dockerignore for the full exclude list):
#   store/   -> runtime VOLUME (holds jsonl/json state AND secrets in config.json)
#   .env     -> mounted at runtime via env_file (NEVER baked into an image layer)
#   .venv/ .git/ *.log media -> not needed / would bloat or leak
#
# store_lib.py is the shared helper the whole tree imports. app/ has the server +
# planner + brain. agents/ + dashboard/ are imported by the server at boot
# (server.py: `from collect import ...`, `from brain import respond`, `import planner`,
# and sys.path inserts for app/ + agents/). tools/ holds the check scripts the
# runner and doctor call. requirements.txt is copied for reference/rebuilds inside.
COPY --chown=brain:brain store_lib.py requirements.txt ./
COPY --chown=brain:brain app/ ./app/
COPY --chown=brain:brain agents/ ./agents/
COPY --chown=brain:brain dashboard/ ./dashboard/
COPY --chown=brain:brain tools/ ./tools/
# The cloud chain runner (self-contained bash; see deploy/cloud/README.md).
COPY --chown=brain:brain deploy/ ./deploy/

# store/ is a mount point. Create it owned by the app user so a fresh `docker run`
# with an empty named volume is writable even before anything is seeded into it.
RUN mkdir -p /app/store && chown -R brain:brain /app/store

USER brain

# The public surface. 8765 matches the app's own default and every doc/tool.
EXPOSE 8765

# Cheap in-image liveness (compose overrides with its own healthcheck too).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8765/pub/health || exit 1

# tini as PID 1 -> clean signal handling + child reaping for the subprocesses agents spawn.
ENTRYPOINT ["/usr/bin/tini", "--"]

# 0.0.0.0 because a container's loopback is not reachable from outside it — the cloud
# needs to accept the connection forwarded in by the runtime/reverse proxy.
# SECURITY: binding 0.0.0.0 exposes the RAW api to whatever can reach the container.
# The app's own Host-guard (_PUBLIC_PREFIXES) only lets /prop /mock /agree /case /og
# /pub through on a public host, but the token-gated /api surface must still sit
# behind a reverse proxy (Caddy/nginx/Cloudflare Tunnel) that terminates TLS and
# forwards only what you intend. NEVER publish this port straight to the internet.
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8765", "--no-access-log"]
