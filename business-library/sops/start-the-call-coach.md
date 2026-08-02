# Start the Call Coach

## Trigger
Before any live warm/discovery call where real-time objection/suggestion
support would help. The coach is "the fantasy tool," live and working per
`second-brain/STATE.md`.

## Steps
1. **One-button launch**, three ways to trigger the same flow:
   - Dashboard: the **📞 COACH RIDES ALONG** button that auto-appears next to
     the warm-call launch button once `loadWarm()` runs.
   - Command palette (⌘K): **"📞 Start call coach (warm)"** or **"📞 Start
     call coach (discovery)"**: picks the framework.
   - Dock 📞 button, or the WARM room button.
2. Whichever entry point, it calls `startCoach(fw)`, which:
   - Shows "starting coach, audio -> Coach Output"
   - `POST /api/coach/start?framework=<warm-reactivation|discovery>`
   - Opens a popup window at `/coach?t=<token>` (430×680)
   - Speaks "Coach is riding along, sir" if voice output is on
3. **What `/api/coach/start` actually does server-side**
   (`app/server.py`): captures the current system audio output device
   (`tools/bin/set_output --get`), switches output to the **"Coach Output"**
   multi-output device (created once via `tools/bin/make_multiout`,
   prefers AirPods when connected, else speakers), then spawns
   `coach/coach.py --framework <fw> --them-device auto` as a detached
   background process.
4. **The coach board** (`/coach` page): a big say-line, flag indicators, a
   stage bar, ME (gold) / THEM (ice) live transcript, SPACE to nudge,
   ▶START/■STOP controls on-page. Suggestions fire event-driven the moment
   THEM speaks (4s min gap, 10s fallback), worker-threaded so it doesn't
   block capture.
5. **Frameworks** live in `second-brain/store/call_frameworks.md`
   (warm-reactivation + discovery), with the 50-item objections library
   (`business-library/playbooks/objections.md`) fed in as context.
6. **Stopping:** command palette **"Stop call coach"**, or the ■STOP control
   on the coach board. Calls `POST /api/coach/stop`, which kills the
   background process and restores the audio device to whatever it was
   before coach started (captured in step 3).

## Rehearsal / demo mode (no live call needed)
`coach/coach.py --demo` runs a typed rehearsal. Type lines as `them: ...`
instead of capturing real audio. Useful for practicing against the objection
library without a real call, or for testing a new framework before using it
live.

## What this requires to already be set up (one-time, not per-call)
- BlackHole 2ch audio driver installed
- "Coach Output" multi-output device created (`tools/bin/make_multiout`).
  Re-run after deleting it to rebuild with AirPods preference
- Whisper model running locally on-device (pywhispercpp, Metal/GPU), no
  network dependency for transcription itself

## Owner
[OWNER], per call. This is a live-assist tool used in real time, not something
that runs unattended.

## Last-verified
2026-07-03 (read directly from `app/server.py` `/api/coach/start`/`/stop`,
`app/static/index.html` `startCoach`/`stopCoach`, and `coach/coach.py`'s
argparse block; cross-checked against `STATE.md`'s CALL COACH section).
