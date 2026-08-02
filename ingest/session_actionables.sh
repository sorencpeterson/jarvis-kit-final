#!/bin/bash
# SessionEnd hook: extract actionable to-dos from the just-ended Claude session
# into store/inbox_staging.jsonl (NOT the trusted store). Fail-soft, logged.
#
# The harness pipes hook JSON on stdin, including .transcript_path. We hand the
# transcript to a cheap headless `claude` and ask only for concrete to-dos.
#
# Gated: skips short sessions; uses Haiku; never blocks; logs to ingest/ingest.log.

set -uo pipefail

# Recursion guard: this hook calls `claude -p`, whose own SessionEnd would re-fire
# this hook. The env var is exported before that call and inherited by the nested
# claude, so the nested invocation sees it and bails immediately. Without this it
# is a fork bomb.
if [ -n "${SECOND_BRAIN_INGEST:-}" ]; then exit 0; fi
export SECOND_BRAIN_INGEST=1

DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$DIR")"
STAGING="$ROOT/store/inbox_staging.jsonl"
LOG="$DIR/ingest.log"
ts() { date '+%Y-%m-%d %H:%M:%S'; }

# Read hook payload from stdin (best-effort; tolerate missing jq)
payload="$(cat)"
transcript="$(printf '%s' "$payload" | /usr/bin/python3 -c 'import sys,json;print(json.load(sys.stdin).get("transcript_path",""))' 2>/dev/null)"

if [ -z "$transcript" ] || [ ! -f "$transcript" ]; then
  echo "$(ts) no transcript path; skip" >> "$LOG"; exit 0
fi

# Gate: skip trivial sessions (< ~25 transcript lines)
lines=$(wc -l < "$transcript" 2>/dev/null || echo 0)
if [ "$lines" -lt 25 ]; then
  echo "$(ts) session too short ($lines lines); skip" >> "$LOG"; exit 0
fi

CLAUDE_BIN="$(command -v claude 2>/dev/null || true)"
[ -z "$CLAUDE_BIN" ] && [ -x "$HOME/.local/bin/claude" ] && CLAUDE_BIN="$HOME/.local/bin/claude"
if [ -z "$CLAUDE_BIN" ]; then
  echo "$(ts) claude CLI not found; skip" >> "$LOG"; exit 0
fi

SCHEMA="$ROOT/store/todos.schema.json"
PROMPT="Read this Claude Code transcript. Extract ONLY concrete, actionable to-dos that [OWNER] himself must do (not things already done, not your suggestions he declined). Output JSON Lines, one object per todo, matching $SCHEMA, with source=\"chat\", status=\"inbox\", and a short text. If there are none, output nothing at all. No prose, no code fences."

# Transcripts can blow the CLI's 10MB stdin cap (tool results dominate a long build
# session). Feed the model a digest of just the user + assistant TEXT — where the
# to-dos actually live — keeping the most recent ~400KB.
digest="$(mktemp "${TMPDIR:-/tmp}/sb-digest.XXXXXX")"
/usr/bin/python3 - "$transcript" "$digest" <<'PY' 2>>"$LOG"
import json, sys
src, dst = sys.argv[1], sys.argv[2]
chunks = []
for line in open(src, encoding="utf-8", errors="replace"):
    try:
        r = json.loads(line)
    except Exception:
        continue
    m = r.get("message") or {}
    role = m.get("role") or r.get("type")
    if role not in ("user", "assistant"):
        continue
    c = m.get("content")
    texts = []
    if isinstance(c, str):
        texts.append(c)
    elif isinstance(c, list):
        texts += [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
    t = "\n".join(x for x in texts if x).strip()
    if t:
        chunks.append(f"[{role}] {t[:2000]}")
open(dst, "w").write("\n\n".join(chunks)[-400_000:])
PY

# Cheapest model, short timeout, never block the harness.
out="$(cat "$digest" | perl -e 'alarm 90; exec @ARGV' "$CLAUDE_BIN" -p "$PROMPT" --model claude-haiku-4-5-20251001 2>>"$LOG")"
rm -f "$digest"

# Keep only lines that look like JSON objects with a "text" field.
echo "$out" | grep -E '^\s*\{.*"text"' >> "$STAGING" 2>/dev/null
added=$(echo "$out" | grep -cE '^\s*\{.*"text"' 2>/dev/null || echo 0)
echo "$(ts) staged $added actionable(s) from $lines-line session" >> "$LOG"
exit 0
