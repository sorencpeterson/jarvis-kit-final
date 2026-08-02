#!/bin/bash
# Hourly snapshot so any agent edit or bad hot-edit is rollback-able (git reflog / reset).
# Commits code + versioned jsonl queues; .env and logs are gitignored.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
cd [APP_ROOT] || exit 0
git add -A 2>/dev/null
# never immortalize a pasted secret. Scan the WHOLE staged diff (broadened 2026-07-07 audit
# S2 from the old config.json-only, lowercase-field-name-only guard) for VALUE-shaped
# secrets by distinctive PROVIDER PREFIX (sk-/sk-ant-/xi-/AIza/ghp_/xoxb-...) or a NAMED
# credential assignment (OPENAI/ELEVENLABS/BRAIN/GUEST/GHL...KEY|TOKEN|SECRET = "long").
# Prefix/name-anchored on purpose: a bare hex blob rule would false-positive on the sha256
# content-hashes that legitimately live in agreements/proposals stores and freeze autosave.
# On a hit: abort the commit, leave everything staged for a human. A missed autosave is
# cheap; a secret in git history is forever.
# two greps: (1) provider prefixes, case-SENSITIVE (sk-/xi-/AIza are literally cased);
# (2) a named-credential assignment, case-INSENSITIVE so it catches this codebase's OWN
# lowercase config.json convention (openai_api_key/brain_token/...), which the first
# uppercase-only version silently missed (red-team #6c regression).
if git diff --cached -U0 2>/dev/null \
     | grep -qE '^\+.*(sk-(ant-)?[A-Za-z0-9_-]{20,}|xi-[A-Za-z0-9]{24,}|AIza[A-Za-z0-9_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|xox[bap]-[A-Za-z0-9-]{20,})' \
   || git diff --cached -U0 2>/dev/null \
     | grep -qiE '^\+.*(openai|elevenlabs|eleven|anthropic|brain|guest|ghl)[a-z_]*(api[_-]?key|key|token|secret)["'"'"' ]*[:=]["'"'"' ]*[A-Za-z0-9/+_-]{16,}'; then
  echo "$(date '+%Y-%m-%d %H:%M') possible secret in staged diff — commit skipped, left staged for review" >> agents/autocommit.log
  exit 0
fi
git commit -qm "autosave $(date '+%Y-%m-%d %H:%M')" 2>/dev/null || true
# Off-machine backup (2026-07-11, [OWNER] approved the private remote): after each
# hourly autosave, push to origin through backup_push.sh (re-runs the secret guard,
# never force-pushes, no-op when nothing new). See deploy/BACKUP-AND-CANARY.md.
bash "$(dirname "$0")/../tools/backup_push.sh" >> agents/autocommit.log 2>&1 || true
