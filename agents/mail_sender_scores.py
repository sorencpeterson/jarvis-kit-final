#!/usr/bin/env python3
"""B82: sender reputation memory. Seeds store/sender_scores.json from real mailbox
history (who he replies to = high, who piles up unread = low), then keeps learning
from what mail_brain.py observes on every classify pass (opens/replies inferred from
label state, archives inferred from absence on a later sync).

Score is a simple float, roughly -1..+1:
  +1.0  he has replied to this sender before (strongest signal)
  +0.3  sender shows up in his own contacts/threads he engages
   0.0  unknown / never seen
  -0.1  per unread message piling up from this sender, floor -0.8

READ-ONLY against Gmail (search + metadata only). Only write is
store/sender_scores.json. Safe to re-run; each run recomputes from a fresh mailbox
read rather than drifting via incremental deltas, since reputation is a slow-moving
signal (weekly re-seed is plenty — see B82 note in mail_brain.py for how it's used
per classify pass without re-scanning the whole mailbox every time).

Run:  .venv/bin/python agents/mail_sender_scores.py            # real seed run
      .venv/bin/python agents/mail_sender_scores.py --window 90 # narrower lookback
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", Path.home() / "Claude" / "gmail"):
    sys.path.insert(0, str(p))
import owner  # noqa: E402
from store_lib import now_iso, _flock  # noqa: E402
import planner  # noqa: E402
import gmail_api  # noqa: E402
from runlog import track  # noqa: E402

SCORES = ROOT / "store" / "sender_scores.json"
DEFAULT_WINDOW_DAYS = 180
SENT_SAMPLE = 150
UNREAD_SAMPLE = 250
UNREAD_PILEUP_THRESHOLD = 10

_ADDR_RE = re.compile(r"<([^>]+)>")


def _extract_email(addr: str) -> str:
    m = _ADDR_RE.search(addr or "")
    e = (m.group(1) if m else (addr or "")).lower().strip()
    return e


def _load() -> dict:
    try:
        return json.loads(SCORES.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    """Atomic full-file write (tmp + os.replace): mail_brain's get_score() reads this
    file on every classify pass, so a torn half-written JSON must be impossible."""
    SCORES.parent.mkdir(parents=True, exist_ok=True)
    tmp = SCORES.with_suffix(SCORES.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.replace(tmp, SCORES)


def seed(window_days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """Real mailbox scan. Returns the sender_scores dict written to disk (also
    returned so a caller / test can inspect it without re-reading the file)."""
    self_hint = owner.get("handle", "")

    # Signal 1: who he's replied to (sent mail's To header) -> strong positive.
    sent_ids = [m["id"] for m in gmail_api.search(f"in:sent newer_than:{window_days}d", SENT_SAMPLE)]
    sent_meta = gmail_api.get_messages_metadata(sent_ids, fields=("To",))
    replied_to = Counter(_extract_email(m.get("to", "")) for m in sent_meta)
    replied_to = {e: n for e, n in replied_to.items() if e and self_hint not in e}

    # Signal 2: unread pileups -> negative, scaled by count.
    unread_ids = [m["id"] for m in gmail_api.search(f"is:unread newer_than:{window_days}d", UNREAD_SAMPLE)]
    unread_meta = gmail_api.get_messages_metadata(unread_ids, fields=("From",))
    unread_from = Counter(_extract_email(m.get("from", "")) for m in unread_meta)
    unread_from = {e: n for e, n in unread_from.items() if e and self_hint not in e}

    # Read-modify-write under store_lib._flock: the load happens INSIDE the lock (not
    # before the Gmail fetches above, which take long enough for a concurrent writer's
    # manual VIP boost to land and get clobbered by our stale merge). Gmail I/O stays
    # outside the lock so it's held for milliseconds, not the whole mailbox scan.
    with _flock(SCORES):
        scores = dict(_load())  # keep any manually-added overrides (e.g. VIP boosts)
        for email, n in replied_to.items():
            base = min(1.0, 0.6 + 0.1 * min(n, 4))  # more replies -> a bit higher, capped
            existing = scores.get(email, {})
            prior = existing.get("score", 0.0)
            scores[email] = {
                # preserve `manual` (and any other hand-set field) across re-seeds --
                # this used to rebuild the dict from scratch each run, which kept the
                # ELEVATED score once (via max(base, prior)) but silently dropped the
                # `manual` key itself, so the NEXT re-seed no longer knew to protect it
                **existing,
                "score": round(max(base, prior), 3) if existing.get("manual") else round(base, 3),
                "replied_count": n,
                "unread_count": existing.get("unread_count", 0),
                "updated": now_iso(),
            }
        for email, n in unread_from.items():
            if email in scores and scores[email].get("manual"):
                continue  # never let pileup signal override a manual VIP entry
            prior = scores.get(email)
            if prior and prior.get("replied_count", 0) > 0:
                # someone he replies to who also has some unread isn't "low rep" —
                # just update the count, don't drag the score down.
                prior["unread_count"] = n
                prior["updated"] = now_iso()
                continue
            penalty = -0.1 * min(n, 8)  # floor at -0.8
            scores[email] = {
                "score": round(penalty, 3),
                "replied_count": 0,
                "unread_count": n,
                "updated": now_iso(),
            }
        _save(scores)
    return scores


def get_score(email: str) -> float:
    """Lookup for classify-pass callers (mail_brain.py). Unknown sender -> 0.0."""
    data = _load()
    rec = data.get((email or "").lower().strip())
    return rec.get("score", 0.0) if rec else 0.0


def is_low_reputation(email: str) -> bool:
    data = _load()
    rec = data.get((email or "").lower().strip())
    return bool(rec) and rec.get("unread_count", 0) >= UNREAD_PILEUP_THRESHOLD and rec.get("replied_count", 0) == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW_DAYS, help="lookback days")
    args = ap.parse_args()

    with track("mail_sender_scores"):
        scores = seed(args.window)

    high = sorted(((e, r["score"]) for e, r in scores.items() if r["score"] > 0), key=lambda x: -x[1])
    low = sorted(((e, r["score"]) for e, r in scores.items() if r["score"] < 0), key=lambda x: x[1])
    print(f"mail_sender_scores: {len(scores)} sender(s) scored from real mailbox "
          f"(window={args.window}d)")
    print(f"  top replied-to (high rep): {high[:5]}")
    print(f"  top unread-pileup (low rep): {low[:5]}")
    if scores:
        planner.feed_add("agent", f"Sender scores refreshed: {len(scores)} sender(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
