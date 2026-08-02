#!/usr/bin/env python3
"""Connection wizard: wire up your own accounts and API keys.

    python3 connect.py            # menu
    python3 connect.py --status   # what is connected, what is not

Everything here is optional. The system runs with none of it: the agents think
through Claude Code, which needs no key. Each integration you add turns on one
more lane.

Keys are written to store/config.json (gitignored, chmod 600). They are never
printed back, never logged, and never leave your machine.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "store" / "config.json"

# key, label, why, where to get it, config path (dotted)
INTEGRATIONS = [
    ("claude_cli", "Claude Code", "REQUIRED. Every agent's thinking runs through it.",
     "https://claude.com/claude-code", None),
    ("ntfy_topic", "Push notifications (ntfy)",
     "Phone alerts: interview replies, things needing you. Free, no account.",
     "Pick any hard-to-guess string, install the ntfy app, subscribe to it.", "ntfy_topic"),
    ("openai_api_key", "OpenAI (images)",
     "Optional, costs money. FREE ALTERNATIVE: skip it. Text posts do fine, or "
     "generate images elsewhere and drop them in content/images/.",
     "https://platform.openai.com/api-keys", "openai_api_key"),
    ("elevenlabs_api_key", "ElevenLabs (voice)",
     "Optional, costs money. FREE ALTERNATIVE: macOS already has `say`. "
     "Try: say -f store/brief.md",
     "https://elevenlabs.io/app/settings/api-keys", "elevenlabs_api_key"),
    ("google", "Google (Gmail + Calendar)",
     "Optional. Reply tracking, interview detection, meeting prep.",
     "See schedule/SETUP.md for the OAuth walkthrough.", None),
    ("ghl", "GoHighLevel",
     "Optional, and only if you already pay for GHL. Nothing depends on it.",
     "Your GHL agency settings, Private Integrations.", "ghl_api_key"),
]


def _load() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save(cfg: dict) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=1))
    tmp.replace(CONFIG)
    try:
        os.chmod(CONFIG, 0o600)
    except OSError:
        pass


def _have(key: str, cfg: dict) -> bool:
    if key == "claude_cli":
        return bool(_which("claude"))
    if key == "google":
        return (ROOT / "schedule" / "credentials" / "token.json").exists()
    if key == "ghl":
        return bool(cfg.get("ghl_api_key"))
    return bool(cfg.get(key))


def _which(cmd: str) -> str:
    try:
        r = subprocess.run(["which", cmd], capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def status() -> None:
    cfg = _load()
    print("\n  Connections\n  " + "-" * 44)
    for key, label, why, where, _ in INTEGRATIONS:
        on = _have(key, cfg)
        mark = "\033[32m●\033[0m" if on else "\033[90m○\033[0m"
        print(f"  {mark} {label}")
        if not on:
            print(f"      {why}")
    print()
    missing_required = not _have("claude_cli", cfg)
    if missing_required:
        print("  \033[31mClaude Code is not installed.\033[0m Nothing will think without it.")
        print("  https://claude.com/claude-code\n")


def _ask_secret(label: str, where: str) -> str:
    print(f"\n  {label}")
    print(f"  Get it: {where}")
    print("  (paste it, or press Enter to skip)")
    try:
        import getpass
        v = getpass.getpass("  > ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""
    return v


def main() -> int:
    if "--status" in sys.argv:
        status()
        return 0

    cfg = _load()
    print("\n  Connection wizard")
    print("  " + "-" * 44)
    print("  Everything is optional except Claude Code.")
    print("  Press Enter to skip anything you do not use.")
    print("  Several of these have a free alternative, noted inline.")
    print("  See COSTS.md for the full breakdown.\n")

    if not _which("claude"):
        print("  \033[31m! Claude Code is not installed.\033[0m")
        print("    The agents cannot think without it. Install it first:")
        print("    https://claude.com/claude-code\n")

    for key, label, why, where, path in INTEGRATIONS:
        if key in ("claude_cli", "google"):
            continue
        if _have(key, cfg):
            print(f"  \033[32m●\033[0m {label} already connected. Enter to keep.")
            continue
        print(f"\n  \033[1m{label}\033[0m")
        print(f"  {why}")
        if key == "ntfy_topic":
            print("  Tip: use something unguessable, e.g. jarvis-a7f3k9x2.")
            print("  Anyone who learns the topic can read your alerts.")
        v = _ask_secret("Value", where)
        if v and path:
            cfg[path] = v
            print(f"  \033[32m✓\033[0m {label} saved")

    _save(cfg)
    print("\n  " + "-" * 44)
    print("  Saved to store/config.json (owner-only permissions).")
    print("\n  Google Gmail/Calendar is a longer OAuth flow.")
    print("  When you want it: see schedule/SETUP.md\n")
    status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
