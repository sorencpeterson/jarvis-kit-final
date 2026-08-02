#!/bin/bash
# Summon JARVIS (#17): opens the dashboard listening. Raycast Script Command.
# @raycast.schemaVersion 1
# @raycast.title Summon JARVIS
# @raycast.mode silent
# @raycast.icon 🟡
# @raycast.packageName Second Brain
DIR="$(cd "$(dirname "$0")/.." && pwd)"
T="$(grep '^BRAIN_TOKEN=' "$DIR/.env" | cut -d= -f2)"
open "http://127.0.0.1:8765/?t=$T&listen=1"
