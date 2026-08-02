#!/usr/bin/env python3
"""Draft context helpers for reply_watch.py: C166 (last-5-messages context window)
and C167 (objection-sequence detection across turns).

C166: reply_watch.py's CLASSIFY prompt used to see only the single latest inbound
line per conversation (c.get('lastMessageBody')). That's the whole picture for a
first reply but loses the thread on turn 3+ ("wait, didn't they already ask this?").
fetch_context() pulls the real message history for one conversation via GHL's
GET /conversations/{id}/messages and formats the last N (default 5) turns, both
directions, into one block the LLM prompt can read directly.

Cost discipline: GHL conversation-message fetches are a real API call per
conversation, so reply_watch.py must cap this at ONE fetch per candidate per run
(never re-fetch a conversation's history multiple times in the same pass, never
fetch history for a conversation that's about to be skipped as spam/already-seen).
That cap is enforced by the CALLER (reply_watch.py only calls fetch_context() on
the already-filtered candidate list), not by this module, since this module has no
visibility into what "this run" means across calls.

C167: objection_sequence_count() scans store/objections.jsonl for prior objections
FROM THE SAME CONTACT (contact_id, with a name-fallback for older/other-sourced rows
that predate the contact_id field this build adds) and returns how many the contact
has raised before this one -- reply_watch.py uses that count to pick a different,
firmer counter on the 2nd+ pushback (per playbook #4 "the price is the price" /
#9 "the price holds 14 days, then it's requoted" family [OWNER] already uses for
repeat price objections) instead of repeating the same first-touch counter.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import owner  # noqa: E402
import ghl_social  # noqa: E402

OBJECTIONS = ROOT / "store" / "objections.jsonl"
DEFAULT_TURNS = 5

# GHL logs CRM system events (opportunity stage changes etc.) into the SAME message
# stream as real texts/emails, tagged as "outbound" with no human content -- confirmed
# live against real conversations (e.g. "Opportunity created", "Opportunity updated").
# These are noise for a drafting context window, not conversation history, so they're
# filtered out before the last-N-turns trim (otherwise a thread with several stage
# changes between real messages would show the LLM mostly system noise instead of
# what was actually said).
_SYSTEM_EVENT_BODIES = {
    "opportunity created", "opportunity updated", "opportunity deleted",
    "opportunity status changed", "pipeline stage updated", "contact created",
    "contact updated", "workflow triggered", "task created", "task completed",
}

# C167: the specific playbook counters (objections.md #4, #9) that are the RIGHT
# response to a repeat price pushback, as named explicitly in this mission's brief.
# reply_watch's CLASSIFY prompt gets told to reach for these once objection_sequence_count
# shows this isn't the contact's first price objection.
REPEAT_PRICE_COUNTERS = (
    '#4 ("Can you do it cheaper?"): "The price is the price. What I can do is trade: '
    'knock 10 percent off for a testimonial on delivery and two intros to people like '
    'you. Deal?"',
    '#9 ("I need to think about the money."): "Sure. While you think: the price holds '
    '14 days, then it\'s requoted. What number would make this a yes today?"',
)


def _load_jsonl(path: Path) -> list[dict]:
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


def fetch_context(convo_id: str, turns: int = DEFAULT_TURNS) -> list[dict]:
    """One GHL API call: GET /conversations/{id}/messages. Returns up to `turns`
    most recent REAL messages (system/CRM events filtered out -- see
    _SYSTEM_EVENT_BODIES) as [{"dir": "inbound"|"outbound", "body": str, "ts": str}],
    oldest first (reading order). Empty list on any failure -- never raises, since
    losing the context window should degrade a draft's quality, not crash the run."""
    if not convo_id:
        return []
    # over-fetch: system events count against GHL's own `limit`, so asking for
    # exactly `turns` and THEN filtering could leave fewer than `turns` real
    # messages even when more exist further back in the thread.
    fetch_limit = max(turns, 1) * 3
    try:
        out = ghl_social._api(["GET", f"/conversations/{convo_id}/messages?limit={fetch_limit}"])
        data = json.loads(out[out.find("{"):])
    except (ValueError, json.JSONDecodeError, IndexError):
        return []
    raw = (data.get("messages") or {}).get("messages") if isinstance(data.get("messages"), dict) \
        else data.get("messages") or []
    if not isinstance(raw, list):
        return []
    msgs = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        direction = "inbound" if (m.get("direction") or "").lower() == "inbound" else "outbound"
        body = (m.get("body") or m.get("message") or "").strip()
        if not body or body.strip().lower() in _SYSTEM_EVENT_BODIES:
            continue
        msgs.append({"dir": direction, "body": body[:400], "ts": m.get("dateAdded") or m.get("dateUpdated") or ""})
    # GHL returns newest-first; reverse to reading order and cap to the last N turns
    msgs = list(reversed(msgs))[-turns:] if msgs else msgs
    return msgs


def format_context(msgs: list[dict]) -> str:
    """Render fetch_context()'s output as a plain reading-order transcript block
    for the LLM prompt. Empty input -> empty string (caller decides what to show
    when there's no history, e.g. 'first touch')."""
    if not msgs:
        return ""
    lines = []
    for m in msgs:
        who = "THEM" if m["dir"] == "inbound" else owner.get("name", "ME").upper()
        lines.append(f"{who}: {m['body']}")
    return "\n".join(lines)


def objection_sequence_count(contact_id: str, name: str = "") -> int:
    """How many objections this contact has already raised, per store/objections.jsonl.
    Matches by contact_id when the record has one (this build's reply_watch.py writer
    adds it); falls back to an exact case-insensitive name match for rows written by
    server.py's /api/objections endpoint (which has no contact_id field at all) so
    older/other-sourced objection history still counts instead of silently resetting
    to zero. Returns 0 for a contact with no prior objections on file (their next one,
    if any, will be their first -- normal first-touch counter applies)."""
    if not contact_id and not name:
        return 0
    name_l = (name or "").strip().lower()
    n = 0
    for r in _load_jsonl(OBJECTIONS):
        r_cid = r.get("contact_id") or ""
        r_name = (r.get("name") or "").strip().lower()
        if contact_id and r_cid == contact_id:
            n += 1
        elif not r_cid and name_l and r_name == name_l:
            n += 1
    return n


def log_objection(objection: str, counter: str, contact_id: str = "", name: str = "",
                  niche: str = "", src: str = "reply_watch") -> None:
    """Append one objection to store/objections.jsonl in the shape reply_watch.py
    already writes, PLUS contact_id/name so objection_sequence_count() can match
    future turns from the same contact. Purely additive fields -- server.py's own
    /api/objections writer and meeting_prep.py's reader both tolerate extra keys
    (meeting_prep filters by 'niche' only if present, server.py just dumps rows)."""
    from store_lib import now_iso
    OBJECTIONS.parent.mkdir(parents=True, exist_ok=True)
    with OBJECTIONS.open("a") as f:
        f.write(json.dumps({"ts": now_iso(), "objection": (objection or "")[:300],
                            "counter": (counter or "")[:400], "src": src,
                            "contact_id": contact_id or "", "name": name or "",
                            "niche": niche or ""}, ensure_ascii=False) + "\n")
