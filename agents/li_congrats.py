#!/usr/bin/env python3
"""Title-change congrats machinery — A63. [E]: needs operator-captured
before/after headline snapshots; this file is the fully-built BRAIN side,
waiting on that data.

CONTRACT (operator side, not yet built into any brief — documented here for
whoever builds the periodic connections-scan brief): a "check my connections
for title changes" operator pass would need to periodically snapshot each
1st-degree connection's current headline and diff it against the last-seen
value, appending a row to store/li_title_changes.jsonl whenever a diff is
detected:

    {"url": "<profile url>", "name": "...", "old_headline": "...",
     "new_headline": "...", "detected_at": "<ISO ts>"}

That store doesn't exist yet (0 rows possible without a snapshot-diffing
operator pass this lane cannot build — it requires PERIODIC re-visits to
every connection's profile, which is real LinkedIn browsing time/cost this
lane doesn't control). Same [E] pattern as li_conveyor.py's li_accepted.jsonl:
this file builds the reader + drafting machinery completely, so the moment
that data exists, congrats drafts start flowing with zero further code
changes needed here.

A "trigger" is only a genuine promotion/role-change (Founder -> CEO, "at
Acme" -> "at BiggerCo", etc), not every cosmetic headline edit (added an
emoji, reworded a tagline) — _looks_like_role_change() applies a light
heuristic filter so this doesn't draft a "congrats!" for someone who just
added a pipe-separated tagline.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import humanize  # noqa: E402
import planner  # noqa: E402
import li_quality  # noqa: E402

TITLE_CHANGES = ROOT / "store" / "li_title_changes.jsonl"
CONGRATS_DRAFTED = ROOT / "store" / "li_congrats_drafted.jsonl"

# Words that, if they appear in the DIFF between old/new headline, suggest a
# real role/seniority change worth congratulating — vs. a cosmetic tagline edit.
_ROLE_SIGNAL_WORDS = re.compile(
    r"\b(founder|co-founder|owner|ceo|coo|cfo|cto|president|partner|principal|"
    r"director|vp|vice president|head of|managing|promoted|now at|joined)\b",
    re.IGNORECASE,
)


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


def looks_like_role_change(old_headline: str, new_headline: str) -> bool:
    """Light heuristic: a real trigger has a role-signal word appearing in the
    NEW headline that either wasn't in the old one, or the whole headline
    structure changed significantly (not just word-order/emoji noise)."""
    old, new = (old_headline or "").strip(), (new_headline or "").strip()
    if not old or not new or old == new:
        return False
    old_roles = set(m.lower() for m in _ROLE_SIGNAL_WORDS.findall(old))
    new_roles = set(m.lower() for m in _ROLE_SIGNAL_WORDS.findall(new))
    new_only = new_roles - old_roles
    if new_only:
        return True
    # fallback: a big structural change (company name after @ changed) even
    # without a role-word delta still counts (e.g. "Founder @ A" -> "Founder @ B")
    old_company = re.search(r"@\s*([\w& -]{2,40})", old)
    new_company = re.search(r"@\s*([\w& -]{2,40})", new)
    if old_company and new_company and old_company.group(1).strip().lower() != new_company.group(1).strip().lower():
        return True
    return False


def _already_drafted() -> set[str]:
    return {r.get("url") for r in _read_jsonl(CONGRATS_DRAFTED) if r.get("url")}


CONGRATS_PROMPT = """You are [OWNER]. This LinkedIn connection just changed their headline in a
way that looks like a real role change or promotion. Write ONE short, genuine
congrats message, no fluff, no em-dashes, no emojis, under 200 characters, NOT a
pitch. Reference the specific change if it's clear from the headlines below.

OLD headline: %s
NEW headline: %s

Return ONLY a JSON object: {"draft": "..."}"""


def draft_congrats(old_headline: str, new_headline: str) -> str:
    out = planner._cli_json(CONGRATS_PROMPT % (old_headline, new_headline), timeout=80, feature="networking")
    if isinstance(out, dict):
        return humanize((out.get("draft") or "").strip())
    return ""


def find_pending_congrats_triggers() -> list[dict]:
    """Rows in li_title_changes.jsonl that look like a real role change AND
    haven't already had a congrats drafted. Read-only, no LLM call."""
    changes = _read_jsonl(TITLE_CHANGES)
    done = _already_drafted()
    out = []
    for c in changes:
        if c.get("url") in done:
            continue
        if looks_like_role_change(c.get("old_headline", ""), c.get("new_headline", "")):
            out.append(c)
    return out


def run(dry: bool = False) -> dict:
    """[E]: returns an honest empty-state note if store/li_title_changes.jsonl
    doesn't exist / has no rows yet (no operator snapshot-diff pass has ever
    run). dry=True: identify triggers, call no LLM, draft nothing."""
    if not TITLE_CHANGES.exists() or not _read_jsonl(TITLE_CHANGES):
        return {"triggers": 0, "note": "no store/li_title_changes.jsonl rows yet "
                 "(no operator snapshot-diff pass exists — this is the [E] gap; "
                 "machinery is ready, waiting on a periodic connections-scan brief)"}

    triggers = find_pending_congrats_triggers()
    if dry:
        return {"triggers": len(triggers), "would_draft": [t.get("url") for t in triggers]}

    drafted = []
    for t in triggers:
        draft = draft_congrats(t.get("old_headline", ""), t.get("new_headline", ""))
        v = li_quality.validate_draft(draft, kind="dm", first_touch=True, name=t.get("name", ""))
        if not v["ok"]:
            continue
        TITLE_CHANGES.parent.mkdir(parents=True, exist_ok=True)
        with CONGRATS_DRAFTED.open("a") as f:
            f.write(json.dumps({"url": t.get("url"), "draft": v["text"], "ts": t.get("detected_at", "")},
                                ensure_ascii=False) + "\n")
        drafted.append({"url": t.get("url"), "name": t.get("name"), "draft": v["text"]})
    return {"triggers": len(triggers), "drafted": drafted}


if __name__ == "__main__":
    print(json.dumps(run(dry=True), indent=2, ensure_ascii=False))
