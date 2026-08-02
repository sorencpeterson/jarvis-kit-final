#!/usr/bin/env python3
"""AI image generation for LinkedIn posts ([OWNER]'s split-comparison style).

Calls OpenAI's image API (gpt-image-1 by default) with a per-post prompt and
writes a PNG. Key + model live in store/config.json. No SDK dependency — raw
HTTPS via urllib so it runs anywhere the rest of the engine does.

Test:  uv run python agents/gen_image.py "a cinematic split-screen ... " /tmp/t.png
"""
from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "store" / "config.json"
API = "https://api.openai.com/v1/images/generations"
sys.path.insert(0, str(ROOT))
try:
    from store_lib import secret as _secret
except Exception:  # noqa: BLE001
    def _secret(name, default=""):
        return _cfg().get(name, default)


def _cfg() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def have_key() -> bool:
    return bool(_secret("openai_api_key"))


def generate_image(prompt: str, out_path: str, size: str = "1024x1024"):
    """Generate one image → write PNG to out_path. Returns (ok, message)."""
    cfg = _cfg()
    key = _secret("openai_api_key")
    if not key:
        return False, "no openai_api_key (set it in second-brain/.env)"
    model = cfg.get("image_model", "gpt-image-1")
    body = {"model": model, "prompt": prompt, "size": size, "n": 1}
    if model == "gpt-image-1":
        body["quality"] = cfg.get("image_quality", "high")
    req = urllib.request.Request(
        API, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:
            detail = ""
        return False, f"HTTP {e.code}: {detail[:240]}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"

    d = (data.get("data") or [{}])[0]
    b64 = d.get("b64_json")
    if b64:
        Path(out_path).write_bytes(base64.b64decode(b64))
        return True, "ok"
    if d.get("url"):  # dall-e-3 returns a URL
        try:
            with urllib.request.urlopen(d["url"], timeout=120) as r:
                Path(out_path).write_bytes(r.read())
            return True, "ok"
        except Exception as e:  # noqa: BLE001
            return False, f"download failed: {e}"
    return False, "no image in API response"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: gen_image.py '<prompt>' [out.png]")
        raise SystemExit(2)
    out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/gen_image_test.png"
    ok, msg = generate_image(sys.argv[1], out)
    print(("OK -> " + out) if ok else ("FAIL: " + msg))
    raise SystemExit(0 if ok else 1)
