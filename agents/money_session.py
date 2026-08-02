#!/usr/bin/env python3
"""The Daily Money Session push (2026-07-12, STATE-OF-JARVIS fix #1).

THE strategic finding: the machine outruns its operator. $46,800 sits staged, content
sits approved, warm contacts sit unworked — every one correctly gated on [OWNER]'s click,
and nothing defends the ~15 minutes of clicking that converts it. This is that defense:
one push, every evening at 18:30, listing his 5 highest-value clicks ordered by dollar
value, sourced from LIVE stores (never invented). The push goes to HIS phone (ntfy via
planner.notify) — it is a nudge to him, not an outward send, so it ships ON.

Self-gating like evening_chain: run.sh calls it every 10-min tick; knob `money_session`
(default 1), window 18:30-18:59 local (right before the 19:00 evening chain), once per
day via store/.money-session-YYYY-MM-DD. Also writes store/money_session.json so the
dashboard/watch can render the same list.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import owner  # noqa: E402
import planner  # noqa: E402


def _now() -> datetime:
    return datetime.now().astimezone()


def _dmarc_missing(domain: str = "") -> bool:
    domain = domain or owner.get("site", "example.com")
    """True when the domain still has no DMARC record (his one pending 30-second click)."""
    try:
        out = subprocess.run(["dig", "+short", "TXT", f"_dmarc.{domain}"],
                             capture_output=True, text=True, timeout=8).stdout
        return "v=DMARC1" not in out
    except Exception:  # noqa: BLE001
        return False  # can't check -> don't nag


def build_clicks() -> list[str]:
    """His 5 highest-value clicks right now, from live stores. Each line is one action."""
    clicks: list[str] = []
    # 1-2: the two biggest staged proposals (the literal money)
    try:
        import proposal_factory as pf
        staged = sorted([x for x in pf.load_queue() if x.get("status") == "staged"],
                        key=lambda x: -(x.get("price") or 0))
        for p in staged[:2]:
            who = (p.get("company") or p.get("name") or "?")[:28]
            clicks.append(f"Send the {who} proposal (${(p.get('price') or 0):,}) - PROPOSALS drawer")
        if len(staged) > 2:
            clicks.append(f"...and {len(staged) - 2} more staged (${sum(x.get('price') or 0 for x in staged[2:]):,} total)")
    except Exception:  # noqa: BLE001
        pass
    # 3: pending reply drafts (a reply is the warmest money there is)
    try:
        import reply_watch as rw
        n = sum(1 for x in rw._load() if x.get("status") == "pending")
        if n:
            clicks.append(f"Approve {n} reply draft(s) - REPLIES drawer")
    except Exception:  # noqa: BLE001
        pass
    # 4: approved content waiting to schedule (approved != published; one push does it)
    try:
        import content_gen
        n = sum(1 for x in content_gen.load_posts() if x.get("status") == "approved")
        if n:
            clicks.append(f"Schedule {n} approved post(s) to LinkedIn - CONTENT drawer")
    except Exception:  # noqa: BLE001
        pass
    # 5: the top human item from the attention brain (his own overdue money todo)
    try:
        d = json.loads((ROOT / "store" / "attention.json").read_text())
        top = next((r for r in (d.get("ranked") or [])
                    if r.get("kind") in ("todo_overdue", "promise")), None)
        if top:
            clicks.append(f"{(top.get('label') or 'Top overdue item')[:60]}")
    except Exception:  # noqa: BLE001
        pass
    # 6: standing 30-second infra click, only while it's actually missing
    if _dmarc_missing():
        clicks.append("Add the DMARC record (30s: Cloudflare -> DNS -> TXT _dmarc, see PROPOSAL-DOMAIN-SETUP.md)")
    return clicks[:5]


def run() -> int:
    cfg = planner._config()
    if not int(cfg.get("money_session", 1) or 0):
        return 0
    now = _now()
    if not (now.hour == 18 and now.minute >= 30):
        return 0  # window is 18:30-18:59; silent otherwise
    stamp = ROOT / "store" / f".money-session-{now.strftime('%Y-%m-%d')}"
    if stamp.exists():
        return 0
    clicks = build_clicks()
    if not clicks:
        stamp.touch()  # nothing to click today — stamp so we stay silent
        print("money session: nothing pending, staying quiet")
        return 0
    body = "\n".join(f"{i}. {c}" for i, c in enumerate(clicks, 1))
    (ROOT / "store" / "money_session.json").write_text(
        json.dumps({"date": now.strftime("%Y-%m-%d"), "clicks": clicks}, ensure_ascii=False))
    # gate the once-a-day stamp on the push actually landing (2026-07-13 hunt): notify() returns
    # False on ntfy outage / no topic, and stamping anyway means the push is silently eaten and
    # never retried that evening. On failure, leave unstamped so the next tick re-fires.
    if not planner.notify(f"Money session: {len(clicks)} clicks, ~15 minutes", body):
        print("money session: push failed, not stamped (retries next tick)")
        return 0
    planner.feed_add("money", f"Money session pushed: {len(clicks)} clicks")
    stamp.touch()
    print(f"money session: pushed {len(clicks)} clicks")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
