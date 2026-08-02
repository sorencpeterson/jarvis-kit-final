#!/usr/bin/env python3
"""Per-contact LinkedIn thread memory — A16, A31.

A16: one memory file per contact (keyed by normalized profile URL) accumulating
every drafted/sent touch (comment/reply/connect-note/dm) so future drafting NEVER
repeats an opener or a value-nugget already used on that person. This is
DIFFERENT from GHL's thread_summaries.jsonl (agents/thread_memory.py, out of this
lane) — that's for GHL sales conversations; this is LinkedIn-specific and reads
straight from networking's own queue (network.jsonl) rather than a separate live
API pull, since every LinkedIn touch this system makes already lives there.

A31: thread_context() assembles the last-3-messages view a reply-drafting prompt
needs, pulled from the same per-contact history.

Storage: store/li_threads/<url_hash>.json, one small file per contact (not one
giant jsonl) so a "read this person's history" lookup is O(1) file read, not an
O(n) scan — this matters once a contact has years of touches and callers need
this on every draft, not just in a batch job.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import networking  # noqa: E402
import li_history  # noqa: E402

THREADS_DIR = ROOT / "store" / "li_threads"


def _thread_path(url: str) -> Path:
    uk = li_history._url_key(url)
    h = hashlib.sha1(uk.encode("utf-8")).hexdigest()[:16]
    return THREADS_DIR / f"{h}.json"


def load_thread(url: str) -> dict:
    """Returns {"url_key": ..., "name": "", "touches": [...]} — empty shell if
    no file exists yet (never an error, this is the common case for a
    brand-new contact)."""
    p = _thread_path(url)
    if not p.exists():
        return {"url_key": li_history._url_key(url), "name": "", "touches": []}
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {"url_key": li_history._url_key(url), "name": "", "touches": []}


def _save_thread(url: str, thread: dict):
    THREADS_DIR.mkdir(parents=True, exist_ok=True)
    _thread_path(url).write_text(json.dumps(thread, indent=2, ensure_ascii=False))


def record_touch(url: str, *, kind: str, draft: str, name: str = "", direction: str = "outbound"):
    """Append one touch to this contact's thread file. direction: outbound
    ([OWNER]'s drafted/sent message) or inbound (their reply, when/if that ever
    gets captured — currently no inbound-DM capture exists in this system, see
    li_conveyor.py's note on the same gap; this function accepts direction as
    a parameter so it's ready the moment that data source exists, [E] until
    then for inbound rows specifically)."""
    thread = load_thread(url)
    if name and not thread.get("name"):
        thread["name"] = name
    thread.setdefault("touches", []).append({
        "ts": now_iso(), "kind": kind, "direction": direction, "text": draft,
    })
    _save_thread(url, thread)


def sync_from_queue() -> int:
    """Rebuild thread files from EVERY record currently in networking's queue
    (any status) — this is how A16 gets populated for touches that were queued
    BEFORE this module existed, and is safe to re-run anytime (idempotent full
    rebuild per contact, not an incremental append, so it never double-counts
    even if called repeatedly). Returns the number of contacts (thread files)
    written."""
    by_url: dict[str, list[dict]] = {}
    names: dict[str, str] = {}
    for rec in networking.load_queue():
        url = rec.get("url", "")
        if not url:
            continue
        uk = li_history._url_key(url)
        by_url.setdefault(uk, []).append(rec)
        if rec.get("author"):
            names[uk] = rec["author"]

    written = 0
    for uk, recs in by_url.items():
        recs.sort(key=lambda r: r.get("created", ""))
        touches = [{"ts": r.get("created", ""), "kind": r.get("kind", ""),
                    "direction": "outbound", "text": r.get("draft", ""),
                    "status": r.get("status", "")}
                   for r in recs]
        thread = {"url_key": uk, "name": names.get(uk, ""), "touches": touches}
        # use the first record's real url (not the normalized key) for the file path,
        # so _thread_path's hash matches what load_thread(real_url) will compute later
        real_url = recs[0].get("url", "")
        _save_thread(real_url, thread)
        written += 1
    return written


def thread_context(url: str, n: int = 3) -> str:
    """(A31) Plain-text rendering of the last N touches for this contact, meant
    to be dropped straight into a reply-drafting prompt so the draft has real
    context instead of just the latest message. Empty string if no history."""
    thread = load_thread(url)
    touches = thread.get("touches", [])[-n:]
    if not touches:
        return ""
    lines = []
    for t in touches:
        text = (t.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"[{t.get('direction', 'outbound')}/{t.get('kind', '?')}] {text[:280]}")
    return "\n".join(lines)


def used_openers(url: str) -> list[str]:
    """Every outbound draft text ever used on this contact — pass this into a
    drafting prompt as 'do not repeat any of these' so A16's 'never repeats'
    guarantee is enforceable, not just aspirational."""
    thread = load_thread(url)
    return [t["text"] for t in thread.get("touches", [])
            if t.get("direction") == "outbound" and t.get("text")]


if __name__ == "__main__":
    n = sync_from_queue()
    print(f"li_thread: synced {n} contact thread file(s) -> {THREADS_DIR}")
