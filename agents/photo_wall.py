#!/usr/bin/env python3
"""Photo wall (#88) — build a manifest of images for a future dashboard photo wall.

Why: nothing currently scans a personal photos folder for the dashboard to draw
from. This reads config `photos_dir`; if it's set and exists, it lists up to 60
image files (jpg/jpeg/png/heic, case-insensitive) under it and writes
store/photo_manifest.json for a future dashboard "saver" screen to read. If
photos_dir is unset (or points nowhere real), it says so plainly and writes an
{"status": "unconfigured"} manifest rather than silently doing nothing.

Read-only against the filesystem under photos_dir; only write is
store/photo_manifest.json (full overwrite each run).
Run standalone: .venv/bin/python agents/photo_wall.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402

CONFIG = ROOT / "store" / "config.json"
OUT = ROOT / "store" / "photo_manifest.json"
MAX_PHOTOS = 60
EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic"}


def _config() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _list_photos(photos_dir: Path) -> list[str]:
    found = []
    try:
        for p in sorted(photos_dir.rglob("*")):
            if p.is_file() and p.suffix.lower() in EXTENSIONS:
                found.append(str(p))
            if len(found) >= MAX_PHOTOS:
                break
    except OSError:
        pass
    return found


def run() -> int:
    raw_dir = (_config().get("photos_dir") or "").strip()
    if not raw_dir:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({"status": "unconfigured", "generated": now_iso()}, indent=2))
        print("photo_wall: set photos_dir in config to enable")
        return 0

    photos_dir = Path(raw_dir).expanduser()
    if not photos_dir.exists() or not photos_dir.is_dir():
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(
            {"status": "unconfigured", "generated": now_iso(),
             "note": f"photos_dir is set ({raw_dir}) but does not exist or is not a directory"},
            indent=2))
        print(f"photo_wall: photos_dir '{raw_dir}' does not exist or is not a directory — "
              f"set photos_dir in config to enable")
        return 0

    photos = _list_photos(photos_dir)
    manifest = {
        "status": "ok",
        "generated": now_iso(),
        "photos_dir": str(photos_dir),
        "count": len(photos),
        "photos": photos,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(manifest, indent=2))
    print(f"photo_wall: {len(photos)} image(s) from {photos_dir} -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
