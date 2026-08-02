#!/bin/bash
# Per-step timeout wrapper for morning.sh's $RUN (red-team: the chain had no per-step
# ceiling, so one hung agent could stall the whole 6:30 run). macOS has no `timeout`,
# so use perl's alarm. A kill here is store-safe: every writer uses atomic tmp+os.replace
# and flock (released on death), so a killed step leaves a stray .tmp, never a corrupt
# store. STEP_TIMEOUT overridable; 900s clears the heaviest legit agent (company_risk
# 5x90s LLM, win_asset 2x180s) with margin. Exits 124 on timeout, else the child's code.
TIMEOUT="${STEP_TIMEOUT:-900}"
PY="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)/.venv/bin/python"
[ -x "$PY" ] || PY="python3"
perl -e '
  my $t = shift;
  my $pid = fork();
  if (!defined $pid) { exec @ARGV or exit 127; }   # fork failed: best-effort direct run
  if ($pid == 0) {
    setpgrp(0, 0);   # R2-54: new process group so a timeout can kill the WHOLE tree,
                      # not just this one PID -- a step that spawns its own children
                      # (browser automation, subprocesses) used to orphan them on kill.
    exec @ARGV or exit 127;
  }
  $SIG{ALRM} = sub { kill("TERM", -$pid); sleep 2; kill("KILL", -$pid);
                     warn "run_step: TIMEOUT after ${t}s: @ARGV\n"; exit 124 };
  alarm $t;
  waitpid($pid, 0);
  exit($? >> 8);
' "$TIMEOUT" "$PY" "$@"
