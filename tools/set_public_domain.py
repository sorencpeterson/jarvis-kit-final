#!/usr/bin/env python3
"""Point the system's public links at a custom domain (e.g. proposals.[OWNER_SITE]),
once its tunnel is live. Fail-closed: it VERIFIES the domain actually serves the public
/case surface before writing public_base_url, so link_for() can never start minting links
at a dead host.

Usage:
  .venv/bin/python tools/set_public_domain.py proposals.[OWNER_SITE]
  .venv/bin/python tools/set_public_domain.py https://proposals.[OWNER_SITE] --force
      (--force writes even if the reachability check fails; use only mid-setup)
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "store" / "config.json"

sys.path.insert(0, str(ROOT))
from store_lib import _flock  # noqa: E402


def _norm(dom: str) -> str:
    dom = dom.strip().rstrip("/")
    if not dom.startswith(("http://", "https://")):
        dom = "https://" + dom
    return dom


def _reachable(base: str) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(base + "/pub/health", headers={"User-Agent": "set-public-domain"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return (r.status == 200, f"/pub/health returned {r.status}")
    except Exception as e:  # noqa: BLE001
        return (False, str(e)[:120])


def main() -> int:
    import json
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv
    if not args:
        cur = json.loads(CFG.read_text()).get("public_base_url", "(unset)")
        print(f"current public_base_url: {cur}\nusage: set_public_domain.py <domain> [--force]")
        return 1
    base = _norm(args[0])
    ok, why = _reachable(base)
    print(f"reachability of {base}/case: {'OK 200' if ok else 'FAILED (' + why + ')'}")
    if not ok and not force:
        print("NOT writing config: the domain must serve /case first (is the tunnel up + DNS live?).")
        print("Re-run with --force only if you are mid-setup and know it will come up.")
        return 2
    # _flock the read-modify-write: a concurrent config.json writer (server.py's
    # own config routes, campaign_guard, etc.) could otherwise race an unlocked
    # read here and have its change silently lost when this replace lands.
    with _flock(CFG):
        cfg = json.loads(CFG.read_text())
        old = cfg.get("public_base_url", "")
        cfg["public_base_url"] = base
        tmp = CFG.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, indent=2))
        tmp.replace(CFG)
    print(f"public_base_url: {old!r} -> {base!r}")
    print("Restart the server so the change takes effect:")
    print("  launchctl kickstart -k gui/501/com.jarvis.brain-server")
    print("New proposal links will now be branded. Existing staged drafts keep their old")
    print("baked-in link text; re-stage them (or re-run the factory) to pick up the new domain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
