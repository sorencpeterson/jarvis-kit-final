#!/bin/bash
# tools/restore_drill.sh — scripted proof that DR-RUNBOOK.md actually restores a
# runnable system, not just a pile of files (survivability drill, 2026-07-07).
#
# What it does, end to end, exactly like a new Mac would:
#   1. git clone file://<this repo> into a temp dir under /private/tmp
#      (clones committed HEAD: anything uncommitted deliberately does NOT restore)
#   2. build a fresh venv there: uv venv --python 3.12 + uv pip install -r requirements.txt
#   3. FRESH-INSTALL store: wipe the cloned store/ to an empty dir. No data, no
#      config.json, no .env (gitignored, so the clone never has one). This is the
#      harshest path: every agent/test must tolerate a store with nothing in it.
#   4. ast gate: the clone's tools/ast_check.py under the clone's python
#   5. the FULL pytest suite in the clone
#   6. print PASS/FAIL per stage + total time; delete the temp dir on success,
#      KEEP it on failure and print the path for inspection.
#
# Timeouts use the repo's perl-alarm pattern (macOS has no `timeout` binary).
# Read-only against the live repo; writes only under the temp dir.
#
# Usage:            bash tools/restore_drill.sh
# Override tempdir: DRILL_ROOT=/some/dir bash tools/restore_drill.sh
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DRILL_ROOT="${DRILL_ROOT:-/private/tmp}"
DEST="$(mktemp -d "$DRILL_ROOT/sb-restore-drill-XXXXXX")" || exit 1
CLONE="$DEST/second-brain"
T_START=$(date +%s)
FINDINGS=()

# uv lives in ~/.local/bin on this Mac; launchd-ish minimal PATHs won't have it.
UV="$(command -v uv || true)"
[ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"

declare -a S_NAME S_RC S_SEC S_LOG
run_stage() { # run_stage <name> <alarm_secs> <cmd...>
  local name="$1" alarm="$2"; shift 2
  local log="$DEST/stage-${name}.log" t0=$(date +%s) rc=0
  echo "--- stage: $name"
  perl -e "alarm $alarm; exec @ARGV" "$@" >"$log" 2>&1 || rc=$?
  local dt=$(( $(date +%s) - t0 ))
  S_NAME+=("$name"); S_RC+=("$rc"); S_SEC+=("$dt"); S_LOG+=("$log")
  if [ "$rc" -ne 0 ]; then
    echo "    FAIL (exit $rc after ${dt}s) — last lines of $log:"
    tail -8 "$log" | sed 's/^/    | /'
  else
    echo "    PASS (${dt}s)"
  fi
  return "$rc"
}

echo "== restore drill: $REPO -> $DEST"
[ -z "$UV" ] && { echo "FATAL: uv not found (DR-RUNBOOK step 2 installs it first)"; exit 1; }

# 1. clone committed HEAD only
run_stage clone 300 git clone --quiet "file://$REPO" "$CLONE"
if [ -d "$CLONE" ]; then
  echo "    cloned HEAD: $(git -C "$CLONE" log -1 --format='%h %s' 2>/dev/null | head -c 90)"
fi

# requirements.txt must come FROM GIT for the drill to be honest. R2-52: this used
# to copy it in from the live working tree when the clone lacked it, which let
# deps-install (and the whole drill) pass even though the BACKUP itself has no
# dependency manifest -- a real disaster has no live tree to borrow from, so that
# silently certified a restore that couldn't actually happen. Record the gap and
# let deps-install fail honestly instead of hiding it.
if [ ! -f "$CLONE/requirements.txt" ]; then
  FINDINGS+=("requirements.txt is NOT COMMITTED: the clone has no dependency manifest. A real restore-from-backup-only would fail here; deps-install is expected to fail next -- that failure is honest, do not patch around it.")
fi

# 2. fresh venv + deps (uv resolves from its cache/PyPI; generous alarm)
run_stage venv-create 300 "$UV" venv --python 3.12 "$CLONE/.venv"
run_stage deps-install 900 env VIRTUAL_ENV="$CLONE/.venv" "$UV" pip install -q -r "$CLONE/requirements.txt"
PY="$CLONE/.venv/bin/python"

# 3. fresh-install store: empty dir, nothing else (no config.json, no .env either —
#    gitignored, so the clone genuinely has none)
run_stage fresh-store 60 bash -c "rm -rf '$CLONE/store' && mkdir '$CLONE/store'"

# 4 + 5. verification gates inside the clone, with the clone's interpreter
run_stage ast-gate 300 bash -c "cd '$CLONE' && '$PY' tools/ast_check.py"
run_stage pytest-full 1800 bash -c "cd '$CLONE' && '$PY' -m pytest tests/ -q"

# -- report -------------------------------------------------------------------
TOTAL=$(( $(date +%s) - T_START ))
OVERALL=0
echo ""
echo "== restore drill results =="
for i in "${!S_NAME[@]}"; do
  if [ "${S_RC[$i]}" -eq 0 ]; then st="PASS"; else st="FAIL"; OVERALL=1; fi
  printf "   %-14s %s  %4ss\n" "${S_NAME[$i]}" "$st" "${S_SEC[$i]}"
done
echo "   TOTAL              ${TOTAL}s ($((TOTAL / 60))m$((TOTAL % 60))s)"
for f in "${FINDINGS[@]:-}"; do [ -n "$f" ] && echo "   FINDING: $f"; done

if [ "$OVERALL" -eq 0 ]; then
  echo "   DRILL: PASS — a bare git clone + requirements.txt restores a green system."
  rm -rf "$DEST"
else
  echo "   DRILL: FAIL — temp dir KEPT for inspection: $DEST"
  # surface which tests broke on the fresh-install path, if pytest was the failure
  for i in "${!S_NAME[@]}"; do
    if [ "${S_NAME[$i]}" = "pytest-full" ] && [ "${S_RC[$i]}" -ne 0 ]; then
      echo "   failing tests (first 30):"
      grep -E "^(FAILED|ERROR) " "${S_LOG[$i]}" | head -30 | sed 's/^/     /'
      tail -2 "${S_LOG[$i]}" | sed 's/^/     /'
    fi
  done
fi
exit "$OVERALL"
