#!/usr/bin/env python3
"""B1: the "who opened but went quiet" worklist. The highest-intent slice of the
staged $46,800 is people who already LOOKED (proposal opens, page reads) and then
stopped. This merges three silences into ONE ranked re-engage table so [OWNER] dials
from a single list instead of cross-referencing three stores by hand.

WHAT: merges (a) proposals with real engagement (opens > 0 or read_secs > 0) from
      proposal_factory.load_queue() whose status is in PROPOSAL_STATUSES and whose
      last activity is older than QUIET_AFTER_DAYS; (b) dormant conversations from
      store/convo_states.json (health_label cold/stalling, last_signal_days between
      CONVO_MIN_DAYS and CONVO_MAX_DAYS); (c) stale warm contacts from
      ~/Claude/WARM-HITLIST.csv joined against store/warm_dispo.jsonl (never
      dispo'd, or last dispo older than WARM_STALE_DAYS; capped at WARM_CAP so 400+
      unworked warm rows don't drown the two high-signal sources). Ranks by intent
      signal (read_secs + opens + scroll_pct, weighted), then $, then recency.
WHEN: morning chain after convo_state.py and warm_block.py, or ad hoc before a
      call block. Cheap, pure local reads, no LLM call, sub-second.
RAILS: READ-ONLY against every source it touches. Only write is
      store/quiet_worklist.json (full overwrite each run) plus one feed line.
      No GHL calls, no sends, no drafts, no LLM. --dry-run prints and writes nothing.

Note on PROPOSAL_STATUSES: the spec targets status="sent". The store currently has
ZERO sent proposals, but several STAGED ones carry real opens (the /prop page was
viewed), which is the exact "opened but went quiet" signal, so staged is included
until sends exist. skipped/superseded are never included (test artifacts and dead
records live there).

Score weights (tune here, not scattered across the codebase):
  PROPOSAL_BASE      = 20    base for any engaged-but-quiet proposal
  OPENS_W            = 10.0  signal points per open
  READ_SECS_W        = 0.5   signal points per second on the page
  SCROLL_W           = 0.2   signal points per scroll percent
  PRICE_DIV          = 200   + price/PRICE_DIV (attention.py convention: $2,000 adds +10)
  RECENCY_BONUS_MAX  = 5.0   freshly-quiet outranks long-quiet at equal signal/$
  RECENCY_CAP_DAYS   = 30.0  recency bonus hits zero at this age
  QUIET_AFTER_DAYS   = 2.0   engagement younger than this is not "quiet" yet
  CONVO_BASE         = 12    base for a dormant conversation
  CONVO_LABEL_BONUS  = {"stalling": 6, "cold": 3}  (stalling had motion, ranks higher)
  CONVO_MIN_DAYS     = 3     younger than this is just a slow reply, not dormant
  CONVO_MAX_DAYS     = 30    older than this belongs to reactivation campaigns, not this list
  WARM_BASE          = 6     base for a stale warm-hitlist contact
  WARM_TIER_BONUS    = {"1": 8, "2": 3, "3": 1}
  WARM_BOOKED_BONUS  = 5     stage mentions "book" (the stuck booked-call cohort)
  WARM_STALE_DAYS    = 14    a dispo newer than this means "being worked", excluded
  WARM_CAP           = 12    max warm rows admitted to the merged table

Run:  .venv/bin/python agents/quiet_worklist.py [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402

OUT = ROOT / "store" / "quiet_worklist.json"
CONVO_STATES = ROOT / "store" / "convo_states.json"
WARM_CSV = Path.home() / "Claude" / "WARM-HITLIST.csv"
WARM_DISPO = ROOT / "store" / "warm_dispo.jsonl"

# ---- tunable weights (documented above; change here, nowhere else) ----
PROPOSAL_STATUSES = ("sent", "staged")
PROPOSAL_BASE = 20
OPENS_W = 10.0
READ_SECS_W = 0.5
SCROLL_W = 0.2
PRICE_DIV = 200
RECENCY_BONUS_MAX = 5.0
RECENCY_CAP_DAYS = 30.0
QUIET_AFTER_DAYS = 2.0
CONVO_BASE = 12
CONVO_LABEL_BONUS = {"stalling": 6, "cold": 3}
CONVO_MIN_DAYS = 3
CONVO_MAX_DAYS = 30
WARM_BASE = 6
WARM_TIER_BONUS = {"1": 8, "2": 3, "3": 1}
WARM_BOOKED_BONUS = 5
WARM_STALE_DAYS = 14
WARM_CAP = 12


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:  # per-line guard: one bad line must never blank the whole source
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _age_days(ts: str) -> float | None:
    """Days since an ISO timestamp; None on any parse failure (caller decides)."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if not dt.tzinfo:
            dt = dt.astimezone()
        now = datetime.now(dt.tzinfo)
        return max(0.0, (now - dt).total_seconds() / 86400.0)
    except (ValueError, TypeError):
        return None


def _recency_bonus(days: float) -> float:
    """Freshly-quiet beats long-quiet: linear falloff to zero at RECENCY_CAP_DAYS."""
    return RECENCY_BONUS_MAX * max(0.0, 1.0 - min(days, RECENCY_CAP_DAYS) / RECENCY_CAP_DAYS)


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _rid(phone: str, name: str) -> str:
    """Same id formula as warm_block._rid, kept local so a warm_block refactor
    cannot silently break the dispo join (call_escalator does the same)."""
    return "w_" + hashlib.sha1((phone or name).encode()).hexdigest()[:10]


def collect_proposals() -> list[dict]:
    """Engaged-but-quiet proposals: real opens/read time, active status, last
    activity older than QUIET_AFTER_DAYS. mock_* engagement fields are ignored."""
    try:
        import proposal_factory
        rows = proposal_factory.load_queue()
    except Exception:  # noqa: BLE001 - fall back to the raw store, never crash the merge
        rows = _read_jsonl(ROOT / "store" / "proposals.jsonl")
    out = []
    for r in rows:
        if r.get("status") not in PROPOSAL_STATUSES:
            continue
        opens = _num(r.get("opens"))
        read_secs = _num(r.get("read_secs"))
        scroll = _num(r.get("scroll_pct"))
        if opens <= 0 and read_secs <= 0:
            continue
        ages = [a for a in (_age_days(r.get("sent_at") or ""),
                            _age_days(r.get("opened_at") or ""),
                            _age_days(r.get("created") or "")) if a is not None]
        days_quiet = min(ages) if ages else None  # min age = most recent activity
        if days_quiet is None or days_quiet < QUIET_AFTER_DAYS:
            continue  # still warm from activity, or unparseable timeline: not "quiet"
        signal = OPENS_W * opens + READ_SECS_W * read_secs + SCROLL_W * scroll
        price = _num(r.get("price"))
        score = PROPOSAL_BASE + signal + price / PRICE_DIV + _recency_bonus(days_quiet)
        why = f"opened {int(opens)}x"
        if read_secs:
            why += f", read {int(read_secs)}s"
        if scroll:
            why += f", scrolled {int(scroll)}%"
        why += f", then quiet {days_quiet:.0f}d ({r.get('status')})"
        out.append({"kind": "proposal", "id": r.get("id", ""),
                    "company": r.get("company") or "", "name": r.get("name") or "",
                    "why": why, "signal": round(signal, 1), "score": round(score, 1),
                    "price": price, "phone": r.get("phone") or "",
                    "email": r.get("email") or "", "days": round(days_quiet, 1)})
    return out


def _contact_lookup() -> dict[str, dict]:
    """contact_id -> {email, phone} from the proposal store, so convo rows (which
    only carry contact_id + name) come out dialable when we know how to reach them."""
    look: dict[str, dict] = {}
    for r in _read_jsonl(ROOT / "store" / "proposals.jsonl"):
        cid = r.get("contact_id")
        if not cid:
            continue
        cur = look.setdefault(cid, {"email": "", "phone": ""})
        cur["email"] = r.get("email") or cur["email"]
        cur["phone"] = r.get("phone") or cur["phone"]
    return look


def collect_convos() -> list[dict]:
    """Dormant conversations: cold/stalling health with a last signal inside the
    re-engage window (younger is a slow reply, older is reactivation-campaign land)."""
    try:
        states = json.loads(CONVO_STATES.read_text()).get("states", {})
    except (OSError, json.JSONDecodeError):
        return []
    look = _contact_lookup()
    out = []
    for cid, rec in states.items():
        label = (rec.get("health_label") or "").lower()
        if label not in CONVO_LABEL_BONUS:
            continue
        days = rec.get("last_signal_days")
        try:
            days = float(days)
        except (TypeError, ValueError):
            continue
        if not (CONVO_MIN_DAYS <= days <= CONVO_MAX_DAYS):
            continue
        score = CONVO_BASE + CONVO_LABEL_BONUS[label] + _recency_bonus(days)
        contact = look.get(cid, {})
        why = f"{label} convo, {days:.0f}d since last signal"
        extra = (rec.get("why") or "").strip()
        if extra:
            why += f"; {extra[:80]}"
        out.append({"kind": "convo", "id": cid,
                    "company": "", "name": rec.get("name") or cid,
                    "why": why, "signal": 0.0, "score": round(score, 1),
                    "price": 0.0, "phone": contact.get("phone", ""),
                    "email": contact.get("email", ""), "days": round(days, 1)})
    return out


def collect_warm() -> list[dict]:
    """Stale warm-hitlist contacts: never dispo'd through call mode, or last dispo
    older than WARM_STALE_DAYS. Capped at WARM_CAP after scoring so the 400+ never-
    worked rows season the table instead of flooding it."""
    if not WARM_CSV.exists():
        return []
    last_dispo: dict[str, float] = {}
    for d in _read_jsonl(WARM_DISPO):
        age = _age_days(d.get("ts") or "")
        rid = d.get("id")
        if rid and age is not None:
            last_dispo[rid] = min(age, last_dispo.get(rid, age))
    out = []
    try:
        rows = list(csv.DictReader(open(WARM_CSV, newline="")))
    except (OSError, csv.Error):
        return []
    for r in rows:
        phone = (r.get("phone") or "").strip()
        name = (r.get("name") or "").strip() or (r.get("company") or "").strip()
        if not name and not phone:
            continue
        rid = _rid(phone, name)
        dispo_age = last_dispo.get(rid)
        if dispo_age is not None and dispo_age < WARM_STALE_DAYS:
            continue  # being worked, not stale
        tier = (r.get("tier") or "").strip()
        stage = (r.get("stage") or "").strip()
        deal_age = _num(r.get("deal_age_days"))
        price = _num(r.get("deal_value"))
        score = WARM_BASE + WARM_TIER_BONUS.get(tier, 0) + price / PRICE_DIV
        if "book" in stage.lower():
            score += WARM_BOOKED_BONUS
        score += _recency_bonus(deal_age)
        why = (f"tier {tier or '?'} {stage or 'no stage'}, "
               + (f"last dispo {dispo_age:.0f}d ago" if dispo_age is not None else "never dispo'd")
               + f", deal {deal_age:.0f}d old")
        out.append({"kind": "warm", "id": rid,
                    "company": (r.get("company") or "").strip(), "name": name.title(),
                    "why": why, "signal": 0.0, "score": round(score, 1),
                    "price": price, "phone": phone,
                    "email": (r.get("email") or "").strip(), "days": round(deal_age, 1)})
    out.sort(key=lambda x: (-x["score"], -x["days"]))  # oldest deals first among equals
    return out[:WARM_CAP]


def build() -> dict:
    items: list[dict] = []
    for fn in (collect_proposals, collect_convos, collect_warm):
        try:
            items.extend(fn())
        except Exception as e:  # noqa: BLE001 - one bad source must never blank the list
            print(f"quiet_worklist: {fn.__name__} failed non-fatally: {e}", file=sys.stderr)
    # rank: intent signal first (baked into score), then $, then recency (fresher first)
    items.sort(key=lambda x: (-x["score"], -x["price"], x["days"]))
    return {"generated": now_iso(), "count": len(items), "items": items}


def _table(items: list[dict]) -> str:
    lines = [f"{'score':>6}  {'kind':<8} {'who':<34} {'$':>6}  {'signal':>6}  why"]
    for it in items:
        who = (it.get("company") or it.get("name") or "?")[:34]
        lines.append(f"{it['score']:>6.1f}  {it['kind']:<8} {who:<34} "
                     f"{it['price']:>6.0f}  {it['signal']:>6.1f}  {it['why']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        data = build()
        print(f"quiet_worklist (dry run): {data['count']} item(s), nothing written")
        if data["items"]:
            print(_table(data["items"]))
        else:
            print("all quiet queues are empty. Honest empty state.")
        return 0

    from runlog import track
    with track("quiet_worklist"):
        data = build()
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    print(f"quiet_worklist: {data['count']} item(s) ranked -> {OUT}")
    if data["items"]:
        print(_table(data["items"][:10]))
        top = data["items"][0]
        who = top.get("company") or top.get("name") or "someone"
        line = f"Quiet worklist: {data['count']} re-engage targets, #1 is {who} ({top['why']})"
    else:
        line = "Quiet worklist: no one opened-and-went-quiet right now. Clear."
    try:
        planner.feed_add("agent", line[:160])
    except Exception:  # noqa: BLE001 - feed logging is best-effort, never blocks the run
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
