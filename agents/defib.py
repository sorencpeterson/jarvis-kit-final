#!/usr/bin/env python3
"""Stale-deal defibrillator (#58) — GHL pipeline deals nobody's touched in 2+ weeks
just sit there decaying silently. Nothing ever surfaces them or drafts the nudge
that would revive them, so they die of neglect instead of an actual no.

Pulls open deals from the local server's /api/deals (which already wraps the GHL
opportunities search), picks ones whose updatedAt is >14 days stale, and asks one
cheap CLI call to draft a short, direct 2-sentence revival message per deal in
[OWNER]'s voice. Drafts are logged, never sent — a todo tells [OWNER] they're ready
to review in the dashboard.

Read-only against GHL/the server; writes are store/revival_drafts.jsonl (append,
skip anything already drafted) + one store_lib todo per new draft + a feed_add.
Run standalone: .venv/bin/python agents/defib.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import append_todo, humanize, new_id, now_iso, secret  # noqa: E402
import planner  # noqa: E402

DRAFTS = ROOT / "store" / "revival_drafts.jsonl"
STALE_DAYS = 14
CAP = 5


def _get(path: str) -> dict:
    req = urllib.request.Request("http://127.0.0.1:8765" + path,
                                 headers={"X-Brain-Token": secret("brain_token")})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _already_drafted() -> set[str]:
    return {r.get("deal") for r in _read_jsonl(DRAFTS) if r.get("deal")}


def _stale_deals(deals: list[dict]) -> list[dict]:
    cutoff = datetime.now().astimezone() - timedelta(days=STALE_DAYS)
    out = []
    for d in deals:
        updated = d.get("updated") or ""
        try:
            ts = datetime.fromisoformat(updated).astimezone() if len(updated) > 8 else \
                datetime.strptime(updated, "%Y-%m-%d").astimezone()
        except ValueError:
            continue  # no usable timestamp -> skip rather than guess it's stale
        if ts < cutoff:
            out.append(d)
    return out


PROMPT = """[OWNER] runs [OWNER_COMPANY] (white-label website builds for agencies,
$1K+, 48-72h turnaround; also agency ops/fractional-COO help). This GHL pipeline deal
has gone quiet:

DEAL: %s
VALUE: $%s
LAST UPDATED: %s

Draft a short revival message [OWNER] could send to re-open the conversation. Exactly
2 sentences. Direct, punchy, no fluff, sounds like a real person texting a prospect,
not a marketing email. Use commas or periods for pauses, never a dash. Don't be
pushy or apologetic, just a genuine low-pressure check-in that gives them an easy
out. Output ONLY the 2 sentences, nothing else: no notes, no alternates, no
commentary about these instructions."""


def _first_message(raw: str) -> str:
    """Defensive parse: models occasionally break format and append meta-commentary
    or a second "corrected" attempt after the actual draft (seen in testing on the
    em-dash instruction specifically). Keep only the text before the first such
    break so a leaked aside never reaches [OWNER] as part of the draft."""
    text = (raw or "").strip()
    for marker in ("\n\n", "(Note", "(note", "Wait,", "Here's the corrected",
                  "Here's an alternative", "*("):
        i = text.find(marker)
        if i > 0:
            text = text[:i].strip()
    return text


def build_drafts() -> list[dict]:
    try:
        deals = _get("/api/deals").get("deals", [])
    except Exception:  # noqa: BLE001
        return []
    stale = _stale_deals(deals)
    covered = _already_drafted()
    out = []
    for d in stale:
        did = d.get("id")
        if not did or did in covered:
            continue
        name = d.get("name") or "?"
        value = d.get("value") or 0
        draft = planner._cli(PROMPT % (name, value, d.get("updated") or "unknown"),
                             timeout=90, feature="content")
        draft = humanize(_first_message(draft or ""))
        if not draft:
            continue
        out.append({"ts": now_iso(), "deal": did, "name": name, "value": value, "draft": draft})
        covered.add(did)
        if len(out) >= CAP:
            break
    return out


def main() -> int:
    drafts = build_drafts()
    if not drafts:
        print("defib: no new stale deals to revive")
        return 0
    DRAFTS.parent.mkdir(parents=True, exist_ok=True)
    with DRAFTS.open("a") as f:
        for rec in drafts:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    for rec in drafts:
        append_todo({
            "id": new_id("defib_" + rec["deal"]), "text": f"Revive stale deal: {rec['name']} (draft ready in dashboard)",
            "status": "inbox", "created": now_iso(), "source": "defib", "source_ref": rec["deal"],
            "project": None, "priority": 2, "scheduled_time": None, "duration_min": None,
            "gcal_event_id": None, "notes": None,
        })
    planner.feed_add("agent", f"Defibrillator: {len(drafts)} stale deal(s) revived with draft nudges")
    print(f"defib: drafted {len(drafts)} revival message(s) -> {DRAFTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
