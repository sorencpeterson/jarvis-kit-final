"""Push content straight into GHL Social Planner (schedules + auto-posts).

Uses the existing GHL public-API CLI (api.sh) — no new auth. Lists connected
social accounts and creates SCHEDULED posts. Text-only for now (the CLI can't
multipart-upload media; card images need GHL media hosting — see note in server).
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

GHL = Path.home() / "Claude" / "playwright-project/automations/ghl/gohighlevel-cli"
API = GHL / "api.sh"


def _api(args: list[str], timeout=40) -> str:
    try:
        r = subprocess.run(["bash", str(API)] + args, cwd=str(GHL),
                           capture_output=True, text=True, timeout=timeout)
        return ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:
        return f"error: {e}"


def list_accounts() -> list[dict]:
    out = _api(["GET", "/social-media-posting/{loc}/accounts"])
    try:
        data = json.loads(out[out.find("{"):])
    except (json.JSONDecodeError, ValueError):
        return []
    accts = data.get("results", {}).get("accounts", [])
    return [{"id": a.get("id"), "platform": a.get("platform"),
             "name": a.get("name"), "expired": a.get("isExpired", False)}
            for a in accts]


_USER = [None]


def user_id() -> str | None:
    """The GHL user the posts are authored as (prefers [OWNER]). Cached."""
    if _USER[0]:
        return _USER[0]
    out = _api(["GET", "/users/", "--query", "locationId={loc}"])
    try:
        users = json.loads(out[out.find("{"):]).get("users", [])
    except (json.JSONDecodeError, ValueError):
        users = []
    pick = None
    for u in users:
        nm = ((u.get("name") or "") + (u.get("email") or "")).lower()
        if "[OWNER]" in nm or "[OWNER_HANDLE]" in nm:
            pick = u.get("id"); break
    if not pick and users:
        pick = users[0].get("id")
    _USER[0] = pick
    return pick


def upload_media(file_path: str) -> str | None:
    """Upload a local image to GHL media library → returns a public CDN URL."""
    out = _api(["POST", "/medias/upload-file", "--file", str(file_path), "--form", "hosted=false"],
               timeout=90)
    m = re.search(r'"url"\s*:\s*"([^"]+)"', out)
    return m.group(1) if m else None


def create_post(text: str, account_ids: list[str], schedule_iso: str,
                media_url: str | None = None) -> dict:
    body = {"type": "post", "accountIds": account_ids, "summary": text,
            "scheduleDate": schedule_iso, "status": "scheduled",
            "media": [{"url": media_url, "type": "image/png"}] if media_url else [],
            "userId": user_id()}
    out = _api(["POST", "/social-media-posting/{loc}/posts", "--json", json.dumps(body)])
    ok = '"success": true' in out or '"id"' in out or '"_id"' in out
    pid = None
    m = re.search(r'"(?:_id|id)"\s*:\s*"([^"]+)"', out)
    if m:
        pid = m.group(1)
    return {"ok": ok, "id": pid, "raw": out[:200]}


def schedule_dates(n: int, days=None, hour=9, start=None):
    """n slots on the given weekdays (0=Mon..6=Sun) at `hour` local, starting
    `start` (YYYY-MM-DD or datetime; default tomorrow). Returns UTC ISO strings."""
    tz = datetime.now().astimezone().tzinfo  # system local zone (follows [OWNER]'s actual location)
    dayset = set(days) if days else {0, 1, 2, 3, 4}  # default: weekdays
    now = datetime.now(tz)
    if isinstance(start, str) and start:
        try:
            d = datetime.fromisoformat(start).replace(tzinfo=tz)
        except ValueError:
            d = now + timedelta(days=1)
    elif start:
        d = start
    else:
        d = now + timedelta(days=1)
    d = d.replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    guard = 0
    while len(out) < n and guard < 400:
        guard += 1
        if d.weekday() in dayset:
            slot = d.replace(hour=int(hour))
            if slot > now:
                out.append(slot.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"))
        d += timedelta(days=1)
    return out
