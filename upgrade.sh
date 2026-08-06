#!/bin/bash
# Upgrade an existing install in place, keeping your data and settings.
#
#   bash upgrade.sh
#
# Run it from inside your existing jarvis folder. It pulls the current code over
# the top of your install and leaves these alone:
#
#   store/               your data: jobs, contacts, ledger, answers, config knobs
#   config/owner.json    your identity
#   .venv/               your Python environment
#   schedule/credentials your Google OAuth, if you set it up
#
# A timestamped backup of those goes in ../jarvis-backup-<date> first, so a bad
# upgrade is recoverable.
#
# Safe to re-run. It never deletes anything it did not bring.
set -uo pipefail
cd "$(dirname "$0")"

REPO="https://github.com/sorencpeterson/jarvis-kit-final.git"
HERE="$(pwd)"
STAMP="$(date +%Y-%m-%d-%H%M)"
BACKUP="$HERE/../jarvis-backup-$STAMP"
TMP="${TMPDIR:-/tmp}/jarvis-upgrade-$STAMP"

b()  { printf "\033[1m%s\033[0m\n" "$*"; }
ok() { printf "  \033[32m✓\033[0m %s\n" "$*"; }
no() { printf "  \033[31m✗\033[0m %s\n" "$*"; }
inf(){ printf "    %s\n" "$*"; }

echo ""
b "Upgrading in place"
echo "────────────────────────────────────────────"

# --- sanity: are we in the right place? --------------------------------------
if [ ! -d agents ] || [ ! -d app ]; then
  no "This does not look like a jarvis install (no agents/ or app/)."
  inf "Run it from inside your jarvis folder."
  exit 1
fi
ok "Found an install at $HERE"

# --- 1. back up what is yours ------------------------------------------------
mkdir -p "$BACKUP"
for keep in store config/owner.json schedule/credentials content/voice.md; do
  if [ -e "$keep" ]; then
    mkdir -p "$BACKUP/$(dirname "$keep")"
    cp -R "$keep" "$BACKUP/$keep" 2>/dev/null && ok "backed up $keep"
  fi
done
inf "backup: $BACKUP"

# --- 2. fetch the current code ----------------------------------------------
echo ""
b "Fetching current code"
rm -rf "$TMP"
if ! git clone -q --depth 1 "$REPO" "$TMP" 2>/dev/null; then
  no "Could not clone $REPO"
  inf "Check your connection, or that the repo is still public."
  exit 1
fi
ok "cloned"

# --- 3. copy code over, never touching your data ----------------------------
echo ""
b "Applying"
rsync -a \
  --exclude='.git/' --exclude='store/' --exclude='config/owner.json' \
  --exclude='.venv/' --exclude='schedule/credentials/' \
  --exclude='content/voice.md' --exclude='__pycache__/' --exclude='*.pyc' \
  "$TMP"/ "$HERE"/ && ok "code updated (your data untouched)"

# --- 4. dependencies, in case new ones landed --------------------------------
if [ -x .venv/bin/pip ]; then
  .venv/bin/pip install -q -r requirements.txt 2>/dev/null && ok "dependencies current"
else
  inf "No .venv found. Run: bash install.sh"
fi

# --- 5. tune for the plan ----------------------------------------------------
echo ""
b "Tuning"
PY=".venv/bin/python"; [ -x "$PY" ] || PY="python3"
PLAN="$($PY -c "import json;print(json.load(open('config/owner.json')).get('claude_plan','pro'))" 2>/dev/null || echo pro)"
$PY tools/tune_for_plan.py "--$PLAN" 2>/dev/null | sed 's/^/  /' || inf "tuner skipped"

# --- 6. verify ---------------------------------------------------------------
echo ""
b "Verifying"
if [ -x .venv/bin/python ]; then
  if .venv/bin/python -m pytest tests/ -q --no-header >"$TMP/tests.log" 2>&1; then
    ok "$(grep -oE '[0-9]+ passed' "$TMP/tests.log" | tail -1)"
  else
    no "Some tests failed: $(tail -2 "$TMP/tests.log" | tr '\n' ' ')"
    inf "See $TMP/tests.log. Your data is safe either way."
  fi
  .venv/bin/python -c "
import os,sys,importlib,warnings; warnings.filterwarnings('ignore')
sys.path[:0]=['.','app','agents']
bad=[]
for n in sorted(f[:-3] for f in os.listdir('agents') if f.endswith('.py') and not f.startswith('_')):
    try: importlib.import_module(n)
    except Exception: bad.append(n)
print('  \033[32m✓\033[0m all agents import' if not bad else f'  \033[31m✗\033[0m {len(bad)} agents failed to import')
" 2>/dev/null
fi

rm -rf "$TMP"
echo ""
echo "────────────────────────────────────────────"
b "Done"
echo ""
echo "  Your data, identity and connections are unchanged."
echo "  Backup: $BACKUP  (delete it once you are happy)"
echo ""
echo "  If the server was running, restart it."
echo ""
