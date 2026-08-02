#!/usr/bin/env python3
"""Gmail OUTBOX — the only module in this codebase that can send email.

Why this exists apart from gmail_api.py: that module is read-only-except-labels BY
DESIGN and is imported by the whole unattended mail fleet; the fleet must never gain
send ability. This module is imported ONLY by app/server.py, and the send route fires
only on [OWNER]'s per-item click in the dashboard.

Rails (each one is a test in tests/test_outbox.py):
- send_one() sends exactly ONE item per call. No bulk-send function exists.
- An item sends once: status must be 'draft'; sent/dismissed/replied refuse.
- Hard cap DAILY_CAP sends per local day, counted from store/sent_log.jsonl.
- store_lib.humanize() runs on every body before send (voice rail: no em-dashes).
- Send never auto-retries: a timeout MAY have delivered, so a retry could
  double-send. One attempt; the error surfaces to [OWNER] and status stays 'draft'.
- Warm mail looks like mail: plain text, no tracking pixels, no shorteners.

Sources of drafts: /api/outbox/stage (JARVIS / money-outreach skill output) and
import_mail_drafts() (pulls the mail-fleet's pending reply drafts from
store/mail_drafts.jsonl — this outbox IS the "future UI" that file's contract names,
so on send/dismiss we append the status update back to it, per its append-only,
last-write-wins-by-id convention).
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.request
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "agents", Path.home() / "Claude" / "gmail"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from store_lib import humanize, now_iso  # noqa: E402
import gmail_api  # noqa: E402  (token reuse only; that module cannot send)

OUTBOX = ROOT / "store" / "outbox.json"
SENT_LOG = ROOT / "store" / "sent_log.jsonl"
MAIL_DRAFTS = ROOT / "store" / "mail_drafts.jsonl"
DAILY_CAP = 30  # Gmail consumer limit is 500/day; staying tiny keeps the account boring

_me_cache = {"t": 0.0, "addr": ""}
_replies_checked = {"t": 0.0}


# ---------- store (server is the single writer; atomic replace) ----------
def _load() -> list[dict]:
    try:
        return json.loads(OUTBOX.read_text())
    except (OSError, json.JSONDecodeError):
        return []


def _save(items: list[dict]) -> None:
    tmp = OUTBOX.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, indent=1))
    os.replace(tmp, OUTBOX)


def _claim(oid: str) -> dict | None:
    """Locked draft->sending compare-and-swap (2026-07-07 D6 audit). Two concurrent sends
    (double-tap before the button disables) can't both pass: the second sees 'sending' and
    gets None. Mirrors reply_watch.claim()/proposal_factory.claim()."""
    from store_lib import _flock
    with _flock(OUTBOX):
        rows = _load()
        rec = next((r for r in rows if r["id"] == oid), None)
        if not rec or rec.get("status") != "draft":
            return None
        rec["status"] = "sending"
        _save(rows)
        return dict(rec)


def reap_stuck() -> int:
    """Startup recovery: an item stuck at 'sending' (crash between claim and the post-send
    save) reverts to draft with a note. Gmail MAY have delivered, so the note warns to
    verify before resending. Called from the server's _reap_stuck_sending()."""
    from store_lib import _flock
    n = 0
    with _flock(OUTBOX):
        rows = _load()
        for r in rows:
            if r.get("status") == "sending":
                r["status"] = "draft"
                r["note"] = "recovered from 'sending' at startup; check Gmail sent before resending"
                n += 1
        if n:
            _save(rows)
    return n


def _new_id() -> str:
    import secrets
    return "ob_" + time.strftime("%Y%m%d") + "_" + secrets.token_hex(3)


def items() -> list[dict]:
    return sorted(_load(), key=lambda x: x.get("created", ""), reverse=True)


def stage(to: str, subject: str, body: str, contact: str = "", source: str = "",
          thread_id: str = "", src_id: str = "") -> dict:
    to, subject, body = (to or "").strip(), (subject or "").strip(), (body or "").strip()
    if not to or "@" not in to:
        return {"ok": False, "error": "need a real to-address"}
    if not body:
        return {"ok": False, "error": "empty body"}
    from store_lib import _flock
    with _flock(OUTBOX):  # RMW under the same lock the send path uses (red-team F1 #4)
        rows = _load()
        if src_id and any(r.get("src_id") == src_id for r in rows):
            return {"ok": False, "error": "already imported", "dup": True}
        rec = {"id": _new_id(), "to": to, "subject": subject or "(no subject)",
               "body": humanize(body), "contact": contact, "source": source,
               "thread_id": thread_id, "src_id": src_id,
               "status": "draft", "created": now_iso()}
        rows.append(rec)
        _save(rows)
    return {"ok": True, "item": rec}


def update(oid: str, fields: dict) -> dict:
    from store_lib import _flock
    with _flock(OUTBOX):  # RMW under lock (red-team F1 #4)
        rows = _load()
        for r in rows:
            if r["id"] == oid:
                if r["status"] != "draft":
                    return {"ok": False, "error": f"can't edit a {r['status']} item"}
                for k in ("to", "subject", "body"):
                    if fields.get(k) is not None:
                        r[k] = humanize(str(fields[k])) if k == "body" else str(fields[k]).strip()
                _save(rows)
                return {"ok": True, "item": r}
    return {"ok": False, "error": "not found"}


def dismiss(oid: str) -> dict:
    from store_lib import _flock
    with _flock(OUTBOX):  # RMW under lock (red-team F1 #4)
        rows = _load()
        for r in rows:
            if r["id"] == oid:
                r["status"] = "dismissed"
                _save(rows)
                _writeback_mail_draft(r, "dismissed")
            return {"ok": True}
    return {"ok": False, "error": "not found"}


# ---------- sending ----------
def _me() -> str:
    if time.time() - _me_cache["t"] < 3600 and _me_cache["addr"]:
        return _me_cache["addr"]
    try:
        req = urllib.request.Request(gmail_api.API + "/profile",
                                     headers={"Authorization": "Bearer " + gmail_api.token()})
        prof = json.loads(urllib.request.urlopen(req, timeout=15).read())
        _me_cache.update(t=time.time(), addr=prof.get("emailAddress", ""))
    except Exception:
        pass
    return _me_cache["addr"] or "[OWNER_EMAIL]"


def sent_today() -> int:
    day = time.strftime("%Y-%m-%d")
    n = 0
    try:
        with open(SENT_LOG) as f:
            for line in f:
                if f'"day": "{day}"' in line:
                    n += 1
    except OSError:
        pass
    return n


def _deliver(raw_b64: str, thread_id: str = "") -> dict:
    """One attempt, NO retry (a timeout may have delivered; retry risks double-send)."""
    payload: dict = {"raw": raw_b64}
    if thread_id:
        payload["threadId"] = thread_id
    req = urllib.request.Request(
        gmail_api.API + "/messages/send", method="POST",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + gmail_api.token(),
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _apply(oid: str, fields: dict) -> dict | None:
    """Locked update of one item by id."""
    from store_lib import _flock
    with _flock(OUTBOX):
        rows = _load()
        for r in rows:
            if r["id"] == oid:
                r.update(fields)
                _save(rows)
                return dict(r)
    return None


def send_one(oid: str, body_override: str | None = None,
             subject_override: str | None = None, _deliver_fn=None) -> dict:
    """Send exactly one outbox item. Requires status 'draft'. Cap-checked. The draft->sending
    claim is atomic so a double-tap can't double-send (2026-07-07 D6 audit)."""
    pre = next((r for r in _load() if r["id"] == oid), None)
    if not pre:
        return {"ok": False, "error": "not found"}
    if pre["status"] != "draft":
        return {"ok": False, "error": f"already {pre['status']} — an item sends once"}
    if sent_today() >= DAILY_CAP:
        return {"ok": False, "error": f"daily cap hit ({DAILY_CAP}). Tomorrow, or raise DAILY_CAP deliberately."}
    rec = _claim(oid)  # atomic draft->sending; a concurrent send gets None
    if not rec:
        return {"ok": False, "error": "already sending or sent (double-send blocked)"}
    body = humanize((body_override if body_override is not None else rec["body"]).strip())
    subject = (subject_override if subject_override is not None else rec["subject"]).strip()
    if not body:
        _apply(oid, {"status": "draft"})  # release the claim
        return {"ok": False, "error": "empty body"}
    msg = MIMEText(body, "plain", "utf-8")
    msg["To"] = rec["to"]
    msg["From"] = _me()
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        res = (_deliver_fn or _deliver)(raw, rec.get("thread_id", ""))
    except Exception as e:  # noqa: BLE001 — revert claim to draft so [OWNER] can retry
        _apply(oid, {"status": "draft", "note": f"send failed (not retried): {str(e)[:120]}"})
        return {"ok": False, "error": f"send failed (NOT retried, item back to draft): {e}"}
    rec = _apply(oid, {"status": "sent", "body": body, "subject": subject, "sent_at": now_iso(),
                       "message_id": res.get("id", ""),
                       "sent_thread": res.get("threadId", rec.get("thread_id", ""))}) or rec
    try:
        with open(SENT_LOG, "a") as f:
            f.write(json.dumps({"day": time.strftime("%Y-%m-%d"), "ts": now_iso(),
                                "id": rec["id"], "to": rec["to"], "subject": subject,
                                "message_id": rec["message_id"]}) + "\n")
    except OSError:
        pass
    _writeback_mail_draft(rec, "sent")
    return {"ok": True, "item": rec, "sent_today": sent_today()}


# ---------- mail-fleet drafts import ----------
def _writeback_mail_draft(rec: dict, status: str) -> None:
    """If this item came from mail_drafts.jsonl, append the status update back
    (append-only, last-write-wins by id — that file's documented convention)."""
    if not rec.get("src_id") or rec.get("source") != "mail_drafts":
        return
    try:
        from store_lib import _flock
        with _flock(MAIL_DRAFTS), MAIL_DRAFTS.open("a") as f:  # last unflocked writer on this store (B0)
            f.write(json.dumps({"id": rec["src_id"], "status": status,
                                "via": "outbox", "ts": now_iso()}) + "\n")
    except OSError:
        pass


def import_mail_drafts(limit: int = 10) -> dict:
    """Pull the mail fleet's pending reply drafts into the outbox (dedup by src_id)."""
    latest: dict[str, dict] = {}
    try:
        with open(MAIL_DRAFTS) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("id"):
                    latest[d["id"]] = {**latest.get(d["id"], {}), **d}
    except OSError:
        return {"ok": True, "imported": 0}
    n = 0
    for d in latest.values():
        if d.get("status") != "pending" or not d.get("draft"):
            continue
        r = stage(to=d.get("to", ""), subject=d.get("subject", ""), body=d["draft"],
                  contact=d.get("context", ""), source="mail_drafts",
                  thread_id=d.get("thread_id", ""), src_id=d["id"])
        if r.get("ok"):
            n += 1
            if n >= limit:
                break
    return {"ok": True, "imported": n}


# ---------- reply detection ----------
def check_replies(max_check: int = 10, min_gap: int = 900) -> int:
    """Mark sent items 'replied' when their thread grew after we sent. Throttled."""
    if time.time() - _replies_checked["t"] < min_gap:
        return -1
    _replies_checked["t"] = time.time()
    rows = _load()
    me = _me().lower()
    changed = 0
    for r in [x for x in rows if x.get("status") == "sent" and x.get("sent_thread")][:max_check]:
        try:
            msgs = gmail_api.get_thread_metadata(r["sent_thread"])
        except Exception:
            continue
        sent_ms = 0
        for m in msgs:
            if m.get("id") == r.get("message_id"):
                sent_ms = int(m.get("internalDate") or 0)
        for m in msgs:
            frm = (m.get("from") or "").lower()
            if int(m.get("internalDate") or 0) > sent_ms and me not in frm and frm:
                r["status"] = "replied"
                r["replied_at"] = now_iso()
                changed += 1
                break
    if changed:
        _save(rows)
    return changed
