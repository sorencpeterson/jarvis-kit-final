#!/usr/bin/env python3
"""Weekly networking digest — A17.

Writes store/li_digest.json: the sourced/sent/accepted/replied funnel for the
current ISO week. Deliberately does NOT edit agents/owner_report.py (out of
this lane's exclusive-files scope) — see the INTEGRATION CONTRACT at the
bottom of this docstring for the one line owner_report.py would need to read
this file, left for the owner_report.py owner to wire in.

"accepted"/"replied" counts are honest about the [E] boundary: today this
system has no LIVE detection of LinkedIn acceptances or replies (that's
store/li_accepted.jsonl, operator-fed, per li_conveyor.py's contract, and
there is no equivalent replies store yet at all). Rather than fake a number,
those two funnel stages report 0 with an explicit "no data source yet" note
until li_accepted.jsonl (or a future li_replied.jsonl) has real rows. sourced/
sent are real today (networking.load_queue()'s own created/done counts).

INTEGRATION CONTRACT for owner_report.py (documented here, not applied there):
  Add ONE line to agents/owner_report.py's _build_report() (or wherever the
  weekly sections are assembled), something like:

      li_digest = json.loads((ROOT / "store" / "li_digest.json").read_text())
      lines.append(f"linkedin: sourced {li_digest['sourced']}, sent "
                    f"{li_digest['sent']}, accepted {li_digest['accepted']}, "
                    f"replied {li_digest['replied']}")

  Wrapped in the same try/except OSError pattern owner_report.py already uses
  elsewhere (e.g. _stuck_deals()) so a missing/stale li_digest.json degrades
  gracefully rather than breaking the Monday report.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import networking  # noqa: E402
import li_conveyor  # noqa: E402

OUT = ROOT / "store" / "li_digest.json"


def _iso_week(d: date | None = None) -> str:
    d = d or date.today()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _in_week(ts: str, week: str) -> bool:
    if not ts:
        return False
    try:
        d = date.fromisoformat(ts[:10])
    except ValueError:
        return False
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}" == week


def build_digest(week: str | None = None) -> dict:
    week = week or _iso_week()
    q = networking.load_queue()

    sourced_this_week = sum(1 for r in q if _in_week(r.get("created", ""), week))
    sent_this_week = sum(1 for r in q if r.get("status") == "done"
                          and _in_week(r.get("acted_at", r.get("created", "")), week))

    by_kind_sourced = Counter(r.get("kind") for r in q if _in_week(r.get("created", ""), week))
    by_kind_sent = Counter(r.get("kind") for r in q if r.get("status") == "done"
                            and _in_week(r.get("acted_at", r.get("created", "")), week))

    accepted_rows = li_conveyor.load_accepted()
    accepted_this_week = sum(1 for r in accepted_rows if _in_week(r.get("accepted_at", ""), week))
    has_accepted_source = bool(accepted_rows)

    digest = {
        "week": week,
        "generated": now_iso(),
        "sourced": sourced_this_week,
        "sent": sent_this_week,
        "by_kind_sourced": dict(by_kind_sourced),
        "by_kind_sent": dict(by_kind_sent),
        "accepted": accepted_this_week,
        "accepted_note": None if has_accepted_source else "no data source yet (store/li_accepted.jsonl empty, operator-fed)",
        "replied": 0,
        "replied_note": "no data source yet (no inbound-DM detection exists in this system yet)",
        "queue_depth_pending": sum(1 for r in q if r.get("status") == "pending"),
    }
    return digest


def run() -> dict:
    digest = build_digest()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(digest, indent=2, ensure_ascii=False))
    print(f"li_digest: week {digest['week']}: sourced {digest['sourced']}, sent {digest['sent']}, "
          f"accepted {digest['accepted']}, replied {digest['replied']} -> {OUT}")
    return digest


if __name__ == "__main__":
    run()
