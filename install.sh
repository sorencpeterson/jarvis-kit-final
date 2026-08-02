#!/bin/bash
# One-command bootstrap. Finds a usable Python, builds the venv, installs deps,
# then hands off to the setup wizard.
#
#   bash install.sh
#
# Why this exists: macOS ships Python 3.9 as `python3`, and this project needs
# 3.11+. Following the old README instructions produced a venv that could not
# install the requirements, with a wall of red pip errors and no explanation.
set -uo pipefail
cd "$(dirname "$0")"

b()  { printf "\033[1m%s\033[0m\n" "$*"; }
ok() { printf "  \033[32m✓\033[0m %s\n" "$*"; }
no() { printf "  \033[31m✗\033[0m %s\n" "$*"; }
inf(){ printf "    %s\n" "$*"; }

echo ""
b "JARVIS Kit installer"
echo "────────────────────────────────────────────"

# ---- 1. find a Python 3.11+ -------------------------------------------------
PY=""
for c in python3.13 python3.12 python3.11 python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  command -v "$c" >/dev/null 2>&1 || continue
  if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done

if [ -z "$PY" ]; then
  no "No Python 3.11 or newer found."
  inf "macOS ships 3.9, which is too old for this project."
  inf ""
  inf "Install one, then re-run this script:"
  inf "  brew install python@3.12"
  inf "  (no brew? get it at https://brew.sh)"
  echo ""
  exit 1
fi
ok "Python: $("$PY" --version 2>&1) at $(command -v "$PY")"

# ---- 2. venv ----------------------------------------------------------------
if [ -d .venv ]; then
  ok "Reusing existing .venv"
else
  "$PY" -m venv .venv || { no "Could not create .venv"; exit 1; }
  ok "Created .venv"
fi

.venv/bin/python -m pip install -q --upgrade pip >/dev/null 2>&1 && ok "pip upgraded"

# ---- 3. dependencies --------------------------------------------------------
echo ""
b "Installing dependencies (this takes a minute)"
if .venv/bin/pip install -q -r requirements.txt 2>/tmp/jarvis_pip_err; then
  ok "Dependencies installed"
else
  no "Some dependencies failed. Retrying without strict version pins..."
  sed 's/==.*//' requirements.txt > /tmp/jarvis_reqs_loose.txt
  if .venv/bin/pip install -q -r /tmp/jarvis_reqs_loose.txt 2>/dev/null; then
    ok "Installed with relaxed versions"
    inf "Pinned versions did not resolve on this machine, so latest compatible"
    inf "releases were used instead. Everything should still work."
  else
    no "Install failed. Last errors:"
    tail -5 /tmp/jarvis_pip_err | sed 's/^/    /'
    exit 1
  fi
fi

# ---- 4. optional tools ------------------------------------------------------
echo ""
b "Optional tools"
command -v claude >/dev/null 2>&1 \
  && ok "Claude Code found (required for the agents to think)" \
  || { no "Claude Code not on PATH"; inf "The agents call it for every LLM step."; \
       inf "Install: https://claude.com/claude-code"; }
command -v node >/dev/null 2>&1 \
  && ok "Node found (used for PDF rendering)" \
  || inf "Node not found. Only needed for resume/proposal PDFs."

# ---- 5. verify --------------------------------------------------------------
echo ""
b "Verifying"
if .venv/bin/python -m pytest tests/ -q --no-header >/tmp/jarvis_tests 2>&1; then
  ok "$(grep -oE '[0-9]+ passed' /tmp/jarvis_tests | tail -1)"
else
  R=$(tail -3 /tmp/jarvis_tests | tr '\n' ' ')
  no "Some tests failed: $R"
  inf "Not necessarily fatal. See /tmp/jarvis_tests"
fi

# ---- 6. hand off ------------------------------------------------------------
echo ""
echo "────────────────────────────────────────────"
if [ -f config/owner.json ]; then
  ok "Already configured. Run 'python3 setup.py' to change your details."
else
  b "Next: tell it who you are"
  echo ""
  echo "    .venv/bin/python setup.py"
  echo ""
fi
echo ""
