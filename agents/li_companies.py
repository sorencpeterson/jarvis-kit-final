#!/usr/bin/env python3
"""Company-page follow list builder — A18.

Derives the list of companies worth [OWNER] following on LinkedIn (for feed
visibility into target-adjacent content) directly from targets already in the
queue — no new operator data needed, this is a pure derivation over existing
history via li_history's own company-extraction. Distinct from li_history.py's
company COOLDOWN tracking (that's "don't source from here for a while," this
is "follow this company's page so target-adjacent content surfaces in feed,"
the opposite instinct — a company can be BOTH cooled-down for outreach AND
worth following for visibility).

follow_list() returns companies ranked by how many distinct people from that
company have been queued (a proxy for "this company is a real cluster of
targets," per A18's own framing "targets' companies"). Never calls a follow
action itself (that's operator/UI work, out of headless scope) — this is the
BRIEF a human or operator would work from.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import networking  # noqa: E402
import li_history  # noqa: E402


def follow_list(min_targets: int = 1) -> list[dict]:
    """Companies extracted from the full queue history, ranked by distinct-
    target count. min_targets filters out one-off mentions (a company with
    just 1 target might not be worth a feed follow; default 1 keeps
    everything, callers can raise it for a tighter list)."""
    by_company: dict[str, set[str]] = {}
    for rec in networking.load_queue():
        company = li_history._company_from_target_text(rec.get("target", ""))
        if not company:
            continue
        key = company.lower()
        by_company.setdefault(key, set()).add(li_history._url_key(rec.get("url", "")))

    out = []
    for key, urls in by_company.items():
        n = len(urls)
        if n >= min_targets:
            out.append({"company": key, "target_count": n})
    out.sort(key=lambda c: c["target_count"], reverse=True)
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(follow_list(), indent=2))
