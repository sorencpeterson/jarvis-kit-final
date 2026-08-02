#!/usr/bin/env bash
# Local Whisper STT (#11): private, offline voice input. One-time setup (~5 min):
#   bash tools/install_whisper.sh
# Builds whisper.cpp + downloads the small English model (~150MB). The server's
# /api/stt endpoint activates automatically once the binary exists.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p vendor && cd vendor
[ -d whisper.cpp ] || git clone --depth 1 https://github.com/ggerganov/whisper.cpp
cd whisper.cpp
make -j
bash ./models/download-ggml-model.sh small.en
echo "DONE. Test: ./main -m models/ggml-small.en.bin -f samples/jfk.wav"
