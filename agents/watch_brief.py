#!/usr/bin/env python3
"""Watch brief: render the JARVIS wrist card and carry it to the Apple Watch.

Tier-1 of JARVIS-on-the-wrist (until the native complications app lands): a
Photos watch face pointed at the "JARVIS" album shows this card behind the time.

WHAT: fetches the scoped /pub/watch feed (same contract the watchOS app will
      use; no second store-parsing path to drift), renders an 820x1004 card
      (2x the Ultra's 410x502 glass) via the playwright CLI, imports it into
      Photos album "JARVIS" (iCloud Photos carries it to the watch face), and
      pushes ntfy so the wrist buzzes when the card is fresh. Top third of the
      card stays quiet so the watch clock overlays cleanly.
WHEN: morning chain, right after one_thing.py (needs attention + one_thing
      fresh). Safe to rerun any time; the PNG just overwrites.
RAILS: read-only against the brain (a signed GET). Writes only
      store/watch_brief.{html,png} and the Photos album. The push is a
      self-notification to [OWNER]'s own phone and stays CONTENT-FREE unless
      config push_full is true (the config _note explains the trade). Photos
      import needs a one-time macOS Automation approval; a denial degrades to
      a printed reason, never a crash (morning-chain safety). NOTE the album
      accumulates one card per day (Photos AppleScript cannot delete media);
      pruning is a 10-second Sunday-review step.

Run: .venv/bin/python agents/watch_brief.py [--dry-run] [--no-photos] [--no-push]
"""
from __future__ import annotations

import argparse
import html as _html
import os
import json
import subprocess
import sys
import urllib.request
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import LOCAL_TZ, sign_secret  # noqa: E402
import planner  # noqa: E402

STORE = ROOT / "store"
OUT_HTML = STORE / "watch_brief.html"
OUT_PNG = STORE / "watch_brief.png"
PLAYWRIGHT = Path(os.environ.get("PLAYWRIGHT_DIR") or (ROOT / "playwright-project")) / "node_modules" / ".bin" / "playwright"
ALBUM = "JARVIS"

_PAGE = Template("""<!doctype html><meta charset="utf-8">
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { width:820px; height:1004px; overflow:hidden; color:#dfe9f5;
         font-family:-apple-system,'SF Pro Display',Helvetica,Arial,sans-serif;
         background:radial-gradient(120% 90% at 50% 0%, #0d1d33 0%, #060d1a 55%, #03060d 100%); }
  .wrap { padding:36px 44px; height:100%; display:flex; flex-direction:column; }
  .clockpad { height:250px; display:flex; flex-direction:column; justify-content:flex-end;
              align-items:center; gap:6px; }
  .wordmark { letter-spacing:14px; font-size:26px; color:#3d5f86; font-weight:600; }
  .date { font-size:22px; color:#5a7396; letter-spacing:2px; }
  .money { margin-top:26px; text-align:center; }
  .usd { font-size:118px; font-weight:700; color:$usd_color; letter-spacing:-2px;
         text-shadow:0 0 34px $usd_glow; }
  .moneysub { font-size:26px; color:#8fa4c0; margin-top:2px; }
  .bar { margin:26px 10px 0; height:16px; border-radius:8px; background:#122036;
         overflow:hidden; border:1px solid #1d3148; }
  .fill { height:100%; width:$pct%; border-radius:8px;
          background:linear-gradient(90deg,#2ea8ff,#65e0c4); }
  .barlab { display:flex; justify-content:space-between; font-size:22px; color:#7d92b0;
            margin:8px 12px 0; }
  .card { margin-top:26px; background:rgba(18,32,54,.55); border:1px solid #1d3148;
          border-radius:18px; padding:20px 24px; }
  .k { font-size:20px; letter-spacing:4px; color:#4d94d6; margin-bottom:8px; }
  .one { font-size:30px; line-height:1.25; color:#eaf2fb; }
  .att { font-size:25px; line-height:1.5; color:#a9bcd6; }
  .att b { color:#d7e4f2; font-weight:600; }
  .foot { margin-top:auto; display:flex; justify-content:space-between;
          font-size:22px; color:#54677c; }
  .dial { color:#ffd166; }
</style>
<div class="wrap">
  <div class="clockpad"><div class="wordmark">J.A.R.V.I.S.</div><div class="date">$date</div></div>
  <div class="money">
    <div class="usd">$usd</div>
    <div class="moneysub">$moneysub</div>
    <div class="bar"><div class="fill"></div></div>
    <div class="barlab"><span>won $won</span><span>need $need/day</span><span>target $target</span></div>
  </div>
  <div class="card"><div class="k">ONE THING</div><div class="one">$one</div></div>
  <div class="card"><div class="k">ATTENTION</div><div class="att">$att</div></div>
  <div class="foot"><span class="dial">DIAL $dial_q queued · $dial_t today</span><span>$ts</span></div>
</div>
""")


def _fetch() -> dict | None:
    """One contract: the same scoped feed the watch app will consume."""
    import hashlib
    import hmac
    sig = hmac.new(sign_secret().encode(), b"watch:v1", hashlib.sha256).hexdigest()[:24]
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:8765/pub/watch?sig={sig}", timeout=8) as r:
            return json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        print(f"watch_brief: /pub/watch unreachable ({e}); is the brain up?")
        return None


def _render(data: dict) -> Path | None:
    from datetime import datetime
    m = data.get("money") or {}
    hot = (m.get("sev") == "hot")
    target = int(m.get("target") or 0)
    won = int(m.get("won_mtd") or 0)
    pct = min(100, round(100 * won / target)) if target else 0
    att_rows = data.get("attention") or []
    att = "<br>".join(
        f"<b>{i+1}.</b> {_html.escape(a.get('label') or '')}" for i, a in enumerate(att_rows)
    ) or "clear"
    one = _html.escape((data.get("one_thing") or "").removeprefix("If you do one thing today: ")) or "standby"
    dial = data.get("dial") or {}
    now = datetime.now(LOCAL_TZ)
    page = _PAGE.substitute(
        usd=f"${int(m.get('staged_usd') or 0):,}",
        usd_color="#ff6b5e" if hot else "#ffd166",
        usd_glow="rgba(255,107,94,.35)" if hot else "rgba(255,209,102,.30)",
        moneysub=_html.escape(
            f"{int(m.get('staged_n') or 0)} staged · unsent · oldest {m.get('oldest_d') or 0:.0f}d"),
        pct=pct, won=f"{won:,}", target=f"{target:,}",
        need=f"{int(m.get('need_day') or 0):,}",
        one=one, att=att,
        dial_q=int(dial.get("queued") or 0), dial_t=int(dial.get("today") or 0),
        date=now.strftime("%a %b %-d").upper(), ts=now.strftime("%H:%M"),
    )
    OUT_HTML.write_text(page)
    if not PLAYWRIGHT.exists():
        print(f"watch_brief: playwright CLI missing at {PLAYWRIGHT}; card HTML written, no PNG")
        return None
    r = subprocess.run(
        [str(PLAYWRIGHT), "screenshot", "--viewport-size=820,1004",
         "--wait-for-timeout=500", f"file://{OUT_HTML}", str(OUT_PNG)],
        capture_output=True, text=True, timeout=180)
    if r.returncode != 0 or not OUT_PNG.exists():
        print(f"watch_brief: screenshot failed: {(r.stderr or r.stdout).strip()[:300]}")
        return None
    return OUT_PNG


def _photos_import(png: Path) -> str:
    """Into album JARVIS via Photos AppleScript. Returns '' on success, reason on failure.
    Quits Photos afterward only if this run launched it."""
    was_running = subprocess.run(["pgrep", "-x", "Photos"], capture_output=True).returncode == 0
    script = (
        f'tell application "Photos"\n'
        f'  if not (exists album "{ALBUM}") then make new album named "{ALBUM}"\n'
        f'  import (POSIX file "{png}") into album "{ALBUM}" skip check duplicates true\n'
        f'end tell'
    )
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return "Photos import timed out"
    if r.returncode == 0 and not was_running:
        subprocess.run(["osascript", "-e", 'tell application "Photos" to quit'],
                       capture_output=True, timeout=60)
    return "" if r.returncode == 0 else (r.stderr.strip()[:200] or "osascript failed")


def run(dry_run: bool = False, photos: bool = True, push: bool = True) -> int:
    data = _fetch()
    if not data:
        return 1
    png = _render(data)
    print(f"watch_brief: card rendered -> {png or OUT_HTML}")
    if dry_run:
        print("[dry-run] no Photos import, no push")
        return 0
    if png and photos:
        err = _photos_import(png)
        print(f"watch_brief: Photos album '{ALBUM}' updated" if not err
              else f"watch_brief: Photos import skipped ({err})")
    if push:
        full = bool(planner._config().get("push_full"))
        line = (data.get("money") or {}).get("line") or ""
        dial = data.get("dial") or {}
        body = (f"{line} Dial: {dial.get('queued', 0)} queued." if full
                else "Wrist brief fresh · JARVIS album updated")
        planner.notify("JARVIS wrist brief", body, tags="watch")
        print("watch_brief: pushed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Render + sync the JARVIS Apple Watch brief card")
    ap.add_argument("--dry-run", action="store_true", help="render only; no Photos, no push")
    ap.add_argument("--no-photos", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()
    if args.dry_run:
        return run(dry_run=True)
    from runlog import track
    with track("watch_brief"):
        return run(dry_run=False, photos=not args.no_photos, push=not args.no_push)


if __name__ == "__main__":
    raise SystemExit(main())
