#!/usr/bin/env bash
# LIVE CALL COACH — setup status:
#   [DONE by the machine] whisper (pywhispercpp, GPU) + ffmpeg + model base.en
#   [YOURS, 2 min, optional] BlackHole 2ch — only needed for clean two-sided capture
#     with headphones. WITHOUT it: take calls on SPEAKERS and the mic hears both sides.
set -euo pipefail
echo "Whisper + ffmpeg: already installed and verified (TTS round-trip passed)."
echo ""
echo "OPTIONAL for headphone calls — BlackHole 2ch (~2 min):"
echo "  1. Download: https://existential.audio/blackhole/  (free, 2ch version)"
echo "  2. Run the pkg installer (your password)."
echo "  3. Audio MIDI Setup.app -> '+' bottom-left -> Create Multi-Output Device"
echo "     -> check BOTH 'MacBook Pro Speakers' AND 'BlackHole 2ch'."
echo "  4. During calls set Mac sound OUTPUT to that Multi-Output Device."
echo ""
echo "Run a call (speakers, zero installs):  .venv/bin/python coach/coach.py --framework discovery"
echo "Run a call (headphones + BlackHole):   .venv/bin/python coach/coach.py --framework discovery --them-device auto"
echo "Rehearse by typing:                    .venv/bin/python coach/coach.py --demo"
echo "The board: open http://127.0.0.1:8765/coach in a small window next to Zoom."
