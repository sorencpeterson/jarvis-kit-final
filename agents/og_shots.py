#!/usr/bin/env python3
"""B22: mockup screenshot pipeline -- shoot each staged/sent proposal's rebuilt-homepage
mockup to a real PNG so link previews (email clients, iMessage) show the PROSPECT'S OWN
mockup, not a generic card.

For every queue record (proposal_factory.load_queue()) with status in (staged, sent) and
no og_done yet: shoot store/proposals/<pid>.mock.html -> store/og/<pid>-<token>.png (token
= proposal_factory.sig_for(pid)[:10], so the filename itself is the capability -- same
pattern as the /prop, /mock, /agree links), then mark the record {og_done: True,
og_file: "<pid>-<token>.png"} via proposal_factory.save().

Idempotent: records that already carry og_done are skipped, so reruns only pick up new
proposals. A shot that fails (missing mock.html, Playwright error, etc.) is skipped WITHOUT
setting og_done, so the next run retries it automatically -- no partial "done" state.

The actual rendering happens in tools/shoot_mockup.js (Playwright); this file's only job
is queue bookkeeping + invoking that script as a subprocess (Playwright itself lives under
the sibling playwright-project checkout, not in this repo's Python venv).

Run:  uv run python agents/og_shots.py            # all eligible, real run
      uv run python agents/og_shots.py --dry       # list what WOULD be shot, shoot nothing
      uv run python agents/og_shots.py --pid prop_20260703_a330bba4   # one proposal only
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import proposal_factory  # noqa: E402

OUT = ROOT / "store" / "og"
SHOOTER = ROOT / "tools" / "shoot_mockup.js"
PLAYWRIGHT_PROJECT = Path.home() / "Claude" / "playwright-project"
ELIGIBLE_STATUSES = ("staged", "sent")
SHOT_TIMEOUT_S = 45  # headless chromium launch + networkidle + 800ms settle, generous


def _token(pid: str) -> str:
    return proposal_factory.sig_for(pid)[:10]


def _og_name(pid: str) -> str:
    return f"{pid}-{_token(pid)}.png"


def eligible(records: list[dict], only_pid: str = "") -> list[dict]:
    out = []
    for r in records:
        pid = r.get("id") or ""
        if not pid:
            continue
        if only_pid and pid != only_pid:
            continue
        if not only_pid and r.get("status") not in ELIGIBLE_STATUSES:
            continue
        if r.get("og_done"):
            continue
        out.append(r)
    return out


def shoot_one(pid: str) -> tuple[bool, str]:
    """Runs tools/shoot_mockup.js against store/proposals/<pid>.mock.html.
    Returns (ok, message). Never raises -- callers loop over many proposals and one bad
    shot (a missing mock.html, a Playwright crash) must not kill the whole batch."""
    mock_html = ROOT / "store" / "proposals" / f"{pid}.mock.html"
    if not mock_html.exists():
        return False, f"no mockup html at {mock_html}"

    OUT.mkdir(parents=True, exist_ok=True)
    out_png = OUT / _og_name(pid)

    if not SHOOTER.exists():
        return False, f"shooter script missing: {SHOOTER}"
    if not PLAYWRIGHT_PROJECT.exists():
        return False, f"playwright-project not found at {PLAYWRIGHT_PROJECT}"

    try:
        # NODE_PATH, not cwd: Node's CommonJS require() resolves node_modules by walking up
        # from the SCRIPT FILE's own directory, not the process's cwd, so cwd=playwright-project
        # alone does not make require("@playwright/test") resolve (confirmed by hand -- the
        # cwd-only approach fails with "Cannot find module"). NODE_PATH is the fix that
        # actually works; see tools/shoot_mockup.js header for the same note.
        env = {**os.environ, "NODE_PATH": str(PLAYWRIGHT_PROJECT / "node_modules")}
        proc = subprocess.run(
            ["node", str(SHOOTER), str(mock_html), str(out_png)],
            cwd=str(PLAYWRIGHT_PROJECT), env=env,
            capture_output=True, text=True, timeout=SHOT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {SHOT_TIMEOUT_S}s"
    except OSError as e:  # e.g. node not on PATH
        return False, f"could not launch node: {e}"

    if proc.returncode != 0 or not out_png.exists():
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        msg = tail[-1] if tail else f"exit {proc.returncode}, no output file"
        return False, msg

    kb = out_png.stat().st_size / 1024
    warn = " (WARNING: >300KB budget)" if kb > 300 else ""
    return True, f"{out_png.name} ({kb:.1f}KB){warn}"


def run(dry: bool = False, only_pid: str = "") -> dict:
    records = proposal_factory.load_queue()
    targets = eligible(records, only_pid)
    if not targets:
        scope = f"pid {only_pid}" if only_pid else "staged/sent proposals without og_done"
        print(f"Nothing to shoot -- no {scope}.")
        return {"shot": 0, "failed": 0, "skipped": 0}

    print(f"{'[dry] ' if dry else ''}{len(targets)} proposal(s) eligible for og shots.")
    shot = failed = 0
    for r in targets:
        pid = r["id"]
        if dry:
            print(f"  would shoot {pid} -> store/og/{_og_name(pid)}")
            continue
        ok, msg = shoot_one(pid)
        if ok:
            proposal_factory.save({**r, "og_done": True, "og_file": _og_name(pid)})
            shot += 1
            print(f"  + {pid}: {msg}")
        else:
            failed += 1
            print(f"  ! {pid}: {msg} (og_done NOT set, will retry next run)")
    if not dry:
        print(f"\nDone -- {shot}/{len(targets)} shot, {failed} failed (will retry on next run).")
    return {"shot": shot, "failed": failed, "skipped": 0}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="list what would be shot, shoot nothing")
    ap.add_argument("--pid", default="", help="shoot only this one proposal id (ignores status/og_done gates)")
    a = ap.parse_args()
    run(dry=a.dry, only_pid=a.pid)
