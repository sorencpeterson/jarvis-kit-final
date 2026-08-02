#!/bin/bash
# J196: zombie process reaper. Kills orphaned wp-playground / call-coach ffmpeg processes
# that have been running longer than 12 hours (a real call or preview session never runs
# that long; anything still alive past 12h is a leak from a crashed parent).
#
# MATCHES NARROWLY on purpose: this codebase currently has a LIVE, legitimate ffmpeg
# process during any real call-coach session (coach/coach.py spawns ffmpeg to capture
# mic audio into store/.coach-ME/ and store/.coach-THEM/ segment files — see
# tools/install_call_coach.sh). A loose `pkill ffmpeg` would kill an in-progress call.
# So the match pattern requires BOTH "ffmpeg" AND one of the specific path fragments
# these processes' command lines actually contain:
#   - ".coach-ME" / ".coach-THEM"   (call coach's own segment output paths)
#   - "wp-playground" / "playground" (WP Playground preview tooling)
# A bare ffmpeg process for some unrelated purpose (e.g. content-factory video render)
# does NOT match and is left alone.
#
# Usage:
#   tools/reaper.sh              # dry-run: report what WOULD be killed (default, safe)
#   tools/reaper.sh --commit     # actually pkill matched processes older than 12h
#   tools/reaper.sh --max-age-h 6   # override the age threshold (hours)
set -uo pipefail

MAX_AGE_H=12
COMMIT=0
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[$i]}" in
    --commit) COMMIT=1 ;;
    --max-age-h)
      if [ -n "${args[$((i + 1))]:-}" ]; then MAX_AGE_H="${args[$((i + 1))]}"; fi
      ;;
  esac
done

MAX_AGE_S=$((MAX_AGE_H * 3600))

# etime_to_seconds: darwin `ps -o etime` prints one of:
#   MM:SS               (under an hour)
#   HH:MM:SS             (under a day)
#   DD-HH:MM:SS          (a day or more)
etime_to_seconds() {
  local etime="$1"
  local days=0 hms="$etime"
  if [[ "$etime" == *-* ]]; then
    days="${etime%%-*}"
    hms="${etime#*-}"
  fi
  local h=0 m=0 s=0
  local IFS=:
  read -r -a parts <<< "$hms"
  case "${#parts[@]}" in
    3) h="${parts[0]}"; m="${parts[1]}"; s="${parts[2]}" ;;
    2) m="${parts[0]}"; s="${parts[1]}" ;;
    1) s="${parts[0]}" ;;
  esac
  # strip any leading zeros bash might otherwise treat as octal
  h=$((10#$h)); m=$((10#$m)); s=$((10#$s)); days=$((10#${days:-0}))
  echo $((days * 86400 + h * 3600 + m * 60 + s))
}

echo "reaper: scanning for orphaned wp-playground/coach ffmpeg (mode=$([ "$COMMIT" = 1 ] && echo COMMIT || echo DRY-RUN), threshold=${MAX_AGE_H}h)"

found=0
killed=0

# ps -eo pid,etime,command, then narrow in awk/grep to: contains "ffmpeg" AND one of the
# specific coach/playground path fragments. Using `command` (not `comm`) so the full
# argv (including the output path) is visible to match on.
while IFS= read -r line; do
  pid=$(echo "$line" | awk '{print $1}')
  etime=$(echo "$line" | awk '{print $2}')
  cmd=$(echo "$line" | awk '{ $1=""; $2=""; sub(/^  */, ""); print }')

  case "$cmd" in
    *ffmpeg*.coach-ME*|*ffmpeg*.coach-THEM*|*ffmpeg*wp-playground*|*ffmpeg*playground*) ;;
    *) continue ;;
  esac

  age_s=$(etime_to_seconds "$etime")
  found=$((found + 1))
  if [ "$age_s" -lt "$MAX_AGE_S" ]; then
    echo "  SKIP pid=$pid age=${etime} (under threshold, likely a live session): $cmd"
    continue
  fi

  echo "  MATCH pid=$pid age=${etime} (OVER ${MAX_AGE_H}h threshold): $cmd"
  if [ "$COMMIT" = 1 ]; then
    if kill "$pid" 2>/dev/null; then
      echo "    -> killed"
      killed=$((killed + 1))
    else
      echo "    -> kill failed (already gone?)"
    fi
  else
    echo "    -> would kill (rerun with --commit)"
  fi
done < <(ps -eo pid,etime,command | tail -n +2)

echo "reaper: $found candidate process(es) matched pattern, $killed killed"
exit 0
