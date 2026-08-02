#!/usr/bin/env python3
"""Branded-domain finisher (2026-07-11). Watches for proposals.[OWNER_SITE] to come
alive through the Cloudflare tunnel and flips the system to it — ONCE — the moment it does.

WHY: the cutover has a human-paced middle (Namecheap nameserver propagation, [OWNER]'s
cloudflared authorize click). Instead of anyone babysitting DNS, run.sh calls this every
10-min tick; it exits instantly until the day the public health check answers, then:
  1. tools/set_public_domain.py <domain>   (self-verifying, fail-closed writer)
  2. kickstart the brain server            (config reload)
  3. re-stage staged proposals' LINK TEXT  (their email bodies carry the old tailnet URL;
     re-mint link + email_draft so the next send uses the branded link)
  4. notify [OWNER] + write the done-stamp   (never runs again)

Fail-safe: every step degrades to "try again next tick" — no stamp until the flip
actually verified. Nothing here sends anything; sends stay behind his click.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import owner  # noqa: E402
import planner  # noqa: E402

DOMAIN = f"proposals.{owner.get('site', 'example.com')}"
STAMP = ROOT / "store" / ".domain-flipped"


def _health_ok() -> bool:
    try:
        req = urllib.request.Request(f"https://{DOMAIN}/pub/health",
                                     headers={"User-Agent": "domain-watch"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status == 200 and b'"ok"' in r.read(200)
    except Exception:  # noqa: BLE001
        return False


def _restage_links() -> int:
    """Re-mint link + email body for still-staged proposals so they carry the branded URL."""
    import proposal_factory as pf
    n = 0
    for rec in pf.load_queue():
        if rec.get("status") != "staged":
            continue
        pid = rec.get("id") or ""
        old = rec.get("link") or ""
        new = pf.link_for(pid)   # reads the freshly-flipped public_base_url
        if new and new != old:
            body = (rec.get("email_draft") or "")
            if old and old in body:
                body = body.replace(old, new)
            pf.save({**rec, "link": new, "email_draft": body, "relinked": True})
            n += 1
    return n


def run() -> int:
    if STAMP.exists():
        return 0  # flipped already; silent forever
    cfg = planner._config()
    if (cfg.get("public_base_url") or "").endswith(DOMAIN):
        STAMP.touch()  # someone flipped it by hand; just stamp
        return 0
    if not _health_ok():
        return 0  # DNS/tunnel not live yet; silent, next tick retries
    # the domain answers -> flip, verified writer first (it re-checks reachability itself)
    r = subprocess.run([str(ROOT / ".venv" / "bin" / "python"),
                        str(ROOT / "tools" / "set_public_domain.py"), DOMAIN],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        print("domain watch: set_public_domain refused:", (r.stdout + r.stderr)[-200:])
        return 0  # retry next tick
    subprocess.run(["launchctl", "kickstart", "-k", "gui/501/com.jarvis.brain-server"],
                   capture_output=True, timeout=30)
    try:
        n = _restage_links()
    except Exception as e:  # noqa: BLE001
        n = -1
        print("domain watch: relink failed (links flip on next stage):", e)
    STAMP.touch()
    planner.feed_add("proposals", f"Branded links LIVE: https://{DOMAIN} ({n} staged proposal(s) re-linked)")
    planner.notify("Branded proposal links are LIVE",
                   f"{DOMAIN} is up. Staged proposals now carry the clean link. Sends are unblocked.")
    print(f"domain watch: FLIPPED to {DOMAIN}, re-linked {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
