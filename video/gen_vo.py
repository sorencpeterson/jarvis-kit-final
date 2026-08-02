#!/usr/bin/env python3
"""Generate the VO ([OWNER]'s ElevenLabs clone) + word-level caption timings for a
short-form video. Writes tt_audio.mp3 + tt_data.json into content/samples."""
import base64
import glob
import json
import sys
import urllib.request
from pathlib import Path

SB = Path(__file__).resolve().parent.parent
OUT = SB / "content" / "samples"
CFG = json.loads((SB / "store" / "config.json").read_text())
sys.path.insert(0, str(SB))
from store_lib import secret as _secret  # noqa: E402
KEY = _secret("elevenlabs_api_key")
VID = CFG.get("elevenlabs_voice_id", "BVGtbykf8TKzwS2aKwJl")

SCRIPT = ("Most agency owners think they have a sales problem. They don't. "
          "They have a delivery problem. You can't sell with confidence when "
          "fulfillment is a coin flip. Fix delivery, and the sales take care of themselves.")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    body = json.dumps({
        "text": SCRIPT, "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.4, "similarity_boost": 0.85,
                           "style": 0.45, "use_speaker_boost": True},
    }).encode()
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{VID}/with-timestamps",
        data=body, headers={"xi-api-key": KEY, "Content-Type": "application/json"})
    res = json.load(urllib.request.urlopen(req, timeout=120))
    (OUT / "tt_audio.mp3").write_bytes(base64.b64decode(res["audio_base64"]))

    al = res["alignment"]
    chars, st, et = al["characters"], al["character_start_times_seconds"], al["character_end_times_seconds"]
    words, cur, ws, we = [], "", None, None
    for c, s, e in zip(chars, st, et):
        if c in (" ", "\n"):
            if cur:
                words.append([cur, ws, we]); cur, ws = "", None
            continue
        if not cur:
            ws = s
        cur += c; we = e
    if cur:
        words.append([cur, ws, we])
    dur = (et[-1] if et else 15.0) + 0.35

    chunks = []
    for i in range(0, len(words), 3):
        g = words[i:i + 3]
        chunks.append({"t": g[0][1], "e": g[-1][2], "text": " ".join(w[0] for w in g)})

    imgs = sorted(glob.glob(str(SB / "content" / "images" / "*.png")),
                  key=lambda p: Path(p).stat().st_mtime, reverse=True)[:3]
    json.dump({"dur": dur, "chunks": chunks, "images": imgs, "script": SCRIPT},
              open(OUT / "tt_data.json", "w"))
    print(f"VO + timings OK -> dur={dur:.1f}s, {len(chunks)} caption chunks, {len(words)} words, {len(imgs)} bg images")


if __name__ == "__main__":
    main()
