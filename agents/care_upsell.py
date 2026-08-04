#!/usr/bin/env python3
"""B6: care plan upsell timer. The pricing tree says the care plan converts AT
DELIVERY ("the site is beautiful today; care keeps it that way") and nothing in
the system prompts it: a build gets delivered, the invoice clears, and the 30%-
attach recurring revenue quietly never gets pitched. This watches every win and
stages the pitch at the right moment.

WHAT: collects wins from the three places this repo records them:
        store/ledger.jsonl rows with kind=won (note carries the client, e.g.
          "Acme Co Soft - WL Webdev"; amount is the build price),
        store/agreements.jsonl (signed acceptances: ts, pid, company, price),
        store/proposals.jsonl records with status=accepted (accepted_at).
      Merges them by a normalized client key. Once a win is UPSELL_AFTER_DAYS old
      (delivery-ish: builds run 3-14 days, so +7d post-win is the "site just went
      live" window), it stages, ONCE PER CLIENT EVER (store/care_upsell_state.json):
        1. a todo "Care plan pitch: <client> (75/150/300)" into store/todos.jsonl
           (same shape send_finger_nag.py stages, deduped by source_ref), and
        2. a pitch draft at store/drafts/care_<key>.md via ONE planner._cli call
           (voice_spec + the care-plan angle read live from
           business-library/playbooks/pricing-tree.md), with a deterministic
           on-tree fallback if the model call fails, so the draft ALWAYS exists.
      Tier honesty: $75 Care Basic / $150 Care Growth (+$250 onboarding) are the
      confirmed tiers for every active niche (hvac/salon/mens-health/agency
      white-label). The old $300 Care Growth+ medspa-lane tier is gone (NO MEDSPAS,
      dropped 2026-07-11) -- every win pitches Growth at $150 now, regardless of
      niche/client wording ("clinic"/"wellness" included).
WHEN: daily (morning chain). Cheap: zero LLM calls until a win crosses +7d, then
      at most LLM_CAP drafts per run. Fresh install (no ledger, no agreements, no
      accepted proposals) prints and exits 0.
RAILS: read-only against ledger/agreements/proposals. Writes: one todo per client
      ever, one draft file per client ever, the state file, one feed line. NOTHING
      is sent: the draft sits in store/drafts/ for HIS hands, no GHL, no email,
      no push (the todo is the surface).

Tunables (change here, nowhere else):
  UPSELL_AFTER_DAYS  = 7    fallback wait (days) when a win's tier can't be resolved
  DELIVERY_GRACE_DAYS = 2   buffer added on top of a KNOWN tier's promised build days
  LLM_CAP            = 3    max pitch drafts generated per run
  CARE_LABEL         = "(75/150)"   the tier ladder in the todo text

Run:  .venv/bin/python agents/care_upsell.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import (LOCAL_TZ, append_todo, load_todos, humanize,  # noqa: E402
                       new_id, now_iso, voice_spec)
import planner  # noqa: E402

LEDGER = ROOT / "store" / "ledger.jsonl"
AGREEMENTS = ROOT / "store" / "agreements.jsonl"
PROPOSALS = ROOT / "store" / "proposals.jsonl"
TODOS = ROOT / "store" / "todos.jsonl"
STATE = ROOT / "store" / "care_upsell_state.json"
DRAFTS = ROOT / "store" / "drafts"
PLAYBOOK = Path(os.environ.get("BIZLIB") or (ROOT / "business-library")) / "playbooks" / "pricing-tree.md"

UPSELL_AFTER_DAYS = 7      # fallback wait when a win's tier can't be resolved (CX8)
DELIVERY_GRACE_DAYS = 2    # buffer added on top of a KNOWN tier's promised build days (CX8)
LLM_CAP = 3
CARE_LABEL = "(75/150)"


def _read_jsonl(path: Path) -> list[dict]:
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


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")[:60]


def _read_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _write_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    tmp.replace(STATE)


def collect_wins() -> list[dict]:
    """One {key, client, won_ts, price, niche, tier, ledger_sourced} per client,
    merged across the three win records (earliest win timestamp wins: the clock
    starts at the FIRST signal). ledger_sourced is True when a kind=won LEDGER row
    backs this client (real cash); accepted-proposal/agreement wins are 'signed',
    not paid, and are gated on an actual payment before a 'site just delivered'
    pitch goes out. `tier` (CX8) is proposal_factory.PRICING's tier key when a
    matching accepted-proposal record carries one -- used to schedule the pitch off
    the build's OWN promised timeline instead of a flat guess; "" when unresolvable
    (e.g. a ledger-only win with no matching proposal record)."""
    wins: dict[str, dict] = {}

    def add(client: str, ts: str, price, niche: str = "", tier: str = "", *, ledger: bool = False):
        client = (client or "").strip()
        k = _key(client)
        if not k or not ts:
            return
        try:
            p = float(price or 0)
        except (TypeError, ValueError):
            p = 0.0
        cur = wins.get(k)
        if cur is None or ts < cur["won_ts"]:
            wins[k] = {"key": k, "client": client, "won_ts": ts,
                       "price": p or (cur or {}).get("price", 0.0),
                       "niche": niche or (cur or {}).get("niche", ""),
                       "tier": tier or (cur or {}).get("tier", ""),
                       "ledger_sourced": ledger or bool((cur or {}).get("ledger_sourced"))}
        elif cur is not None:
            cur["price"] = cur["price"] or p
            cur["niche"] = cur["niche"] or niche
            cur["tier"] = cur.get("tier") or tier
            cur["ledger_sourced"] = cur["ledger_sourced"] or ledger

    for r in _read_jsonl(LEDGER):
        if r.get("kind") != "won":
            continue
        note = (r.get("note") or "").strip()
        client = note.split(" - ")[0].strip() if " - " in note else note
        if client:
            add(client, r.get("ts") or "", r.get("amount"), ledger=True)
    for r in _read_jsonl(AGREEMENTS):
        add(r.get("company") or r.get("signed_name") or "", r.get("ts") or "", r.get("price"))
    by_id: dict[str, dict] = {}
    for r in _read_jsonl(PROPOSALS):
        if r.get("id"):
            by_id[r["id"]] = r
    for r in by_id.values():
        if r.get("status") == "accepted":
            add(r.get("company") or r.get("name") or "",
                r.get("accepted_at") or r.get("created") or "",
                r.get("price"), r.get("niche") or "", r.get("tier") or "")
    return list(wins.values())


def _delivery_wait_days(win: dict) -> int:
    """CX8: a KNOWN tier's own promised build length (+ a small grace buffer) is a
    truer 'is it actually delivered yet' signal than the flat UPSELL_AFTER_DAYS --
    Booking (10d) and White-Glove (14d) builds were getting the 'your site is live'
    pitch before the build's own promised days had even elapsed. When no tier is
    resolvable (e.g. a ledger-only win with no matching proposal/agreement record),
    UPSELL_AFTER_DAYS remains the wait -- the best available signal, unchanged."""
    tier = win.get("tier") or ""
    try:
        import proposal_factory
        tier_days = int(proposal_factory.PRICING[tier]["days"])
    except (ImportError, KeyError, TypeError, ValueError):
        return UPSELL_AFTER_DAYS
    return max(UPSELL_AFTER_DAYS, tier_days + DELIVERY_GRACE_DAYS)


def _win_is_paid(win: dict, ledger_rows: list[dict]) -> bool:
    """A win is 'real' (safe to pitch care against) when either it is ledger-sourced
    (money already logged) OR a deposit/payment ledger row exists for the client. A
    signed-but-unpaid deal (accepted proposal / agreement with a stalled deposit) is
    NOT a delivered build: deposit_nudge exists precisely because signed != paid, and
    the build only starts on deposit (pricing tree). Reuses deposit_nudge.has_payment
    so both agents share one ledger-match definition."""
    if win.get("ledger_sourced"):
        return True
    try:
        import deposit_nudge
        return deposit_nudge.has_payment({"company": win.get("client", "")}, ledger_rows)
    except Exception:  # noqa: BLE001 — if the check can't run, fail CLOSED (don't pitch)
        return False


def _care_angle() -> str:
    """The care-plan lines from the live pricing tree (the single source of pricing
    truth), so the draft can never drift from the doc."""
    try:
        txt = PLAYBOOK.read_text()
    except OSError:
        return ""
    keep = [ln for ln in txt.splitlines()
            if "care" in ln.lower() or "delivery" in ln.lower()]
    return "\n".join(keep)[:1200]


def _steer(win: dict) -> tuple[str, str]:
    """(tier_name, price_line). M / NO MEDSPAS (dropped 2026-07-11): every active
    niche (hvac/salon/mens-health/agency white-label) pitches the same Care Growth
    $150 tier, Care Basic $75 named as the fallback. The old $300 "Growth+"
    medspa-lane tier is gone -- it used to trip on "clinic"/"wellness"/"spa" wording
    even for an active-niche client (e.g. a mens-health clinic), silently
    overcharging someone who was never a medspa."""
    return ("Care Growth", "$150/mo + $250 onboarding (fallback: Care Basic $75/mo)")


PITCH_PROMPT = """You write a short care-plan upsell pitch for [OWNER] ([OWNER_COMPANY]).
VOICE SPEC (follow it to the letter, no em-dashes ever):
{voice}

CLIENT: {client} (site build just delivered{price_bit})
TIER TO PITCH: {tier} at {price_line}
CARE-PLAN ANGLE (from the pricing tree, the source of truth):
{angle}

Write the pitch [OWNER] sends AT DELIVERY: subject line, then a message under 120 words.
Angle: the site is live and working today, care is what keeps it that way (updates,
backups, uptime, small edits). Pitch ONE tier with its real price. One plain CTA
(reply "care" and it starts this month). Sign "[OWNER]". No links, no invented stats.
Return plain text: first line "Subject: ...", blank line, then the message."""


def draft_pitch(win: dict) -> str:
    """The pitch draft body (LLM first, deterministic on-tree fallback second, so
    the file always exists and always carries true prices)."""
    tier, price_line = _steer(win)
    price_bit = f", ${win['price']:,.0f} build" if win.get("price") else ""
    out = planner._cli(PITCH_PROMPT.format(voice=voice_spec(1600), client=win["client"],
                                           price_bit=price_bit, tier=tier,
                                           price_line=price_line, angle=_care_angle()),
                       timeout=120, feature="proposal")
    if out and len(out.strip()) > 40:
        return humanize(out.strip())
    return humanize(
        f"Subject: keeping {win['client']} fast\n\n"
        f"Your site is live and doing its job. The care plan is what keeps it that way: "
        f"updates, backups, uptime watched, small edits handled without a ticket queue.\n\n"
        f"{tier} is {price_line}.\n\n"
        f"Reply \"care\" and it starts this month.\n\n[OWNER]")


def _stage_todo(win: dict) -> bool:
    """Once per client ever, belt and suspenders: deduped by source_ref on top of
    the state file (same pattern send_finger_nag.py ships)."""
    ref = f"care_{win['key']}"
    if ref in {t.get("source_ref") for t in load_todos(TODOS)}:
        return False
    rec = {"id": new_id(ref), "text": f"Care plan pitch: {win['client']} {CARE_LABEL}",
           "status": "inbox", "created": now_iso(), "source": "care_upsell",
           "source_ref": ref, "project": None, "priority": 2, "scheduled_time": None,
           "duration_min": None, "gcal_event_id": None, "notes": None}
    append_todo(rec, TODOS)
    return True


def run(*, dry_run: bool = False) -> int:
    wins = collect_wins()
    if not wins:
        print("care upsell: no wins on record yet (ledger/agreements/accepted all empty), "
              "nothing to pitch")
        return 0

    state = _read_state()
    ledger_rows = _read_jsonl(LEDGER)
    now = datetime.now(LOCAL_TZ)
    due, unpaid = [], 0
    for w in wins:
        if w["key"] in state:
            continue  # once per client EVER
        try:
            won = datetime.fromisoformat(w["won_ts"])
            if not won.tzinfo:
                won = won.astimezone()
        except (ValueError, TypeError):
            continue
        age_d = (now - won).total_seconds() / 86400.0
        if age_d < _delivery_wait_days(w):
            continue
        if not _win_is_paid(w, ledger_rows):
            # signed but no deposit/payment on the ledger: the build never started,
            # so a "site just delivered" care pitch would be a lie. Skip (NOT stamp),
            # so it becomes due the moment the deposit lands. deposit_nudge owns this.
            unpaid += 1
            continue
        due.append((w, age_d))

    if unpaid:
        print(f"care upsell: {unpaid} signed-but-unpaid win(s) skipped (no deposit on "
              "the ledger yet, build not started)")
    if not due:
        print(f"care upsell: {len(wins)} win(s) on record, none crossing the "
              f"+{UPSELL_AFTER_DAYS}d window unpitched")
        return 0

    if dry_run:
        print(f"[dry-run] {len(due)} care pitch(es) would be staged, nothing written:")
        for w, age_d in due:
            tier, price_line = _steer(w)
            print(f"  {w['client']} (won {age_d:.0f}d ago) -> {tier} at {price_line}")
        return 0

    staged = 0
    for w, age_d in due:
        if staged >= LLM_CAP:
            print(f"  LLM cap ({LLM_CAP}) reached, the rest wait for the next run")
            break
        DRAFTS.mkdir(parents=True, exist_ok=True)
        draft_path = DRAFTS / f"care_{w['key']}.md"
        draft_path.write_text(draft_pitch(w) + "\n")
        _stage_todo(w)
        state[w["key"]] = {"pitched": now_iso(), "client": w["client"]}
        staged += 1
        print(f"  staged: {w['client']} (won {age_d:.0f}d ago) -> {draft_path.name} + todo")
    _write_state(state)

    if staged:
        try:
            planner.feed_add("agent", f"Care upsell: {staged} pitch(es) staged "
                                      f"({', '.join(w['client'] for w, _ in due[:staged])})")
        except Exception:  # noqa: BLE001
            pass
    print(f"care upsell: {staged} pitch(es) staged -> {DRAFTS}/care_*.md")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="stage the +7d post-win care plan pitch")
    ap.add_argument("--dry-run", action="store_true", help="print what would stage, write nothing")
    args = ap.parse_args()
    from runlog import track
    with track("care_upsell"):
        return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
