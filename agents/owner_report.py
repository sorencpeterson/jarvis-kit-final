#!/usr/bin/env python3
"""Owner Number report: the Monday "state of the business" number [OWNER] sells as
the $5k AI Ops Install, pointed at himself. All local except ONE read-only GHL
call (opportunities search, for the stuck-deals line; any failure degrades to an
honest "unavailable" note). Everything else mirrors /api/plan's math directly
off store/ so this never depends on the server being up.

Covers: MONEY (closed MTD vs plan, need/day, p50 forecast), PIPELINE MOVED THIS
WEEK (warm calls worked, proposals sent/opened, job applications/interviews),
WAITING ON YOU (pending replies), and a blunt one-line verdict.

Writes store/owner_report.md, feeds the dashboard, pushes a phone summary.
Idempotent per ISO week (front line carries a YYYY-Www stamp); pass --force to
rebuild anyway. Runs in the morning chain but morning.sh gates the whole call to
Mondays (see the weekday check below, mirrored there so a stray manual run on
any other day is still a no-op unless --force).
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import planner  # noqa: E402
from store_lib import LOCAL_TZ, now_iso  # noqa: E402

CONFIG = ROOT / "store" / "config.json"
LEDGER = ROOT / "store" / "ledger.jsonl"
FORECAST_CLOSE = ROOT / "store" / "forecast_close.json"
WARM_DISPO = ROOT / "store" / "warm_dispo.jsonl"
REPLIES = ROOT / "store" / "replies.jsonl"
PROPOSALS = ROOT / "store" / "proposals.jsonl"
JOBS = ROOT / "store" / "jobs.jsonl"
OUT = ROOT / "store" / "owner_report.md"


def _iso_week(d: date | None = None) -> str:
    d = d or date.today()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _load_jsonl_dedup(path: Path) -> list[dict]:
    """Last-write-wins by id — same discipline as reply_watch/proposal_factory/
    jobs.load_jobs/win_loss: these stores append a fresh line per edit, so only
    the latest record per id should count."""
    if not path.exists():
        return []
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            continue
        rid = r.get("id")
        if rid is None:
            continue
        if rid not in by_id:
            order.append(rid)
        by_id[rid] = r
    return [by_id[i] for i in order]


def _in_week(ts: str, week: str) -> bool:
    return bool(ts) and _iso_week_of(ts) == week


def _iso_week_of(ts: str) -> str:
    try:
        # date.fromisoformat handles "YYYY-MM-DD..." prefixes fine once sliced.
        return _iso_week(date.fromisoformat(ts[:10]))
    except ValueError:
        return ""


def _money(week: str) -> dict:
    """Mirrors /api/plan (app/server.py) exactly: same target/closed/p50/need-
    per-day math, read straight from store/ instead of over HTTP."""
    cfg = {}
    try:
        cfg = json.loads(CONFIG.read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    month = now_iso()[:7]
    target = int((cfg.get("plan") or {}).get(month) or 0)
    closed = 0.0
    try:
        for line in LEDGER.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            x = json.loads(line)
            if x.get("kind") in ("won", "payment", "closed") and (x.get("ts") or "")[:7] == month:
                closed += float(x.get("amount") or 0)
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    p50 = 0
    try:
        p50 = json.loads(FORECAST_CLOSE.read_text()).get("p50") or 0
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    import calendar
    today = date.today()
    days_left = calendar.monthrange(today.year, today.month)[1] - today.day
    need_per_day = round(max(0, target - closed) / max(1, days_left)) if target else 0
    return {"month": month, "target": target, "closed": round(closed), "p50": p50,
            "days_left": days_left, "need_per_day": need_per_day}


def _warm(week: str) -> dict:
    rows = _load_jsonl_dedup(WARM_DISPO)
    this_week = [r for r in rows if _in_week(r.get("ts", ""), week)]
    by_dispo = Counter(r.get("dispo", "(none)") for r in this_week)
    return {"worked": len(this_week), "by_dispo": dict(by_dispo),
            "booked": by_dispo.get("booked", 0)}


def _replies(week: str) -> dict:
    rows = _load_jsonl_dedup(REPLIES)
    sent = sum(1 for r in rows if r.get("status") == "sent" and _in_week(r.get("sent_at", ""), week))
    pending = sum(1 for r in rows if r.get("status") == "pending")
    return {"sent": sent, "pending": pending}


def _proposals(week: str) -> dict:
    rows = _load_jsonl_dedup(PROPOSALS)
    staged = sum(1 for r in rows if r.get("status") == "staged" and _in_week(r.get("created", ""), week))
    sent = sum(1 for r in rows if r.get("status") == "sent" and _in_week(r.get("sent_at", ""), week))
    opened = sum(1 for r in rows if (r.get("opens") or 0) > 0 and _in_week(r.get("opened_at", ""), week))
    return {"staged": staged, "sent": sent, "opened": opened}


def _jobs(week: str) -> dict:
    rows = _load_jsonl_dedup(JOBS)
    applied = sum(1 for r in rows if _in_week(r.get("applied_at", ""), week))
    confirmed_now = sum(1 for r in rows if r.get("status") == "confirmed")
    interviews_now = sum(1 for r in rows if r.get("status") == "interview")
    return {"applied": applied, "confirmed_now": confirmed_now, "interviews_now": interviews_now}


STUCK_DAYS = 14  # matches defib.py's STALE_DAYS: untouched 2+ weeks = stuck


def _stuck_from_opportunities(opps: list, today: date) -> list[dict]:
    """Pure filter (fixture-testable): open opportunities whose updatedAt is more
    than STUCK_DAYS days old. No usable timestamp -> can't call it stuck (same
    skip-don't-guess rule defib.py uses). Sorted stalest first."""
    stuck = []
    for o in opps:
        if not isinstance(o, dict) or o.get("status") != "open":
            continue
        upd = str(o.get("updatedAt") or o.get("lastStatusChangeAt") or "")[:10]
        try:
            upd_d = date.fromisoformat(upd)
        except ValueError:
            continue
        days = (today - upd_d).days
        if days > STUCK_DAYS:
            name = o.get("name") or (o.get("contact") or {}).get("name") or "?"
            try:
                value = float(o.get("monetaryValue") or 0)
            except (TypeError, ValueError):
                value = 0.0
            stuck.append({"name": str(name), "value": value, "days": days})
    stuck.sort(key=lambda d: -d["days"])
    return stuck


def _stuck_deals() -> str | None:
    """Build-queue #25: open GHL opportunities untouched > STUCK_DAYS days.
    Rung-1 READ-ONLY GET against /opportunities/search (shape verified live
    2026-07-07: opportunities[].status/name/monetaryValue/updatedAt + contact.name;
    same call close_prob._fetch_live_deals and /api/deals already make). This is
    the report's one external call; any failure returns an honest 'unavailable'
    note, never a crash and never a fake 'no stuck deals'."""
    try:
        import ghl_social
        loc = ""
        for line in (ghl_social.GHL / ".env").read_text().splitlines():
            if line.startswith("GHL_LOCATION_ID="):
                loc = line.split("=", 1)[1].strip()
                break
        if not loc:
            return "stuck-deal check unavailable (no GHL location configured)"
        out = ghl_social._api(["GET", f"/opportunities/search?location_id={loc}&limit=100"])
        j = json.loads(out[out.find("{"):], strict=False)
        opps = j.get("opportunities")
        if not isinstance(opps, list):
            return "stuck-deal check unavailable (GHL response had no opportunities list)"
    except Exception:  # noqa: BLE001
        return "stuck-deal check unavailable (GHL call failed)"
    stuck = _stuck_from_opportunities(opps, date.today())
    if not stuck:
        return None  # genuinely nothing stuck -> no line in the report
    top = ", ".join(f"{d['name'][:48]} (${d['value']:,.0f}, {d['days']}d untouched)"
                    for d in stuck[:3])
    more = f", +{len(stuck) - 3} more" if len(stuck) > 3 else ""
    return f"{len(stuck)} open deal(s) untouched >{STUCK_DAYS}d: {top}{more}"


def _verdict(money: dict, warm: dict, props: dict, jobs_: dict) -> str:
    if money["target"] <= 0:
        return "no plan number set for this month, nothing to hold against."
    gap = money["target"] - money["closed"]
    if gap <= 0:
        return f"plan's already hit for {money['month']}, {money['closed']} against {money['target']}. bank it, don't coast."
    verdict = f"plan needs ${money['need_per_day']}/day and {money['days_left']} days left to hit ${money['target']}."
    if warm["worked"] == 0 and props["sent"] == 0:
        return verdict + " nothing moved in the pipeline this week, the 10-block is the whole game."
    if warm["worked"] > 0:
        return verdict + f" the 10-block is the whole game this week, {warm['worked']} worked so far."
    return verdict + f" {props['sent']} proposals out this week, follow those before sourcing more."



def _linkedin_digest() -> str | None:
    """LinkedIn lane weekly line (contract: agents/li_digest.py writes the store)."""
    try:
        d = json.loads((ROOT / "store" / "li_digest.json").read_text())
        return (f"linkedin: sourced {d['sourced']}, sent {d['sent']}, "
                f"accepted {d['accepted']}, replied {d['replied']}")
    except (OSError, ValueError, json.JSONDecodeError, KeyError):
        return None


def _build_report(week: str) -> str:
    money = _money(week)
    warm = _warm(week)
    replies = _replies(week)
    props = _proposals(week)
    jobs_ = _jobs(week)
    stuck_note = _stuck_deals()

    lines = []
    lines.append(f"# owner number — {week}")
    lines.append("")
    # R5 rubric: the first line is what to DO, not what happened
    act = "Make the 10-block calls today"
    try:
        att = json.loads((ROOT / "store" / "attention.json").read_text())
        if att.get("top_line") and "clear" not in att["top_line"]:
            act = "First: " + att["top_line"]
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    lines.append("DO THIS FIRST: " + act)
    lines.append("")
    lines.append("## MONEY")
    lines.append(f"closed MTD: ${money['closed']} vs plan ${money['target']} ({money['month']})")
    lines.append(f"need/day: ${money['need_per_day']}, {money['days_left']} days left")
    lines.append(f"p50 forecast: ${money['p50']}")
    lines.append("")
    lines.append("## PIPELINE MOVED THIS WEEK")
    lines.append(f"calls worked {warm['worked']}, booked {warm['booked']}")
    li = _linkedin_digest()
    if li:
        lines.append(li)
    lines.append(f"proposals staged {props['staged']}, sent {props['sent']}, opened {props['opened']}")
    lines.append(f"job apps sent {jobs_['applied']}, interviews live {jobs_['interviews_now']}")
    lines.append("")
    lines.append("## WAITING ON YOU")
    lines.append(f"replies pending: {replies['pending']}")
    lines.append(f"replies sent this week: {replies['sent']}")
    if stuck_note:
        lines.append(f"stuck deals: {stuck_note}")
    lines.append("")
    lines.append("## VERDICT")
    lines.append(_verdict(money, warm, props, jobs_))
    lines.append("")
    return "\n".join(lines)


def _existing_week(text: str) -> str:
    first = text.splitlines()[0] if text else ""
    # front line looks like "# owner number — 2026-W27"
    if " — " in first:
        return first.rsplit(" — ", 1)[-1].strip()
    return ""


def run(force: bool = False) -> dict:
    week = _iso_week()
    if OUT.exists() and not force:
        cur = OUT.read_text()
        if _existing_week(cur) == week:
            print(f"owner report already built for {week}, skipping (--force to rebuild)")
            return {"skipped": True, "week": week}

    report = _build_report(week)
    OUT.write_text(report)

    body_lines = report.splitlines()
    first_line = next((l for l in body_lines if l.strip()), "owner report ready")
    closed_line = next((l for l in body_lines if l.startswith("closed MTD")), first_line)
    planner.feed_add("money", closed_line)

    notify_body = "\n".join(l for l in body_lines[:3])
    pushed = planner.notify("Monday number", notify_body, tags="moneybag")

    print(report)
    print(f"[pushed={pushed}]")
    return {"skipped": False, "week": week, "report": report, "pushed": pushed}


if __name__ == "__main__":
    force = "--force" in sys.argv
    # Belt-and-suspenders: morning.sh already gates the call to Mondays, but a
    # direct/manual invocation should behave the same way unless --force.
    if not force and datetime.now(LOCAL_TZ).isoweekday() != 1:
        print("owner_report: not Monday, nothing to do (use --force to override)")
        sys.exit(0)
    run(force=force)
