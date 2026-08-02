#!/usr/bin/env bash
# =============================================================================
# agent-runner.sh — portable chain runner for the CLOUD-SAFE agents.
#
# Replaces launchd (Mac-only) + morning.sh's macOS-isms (caffeinate, `caffeinate`
# has no Linux analog and none is needed on an always-on box). Pure bash, Linux-
# compatible. Runs a short list of agents that do LOCAL FILE COMPUTE ONLY, on a
# fixed interval, each wrapped in a hard timeout.
#
# This is the container/VPS counterpart to morning.sh — but DELIBERATELY TINY.
# morning.sh runs ~120 agents; almost all of them need the `claude` CLI (LLM,
# Max-plan, Mac-login-bound), [OWNER]'s GHL/Gmail creds, or a real browser. NONE of
# those work headless in the cloud. So this runner includes ONLY the handful that
# are pure math over store/*.jsonl. See the classification block below.
#
# Usage:
#   ./agent-runner.sh                 # loop forever, default 1h interval
#   INTERVAL=1800 ./agent-runner.sh   # loop every 30 min
#   ./agent-runner.sh --once          # run the chain once and exit (cron-friendly)
#
# In the container this is NOT started by default (the image CMD is uvicorn). Run
# it as a second service / sidecar / cron only if you want cloud-side rollups. See
# deploy/cloud/README.md.
# =============================================================================
set -uo pipefail

# --- Locate the repo root (this script lives at deploy/cloud/, so ../../ = root) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT"

# --- Pick a Python: the container venv is on PATH; fall back to a local .venv or python3 ---
if command -v python >/dev/null 2>&1; then
  PY="python"
elif [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi

INTERVAL="${INTERVAL:-3600}"        # seconds between chain runs (default 1h)
PER_AGENT_TIMEOUT="${PER_AGENT_TIMEOUT:-300}"   # hard cap per agent (seconds)
LOG="${LOG:-deploy/cloud/agent-runner.log}"

# =============================================================================
# CLOUD-SAFE CLASSIFICATION  (reasoning — when unsure, EXCLUDED)
# -----------------------------------------------------------------------------
# Method: read agents/morning.sh, then for each candidate grep for ._cli( /
# _cli_json / _find_claude_cli (LLM), ghl|gohighlevel|api.sh (GHL creds),
# gmail|imaplib|smtplib|google (mail creds), playwright|chrome (browser).
#
# INCLUDED — verified LLM:0, GHL:0, GMAIL:0, BROWSER:0, pure store/*.json(l) math:
#   attention.py            NEXT-strip ranker; imports planner only for notify(). No _cli.
#   dropoff_audit.py        booked-call -> proposal leak list (csv/re over stores).
#   tier_winrate.py         acceptance rate per price point (arithmetic over ledger).
#   reactivation_triage.py  replier-lane bucketing for the 423 drip (csv over stores).
#   salary_ladder.py        comp-band conversion table (imports jobs for constants; no net).
#   job_pipeline_quality.py job funnel analytics. Its only network call is the LOCAL
#                           server API (127.0.0.1:8765) with the brain token — in-cluster,
#                           no external creds. Safe when the server service is up.
#   job_efficiency.py       apply-velocity governance (local file compute).
#   metrics_rollup.py       folds endpoint reads into store/metrics.json. Talks ONLY to
#                           the local server (127.0.0.1:8765 + token). No external service.
#
#   NOTE on planner import: several of the above `import planner` — but ONLY for
#   planner.notify()/feed_add() (ntfy.sh push + local feed append). They never call
#   planner._cli(), so importing planner does NOT invoke the LLM. Verified: with no
#   `claude` binary present, _find_claude_cli() returns None and nothing breaks; the
#   notify() call just POSTs to ntfy.sh (harmless, degrades to False offline).
#
# EXCLUDED — and why (this is the important half; do not "just add one"):
#   * Everything that writes copy or plans:  content_gen, daily_brief, daily_insight,
#     day_plan, organize, standup, one_thing, owner_report, promises, morning_chain,
#     postmortem, correlate, voice_drift, defib, thankyou, repurpose, meeting_prep,
#     client_pack, template_learn, answer_bank ... -> ALL call planner._cli / brain
#     (the `claude` CLI). No CLI in the cloud => they either no-op or produce nothing.
#     Keep them on the Mac where the free Max plan lives.
#   * GHL-touching:  cold_import, cold_feeder, cold_preflight, campaign_guard,
#     warm_block, warm_followup, warm_refresh, webfix_refresh, ghl_social,
#     proposal_factory, reply_watch, expand_pipeline ... -> need [OWNER]'s GHL creds /
#     the gohighlevel-cli (~/Claude/playwright-project/...), which isn't in the image.
#   * Gmail-touching:  the whole mail_* chain (mail_brain, mail_sync, mail_threads,
#     mail_signals, mail_drafts, mail_hygiene, mail_digest, mail_sender_scores) +
#     job_replies, job_mail_patterns -> need his Google OAuth token. Mac-only.
#   * Browser / ATS scrape:  job_ats_watch, portfolio_teardown, jobs (apply flow),
#     networking / li_* -> drive Playwright/Chrome or hit rate-limited external
#     endpoints under his identity. Mac-only.
#   * Reminders / HealthKit / audio:  pull_reminders, sleep_aware, energy_blocks,
#     travel_mode, the coach chain -> macOS subsystems. Not portable at all.
#   * Weekly deep checks (chaos_drill, egress_audit, backup_verify, bakeoff, ...)
#     -> either LLM-backed or assume the Mac filesystem/launchd. EXCLUDED.
#
# RULE FOR FUTURE EDITS: before adding a name here, grep it —
#   grep -E '\._cli\(|_cli_json|_find_claude_cli|ghl|gohighlevel|api\.sh|gmail|imaplib|smtplib|playwright|chrome' agents/<name>.py
# If ANY line matches, it is NOT cloud-safe. When unsure, leave it out.
# =============================================================================
CLOUD_SAFE_AGENTS=(
  "agents/attention.py"
  "agents/dropoff_audit.py"
  "agents/tier_winrate.py"
  "agents/reactivation_triage.py"
  "agents/salary_ladder.py"
  "agents/job_pipeline_quality.py"
  "agents/job_efficiency.py"
  "agents/metrics_rollup.py"
)

# --- Portable per-agent timeout --------------------------------------------------
# The Mac has no `timeout` (morning.sh uses a perl-alarm wrapper for exactly this).
# The Linux target usually HAS GNU coreutils `timeout`. Prefer it; fall back to the
# perl-alarm trick if it's missing, and to a bare run if neither exists.
if command -v timeout >/dev/null 2>&1; then
  run_bounded() { timeout "${PER_AGENT_TIMEOUT}s" "$@"; }
elif command -v perl >/dev/null 2>&1; then
  # perl alarm sends SIGALRM after N seconds; exec replaces perl with the command.
  run_bounded() { perl -e 'alarm shift; exec @ARGV' "$PER_AGENT_TIMEOUT" "$@"; }
else
  run_bounded() { "$@"; }   # last resort: no timeout available
fi

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }

run_chain() {
  mkdir -p "$(dirname "$LOG")"
  log "=== cloud agent chain start (${#CLOUD_SAFE_AGENTS[@]} agents, ${PER_AGENT_TIMEOUT}s cap each) ==="
  local rc
  for agent in "${CLOUD_SAFE_AGENTS[@]}"; do
    if [ ! -f "$agent" ]; then
      log "SKIP $agent (not found in this image)"
      continue
    fi
    log "RUN  $agent"
    # Never let one agent's non-zero exit kill the loop (set +e semantics via ||).
    run_bounded "$PY" "$agent" >>"$LOG" 2>&1
    rc=$?
    if [ "$rc" -eq 124 ]; then
      log "TIMEOUT $agent (killed after ${PER_AGENT_TIMEOUT}s)"
    elif [ "$rc" -ne 0 ]; then
      log "WARN $agent exited $rc (continuing)"
    fi
  done
  log "=== cloud agent chain done ==="
}

# --- One-shot (cron) vs loop (long-running sidecar) ------------------------------
if [ "${1:-}" = "--once" ]; then
  run_chain
  exit 0
fi

log "agent-runner starting: interval=${INTERVAL}s, python=${PY}, root=${ROOT}"
while true; do
  run_chain
  sleep "$INTERVAL"
done
