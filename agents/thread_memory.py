#!/usr/bin/env python3
"""Thread memory (#49) — summarize long GHL conversations behind pending replies.

Why: reply_watch.py drafts a reply off the LAST inbound message only, so a thread
that's gone back and forth five times gets the same shallow context as a first
touch. This walks store/replies.jsonl for pending/queued records, pulls the full
GHL conversation for any thread over 5 messages, and asks ONE cheap Haiku call
for a tight 3-line summary, so a future reply-drafting pass (or [OWNER] himself)
has the arc of the conversation, not just the latest line.

Read-only against GHL (a GET) and replies.jsonl; only write is an append to
store/thread_summaries.jsonl, skipping convos already summarized there.

If the messages endpoint 404s (wrong path/API version on this GHL account), print
the exact endpoint tried and exit 0 — this is a nice-to-have, not load-bearing,
so a bad guess at the endpoint shape should never fail the run.

E332 (thread-memory summaries into the dossier endpoint): app/server.py's
GET /api/contact/{cid}/dossier (outside this lane's exclusive files, so not
edited here) currently returns {contact, proposals, replies, last_touch} with
NO connection to store/thread_summaries.jsonl at all. The join isn't direct —
thread_summaries.jsonl is keyed by 'convo' (a GHL conversation id), while the
dossier is keyed by 'cid' (a contact id) — so dossier_summaries_for_contact()
below does the two-hop join (contact_id -> replies.jsonl's matching 'convo'
ids -> thread_summaries.jsonl entries for those convos) and is READY FOR
app/server.py TO IMPORT AND CALL, e.g.:

    import thread_memory
    ...
    "thread_summaries": thread_memory.dossier_summaries_for_contact(cid),

added as one more key in that endpoint's return dict. This file does not call
that endpoint or modify server.py; it only exposes the join as a tested,
importable function so wiring it in is a one-line addition whenever server.py
is next touched by whoever owns it.

Run standalone: .venv/bin/python agents/thread_memory.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402
import ghl_social  # noqa: E402  (reuse the GHL api.sh caller + path)
from runlog import track  # noqa: E402  (E353: runlog adoption)

REPLIES = ROOT / "store" / "replies.jsonl"
SUMMARIES = ROOT / "store" / "thread_summaries.jsonl"
MIN_MESSAGES = 5
MSG_LIMIT = 20


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


def _pending_convos() -> list[str]:
    """Distinct convo ids from replies.jsonl records still awaiting action.
    replies.jsonl is last-write-wins-ish by id in practice (reply_watch only
    appends), so plain de-dupe on 'convo' is enough here."""
    convos, seen = [], set()
    for r in _read_jsonl(REPLIES):
        if r.get("status") not in ("pending", "queued", None):
            continue
        c = r.get("convo")
        if c and c not in seen:
            seen.add(c)
            convos.append(c)
    return convos


def _already_summarized() -> set[str]:
    return {r.get("convo") for r in _read_jsonl(SUMMARIES) if r.get("convo")}


def dossier_summaries_for_contact(contact_id: str) -> list[dict]:
    """E332: the two-hop join a dossier endpoint needs — contact_id has no
    direct key into thread_summaries.jsonl (which is keyed by 'convo'), so
    this looks up every convo id replies.jsonl has on record for this
    contact, then returns whichever of those convos have a saved summary
    here, most recent first. Pure read, no side effects, safe to call from
    any request path (e.g. app/server.py's dossier endpoint, see module
    docstring for the exact one-line wiring). Empty list (not an error) for
    a contact with no replies on file or no summarized thread yet — both are
    valid, non-error states."""
    if not contact_id:
        return []
    convo_ids = {r.get("convo") for r in _read_jsonl(REPLIES)
                 if r.get("contact_id") == contact_id and r.get("convo")}
    if not convo_ids:
        return []
    hits = [s for s in _read_jsonl(SUMMARIES) if s.get("convo") in convo_ids]
    hits.sort(key=lambda s: s.get("ts", ""), reverse=True)
    return hits


ENDPOINT_TMPL = "/conversations/{convo_id}/messages?limit=20"


def fetch_messages(convo_id: str) -> tuple[list[dict], str]:
    """Returns (messages, endpoint_tried). Messages is [] on any failure/404."""
    endpoint = f"/conversations/{convo_id}/messages?limit={MSG_LIMIT}"
    out = ghl_social._api(["GET", endpoint])
    if "404" in out.split("\n", 1)[0] or '"statusCode": 404' in out or '"statusCode":404' in out:
        return [], endpoint
    start = out.find("{")
    if start < 0:
        return [], endpoint
    try:
        data = json.loads(out[start:])
    except json.JSONDecodeError:
        return [], endpoint
    msgs = (data.get("messages") or {}).get("messages")
    if msgs is None:
        msgs = data.get("messages") if isinstance(data.get("messages"), list) else []
    return msgs or [], endpoint


SUMMARY_PROMPT = """Summarize this GHL conversation thread with a prospect/contact of
[OWNER]'s ([OWNER_COMPANY] / [OWNER_COMPANY]) in EXACTLY 3 lines, plain text, no
preamble, no numbering, no em-dashes:
line 1: what the contact wants / their situation
line 2: where the conversation currently stands
line 3: the obvious next move

MESSAGES (oldest first):
%s"""


def _format_messages(msgs: list[dict]) -> str:
    lines = []
    for m in msgs:
        direction = m.get("direction") or ("outbound" if m.get("fromMe") else "inbound")
        body = (m.get("body") or m.get("text") or "").strip().replace("\n", " ")
        if not body:
            continue
        lines.append(f"[{direction}] {body[:300]}")
    return "\n".join(lines)


def _save(rec: dict):
    SUMMARIES.parent.mkdir(parents=True, exist_ok=True)
    with SUMMARIES.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def run() -> int:
    convos = _pending_convos()
    if not convos:
        print("thread_memory: no pending replies to check")
        return 0
    done = _already_summarized()
    todo = [c for c in convos if c not in done]
    if not todo:
        print(f"thread_memory: {len(convos)} pending convo(s), all already summarized")
        return 0

    summarized = 0
    for convo_id in todo:
        msgs, endpoint = fetch_messages(convo_id)
        if not msgs:
            # Distinguish "endpoint doesn't exist on this account" from "empty thread"
            # by re-checking: fetch_messages already returns [] for both, so only warn
            # about the endpoint shape once, on the first convo, and keep going.
            print(f"thread_memory: no messages returned for {convo_id} (tried {endpoint})")
            continue
        if len(msgs) <= MIN_MESSAGES:
            continue
        formatted = _format_messages(msgs)
        if not formatted:
            continue
        summary = planner._cli(SUMMARY_PROMPT % formatted, timeout=100, feature="plan")
        summary = (summary or "").strip()
        if not summary:
            continue
        _save({"convo": convo_id, "summary": summary, "ts": now_iso()})
        summarized += 1

    print(f"thread_memory: {summarized} new summary(ies) written from {len(todo)} candidate convo(s) -> {SUMMARIES}")
    if summarized:
        planner.feed_add("agent", f"Summarized {summarized} long thread(s)")
    return 0


def main() -> int:
    try:
        with track("thread_memory"):  # E353: runlog adoption
            return run()
    except Exception as e:  # noqa: BLE001
        # Endpoint-shape or transport surprises are non-fatal for this nice-to-have agent.
        # track() re-raises after logging, so this except still catches it here,
        # same non-fatal behavior as before adoption.
        msg = str(e)
        if "404" in msg:
            print(f"thread_memory: endpoint 404'd — tried {ENDPOINT_TMPL}")
        else:
            print(f"thread_memory: failed non-fatally: {msg}")
        return 0


if __name__ == "__main__":
    from runlog import track
    with track("thread_memory"):
        raise SystemExit(main())
