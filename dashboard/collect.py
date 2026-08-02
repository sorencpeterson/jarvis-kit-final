"""Gather all data the dashboard renders, from the sources that already exist.

Kept separate from rendering so panels can be added without touching the HTML.
Everything degrades gracefully — a dead source returns an empty/expandable shape,
never an exception that breaks the build.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from store_lib import LOCAL_TZ, load_todos  # noqa: E402

WORKSPACE = Path.home() / "Claude"
SCHEDULED_DIR = WORKSPACE / "Scheduled"
SCHENGEN_DIR = WORKSPACE / "gmail" / "schengen"
GHL_API = WORKSPACE / "playwright-project/automations/ghl/gohighlevel-cli/api.sh"
GOALS = Path(__file__).resolve().parent.parent / "store" / "goals.json"
DONE_SEEN = Path(__file__).resolve().parent / "done_seen.json"


def _now():
    return datetime.now(LOCAL_TZ)


def _parse_dt(iso):
    try:
        return datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None


def _load_done_seen() -> dict:
    try:
        return json.loads(DONE_SEEN.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_done_seen(seen: dict) -> None:
    try:
        DONE_SEEN.write_text(json.dumps(seen))
    except OSError:
        pass


def todos_buckets() -> dict:
    todos = load_todos()
    today = _now().date()
    today_s = today.isoformat()
    week = today + timedelta(days=7)
    inbox, due_today, upcoming, done_today = [], [], [], []
    # "done" records carry no completion timestamp (completing a todo only flips status;
    # scheduled_time/created keep whatever they were before), so keying "done today" off
    # them undercounts every overdue task finished today. This builds every ~10 min
    # (run.sh), so stamp the first run that sees an id as done and reuse that date after --
    # a close proxy for the real completion date. Pruned to ids still done, so it can't grow
    # unbounded.
    bootstrap = not DONE_SEEN.exists()  # first run ever: seed silently, don't backfill today's count
    seen = _load_done_seen()
    changed = False
    live_done_ids = set()
    for t in todos:
        st = t.get("status")
        sched = _parse_dt(t.get("scheduled_time"))
        if st == "inbox":
            inbox.append(t)
        elif st in ("scheduled", "doing"):
            if sched and sched.date() == today:
                due_today.append(t)
            elif sched and today < sched.date() <= week:
                upcoming.append(t)
            elif sched and sched.date() < today:
                due_today.append(t)  # overdue surfaces under Today
            else:
                inbox.append(t)
        elif st == "done":
            tid = t.get("id")
            if tid:
                live_done_ids.add(tid)
                first_seen = seen.get(tid)
                if first_seen is None:
                    # bootstrap: true completion date unknown, seed with a sentinel that can
                    # never match a real today_s, so it stays silent today AND every day after
                    first_seen = seen[tid] = ("backfill" if bootstrap else today_s)
                    changed = True
                if first_seen == today_s:
                    done_today.append(t)
    stale = set(seen) - live_done_ids
    if changed or stale:
        for k in stale:
            del seen[k]
        _save_done_seen(seen)
    due_today.sort(key=lambda t: t.get("scheduled_time") or "")
    upcoming.sort(key=lambda t: t.get("scheduled_time") or "")
    return {
        "today": due_today, "inbox": inbox,
        "upcoming": upcoming, "done_today": done_today,
        "total_open": len(inbox) + len(due_today) + len(upcoming),
    }


def scheduled_agents() -> list[dict]:
    """Read the 4 Scheduled/*/SKILL.md — name, cadence hint, one-line purpose."""
    agents = []
    if not SCHEDULED_DIR.exists():
        return agents
    for d in sorted(SCHEDULED_DIR.iterdir()):
        skill = d / "SKILL.md"
        if not skill.is_file():
            continue
        text = skill.read_text(errors="ignore")
        name = d.name.replace("-", " ").title()
        m = re.search(r'^name:\s*(.+)$', text, re.M)
        if m:
            name = m.group(1).strip().strip('"')
        desc = ""
        m = re.search(r'^description:\s*(.+)$', text, re.M)
        if m:
            desc = m.group(1).strip().strip('"')
        else:  # first non-heading, non-frontmatter line
            for line in text.splitlines():
                s = line.strip()
                if s and not s.startswith("#") and not s.startswith("---") and ":" not in s[:12]:
                    desc = s
                    break
        cad = ""
        cm = re.search(r'(daily|every day|each morning|hourly|weekly|\d{1,2}\s?(?:am|pm)|cron[^\n]*)',
                       text, re.I)
        if cm:
            cad = cm.group(1)
        agents.append({"name": name, "desc": desc[:120], "cadence": cad})
    return agents


# build_state() (server.py) calls ghl_status() on EVERY dashboard poll -- every 20s per
# open tab. Uncached, that fired a perl+bash+GHL-CLI subprocess (~0.5s, up to a 15s alarm)
# thousands of times a day for a contact count that barely moves minute to minute. A 5-min
# TTL cache (mirrors server.py's _MONEY_CACHE) collapses that to ~1 real call per 5 min.
_GHL_CACHE = {"t": 0.0, "data": None}
_GHL_TTL_S = 300


def ghl_status(force: bool = False) -> dict:
    """Best-effort live GHL read; degrades to a 'disconnected' card on any failure.
    Cached 5 min (pass force=True to bypass) so the 20s dashboard poll can't machine-gun
    a subprocess. A failure result is cached too, so a token-refresh state also stops
    hammering; force=True is the manual-refresh escape hatch."""
    now = time.monotonic()
    if not force and _GHL_CACHE["data"] is not None and now - _GHL_CACHE["t"] < _GHL_TTL_S:
        return _GHL_CACHE["data"]
    data = _ghl_status_uncached()
    _GHL_CACHE.update(t=now, data=data)
    return data


def _ghl_status_uncached() -> dict:
    if not GHL_API.exists():
        return {"ok": False, "msg": "GHL CLI not found"}
    try:
        # perl alarm caps the call so a hung/slow API never stalls the build
        res = subprocess.run(
            ["perl", "-e", "alarm 15; exec @ARGV", "bash", str(GHL_API),
             "GET", "/contacts/", "--loc", "--query", "limit=1"],
            capture_output=True, text=True, timeout=20,
        )
        out = (res.stdout or "") + (res.stderr or "")
        if "403" in out or "401" in out:
            return {"ok": False, "msg": "token needs refresh"}
        m = re.search(r'"total"\s*:\s*(\d+)', out)
        if m:
            return {"ok": True, "contacts": int(m.group(1))}
        if res.returncode == 0 and out.strip():
            return {"ok": True, "contacts": None}
        return {"ok": False, "msg": "no response"}
    except Exception:
        return {"ok": False, "msg": "unreachable"}


def goals() -> list[dict]:
    if not GOALS.exists():
        return []
    try:
        data = json.loads(GOALS.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def schengen() -> dict:
    """Live Schengen 90/180 status from gmail/schengen/tracker.py (pure date math,
    offline). Auto-updates as new flights land in trips.json. Degrades to {ok:False}."""
    try:
        import importlib.util
        tp = SCHENGEN_DIR / "tracker.py"
        if not tp.exists():
            return {"ok": False}
        spec = importlib.util.spec_from_file_location("schengen_tracker", tp)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        st = mod.status()
        st["ok"] = True
        return st
    except Exception:
        return {"ok": False}


def collect_all(include_ghl: bool = True) -> dict:
    return {
        "todos": todos_buckets(),
        "agents": scheduled_agents(),
        "ghl": ghl_status() if include_ghl else {"ok": False, "msg": "skipped"},
        "goals": goals(),
        "schengen": schengen(),
        "generated": _now().strftime("%a %b %-d · %-I:%M %p"),
    }


if __name__ == "__main__":
    print(json.dumps(collect_all(), indent=2, default=str))
