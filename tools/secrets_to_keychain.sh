#!/bin/bash
# D9/DR: copy .env secrets into the macOS Keychain as a second on-machine copy that
# survives a deleted working tree (NOT an off-machine backup; the git remote is that).
# [OWNER] RUNS THIS HIMSELF: ./tools/secrets_to_keychain.sh   (values never print)
# Restore later:            ./tools/secrets_to_keychain.sh restore > .env.restored
set -euo pipefail
cd "$(dirname "$0")/.."
SERVICE_PREFIX="second-brain"
KEYS=(BRAIN_TOKEN OPENAI_API_KEY ELEVENLABS_API_KEY GUEST_TOKEN)

if [ "${1:-}" = "restore" ]; then
  for k in "${KEYS[@]}"; do
    v=$(security find-generic-password -a "$USER" -s "$SERVICE_PREFIX.$k" -w 2>/dev/null || true)
    [ -n "$v" ] && echo "$k=$v"
  done
  exit 0
fi

[ -f .env ] || { echo "no .env here; run from the repo root"; exit 1; }
n=0
for k in "${KEYS[@]}"; do
  v=$(grep -E "^$k=" .env | head -1 | cut -d= -f2- || true)
  [ -z "$v" ] && continue
  # -U updates if the item already exists
  security add-generic-password -U -a "$USER" -s "$SERVICE_PREFIX.$k" -w "$v" >/dev/null
  n=$((n+1))
done
echo "stored $n secret(s) in the login Keychain under $SERVICE_PREFIX.*"
echo "restore with: $0 restore > .env.restored"
