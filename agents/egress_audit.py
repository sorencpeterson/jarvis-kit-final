#!/usr/bin/env python3
"""Egress audit (#101) — inventory every external domain the codebase can reach out to.

Greps py/sh/mjs source for http(s):// URLs, skipping vendored/dependency dirs, and
groups the hits by bare domain -> [files that reference it]. Writes store/egress.json
for a point-in-time record, and prints anything NOT covered by the known-good
allowlist so a new dependency quietly phoning home somewhere unexpected gets
noticed instead of blending in.

Read-only, no network calls of its own — this only reads source text.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "store"
OUT = STORE / "egress.json"

SKIP_DIRS = {".venv", ".git", "node_modules", "__pycache__", "tts-cache", ".browser-profile"}
SOURCE_EXTS = {".py", ".sh", ".mjs"}
URL_RE = re.compile(r'https?://[^\s"\'<>)\]]+')

# Known-good domains this system is expected to talk to. Anything outside this set
# that shows up in the grep is worth a human glance — could be a new legit
# integration, could be a stray test URL, could be something worse.
ALLOWLIST_DOMAINS = {
    "anthropic.com", "api.anthropic.com",
    "ntfy.sh",
    "elevenlabs.io", "api.elevenlabs.io",
    "gohighlevel.com", "leadconnectorhq.com",
    "gmail.com", "googleapis.com", "accounts.google.com",
    "linkedin.com",
    "ts.net",
    "remotive.com", "remoteok.com", "remoteok.io", "jobicy.com",
    "hiring.cafe", "hiringcafe.com",  # migrated 2026-07; old domain 301s to the new one
    "github.com",
    "localhost", "127.0.0.1",
}


def _base_domain(host: str) -> str:
    """Reduce a hostname to the registrable-ish suffix used for allowlist matching,
    e.g. api.elevenlabs.io -> elevenlabs.io, get.thenobsmarketing.com stays as-is
    (no public-suffix list here, just enough to match the allowlist patterns above)."""
    host = host.lower()
    for allowed in ALLOWLIST_DOMAINS:
        if host == allowed or host.endswith("." + allowed):
            return allowed
    return host


def _iter_source_files():
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix not in SOURCE_EXTS:
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        yield p


def scan() -> dict[str, list[str]]:
    domains: dict[str, set[str]] = {}
    for path in _iter_source_files():
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        rel = str(path.relative_to(ROOT))
        for m in URL_RE.finditer(text):
            url = m.group(0)
            host = urlparse(url).hostname
            if not host:
                continue
            domains.setdefault(host, set()).add(rel)
    return {d: sorted(files) for d, files in sorted(domains.items())}


def main() -> int:
    domains = scan()
    out = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
           "domains": domains}
    STORE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    unlisted = [d for d in domains if _base_domain(d) not in ALLOWLIST_DOMAINS]
    print(f"egress_audit: {len(domains)} domain(s) found -> {OUT}")
    if unlisted:
        print(f"egress_audit: {len(unlisted)} NOT in allowlist:")
        for d in sorted(unlisted):
            files = domains[d]
            sample = ", ".join(files[:3]) + ("..." if len(files) > 3 else "")
            print(f"  {d}  ({sample})")
    else:
        print("egress_audit: all domains covered by allowlist")
    return 0


if __name__ == "__main__":
    sys.exit(main())
