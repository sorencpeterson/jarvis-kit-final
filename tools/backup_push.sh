#!/bin/bash
# backup_push — the ONLY thing that gets this repo off the Mac.
#
# autocommit.sh (agents/autocommit.sh) commits hourly but NEVER pushes, so today
# every commit lives only on the Mac's disk — backup_verify.py correctly reports
# "no_remote" (there is no off-machine copy). This script does the push, safely:
#   1. runs the SAME secret-guard autocommit.sh uses, so a push can't ship a
#      secret that somehow slipped into a commit;
#   2. refuses to run if there's no `origin` remote yet (tells you the exact
#      command to add one);
#   3. pushes the CURRENT branch to origin, setting upstream on first push;
#   4. NEVER force-pushes — a backup that can rewrite history isn't a backup.
#
# Meant to be called at the TAIL of agents/autocommit.sh (one added line — see
# deploy/BACKUP-AND-CANARY.md). Safe to run by hand any time. Idempotent: if
# there's nothing new, `git push` is a no-op.
#
# Exit codes: 0 = pushed (or nothing to push) | 1 = no remote configured
#             2 = secret guard tripped (push aborted) | 3 = push failed
set -uo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
REPO="[APP_ROOT]"
cd "$REPO" || { echo "backup_push: cannot cd to $REPO" >&2; exit 1; }

# ── 1. secret guard ──────────────────────────────────────────────────────────
# Scan the diff about to leave the machine for VALUE-shaped secrets. These two
# grep patterns are copied VERBATIM from agents/autocommit.sh (lines 19-22 as of
# 2026-07-07): (a) case-SENSITIVE provider prefixes (sk-/sk-ant-/xi-/AIza/ghp_/
# xoxb-...), (b) case-INSENSITIVE named-credential assignment (openai_api_key,
# brain_token, ...). If you change them THERE, change them HERE, same commit.
# We scan the range origin/<branch>..HEAD when a remote exists (exactly what's
# about to be pushed); on first push (no upstream yet) we scan the whole HEAD
# tree diff so nothing already committed leaks on the initial upload.
BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
if [ -z "${BRANCH}" ] || [ "${BRANCH}" = "HEAD" ]; then
  echo "backup_push: detached HEAD or no branch — refusing to push." >&2
  exit 1
fi

# Pick the diff that represents "what this push would add".
if git rev-parse --verify --quiet "origin/${BRANCH}" >/dev/null 2>&1; then
  DIFF_RANGE="origin/${BRANCH}..HEAD"
else
  # nothing on the remote yet: treat the entire current tree as new.
  DIFF_RANGE="$(git hash-object -t tree /dev/null)..HEAD"
fi

if git diff "${DIFF_RANGE}" -U0 2>/dev/null \
     | grep -qE '^\+.*(sk-(ant-)?[A-Za-z0-9_-]{20,}|xi-[A-Za-z0-9]{24,}|AIza[A-Za-z0-9_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|xox[bap]-[A-Za-z0-9-]{20,})' \
   || git diff "${DIFF_RANGE}" -U0 2>/dev/null \
     | grep -qiE '^\+.*(openai|elevenlabs|eleven|anthropic|brain|guest|ghl)[a-z_]*(api[_-]?key|key|token|secret)["'"'"' ]*[:=]["'"'"' ]*[A-Za-z0-9/+_-]{16,}'; then
  echo "backup_push: BLOCKED — a provider-prefixed token or named credential is in the diff about to be pushed." >&2
  echo "  A secret in git history is forever. Inspect: git diff ${DIFF_RANGE} | grep -nE 'sk-|xi-|AIza|gh[pousr]_|xox|_key|_token|_secret'" >&2
  echo "  Fix the offending commit(s), then re-run. Nothing was pushed." >&2
  exit 2
fi

# ── 2. remote must exist ─────────────────────────────────────────────────────
if ! REMOTE_URL="$(git remote get-url origin 2>/dev/null)" || [ -z "${REMOTE_URL}" ]; then
  echo "backup_push: no remote yet, run: git remote add origin <url>" >&2
  echo "  (create a PRIVATE GitHub repo first — see deploy/BACKUP-AND-CANARY.md)" >&2
  exit 1
fi

# ── 3. push current branch, NEVER forced ─────────────────────────────────────
# --set-upstream is harmless once upstream exists; on first push it wires it up.
# No --force / --force-with-lease anywhere: a backup must only ever fast-forward.
if git push --set-upstream origin "${BRANCH}" 2>&1; then
  echo "backup_push: pushed ${BRANCH} -> ${REMOTE_URL}"
  exit 0
else
  echo "backup_push: git push FAILED (network? auth? non-fast-forward?)." >&2
  echo "  This script never force-pushes. If histories diverged, reconcile by hand." >&2
  exit 3
fi
