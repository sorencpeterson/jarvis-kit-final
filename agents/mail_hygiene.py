#!/usr/bin/env python3
"""B86: unsubscribe-candidate report. Senders with 10+ unopened mail (from
store/sender_scores.json's unread_count, seeded by mail_sender_scores.py against the
real mailbox) and zero reply history -> monthly cleanup list, written to
store/mail_hygiene.md.

Cross-references store/mail_triage.jsonl when available: a sender already landing in
the `jobs` lane (ATS confirmations) is noted separately, not lumped in with genuine
newsletter/marketing noise, since unsubscribing from an ATS mid-application would be
counterproductive.

READ-ONLY (reads two local JSON/JSONL stores, no Gmail calls of its own — relies on
mail_sender_scores.py having been run first). Only write is store/mail_hygiene.md.

Run:  .venv/bin/python agents/mail_hygiene.py            # real report from real scores
      .venv/bin/python agents/mail_hygiene.py --threshold 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402
from runlog import track  # noqa: E402

SCORES = ROOT / "store" / "sender_scores.json"
TRIAGE = ROOT / "store" / "mail_triage.jsonl"
OUT = ROOT / "store" / "mail_hygiene.md"
DEFAULT_THRESHOLD = 10


def _load_scores() -> dict:
    try:
        return json.loads(SCORES.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _ats_senders_from_triage() -> set[str]:
    """Senders already routed to the jobs lane — excluded from the unsubscribe list
    even if they have unread pileups, since they're application infrastructure."""
    if not TRIAGE.exists():
        return set()
    out = set()
    for line in TRIAGE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("lane") == "jobs" and r.get("sender_email"):
            out.add(r["sender_email"])
    return out


def build_report(threshold: int = DEFAULT_THRESHOLD) -> dict:
    scores = _load_scores()
    ats = _ats_senders_from_triage()

    candidates = []
    for email, rec in scores.items():
        if rec.get("replied_count", 0) > 0:
            continue  # he's engaged with this sender before, never suggest unsubscribe
        if rec.get("unread_count", 0) < threshold:
            continue
        candidates.append({
            "email": email,
            "unread_count": rec["unread_count"],
            "is_ats": email in ats,
        })
    candidates.sort(key=lambda c: -c["unread_count"])

    genuine = [c for c in candidates if not c["is_ats"]]
    ats_excluded = [c for c in candidates if c["is_ats"]]

    lines = [
        "# Unsubscribe-candidate report (B86)",
        f"_Generated {now_iso()} · threshold: {threshold}+ unopened, zero reply history._",
        "",
        f"## Cleanup candidates ({len(genuine)})",
        "Senders you've never replied to, with a pile of unopened mail. Likely safe to",
        "unsubscribe or filter to archive-on-arrival.",
        "",
    ]
    if genuine:
        for c in genuine:
            lines.append(f"- **{c['email']}** — {c['unread_count']} unopened")
    else:
        lines.append("_None over the threshold this run._")

    lines += ["", f"## Excluded — ATS/job application senders ({len(ats_excluded)})",
              "High unread count here is normal (automated confirmations), not a signal",
              "to unsubscribe mid-application. Listed for visibility only.", ""]
    if ats_excluded:
        for c in ats_excluded:
            lines.append(f"- {c['email']} — {c['unread_count']} unopened (jobs lane)")
    else:
        lines.append("_None._")

    # atomic tmp + os.replace so a reader never catches the report half-written
    tmp = OUT.with_suffix(OUT.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, OUT)
    return {"genuine": len(genuine), "ats_excluded": len(ats_excluded), "threshold": threshold}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    args = ap.parse_args()

    with track("mail_hygiene"):
        result = build_report(args.threshold)

    print(f"mail_hygiene: {result['genuine']} cleanup candidate(s), "
          f"{result['ats_excluded']} ATS-excluded, threshold={result['threshold']} -> {OUT}")
    if result["genuine"]:
        planner.feed_add("agent", f"Unsubscribe report: {result['genuine']} candidate(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
