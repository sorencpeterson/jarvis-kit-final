#!/usr/bin/env python3
"""#166 [E]: per-niche subject-line rotation pools + win tracking. Read-only against GHL;
writes only to store/subject_stats.json (local scaffold, no GHL writes at all).

Investigation (do this before trusting anything below): the gohighlevel-cli
CAPABILITY-AUDIT.md lists "Emails (campaigns) — read: list" and the underlying
endpoint (`GET /emails/schedule?locationId=<loc>`, live-probed against [OWNER]'s own
account) genuinely returns campaign objects with `name`, `subject`, `status`,
`hasTracking` -- but NO opens/clicks/sent/delivered/recipient-count field anywhere in
the payload (checked the full key set across the response). That endpoint also belongs
to GHL's separate "Email Marketing / Bulk Campaigns" product; the cold engine's actual
sends happen INSIDE tag-triggered workflows (wl-cold / wl-webfix), which is a different
system and wouldn't be listed there at all even if stats existed.

Conclusion: subject TEXT is readable (pull_subjects_from_ghl(), real, works today).
Subject-level WIN TRACKING (opens by subject family) is NOT exposed by any endpoint
this toolkit can reach -> marked [E]. What's built: the local pool scaffold (subjects
grouped by niche, a stub for recording an outcome once a win signal exists), a puller
that's real today, and a synthetic win-recording path fixture-tested end to end so the
day a stats source shows up (webhook event, a GHL analytics endpoint, or manual entry
from the GHL UI) this file only needs its one puller function swapped.

Usage:
  subject_stats.py                 # pull real GHL email schedules, seed the pool, print a summary
  subject_stats.py --niche agency --subject "..." --outcome open   # manually record a win signal
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import ghl_social  # noqa: E402

STATS_FILE = ROOT / "store" / "subject_stats.json"

# Starter pools per niche/campaign, used until real subjects get pulled or [OWNER] adds
# his own. Matches the niches reply_watch._niche_for() and cold_feeder's CAMPAIGNS
# already use, so this plugs into the existing vocabulary rather than inventing a new one.
DEFAULT_POOLS = {
    "wl": [
        "quick math on your web builds",
        "overflow dev capacity for {company}",
        "white-label sites, 48-72 hrs",
    ],
    "webfix": [
        "your site's been down for a bit",
        "found a few things on {company}'s site",
        "quick note on {company}.com",
    ],
}


def _loc() -> str:
    try:
        for line in (ghl_social.GHL / ".env").read_text().splitlines():
            if line.startswith("GHL_LOCATION_ID="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _load() -> dict:
    try:
        return json.loads(STATS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {"pools": {}, "outcomes": [], "updated": ""}


def _save(data: dict):
    STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data["updated"] = now_iso()
    STATS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def pull_subjects_from_ghl() -> list[dict]:
    """REAL and working: GET /emails/schedule, returns [{"name","subject","status"}].
    This is GHL's bulk-campaign email list, not the workflow-embedded cold sends, so
    treat this as a source of subject-line INSPIRATION (what [OWNER] has written before)
    rather than a live feed of what the cold workflows are currently sending."""
    loc = _loc()
    if not loc:
        return []
    out = ghl_social._api(["GET", f"/emails/schedule?locationId={loc}"])
    try:
        j = json.loads(out[out.find("{"):])
    except (ValueError, json.JSONDecodeError):
        return []
    return [{"name": s.get("name", ""), "subject": s.get("subject", ""), "status": s.get("status", "")}
            for s in j.get("schedules", []) if s.get("subject")]


def pull_stats_from_ghl_STUB(subject: str) -> dict | None:
    """[E] STUB. No endpoint in this toolkit's reach exposes open/click counts per
    subject or per campaign (see module docstring). Returns None always, on purpose,
    so callers fall back to the manual/webhook recording path below. If GHL ever adds
    a real analytics endpoint (or #176's webhook receiver starts forwarding email-open
    events), replace this function's body — nothing else in this file needs to change,
    record_outcome() already writes to the same schema this WOULD populate."""
    return None


def record_outcome(niche: str, subject: str, outcome: str):
    """outcome: 'open' | 'no_open' | 'reply' | 'bounce'. Manual for now ([OWNER] eyeballing
    GHL's UI open counts, or a future real source) until pull_stats_from_ghl_STUB has a
    real implementation."""
    data = _load()
    data.setdefault("outcomes", []).append(
        {"ts": now_iso(), "niche": niche, "subject": subject, "outcome": outcome})
    _save(data)


def win_rates() -> dict[str, dict[str, float]]:
    """{niche: {subject: open_rate}} from whatever outcomes have been recorded so far.
    Empty/near-empty until real data accumulates — that's expected and fine, it's a
    scaffold, not a claim of statistical significance."""
    data = _load()
    by_subject: dict[str, dict[str, list]] = {}
    for o in data.get("outcomes", []):
        key = (o.get("niche", ""), o.get("subject", ""))
        by_subject.setdefault(key, []).append(o.get("outcome"))
    out: dict[str, dict[str, float]] = {}
    for (niche, subject), outcomes in by_subject.items():
        n = len(outcomes)
        opens = sum(1 for o in outcomes if o in ("open", "reply"))
        out.setdefault(niche, {})[subject] = round(opens / n, 3) if n else 0.0
    return out


def run() -> dict:
    data = _load()
    pools = data.get("pools") or {}
    for niche, subjects in DEFAULT_POOLS.items():
        pools.setdefault(niche, subjects)
    ghl_subjects = pull_subjects_from_ghl()
    if ghl_subjects:
        pools.setdefault("ghl_history", [])
        seen = set(pools["ghl_history"])
        for s in ghl_subjects:
            if s["subject"] and s["subject"] not in seen:
                pools["ghl_history"].append(s["subject"])
                seen.add(s["subject"])
    data["pools"] = pools
    _save(data)
    print(f"subject_stats: {sum(len(v) for v in pools.values())} subject line(s) across "
          f"{len(pools)} pool(s) ({len(ghl_subjects)} pulled from GHL email schedules)")
    rates = win_rates()
    if rates:
        print("win rates so far:", json.dumps(rates, indent=2))
    else:
        print("win rates: none recorded yet [E] — no GHL stats endpoint exists; use "
              "--niche/--subject/--outcome to record manually, or wire a real source "
              "into pull_stats_from_ghl_STUB() once one exists")
    return data


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--niche", default="")
    ap.add_argument("--subject", default="")
    ap.add_argument("--outcome", choices=("open", "no_open", "reply", "bounce"), default="")
    args = ap.parse_args()
    if args.niche and args.subject and args.outcome:
        record_outcome(args.niche, args.subject, args.outcome)
        print(f"recorded: [{args.niche}] \"{args.subject}\" -> {args.outcome}")
    else:
        run()
