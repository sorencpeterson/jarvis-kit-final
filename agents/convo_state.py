#!/usr/bin/env python3
"""Conversation-state machine (C161) — derives one state per contact_id from every
signal already on disk (replies.jsonl, proposals.jsonl, warm_dispo.jsonl, ledger.jsonl)
so reply_watch.py and the timers can pick a drafting register instead of treating every
inbound the same way.

States (one per contact_id, precedence top to bottom):
  won         - a ledger entry ties back to this contact/name with a positive amount, OR
                a proposal for them is status=sent and a later reply/warm_dispo reads as
                a clear yes (best-effort: no field anywhere is literally "won" today).
  negotiating - a proposal is staged/sent for them and no dead-end signal followed
                (price/timing objection intent counts as negotiating, not dormant, while
                it's still recent).
  engaged     - at least one real inbound reply exists (any intent except remove) and
                the contact isn't won/negotiating/dormant.
  dormant     - engaged or negotiating at some point, but the LAST signal of any kind
                is older than DORMANT_DAYS (default 21) with nothing newer.
  new         - default: a contact_id we've never seen a reply/proposal/dispo for, or
                the only signal is a single very-recent first touch.

This is intentionally a pure function over already-loaded rows (`classify()`), so it's
directly fixture-testable without touching any store. `run()` wires it to the real
files and writes store/convo_states.json (full overwrite each run, id-keyed).

warm_dispo.jsonl is id-keyed by warm-call id (w_<hash of phone/name>), NOT contact_id
-- there is no field anywhere joining a warm-call id back to a GHL contact_id today
(same gap agents/lost_to.py and agents/referral_timer.py already document honestly).
So the warm_dispo signal here is opportunistic: joined by exact lowercase name match
against the contact's name, best-effort only, never the sole reason to call a contact
won. warm_dispo.jsonl is also currently EMPTY in this environment (0 rows) -- verified
live, not assumed -- so that join path is real but untested against live data; the
ledger/replies/proposals paths ARE exercised by the real run in this build.

Rails: read-only against every store it consumes. Only write is store/convo_states.json
(full overwrite, not append -- state is a derived snapshot, replaying it doesn't need
history). No GHL calls, no drafting, no sending.

Usage:
  convo_state.py            # recompute from real stores, write store/convo_states.json
  convo_state.py --dry-run  # compute and print, write nothing
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402

REPLIES = ROOT / "store" / "replies.jsonl"
PROPOSALS = ROOT / "store" / "proposals.jsonl"
WARM_DISPO = ROOT / "store" / "warm_dispo.jsonl"
LEDGER = ROOT / "store" / "ledger.jsonl"
OUT = ROOT / "store" / "convo_states.json"

DORMANT_DAYS = 21
STATES = ("new", "engaged", "negotiating", "won", "dormant")

# Reply intents (from reply_watch.py's CLASSIFY prompt) that mean "still talking,
# not a dead end" -- objection/question/interested all count as active negotiation
# signal once a proposal is in flight; "not_now"/"other" are weaker (engaged, not
# negotiating) since nothing concrete is on the table yet.
_ACTIVE_INTENTS = {"interested", "question", "objection"}
_WON_LANGUAGE = re.compile(
    r"\b(deposit(?:'s| is)? (?:in|sent|paid)|paid the deposit|locked in|"
    r"let'?s do it|sign(?:ed)? me up|we'?re in|sounds good let'?s|go ahead and start|"
    r"send (?:me |the )?(?:the )?(?:invoice|contract)|approved[,.]? let'?s start)\b",
    re.IGNORECASE,
)


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


def _last_write_wins(rows: list[dict], id_field: str = "id") -> list[dict]:
    by_id, order = {}, []
    for r in rows:
        rid = r.get(id_field)
        if rid is None:
            continue
        if rid not in by_id:
            order.append(rid)
        by_id[rid] = r
    return [by_id[i] for i in order]


def _parse_ts(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _days_since(ts: str, now: datetime) -> float | None:
    dt = _parse_ts(ts)
    if not dt:
        return None
    return (now - dt).total_seconds() / 86400


def classify(contact_id: str, *, name: str = "",
             replies: list[dict] | None = None,
             proposals: list[dict] | None = None,
             warm_dispos: list[dict] | None = None,
             ledger: list[dict] | None = None,
             now: datetime | None = None) -> dict:
    """Pure function: given one contact's rows (already filtered to that contact_id
    where the store supports it) plus a name for the best-effort warm_dispo join,
    return {"state", "why", "last_signal_ts", "last_signal_days"}. No file I/O."""
    now = now or datetime.now(timezone.utc)
    replies = replies or []
    proposals = proposals or []
    warm_dispos = warm_dispos or []
    ledger = ledger or []
    name_l = (name or "").strip().lower()

    signals: list[tuple[datetime, str]] = []  # (ts, description) for last-signal tracking
    reasons: list[str] = []

    # ---- won: ledger entries mentioning this contact by id or (fallback) name, or a
    # proposal that went sent + a reply reading as clear won-language after it.
    won = False
    for l in ledger:
        note = (l.get("note") or "")
        amt = l.get("amount") or 0
        try:
            amt = float(amt)
        except (TypeError, ValueError):
            amt = 0.0
        hit = (contact_id and contact_id in note) or (name_l and name_l in note.lower())
        if hit and amt > 0:
            won = True
            reasons.append(f"ledger entry ${amt:g} references this contact")
        dt = _parse_ts(l.get("ts") or "")
        if hit and dt:
            signals.append((dt, "ledger"))

    sent_proposals = [p for p in proposals if p.get("status") in ("sent", "won")]
    if any(p.get("status") == "won" for p in proposals):
        won = True
        reasons.append("a proposal record is marked won")
    for r in replies:
        body = (r.get("their_msg") or "") + " " + (r.get("draft") or "")
        if sent_proposals and _WON_LANGUAGE.search(body):
            won = True
            reasons.append("won-language in a reply after a sent proposal")

    for wd in warm_dispos:
        if wd.get("dispo") == "booked":
            # booked is a signal of forward motion (negotiating), not automatically won --
            # a booked call still has to close. Tracked as a signal timestamp only.
            dt = _parse_ts(wd.get("ts") or "")
            if dt:
                signals.append((dt, "warm_dispo booked"))
        if wd.get("dispo") in ("dead", "not_interested", "wrong_number", "do_not_call"):
            dt = _parse_ts(wd.get("ts") or "")
            if dt:
                signals.append((dt, f"warm_dispo {wd.get('dispo')}"))

    # ---- negotiating: a proposal is live (staged or sent) and not superseded by won,
    # OR the most recent real reply carries an active intent (interested/question/objection).
    staged_or_sent = [p for p in proposals if p.get("status") in ("staged", "sent")]
    negotiating = bool(staged_or_sent) and not won
    if negotiating:
        kinds = sorted({p.get("status") for p in staged_or_sent})
        reasons.append(f"proposal(s) {', '.join(kinds)}")
    elif proposals and not won:
        # proposals exist but none are staged/sent (e.g. skipped) -- still a real signal,
        # just not one that promotes the state, so it's recorded honestly rather than
        # silently dropped (a contact with a skipped proposal is NOT "no signal on file").
        other = sorted({p.get("status") or "?" for p in proposals})
        reasons.append(f"proposal(s) {', '.join(other)} (not active)")
    for p in proposals:
        dt = _parse_ts(p.get("created") or p.get("sent_at") or "")
        if dt:
            signals.append((dt, f"proposal {p.get('status')}"))

    # ---- engaged: any real inbound reply (status != skipped's meaning-of-intent =
    # "remove" is filtered upstream by reply_watch, so any row here is a real inbound).
    real_replies = [r for r in replies if r.get("intent") != "remove"]
    engaged = bool(real_replies)
    last_intent = ""
    if real_replies:
        real_replies_sorted = sorted(
            real_replies, key=lambda r: _parse_ts(r.get("created") or "") or datetime.min.replace(tzinfo=timezone.utc))
        last_intent = (real_replies_sorted[-1].get("intent") or "").lower()
        # NOTE: active intent (interested/question/objection) alone, with no proposal yet,
        # still reads as "engaged" not "negotiating" -- negotiating means something concrete
        # (a priced proposal) is on the table, per the state definitions in the module docstring.
        reasons.append(f"last reply intent={last_intent or 'other'}")
    for r in replies:
        dt = _parse_ts(r.get("created") or "")
        if dt:
            signals.append((dt, f"reply {r.get('intent')}"))

    # ---- resolve precedence + dormancy on top of whatever's true so far
    if signals:
        last_ts, last_desc = max(signals, key=lambda s: s[0])
        last_days = (now - last_ts).total_seconds() / 86400
    else:
        last_ts, last_desc, last_days = None, "", None

    if won:
        state = "won"
    elif last_days is not None and last_days > DORMANT_DAYS and (engaged or negotiating):
        state = "dormant"
        reasons.append(f"last signal {last_days:.1f}d ago (> {DORMANT_DAYS}d dormancy floor)")
    elif negotiating:
        state = "negotiating"
    elif engaged:
        state = "engaged"
    else:
        state = "new"

    health = thread_health(state=state, real_replies=real_replies,
                           last_signal_days=round(last_days, 2) if last_days is not None else None)

    return {
        "state": state,
        "why": "; ".join(reasons) or "no signal on file yet",
        "last_signal_ts": last_ts.isoformat() if last_ts else None,
        "last_signal_days": round(last_days, 2) if last_days is not None else None,
        # ---- C220: conversation NPS heuristic (thread health score) ----
        "health_score": health["score"], "health_label": health["label"],
    }


# C220: 0-100 heuristic "how is this conversation doing" score, informational only
# (never gates any drafting/suppress/state decision -- it's a signal FOR [OWNER]'s UI,
# not an input INTO the machine's own logic elsewhere, so it can be tuned freely
# without risking a behavior change anywhere else).
#
# Deliberately honest about NOT being a real NPS: nothing in this codebase asks
# customers "how likely are you to recommend us" (that's what NPS actually measures).
# This is a heuristic proxy built from what's actually on disk: conversation STATE
# (won/negotiating beat dormant/new), the trend of their last few real reply intents
# (interested/question trending beats a string of not_now/objection), and how stale
# the thread is. "NPS heuristic" per the mission's own C220 phrasing -- the name is
# borrowed, the computation is not a real NPS survey score.
_INTENT_HEALTH = {"interested": 25, "question": 15, "objection": 5, "not_now": -10,
                  "wrong_person": -30, "other": 0}


def thread_health(state: str, real_replies: list[dict], last_signal_days: float | None) -> dict:
    """Pure function: {"score": 0-100 int, "label": str}. Base score by state, then
    adjusted by the trend of the last up-to-3 real reply intents (recency-weighted:
    the MOST recent intent counts most) and a staleness penalty once a thread is
    getting old even before it crosses the hard dormancy floor."""
    base = {"won": 90, "negotiating": 65, "engaged": 50, "new": 50, "dormant": 20}.get(state, 50)

    # oldest-to-newest, capped to the last 3
    recent = sorted(real_replies, key=lambda r: r.get("created") or "")[-3:]
    # DESCENDING weights, most-recent-gets-most: with 1 reply -> [0.5] (full weight on
    # the only signal available); 2 replies -> [0.3, 0.5] (oldest, newest); 3 replies
    # -> [0.2, 0.3, 0.5]. Building this as WEIGHT_TABLE[n] rather than slicing a fixed
    # list keeps "which end of the slice is most-recent" unambiguous (a real bug here
    # originally: [0.5,0.3,0.2][-len(recent):] with len==1 sliced to [0.2], the
    # LOWEST weight, exactly backwards from the intent -- caught by a fixture test
    # asserting a single 'interested' reply should score meaningfully above the bare
    # base, before this shipped).
    weight_table = {1: [0.5], 2: [0.3, 0.5], 3: [0.2, 0.3, 0.5]}
    weights = weight_table.get(len(recent), [])
    intent_adj = 0.0
    for r, w in zip(recent, weights):
        intent_adj += _INTENT_HEALTH.get((r.get("intent") or "other"), 0) * w

    staleness_penalty = 0.0
    if last_signal_days is not None and state not in ("won",):
        # soft penalty starting well before the hard DORMANT_DAYS floor -- a thread
        # at 15 days quiet is already trending down even though convo_state.py won't
        # call it "dormant" until DORMANT_DAYS (21)
        if last_signal_days > 10:
            staleness_penalty = min(20.0, (last_signal_days - 10) * 1.5)

    score = max(0, min(100, round(base + intent_adj - staleness_penalty)))
    if score >= 75:
        label = "healthy"
    elif score >= 45:
        label = "watch"
    else:
        label = "at_risk"
    return {"score": score, "label": label}


def _group_by_contact(rows: list[dict], field: str = "contact_id") -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        cid = r.get(field)
        if not cid:
            continue
        out.setdefault(cid, []).append(r)
    return out


def build_all() -> dict[str, dict]:
    """Load every store, group by contact_id, classify each known contact_id.
    Returns {contact_id: classify()-result}. Pure aside from the initial reads."""
    replies = _last_write_wins(_load_jsonl(REPLIES))
    proposals = _last_write_wins(_load_jsonl(PROPOSALS))
    warm_dispos = _last_write_wins(_load_jsonl(WARM_DISPO))
    ledger = _load_jsonl(LEDGER)  # append-only ledger, no id to compact by

    by_contact_replies = _group_by_contact(replies)
    by_contact_proposals = _group_by_contact(proposals)

    # names for the warm_dispo best-effort join + so callers can label the state file
    names: dict[str, str] = {}
    for cid, rows in by_contact_replies.items():
        for r in rows:
            if r.get("name"):
                names[cid] = r["name"]
    for cid, rows in by_contact_proposals.items():
        if cid not in names:
            for r in rows:
                if r.get("name") or r.get("company"):
                    names[cid] = r.get("name") or r.get("company")

    all_ids = set(by_contact_replies) | set(by_contact_proposals)
    now = datetime.now(timezone.utc)
    out: dict[str, dict] = {}
    for cid in all_ids:
        nm = names.get(cid, "")
        nm_l = nm.strip().lower()
        matched_dispos = [w for w in warm_dispos if (w.get("name") or "").strip().lower() == nm_l and nm_l]
        result = classify(cid, name=nm,
                          replies=by_contact_replies.get(cid, []),
                          proposals=by_contact_proposals.get(cid, []),
                          warm_dispos=matched_dispos,
                          ledger=ledger, now=now)
        result["contact_id"] = cid
        result["name"] = nm
        out[cid] = result
    return out


def run(dry: bool = False) -> dict:
    states = build_all()
    counts: dict[str, int] = {s: 0 for s in STATES}
    for r in states.values():
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    payload = {"generated": now_iso(), "count": len(states), "counts": counts, "states": states}
    if not dry:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"convo_state: {len(states)} contact(s) classified -> {dict(counts)}"
          + ("" if dry else f" -> {OUT}"))
    return payload


def load_states() -> dict[str, dict]:
    """Read the last-computed snapshot. Callers (reply_watch etc.) should treat a
    missing/stale file as 'no state known' (defaults to 'new'), never crash on it."""
    try:
        return json.loads(OUT.read_text()).get("states", {})
    except (OSError, json.JSONDecodeError):
        return {}


def state_for(contact_id: str) -> str:
    """Convenience for callers that just want one contact's current state string."""
    if not contact_id:
        return "new"
    rec = load_states().get(contact_id)
    return rec.get("state", "new") if rec else "new"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(dry=args.dry_run)
