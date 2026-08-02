#!/usr/bin/env python3
"""Owner identity layer.

The whole system was built for one person, so their name, site, company and
handles were baked into ~200 files of prompts, comments and templates. Those
are now TOKENS ([OWNER], [OWNER_SITE], ...) and this module swaps them for
whoever owns this copy, at runtime.

One integration point: personalize() runs on every LLM prompt (wired into
app/planner.py), so every agent speaks as the configured owner without any
agent needing to know this module exists.

Config lives in config/owner.json (created by setup.py, gitignored).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config" / "owner.json"
EXAMPLE = ROOT / "config" / "owner.example.json"

# token -> config key. Anything missing falls back to a readable placeholder
# rather than crashing, so a half-finished setup still runs.
_TOKENS = {
    "[OWNER]": ("name", "the owner"),
    "[OWNER_EMAIL]": ("email", "you@example.com"),
    "[OWNER_SITE]": ("site", "example.com"),
    "[OWNER_COMPANY]": ("company", "the company"),
    "[OWNER_LINKEDIN]": ("linkedin", "linkedin.com/in/you"),
    "[OWNER_HANDLE]": ("handle", "you"),
    "[OWNER_CITY]": ("city", "your city"),
    "[OWNER_ADDRESS]": ("address", "your address"),
    "[HOME]": ("home", str(Path.home())),
    "[APP_ROOT]": ("app_root", str(ROOT)),
}

_cache: dict | None = None


def load(refresh: bool = False) -> dict:
    """Owner config, cached. Falls back to the example file, then to {}."""
    global _cache
    if _cache is not None and not refresh:
        return _cache
    for p in (CONFIG, EXAMPLE):
        try:
            _cache = json.loads(p.read_text())
            return _cache
        except (OSError, json.JSONDecodeError):
            continue
    _cache = {}
    return _cache


def get(key: str, default: str = "") -> str:
    return str(load().get(key) or default)


def is_configured() -> bool:
    """True once setup.py has written a real config with a name in it."""
    return CONFIG.exists() and bool(load().get("name"))


def personalize(text):
    """Replace [OWNER]-style tokens with this owner's real values.

    Non-str input passes through untouched so it is safe to wrap anything.
    """
    if not isinstance(text, str) or "[" not in text:
        return text
    cfg = load()
    for token, (key, fallback) in _TOKENS.items():
        if token in text:
            text = text.replace(token, str(cfg.get(key) or fallback))
    return text


if __name__ == "__main__":
    cfg = load()
    print(f"configured: {is_configured()}")
    for k, v in cfg.items():
        if not k.startswith("_"):
            print(f"  {k}: {v}")
