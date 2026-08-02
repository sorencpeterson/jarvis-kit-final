---
name: brain-deploy
description: The safe edit→deploy→verify ritual for the this repo codebase (server.py, index.html, agents, sw.js). Use whenever changing code in this repo, restarting the brain server, or debugging why a change is not visible.
---

# Deploying changes to the second brain (do it exactly like this)

**First session here? Read `LETTER-TO-THE-NEXT-MODELS.md` before anything.**

Distilled at peak intelligence during the Fable window (2026-07). These are paid-for
lessons; every rule here broke something once.

## The deploy ritual (every server.py or agent change)
1. Syntax gate BEFORE deploy: `python3 -c "import ast;ast.parse(open('app/server.py').read())"`.
   For agents also IMPORT-check with the venv: `.venv/bin/python -c "import sys;sys.path[:0]=['.','app','agents'];import <module>"`.
2. Deploy = `launchctl kickstart -k gui/501/com.jarvis.brain-server` (the launchd
   service owns port 8765 and serves static files fresh per request; only PYTHON
   changes need the restart).
3. The PREVIEW server (port 8799, launch.json "brain-preview") is a SEPARATE uvicorn:
   it does NOT restart with the launchd one. After any server.py change:
   preview_stop + preview_start, or you will chase ghost 404s.
4. Never `sleep` alone (blocked); use `perl -e 'select(undef,undef,undef,N)'` or an
   until-loop. macOS here has no `timeout`, no brew, no cmake; venv pip via `uv`.

## index.html (190KB single file) editing rules
- Anchor JS insertions on `/* v2 boot */`; CSS on `/* ===== v2: mobile bottom nav`.
  BEWARE anchor collisions: match on a phrase that exists ONCE (grep -c first).
- After every edit: `node --check` the extracted script block:
  `node --check <(python3 -c "s=open('app/static/index.html').read();i=s.rindex('<script>');j=s.rindex('</script>');print(s[i+8:j])")`
- Escaping trap: writing onclick handlers through python heredocs eats `\'`.
  Use data-attributes instead: `data-id="..." onclick="fn(this.dataset.id)"`.
- `let` top-level vars are NOT on window; test with `typeof X`.
- Drag & drop: native HTML5 drag on draggable=true elements KILLS pointer events on
  real trackpads and synthetic tests cannot reproduce it. The v12 system suppresses
  native dragstart on cards and uses pointer events + setPointerCapture only. Do not
  add draggable=true to anything in JSEL.

## Service worker (sw.js)
- '/' and '/api/' are network-first; NEVER cache a non-OK response (a cached 404
  poisoned /agree once — the `put()` helper checks r.ok, keep it).
- Signed public pages (/prop /mock /agree /case) BYPASS the SW entirely.
- Any sw.js change: bump `CACHE = 'brain-vN'` and, when preview-testing, unregister
  SW + delete caches before reload or you test the old build.
- Verify what the browser ACTUALLY runs: `window.SB_BUILD` / UI doctor header.

## Verification (before saying "done")
- Preview evals: suppress the 60s screensaver first: `lastInteract=Date.now()+9999999`.
- Run `uiDoctor()` (expect 0 findings) and check `(window.ERRLOG||[]).slice(-3)`.
- Playground/browser caching lies: cache-bust with `?v=N` when a change "didn't apply".
- Test proposals/agreements pollute real queues: mark test records skipped and strip
  test lines from agreements.jsonl when finished.

## Rails (unchanged, always)
- Every outward send is gated behind [OWNER]'s click or a config knob HE flips
  (cold_daily_enroll etc. ship 0). Launchd plist LOADS are his. No credentials ever.
- GHL quirks: posts/list wants STRING skip/limit; internal-API workflows show blank
  status; the gohighlevel-cli .env clobbers PATH (use /usr/bin/head inside it);
  cold_pipeline.jsonl is last-write-wins keyed by EMAIL (append, never rewrite).
- Public links (/prop /mock /agree) are DEAD until tailscale funnel is up —
  `_public_links_live()` guards sends; do not remove that guard.

## Lessons from the 544 buildout (2026-07-03)
- server.py imports hashlib ONLY inside functions historically. New module-level code
  using hashlib/hmac/etc: check the import exists at module level (a missing one
  500'd the /agree accept flow silently after logging: prospects saw errors).
- Long-lived child processes (coach.py) survive server restarts; any stop endpoint
  holding an in-memory Popen handle MUST also pattern-kill (`pkill -f`) or orphans
  capture the mic for hours. Idle-timeouts belong in the child itself.
- Fleet/subagent rules that worked: exclusive file ownership per agent (never two
  owners of server.py/index.html), contracts documented in status files for
  cross-boundary endpoints, self-verification required (ast + import + RUN),
  em-dash sweeps as cleanup children. Aggregate statuses from scratchpad files.
- morning.sh is a shared edit surface: serialize edits, `bash -n` after every one.
- `make test` = the verification sweep for a fresh clone (no server needed).
  `make doctor` adds live-server and launchd checks. Run one before "done."
- Test artifacts pollute real queues: every synthetic proposal/acceptance/draft gets
  skipped/stripped immediately after the assertion passes.
