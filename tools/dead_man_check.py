#!/usr/bin/env python3
"""dead_man_check — the LOCAL half of a dead-man's switch for the second brain,
plus the design for the cloud-mirror half.

WHAT IT CHECKS (read-only, never writes, always exits 0 with one JSON line):
  1. MORNING FRESHNESS — did the morning chain finish recently? Reads
     store/.morning-done-<date> (the COMPLETION stamp morning.sh touches on
     success, store/.morning-done-YYYY-MM-DD). Staleness mirrors the server's own
     definition in app/server.py api_health() (2026-07-06 audit H4): stale if the
     newest stamp is >1 day old, OR exactly 1 day old and it's already past the
     08:00 local window when today's stamp should exist.
  2. OFF-MACHINE BACKUP FRESHNESS — when did a commit last actually leave the Mac?
     autocommit.sh commits hourly but never pushes, so "committed" != "backed up".
     If an `origin` remote + remote-tracking branch exist, we read the commit date
     of origin/<branch> (the newest commit KNOWN to be on the remote) and flag it
     if that's >26h old. If there's no remote yet (true as of this writing —
     backup_verify.py reports "no_remote"), we say so loudly: an absent backup is
     the worst dead-man state, not a passing one.

WHY 26h: autocommit + backup_push are meant to run hourly, so a healthy machine
pushes many times a day. 26h gives one full day plus slack for a late/skipped run
before we call the backup stale — long enough to avoid false alarms, short enough
that a Mac that went dark yesterday trips it today.

EXIT / OUTPUT CONTRACT: prints exactly one line of JSON (the status object) to
stdout and exits 0 regardless of health — it's a probe, not a gate. The caller
(a human, `make doctor`, or a cloud cron) reads the "alert" boolean and the
"reasons" list. Nothing here mutates state; safe to run as often as you like.

    python3 tools/dead_man_check.py            # human/cron: one JSON line
    python3 tools/dead_man_check.py --help      # this docstring
    python3 tools/dead_man_check.py --pretty     # indented JSON for eyeballing

──────────────────────────────────────────────────────────────────────────────
CLOUD-MIRROR DESIGN (NOT BUILT SERVER-SIDE — design only):

The local check above still dies with the Mac. The true external dead-man's
switch is the GitHub Actions canary (.github/workflows/uptime-canary.yml), which
already curls /pub/health from off-machine every 15 min. To let that cloud cron
verify not just "the web server answers" but "the brain's morning + backup are
actually fresh", add a FUTURE public, reveal-nothing endpoint:

    GET /pub/deadman  ->  200 {"ok": true}   when this check would NOT alert
                          503 {"ok": false}  when it WOULD (morning/backup stale)

  Server side it would import this module and call `evaluate()` (below), mapping
  status["alert"] to the HTTP code. It MUST stay content-free (a bare ok flag,
  like /pub/health) since /pub/* is internet-reachable over the tunnel. The
  canary then treats a 503 (or any non-200) on /pub/deadman exactly like a dark
  brain and pushes ntfy — giving a real external check of internal freshness,
  not just "the port is open". This endpoint does NOT exist yet; wiring it into
  app/server.py is a separate, server-side change tracked outside this task.
──────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "store"

# Threshold: how old the last OFF-MACHINE push may be before we alert.
BACKUP_MAX_AGE_HOURS = 26


def _morning_status() -> dict:
    """Freshness of the morning-completion stamp, using the server's own rule."""
    stamps = sorted(STORE.glob(".morning-done-*"))
    if not stamps:
        return {"ok": False, "last": None, "reason": "no morning-done stamp ever"}
    last = stamps[-1].name.replace(".morning-done-", "")
    try:
        done_day = _dt.date.fromisoformat(last)
    except ValueError:
        return {"ok": False, "last": last, "reason": f"unparseable stamp {last!r}"}
    age_days = (_dt.date.today() - done_day).days
    # mirror app/server.py api_health(): stale if >1 day, or ==1 day past 08:00
    stale = age_days > 1 or (age_days == 1 and _dt.datetime.now().hour >= 8)
    reason = "" if not stale else f"morning last completed {last} ({age_days}d ago)"
    return {"ok": not stale, "last": last, "age_days": age_days, "reason": reason}


def _remote_url() -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(ROOT), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=15,
        )
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
    except Exception:  # noqa: BLE001
        return None


def _current_branch() -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=15,
        )
        b = r.stdout.strip()
        return b if r.returncode == 0 and b and b != "HEAD" else None
    except Exception:  # noqa: BLE001
        return None


def _backup_status() -> dict:
    """When did a commit last provably reach the remote? (git log on origin/<branch>)."""
    url = _remote_url()
    if not url:
        return {
            "ok": False,
            "reason": ("no git remote configured — there is NO off-machine backup "
                       "(matches backup_verify.py 'no_remote'). Run: "
                       "git remote add origin <url>"),
            "remote": None,
        }
    branch = _current_branch() or "main"
    ref = f"origin/{branch}"
    try:
        # %cI = committer date, strict ISO 8601. Reads the remote-TRACKING ref
        # (last fetched/pushed state), the newest commit known to be on origin.
        r = subprocess.run(
            ["git", "-C", str(ROOT), "log", "-1", "--format=%cI", ref],
            capture_output=True, text=True, timeout=15,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"git log failed: {e}", "remote": url}
    if r.returncode != 0 or not r.stdout.strip():
        return {
            "ok": False,
            "reason": (f"remote '{url}' is configured but {ref} is missing — "
                       "nothing pushed yet on this branch. Run tools/backup_push.sh"),
            "remote": url,
        }
    iso = r.stdout.strip()
    try:
        last_dt = _dt.datetime.fromisoformat(iso)
    except ValueError:
        return {"ok": False, "reason": f"unparseable commit date {iso!r}", "remote": url}
    now = _dt.datetime.now(tz=last_dt.tzinfo) if last_dt.tzinfo else _dt.datetime.now()
    age_h = (now - last_dt).total_seconds() / 3600.0
    stale = age_h > BACKUP_MAX_AGE_HOURS
    reason = "" if not stale else (
        f"last off-machine push was {age_h:.1f}h ago (> {BACKUP_MAX_AGE_HOURS}h)"
    )
    return {
        "ok": not stale,
        "last_push": iso,
        "age_hours": round(age_h, 1),
        "reason": reason,
        "remote": url,
    }


def evaluate() -> dict:
    """Return the full dead-man status object. Also used by the cloud-mirror design
    (a future GET /pub/deadman would map status['alert'] to 200/503). Pure/read-only."""
    morning = _morning_status()
    backup = _backup_status()
    reasons = [r for r in (morning.get("reason"), backup.get("reason")) if r]
    alert = not (morning["ok"] and backup["ok"])
    return {
        "ts": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "alert": alert,
        "ok": not alert,
        "reasons": reasons,
        "morning": morning,
        "backup": backup,
        "backup_max_age_hours": BACKUP_MAX_AGE_HOURS,
    }


def main(argv: list[str]) -> int:
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    status = evaluate()
    if "--pretty" in argv:
        print(json.dumps(status, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(status, ensure_ascii=False))
    # Always 0: this is a read-only probe, not a gate. Callers read status["alert"].
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
