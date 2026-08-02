#!/usr/bin/env python3
"""Auto-triage agent: classify untriaged inbox todos (project + priority).

One cheap Haiku call classifies the whole inbox at once, then writes the tags back
to the store. Safe + idempotent — only touches inbox items missing a project.

Run:  uv run python agents/triage_inbox.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "dashboard"):
    sys.path.insert(0, str(p))
from store_lib import append_todo, compact, load_todos  # noqa: E402
import planner  # noqa: E402

PROJECTS = ("ghl-dbr", "agency-cold-outreach", "web-automation")

PROMPT = """Classify each of [OWNER]'s inbox to-dos. For each, choose:
- project: one of ghl-dbr (GHL campaigns/CRM/email-SMS), agency-cold-outreach (agency lead gen/enrichment), web-automation (Upwork/LinkedIn/scraping/build work), or null (personal/other)
- priority: 1 (high/urgent), 2 (normal), 3 (low)
Warm-channel / revenue-driving work ranks higher than cold outreach.

Output ONLY a JSON array, one object per item: [{"id":"...","project":"...|null","priority":1}]
No prose.

ITEMS:
%s"""


def main() -> int:
    todos = load_todos()
    inbox = [t for t in todos if t["status"] == "inbox" and not t.get("project")]
    if not inbox:
        print("Nothing to triage.")
        return 0

    listing = "\n".join(f'- id={t["id"]}: {t["text"]}' for t in inbox)
    data = planner._cli_json(PROMPT % listing)
    if not isinstance(data, list):
        print("Triage agent returned nothing usable.")
        return 1

    by_id = {t["id"]: t for t in inbox}
    n = 0
    for r in data:
        t = by_id.get(r.get("id"))
        if not t:
            continue
        upd = dict(t)
        proj = r.get("project")
        upd["project"] = proj if proj in PROJECTS else None
        pr = r.get("priority")
        upd["priority"] = pr if pr in (1, 2, 3) else 2
        append_todo(upd)
        n += 1
        print(f"  · {t['text'][:48]:<48} -> {upd['project'] or '—'} / P{upd['priority']}")
    compact()
    planner.feed_add("agent", f"Auto-triaged {n} inbox item(s)")
    print(f"Triaged {n}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
