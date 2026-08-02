#!/usr/bin/env python3
"""#176 GHL webhook receiver — the PROCESSOR half. The SERVER half (an actual FastAPI
route accepting POST /api/ghl/webhook) is explicitly NOT this mission's file (server.py
is owned elsewhere) — this file is the consumer that would sit downstream of it.

CONTRACT (for whoever builds the server route — documented here AND in the mission
status file per the brief):
  Endpoint:  POST /api/ghl/webhook
  Auth:      shared-secret header, e.g. X-GHL-Webhook-Secret: <secret>, compared with
             hmac.compare_digest() against a secret stored via store_lib.secret()
             (matches this codebase's existing secret-resolution convention). NOT a
             URL-embedded HMAC sig like /api/act/* uses — GHL's own workflow "webhook"
             step (confirmed real: WORKFLOW-BUILDER.md documents
             {"type":"webhook","url":...,"method":"POST","data":[...]}) can set a
             custom header, so a shared secret in a header is the natural fit and
             matches server.py's OWN existing anticipation of this route: it already
             lists "/api/ghl/webhook" in _AUTH_EXEMPT with the comment "webhook carries
             its own shared-secret header" (read live in server.py — not edited by this
             mission, just confirming the contract lines up with what's already there).
  Body:      JSON. Exact GHL payload shape varies by event kind, but every event this
             processor cares about needs at minimum:
               {"event": "<type>", "contactId": "...", "email": "...", "ts": "..."}
             GHL workflow webhook steps can attach whatever `data` fields the workflow
             author wires in (merge tags etc.) — the server route's job is to pass the
             raw body through mostly as-is, stamping a receipt ts, NOT to reshape it;
             reshaping belongs here (route_event()), one place, testable in isolation.
  Event types this processor routes:
    - "bounce" / "email_bounced"      -> store/bounce_events.jsonl (same file/shape
                                          campaign_guard.py's #162 heuristic already
                                          writes, so the threshold-pause logic there
                                          picks up webhook-sourced bounces for free)
    - "unsubscribe" / "email_unsubscribed" / "sms_opt_out"
                                       -> store/suppress.jsonl (same shape
                                          reply_watch._suppress() writes)
    - "reply" / "inbound_message"     -> logged to store/webhook_replies_seen.jsonl as
                                          a signal only (NOT written into replies.jsonl
                                          directly — reply_watch.py already owns
                                          drafting logic and classification via the LLM;
                                          this just means "reply_watch's next run should
                                          prioritize this contact," not "draft one now")
    - anything else                   -> store/ghl_events_unrouted.jsonl (visibility,
                                          nothing silently dropped)

  Source file THIS processor reads: store/ghl_events.jsonl (append-only, one JSON
  object per line, written by the future server route). Processing is idempotent by
  event id (or a content hash if the payload has no id) via
  store/webhook_processor_state.json (last-processed line count) so re-running never
  double-routes the same events.

Nothing here writes to GHL. Nothing here sends anything. Local jsonl writes only.

Usage:
  webhook_processor.py             # process any new events since the last run
  webhook_processor.py --dry-run   # show routing decisions, write nothing
  webhook_processor.py --replay    # ignore the processed-count checkpoint, reprocess everything
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402

EVENTS = ROOT / "store" / "ghl_events.jsonl"
STATE = ROOT / "store" / "webhook_processor_state.json"
SUPPRESS = ROOT / "store" / "suppress.jsonl"
BOUNCE_LOG = ROOT / "store" / "bounce_events.jsonl"
REPLIES_SEEN = ROOT / "store" / "webhook_replies_seen.jsonl"
UNROUTED = ROOT / "store" / "ghl_events_unrouted.jsonl"

BOUNCE_EVENTS = {"bounce", "email_bounced", "hard_bounce"}
UNSUB_EVENTS = {"unsubscribe", "email_unsubscribed", "sms_opt_out", "opt_out", "dnd"}
REPLY_EVENTS = {"reply", "inbound_message", "inbound_email", "inbound_sms"}


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


def _append(path: Path, rec: dict):
    # flock: suppress.jsonl/bounce_events.jsonl are also written by the server's webhook
    # route + campaign_guard; match the house locking pattern so concurrent appends can't
    # interleave (red-team F2, LETTER bug-class 3).
    from store_lib import _flock
    path.parent.mkdir(parents=True, exist_ok=True)
    with _flock(path), path.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _event_key(evt: dict) -> str:
    """Stable dedupe key: the event's own id if it has one, else a content hash."""
    if evt.get("id"):
        return str(evt["id"])
    blob = json.dumps(evt, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, json.JSONDecodeError):
        return {"processed_keys": []}


def _save_state(state: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    # cap the remembered-keys list so this file doesn't grow forever; the tail is
    # what matters for de-dupe against a webhook that might redeliver recently
    state["processed_keys"] = state.get("processed_keys", [])[-5000:]
    STATE.write_text(json.dumps(state, indent=2))


def route_event(evt: dict) -> tuple[str, dict]:
    """Pure function: given one raw webhook event dict, decide where it belongs and
    what shape to write. Returns (destination_name, record_to_write). Isolated from
    file I/O so it's directly fixture-testable."""
    kind = (evt.get("event") or evt.get("type") or "").strip().lower()
    contact_id = evt.get("contactId") or evt.get("contact_id") or ""
    email = (evt.get("email") or "").strip().lower()
    ts = evt.get("ts") or now_iso()

    if kind in BOUNCE_EVENTS:
        rec = {"ts": ts, "convo": evt.get("convo") or evt.get("messageId") or "",
               "sender": evt.get("from") or evt.get("sender") or "",
               "body": (evt.get("body") or evt.get("reason") or "")[:200],
               "contact_id": contact_id, "email": email, "src": "webhook"}
        return "bounce", rec
    if kind in UNSUB_EVENTS:
        rec = {"ts": ts, "contact_id": contact_id, "email": email,
               "why": f"GHL webhook event '{kind}'"}
        return "suppress", rec
    if kind in REPLY_EVENTS:
        rec = {"ts": ts, "contact_id": contact_id, "email": email,
               "convo": evt.get("convo") or evt.get("conversationId") or "", "kind": kind}
        return "reply_seen", rec
    return "unrouted", {"ts": ts, **evt}


def process(dry: bool = False, replay: bool = False) -> dict:
    events = _load_jsonl(EVENTS)
    state = {"processed_keys": []} if replay else _load_state()
    seen = set(state.get("processed_keys", []))
    counts = {"bounce": 0, "suppress": 0, "reply_seen": 0, "unrouted": 0, "skipped_dup": 0}
    new_keys = []
    for evt in events:
        key = _event_key(evt)
        if key in seen:
            counts["skipped_dup"] += 1
            continue
        seen.add(key)  # dedupe WITHIN this batch too, not just against the prior checkpoint —
                       # a redelivered webhook can land twice in the same ghl_events.jsonl
                       # read before this processor's next run ever sees the first copy.
        dest, rec = route_event(evt)
        counts[dest] += 1
        if not dry:
            target = {"bounce": BOUNCE_LOG, "suppress": SUPPRESS,
                      "reply_seen": REPLIES_SEEN, "unrouted": UNROUTED}[dest]
            _append(target, rec)
        new_keys.append(key)
    if new_keys and not dry:
        state["processed_keys"] = state.get("processed_keys", []) + new_keys
        _save_state(state)
    total_new = len(new_keys)
    print(f"webhook_processor: {len(events)} event(s) in store, {total_new} new "
          f"({counts['skipped_dup']} already processed)")
    if total_new:
        print(f"  routed: bounce={counts['bounce']} suppress={counts['suppress']} "
              f"reply_seen={counts['reply_seen']} unrouted={counts['unrouted']}")
        if not dry and (counts["bounce"] or counts["suppress"]):
            planner.feed_add("cold", f"webhook events processed: {counts['bounce']} bounce(s), "
                                     f"{counts['suppress']} suppression(s)")
    return counts


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--replay", action="store_true", help="ignore the checkpoint, reprocess every event in the store")
    args = ap.parse_args()
    process(dry=args.dry_run, replay=args.replay)
