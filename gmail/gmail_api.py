#!/usr/bin/env python3
"""Minimal Gmail REST helper that reuses the gmail-mcp OAuth credentials.

Reads ~/.gmail-mcp/credentials.json (token) + ~/.gmail-mcp/gcp-oauth.keys.json
(client id/secret), auto-refreshes the access token when expired, and exposes
search() / get_message() against the Gmail REST API using only the stdlib.
"""
import http.client
import json, os, time, base64, urllib.parse, urllib.request, urllib.error

GMAIL_DIR = os.path.expanduser("~/.gmail-mcp")
CRED = os.path.join(GMAIL_DIR, "credentials.json")
KEYS = os.path.join(GMAIL_DIR, "gcp-oauth.keys.json")
API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _load():
    with open(CRED) as f:
        return json.load(f)


def _save(c):
    with open(CRED, "w") as f:
        json.dump(c, f)


def _client():
    with open(KEYS) as f:
        k = json.load(f)
    return k["installed"]


def token():
    c = _load()
    # expiry_date is ms epoch; refresh if within 60s of expiry
    if c.get("expiry_date", 0) / 1000.0 - 60 > time.time():
        return c["access_token"]
    cl = _client()
    data = urllib.parse.urlencode({
        "client_id": cl["client_id"],
        "client_secret": cl["client_secret"],
        "refresh_token": c["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(cl["token_uri"], data=data)
    with urllib.request.urlopen(req) as r:
        tok = json.load(r)
    c["access_token"] = tok["access_token"]
    c["expiry_date"] = int((time.time() + tok.get("expires_in", 3600)) * 1000)
    _save(c)
    return c["access_token"]


_QUOTA_FILE = os.path.join(GMAIL_DIR, "quota_count.json")


def _bump_quota(n=1):
    """B105: cheap local call counter (not Google's real quota units, just a call
    count) so callers can self-throttle before hitting Gmail's per-user rate limit.
    Best-effort; a write failure here must never break the actual API call."""
    try:
        day = time.strftime("%Y-%m-%d")
        try:
            with open(_QUOTA_FILE) as f:
                q = json.load(f)
        except (OSError, json.JSONDecodeError):
            q = {}
        q[day] = q.get(day, 0) + n
        # keep it small: only today + yesterday
        for k in list(q):
            if k not in (day, time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))):
                del q[k]
        with open(_QUOTA_FILE, "w") as f:
            json.dump(q, f)
    except Exception:
        pass


def quota_today():
    """Return today's call count (B105), or 0 if never tracked."""
    try:
        with open(_QUOTA_FILE) as f:
            return json.load(f).get(time.strftime("%Y-%m-%d"), 0)
    except (OSError, json.JSONDecodeError):
        return 0


def _request(url, method="GET", data=None):
    """Shared HTTP call with exponential backoff (B106) on 429/5xx (Gmail's documented
    transient-error set). 4 attempts: 0.5s, 1s, 2s, 4s. 404s and other 4xx raise
    immediately (not transient — retrying won't help and callers like list_history()
    depend on a fast, clean 404 to detect a stale cursor)."""
    headers = {"Authorization": f"Bearer {token()}"}
    body = None
    if data is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(data).encode("utf-8")
    delay = 0.5
    for attempt in range(4):
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            _bump_quota()
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f"{e.code}: {e.read().decode()[:300]}")
        except (http.client.IncompleteRead, http.client.HTTPException,
                urllib.error.URLError, TimeoutError, ConnectionError) as e:
            # Connection-level transients (truncated chunked read, reset, DNS blip) used to
            # escape the 429/5xx retry above and crash whole agents mid-chain with a raw
            # IncompleteRead traceback (mail_sender_scores, 2026-07-11 audit). Same backoff.
            if attempt < 3:
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f"network: {type(e).__name__}: {e}"[:300])
    raise RuntimeError("unreachable")  # loop always returns or raises


def _get(url):
    return _request(url, "GET")


def _post(url, data=None):
    return _request(url, "POST", data)


def search(query, max_results=100):
    """Return list of {id, threadId} matching a Gmail search query."""
    out, page = [], None
    while len(out) < max_results:
        params = {"q": query, "maxResults": min(100, max_results - len(out))}
        if page:
            params["pageToken"] = page
        res = _get(f"{API}/messages?" + urllib.parse.urlencode(params))
        out += res.get("messages", [])
        page = res.get("nextPageToken")
        if not page:
            break
    return out


def current_history_id():
    """Return the mailbox's current historyId (profile call) — the seed for a fresh
    cursor when none exists yet or after a stale-cursor reset."""
    p = _get(f"{API}/profile")
    return p.get("historyId")


class HistoryStale(Exception):
    """Raised when startHistoryId is too old for the History API (Gmail expires
    history ~7-14d back). Caller should fall back to a full search (B109) and
    re-seed the cursor via current_history_id()."""


def list_history(start_history_id, max_results=500, types=("messageAdded",)):
    """Incremental sync (B81): return new/changed message ids since start_history_id,
    plus the mailbox's latest historyId to save as the next cursor.

    Returns {"message_ids": [...], "history_id": "<latest>"}. message_ids is deduped,
    insertion-order, built only from messagesAdded records (types is a placeholder for
    future filtering — Gmail's API doesn't let us filter server-side by change type).
    Raises HistoryStale on a 404 (cursor older than Gmail's history retention window);
    callers should catch this and fall back to search() with a full-search-window query.
    """
    ids, seen, page, latest = [], set(), None, None
    while True:
        params = {"startHistoryId": start_history_id, "maxResults": min(500, max_results)}
        if page:
            params["pageToken"] = page
        url = f"{API}/history?" + urllib.parse.urlencode(params)
        try:
            res = _get(url)
        except RuntimeError as e:
            if str(e).startswith("404"):
                raise HistoryStale(str(e)) from e
            raise
        latest = res.get("historyId", latest)
        for h in res.get("history", []):
            for added in h.get("messagesAdded", []) or []:
                mid = (added.get("message") or {}).get("id")
                if mid and mid not in seen:
                    seen.add(mid)
                    ids.append(mid)
        page = res.get("nextPageToken")
        if not page or len(ids) >= max_results:
            break
    return {"message_ids": ids[:max_results], "history_id": latest or start_history_id}


def get_messages_metadata(mids, fields=("From", "To", "Subject", "Date")):
    """B101/B102: batch-friendly metadata-only fetch (no body download) for a list of
    message ids. Gmail's REST API has no true batch endpoint without the batch/multipart
    machinery, so this is sequential HTTP but format=metadata is far cheaper per-call than
    format=full (no body parts to transfer/decode) — use this for classify-pass triage,
    reserve get_message(..., 'full') for the messages that actually need a body.

    Returns every header actually present under its lowercased name (so a caller asking
    for fields=("To",) gets out[i]["to"], not just the from/subject/date shorthand),
    PLUS the familiar from/subject/date keys always populated when those headers were
    requested — this keeps existing callers (e.g. mail_sync's fallback path) working
    while letting new callers (e.g. sender-score seeding, which needs "To" on sent mail)
    ask for exactly the headers they need instead of guessing.
    """
    out = []
    field_q = "&".join(f"metadataHeaders={f}" for f in fields)
    for mid in mids:
        try:
            m = _get(f"{API}/messages/{mid}?format=metadata&{field_q}")
        except RuntimeError:
            continue
        hdrs = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
        rec = {
            "id": mid,
            "threadId": m.get("threadId", ""),
            "snippet": m.get("snippet", ""),
            "internalDate": m.get("internalDate", ""),
            "labelIds": m.get("labelIds", []),
        }
        for f in fields:
            rec[f.lower()] = hdrs.get(f.lower(), "")
        out.append(rec)
    return out


import re, html as _html


def _strip_html(s):
    s = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|tr|td|th|li|h[1-6]|table)>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = _html.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    return s.strip()


def _walk_parts(payload, plain, htm, attachments=None):
    mt = payload.get("mimeType", "")
    body = payload.get("body", {})
    filename = payload.get("filename", "")
    if filename and attachments is not None:
        # B88/B110: filename + size only, lazy — never downloads the attachment's
        # actual bytes here (that would need a separate .../attachments/{id} GET,
        # which this module deliberately doesn't call unless a future feature needs
        # the real file, keeping every classify-pass fetch cheap).
        attachments.append({"filename": filename, "mimeType": mt, "size": body.get("size", 0)})
    if body.get("data"):
        raw = base64.urlsafe_b64decode(body["data"]).decode("utf-8", "replace")
        if mt == "text/plain":
            plain.append(raw)
        elif mt == "text/html":
            htm.append(raw)
    for p in payload.get("parts", []) or []:
        _walk_parts(p, plain, htm, attachments)


def get_thread_metadata(thread_id, fields=("From", "To", "Subject", "Date")):
    """B87/B112: thread-level state, not message-level — one call returns every
    message in the thread with metadata headers only (no bodies), so a caller can
    decide whether a thread is "long" (needs summarizing) before paying for any
    body fetches. Returns list of message dicts in the same shape as
    get_messages_metadata()'s per-item output, oldest first (Gmail's natural order)."""
    field_q = "&".join(f"metadataHeaders={f}" for f in fields)
    try:
        t = _get(f"{API}/threads/{thread_id}?format=metadata&{field_q}")
    except RuntimeError:
        return []
    out = []
    for m in t.get("messages", []):
        hdrs = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
        rec = {"id": m.get("id", ""), "threadId": thread_id, "snippet": m.get("snippet", ""),
               "internalDate": m.get("internalDate", ""), "labelIds": m.get("labelIds", [])}
        for f in fields:
            rec[f.lower()] = hdrs.get(f.lower(), "")
        out.append(rec)
    return out


def get_message(mid, fmt="full"):
    m = _get(f"{API}/messages/{mid}?format={fmt}")
    hdrs = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
    plain, htm, attachments = [], [], []
    _walk_parts(m.get("payload", {}), plain, htm, attachments)
    body = "\n".join(plain).strip()
    if len(body) < 40 and htm:
        body = _strip_html("\n".join(htm))
    return {
        "id": mid,
        "date": hdrs.get("date", ""),
        "from": hdrs.get("from", ""),
        "subject": hdrs.get("subject", ""),
        "snippet": m.get("snippet", ""),
        "internalDate": m.get("internalDate", ""),
        "body": body,
        "attachments": attachments,  # B88: [{"filename","mimeType","size"}], [] if none
    }


# --- Label operations -------------------------------------------------------
# RAILS: this module is read-only against Gmail EXCEPT label create/apply, and even
# those are hard-scoped to the "brain/" namespace (organization, not communication —
# never send/trash/delete/archive/modify-outside-labels here). Every function below
# refuses to touch a label that doesn't start with "brain/".
LABEL_PREFIX = "brain/"


def _require_brain_label(name):
    if not name.startswith(LABEL_PREFIX):
        raise ValueError(f"refusing to touch non-brain label {name!r} (must start with {LABEL_PREFIX!r})")


_label_cache = {"t": 0.0, "by_name": {}}


def list_labels(force=False):
    """All labels on the account -> {name: id}, cached 5min (labels rarely change)."""
    if not force and time.time() - _label_cache["t"] < 300 and _label_cache["by_name"]:
        return _label_cache["by_name"]
    res = _get(f"{API}/labels")
    by_name = {l["name"]: l["id"] for l in res.get("labels", [])}
    _label_cache.update(t=time.time(), by_name=by_name)
    return by_name


def get_or_create_brain_label(name):
    """name like 'brain/triaged' or 'brain/vip'. Creates it (nested under a visible
    'brain' parent in Gmail's label list) if missing, returns the label id."""
    _require_brain_label(name)
    labels = list_labels()
    if name in labels:
        return labels[name]
    body = {
        "name": name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    }
    created = _post(f"{API}/labels", body)
    list_labels(force=True)  # refresh cache so the next call sees it
    return created["id"]


def apply_label(mid, label_name):
    """Apply one brain/* label to one message id. Never removes labels, never touches
    any other field (no read/unread, no star, no archive — labels.modify with only
    addLabelIds)."""
    _require_brain_label(label_name)
    label_id = get_or_create_brain_label(label_name)
    return _post(f"{API}/messages/{mid}/modify", {"addLabelIds": [label_id], "removeLabelIds": []})


def apply_labels_batch(mids, label_name):
    """Apply one brain/* label to many message ids; returns count applied, skips (not
    raises) on a per-message failure so one bad id doesn't kill the whole batch (B120)."""
    ok = 0
    for mid in mids:
        try:
            apply_label(mid, label_name)
            ok += 1
        except Exception:
            continue
    return ok


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "in:anywhere flight"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 25
    msgs = search(q, n)
    print(f"# {len(msgs)} hits for: {q}\n")
    for mm in msgs:
        d = get_message(mm["id"], "metadata")
        print(f'{d["date"][:25]:28} | {d["from"][:32]:34} | {d["subject"][:60]}')
