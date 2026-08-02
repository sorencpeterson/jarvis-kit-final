#!/usr/bin/env python3
"""#171 lost-deal autopsy: dead warm dispos older than 30 days, no autopsy sent yet,
get a one-question "what did we get wrong?" draft staged into the SAME approve queue
warm_followup.py and reply_watch.py already use (store/replies.jsonl). Nothing sends
itself — [OWNER] approves the draft like any other queued reply. Read-only against GHL
(a contact lookup to resolve who to attach the draft to), local-only writes otherwise.

Learning-loop framing (VOICE-SPEC): this isn't a "please come back" email, it's a
genuine one-question ask ("what would've made you say yes") — the reply, if any, is
raw market intel for the objections library, not just a re-engagement attempt.

Idempotent: every autopsied dispo id gets appended to store/autopsy_log.jsonl, so a
second run never double-drafts the same dead deal.

Run weekly (or from the morning chain, [OWNER]'s call where to slot it — not wired into
morning.sh by this mission since #171 wasn't listed in the H161/162/164 "add a line"
set, only campaign_guard/cold_preflight --daily were).

Usage:
  autopsy.py             # draft autopsies for anything eligible
  autopsy.py --dry-run   # show what would be drafted, write nothing
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso, new_id, humanize, voice_spec, LOCAL_TZ  # noqa: E402
import reply_watch  # noqa: E402  (ONLY for ._save — the approve-queue writer, exactly as warm_followup.py does)
import planner  # noqa: E402
import ghl_social  # noqa: E402

WARM_DISPO = ROOT / "store" / "warm_dispo.jsonl"
AUTOPSY_LOG = ROOT / "store" / "autopsy_log.jsonl"
MIN_AGE_DAYS = 30

AUTOPSY_PROMPT = """Write ONE short email for [OWNER] to send a dead prospect (a web-design
sales conversation that went nowhere {age} days ago). Voice: exactly as specified below.
This is a genuine one-question learning ask, NOT a re-pitch. Do not sell anything. Do
not mention price. Ask the one honest question and stop.

Business: {biz}
Niche: {niche}

VOICE SPEC (hard rules):
{voice}

Return ONLY the email body text, no subject line, no greeting boilerplate beyond a
natural opener using their first name if given ({first}), no signoff beyond "[OWNER]"
alone. Under 60 words total. End on the question itself, not a soft closer."""


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


def _dead_dispos() -> dict[str, dict]:
    """id -> latest dispo record (last-write-wins, same compaction style as store_lib)."""
    out = {}
    for r in _load_jsonl(WARM_DISPO):
        if r.get("id"):
            out[r["id"]] = r
    return {k: v for k, v in out.items() if v.get("dispo") == "dead"}


def _already_autopsied() -> set[str]:
    return {r.get("id") for r in _load_jsonl(AUTOPSY_LOG) if r.get("id")}


def _warm_row_lookup() -> dict[str, dict]:
    """id -> {name, phone, niche} from WARM-HITLIST.csv, the same source warm_block.py
    and server.py's _warm_rows() read. Best-effort: if the CSV row is gone (dropped by
    a warm_refresh.py run since), the dispo's own note/id is all we have."""
    import csv
    import hashlib
    csv_path = Path.home() / "Claude" / "WARM-HITLIST.csv"
    out = {}
    if not csv_path.exists():
        return out
    with open(csv_path, newline="") as f:
        for r in csv.DictReader(f):
            phone = (r.get("phone") or "").strip()
            name = (r.get("name") or "").strip() or (r.get("company") or "").strip()
            rid = "w_" + hashlib.sha1((phone or name).encode()).hexdigest()[:10]
            out[rid] = {"name": name, "phone": phone, "niche": (r.get("niche") or "").strip(),
                       "email": (r.get("email") or "").strip()}
    return out


def _find_contact(phone: str, email: str) -> dict:
    q = phone or email
    if not q:
        return {}
    j_out = ghl_social._api(["GET", f"/contacts/?query={q}&limit=3"])
    try:
        j = json.loads(j_out[j_out.find("{"):])
    except (ValueError, json.JSONDecodeError):
        return {}
    for c in j.get("contacts", []):
        if (phone and c.get("phone") == phone) or (email and (c.get("email") or "").lower() == email.lower()):
            return c
    return {}


def eligible_autopsies(min_age_days: int = MIN_AGE_DAYS) -> list[dict]:
    """Dead dispos older than the threshold, not already autopsied. Returns
    [{"id", "dispo_rec", "warm_row"}] ready to draft."""
    dead = _dead_dispos()
    done = _already_autopsied()
    rows_by_id = _warm_row_lookup()
    cutoff = datetime.now(LOCAL_TZ) - timedelta(days=min_age_days)
    out = []
    for wid, rec in dead.items():
        if wid in done:
            continue
        try:
            ts = datetime.fromisoformat(rec.get("ts") or "")
        except (ValueError, TypeError):
            continue
        if ts > cutoff:
            continue
        out.append({"id": wid, "dispo_rec": rec, "warm_row": rows_by_id.get(wid, {})})
    return out


def draft_autopsy(item: dict) -> str | None:
    warm_row = item["warm_row"]
    name = warm_row.get("name", "") or "there"
    first = name.split()[0].title() if name else "there"
    niche = warm_row.get("niche", "") or "local service"
    try:
        ts = datetime.fromisoformat(item["dispo_rec"].get("ts") or "")
        age = (datetime.now(LOCAL_TZ) - ts).days
    except (ValueError, TypeError):
        age = MIN_AGE_DAYS
    prompt = AUTOPSY_PROMPT.format(age=age, biz=name, niche=niche, first=first, voice=voice_spec(1600))
    out = planner._cli(prompt, timeout=90, feature="reply")
    if not out:
        return None
    return humanize(out.strip())


def _mark_autopsied(wid: str, drafted: bool, reason: str = ""):
    AUTOPSY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with AUTOPSY_LOG.open("a") as f:
        f.write(json.dumps({"id": wid, "ts": now_iso(), "drafted": drafted,
                            "reason": reason[:200]}, ensure_ascii=False) + "\n")


def run(dry: bool = False, min_age_days: int = MIN_AGE_DAYS) -> list[dict]:
    items = eligible_autopsies(min_age_days)
    if not items:
        print("autopsy: nothing eligible (no dead dispos older than "
              f"{min_age_days}d without an autopsy yet)")
        return []
    print(f"autopsy: {len(items)} dead dispo(s) eligible")
    drafted = []
    for item in items:
        wid = item["id"]
        warm_row = item["warm_row"]
        if not warm_row.get("name") and not warm_row.get("phone"):
            print(f"  {wid}: no matching WARM-HITLIST row (list may have refreshed since), skipping, marking done")
            if not dry:
                _mark_autopsied(wid, drafted=False, reason="no matching hitlist row")
            continue
        draft = draft_autopsy(item)
        if not draft:
            print(f"  {wid} ({warm_row.get('name')}): draft generation failed, will retry next run")
            continue  # do NOT mark autopsied — allow a retry on the next run
        contact = _find_contact(warm_row.get("phone", ""), warm_row.get("email", ""))
        cid = contact.get("id", "")
        rec = {"id": new_id("autopsy_" + wid), "convo": None, "contact_id": cid,
               "name": warm_row.get("name") or "dead lead", "phone": warm_row.get("phone", ""),
               "channel": "SMS" if warm_row.get("phone") else "Email",
               "their_msg": f"[lost-deal autopsy: dispo'd dead, now {min_age_days}+ days ago]",
               "intent": "followup", "draft": draft,
               "status": "pending", "created": now_iso(), "src": "autopsy"}
        print(f"  {wid} ({warm_row.get('name')}): drafted -> {draft[:70]}...")
        if not dry:
            reply_watch._save(rec)
            _mark_autopsied(wid, drafted=True)
        drafted.append(rec)
    if drafted and not dry:
        planner.feed_add("warm", f"{len(drafted)} lost-deal autopsy draft(s) queued for approval")
        planner.notify("Autopsy drafts ready", f"{len(drafted)} lost-deal follow-up(s) waiting on approval")
    return drafted


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-age-days", type=int, default=MIN_AGE_DAYS)
    args = ap.parse_args()
    run(dry=args.dry_run, min_age_days=args.min_age_days)
