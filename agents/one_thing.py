#!/usr/bin/env python3
"""One-thing pusher: attention.py computes the ranked list, nothing SAYS it.
This agent picks the single highest-leverage item, biased toward cash, and
pushes ONE line to [OWNER]'s phone: "If you do one thing today: <action>".

WHAT: reads store/attention.json (written by agents/attention.py: ranked[] +
      top_line), re-weights money kinds (proposal/reply/email/signed) by
      MONEY_MULT so a $3,500 staged proposal beats a pile of job applications
      at a slightly higher raw score, then pushes the winner via planner.notify
      and logs it to the feed.
WHEN: morning chain, right after attention.py. Idempotent per day via a
      store/.one_thing_sent-<date> sentinel; a rerun the same day is a no-op
      unless --force.
RAILS: read-only against attention.json. Only writes are the day sentinel and
      the feed line. The push is a self-notification (ntfy to [OWNER]'s own
      phone), never outward to a prospect. --dry-run computes and prints only.

Run: .venv/bin/python agents/one_thing.py [--dry-run] [--force]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import LOCAL_TZ, humanize  # noqa: E402
import planner  # noqa: E402

# ---- tunables ----
ATTENTION = ROOT / "store" / "attention.json"
STORE_DIR = ROOT / "store"  # sentinel files live here; tests point this at tmp
MONEY_KINDS = {"proposal", "reply", "email", "signed"}  # cash-adjacent kinds
MONEY_MULT = 1.25  # money kinds win ties and near-ties against busywork kinds


def _today() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


def _sentinel(day: str) -> Path:
    return STORE_DIR / f".one_thing_sent-{day}"


def _load_ranked() -> list[dict]:
    """attention.json's ranked list; missing/broken file is a valid fresh-install
    state, never a crash."""
    try:
        data = json.loads(ATTENTION.read_text())
        return list(data.get("ranked") or [])
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def pick(ranked: list[dict]) -> dict | None:
    """The one item: highest score after the money multiplier. Ties keep the
    attention router's own order (ranked comes in sorted desc)."""
    best, best_score = None, float("-inf")
    for item in ranked:
        try:
            score = float(item.get("score") or 0)
        except (TypeError, ValueError):
            continue
        if item.get("kind") in MONEY_KINDS:
            score *= MONEY_MULT
        if score > best_score:
            best, best_score = item, score
    return best


def action_line(item: dict) -> str:
    """Turn a ranked item into an imperative action, not a status report."""
    kind = item.get("kind") or ""
    label = item.get("label") or "the top item"
    rest = label.split(": ", 1)[1] if ": " in label else label
    why = item.get("why") or ""
    if kind == "proposal":
        tier = ""
        for tok in why.replace(",", " ").split():
            if tok.startswith("$"):
                tier = f" ({tok} tier)"
                break
        return f"send the {rest} proposal{tier}"
    if kind in ("reply", "email"):
        return f"answer {rest}"
    if kind == "todo_overdue":
        return f"close the overdue item: {rest}"
    if kind == "jobs_manual":
        return f"clear the manual job-application pile ({why})"
    if kind == "linkedin_queue":
        return f"approve the LinkedIn queue ({why})"
    return label


def run(dry_run: bool = False, force: bool = False) -> int:
    day = _today()
    mark = _sentinel(day)
    if mark.exists() and not force and not dry_run:
        print(f"one_thing: already pushed today ({day}), skipping (use --force)")
        return 0

    ranked = _load_ranked()
    if not ranked:
        print("one_thing: attention.json missing or empty, nothing to push")
        return 0

    item = pick(ranked)
    if not item:
        print("one_thing: no scoreable items, nothing to push")
        return 0

    action = humanize(action_line(item))
    body = f"If you do one thing today: {action}"
    print(f"one_thing: picked [{item.get('score')}] {item.get('kind')} {item.get('label')}")
    print(body)

    if dry_run:
        print("[dry-run] no push, no state write")
        return 0

    planner.notify("One thing today", body, tags="dart")
    try:
        planner.feed_add("agent", "One thing today", body[:140])
    except Exception:  # noqa: BLE001
        pass
    # sentinel for today + sweep stale ones so store/ doesn't collect litter
    try:
        mark.write_text(body)
        for old in STORE_DIR.glob(".one_thing_sent-*"):
            if old.name != mark.name:
                old.unlink(missing_ok=True)
    except OSError:
        pass
    print("one_thing: pushed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Push the single highest-leverage action of the day")
    ap.add_argument("--dry-run", action="store_true", help="compute and print, no push, no state write")
    ap.add_argument("--force", action="store_true", help="push even if already sent today")
    args = ap.parse_args()
    if args.dry_run:
        return run(dry_run=True, force=args.force)
    from runlog import track
    with track("one_thing"):
        return run(dry_run=False, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
