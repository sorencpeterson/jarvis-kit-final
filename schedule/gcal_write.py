#!/usr/bin/env python3
"""Write scheduled todos into Google Calendar (confirm-before-write).

The brain proposes time blocks (sets scheduled_time + duration_min on todos and
status "scheduled"); this executor turns those into real calendar events and
writes the gcal_event_id back into the store. It NEVER picks times itself and
NEVER writes events for todos still in "inbox".

Usage:
  uv run python schedule/gcal_write.py --list     # show what WOULD be created
  uv run python schedule/gcal_write.py --confirm   # actually create the events

One-time setup ([OWNER]'s hands) — see schedule/SETUP.md:
  1. Google Cloud Console: enable Calendar API, make a "Desktop app" OAuth client.
  2. Download client_secret.json into schedule/credentials/.
  3. First --confirm run opens a browser to consent; token cached as token.json.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from store_lib import append_todo, compact, load_todos  # noqa: E402

CREDS = Path(__file__).resolve().parent / "credentials"
CLIENT_SECRET = CREDS / "client_secret.json"
TOKEN = CREDS / "token.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def pending() -> list[dict]:
    """Scheduled todos with a time but no calendar event yet."""
    return [
        t for t in load_todos()
        if t.get("status") == "scheduled"
        and t.get("scheduled_time")
        and not t.get("gcal_event_id")
    ]


def _service():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        print("Missing deps. Install once:\n"
              "  uv pip install google-api-python-client google-auth-oauthlib",
              file=sys.stderr)
        raise SystemExit(2)

    if not CLIENT_SECRET.exists():
        print(f"Missing {CLIENT_SECRET}. See schedule/SETUP.md.", file=sys.stderr)
        raise SystemExit(2)

    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN.write_text(creds.to_json())
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=creds)


def read_events(days_back: int = 31, days_fwd: int = 200) -> list[dict]:
    """List primary-calendar events in a window → [{date, text, when}]."""
    from datetime import timezone
    svc = _service()
    tmin = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
    tmax = (datetime.now(timezone.utc) + timedelta(days=days_fwd)).isoformat()
    res = svc.events().list(calendarId="primary", timeMin=tmin, timeMax=tmax,
                            singleEvents=True, orderBy="startTime", maxResults=250).execute()
    out = []
    for e in res.get("items", []):
        s = e.get("start", {})
        dt = s.get("dateTime") or s.get("date")
        if dt:
            out.append({"date": dt[:10], "text": e.get("summary", "(busy)"), "when": dt})
    return out


def main() -> int:
    if "--connect" in sys.argv:
        from datetime import timezone
        svc = _service()  # triggers the one-time browser consent
        now = datetime.now(timezone.utc).isoformat()
        res = svc.events().list(calendarId="primary", timeMin=now, maxResults=5,
                                singleEvents=True, orderBy="startTime").execute()
        evs = res.get("items", [])
        print("Connected to Google Calendar ✓  (token cached)")
        for e in evs:
            s = e.get("start", {})
            print("  ·", s.get("dateTime", s.get("date", "")), e.get("summary", ""))
        if not evs:
            print("  (no upcoming events found — connection still works)")
        return 0

    todos = pending()
    if not todos:
        print("Nothing to schedule (no 'scheduled' todos without a calendar event).")
        return 0

    print("Will create these events:")
    for t in todos:
        dur = t.get("duration_min") or 30
        print(f"  · {t['scheduled_time']}  ({dur}m)  {t['text']}")

    if "--confirm" not in sys.argv:
        print("\nDry run. Re-run with --confirm to create them in Google Calendar.")
        return 0

    svc = _service()
    written = 0
    for t in todos:
        start = datetime.fromisoformat(t["scheduled_time"])
        dur = t.get("duration_min") or 30
        end = start + timedelta(minutes=dur)
        body = {
            "summary": t["text"],
            "description": f"second-brain · {t['id']}" + (f" · {t['project']}" if t.get("project") else ""),
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
            "reminders": {"useDefault": False,
                          "overrides": [{"method": "popup", "minutes": 10}]},
        }
        ev = svc.events().insert(calendarId="primary", body=body).execute()
        updated = dict(t)
        updated["gcal_event_id"] = ev["id"]
        append_todo(updated)
        written += 1
        print(f"  ✓ {t['text']}  ->  {ev.get('htmlLink','')}")
    compact()
    print(f"Created {written} event(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
