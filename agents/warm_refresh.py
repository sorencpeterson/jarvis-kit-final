#!/usr/bin/env python3
"""#167 warm hitlist auto-refresh (weekly): re-pull each hitlist contact's live deal
state from GHL, re-score tiers by deal age/value/stage, regenerate WARM-HITLIST.csv.
Read-only against GHL.

CRITICAL scope note found during investigation (do not "fix" this without re-reading):
this GHL location has 23,270+ total opportunities across pipelines used for entirely
unrelated purposes (this is a big multi-purpose account). The 481-row hitlist is NOT
"every open deal in a few pipelines" — every single row carries specific campaign tags
(men-clinic / aoa-men / aoa - men 3 men offer, an "Aesthetics of America" style warm
campaign) scattered across SIX different pipelines that also each hold thousands of
unrelated opportunities. First attempt at this file pulled whole pipelines scoped by
name-match and got a 380% size blowout (2310 rows) with zero of the campaign-tag
signal respected — wrong. There is no single clean server-side filter for "this
campaign's deals only" available via the read endpoints this toolkit exposes.

So the design is CONTACT-ANCHORED, not pipeline-anchored: refresh_contact() re-looks-up
each row ALREADY on the hitlist (by phone, falling back to email) and pulls that one
contact's current opportunity state directly (GET /contacts/?query=, then
GET /opportunities/search?contact_id=). This can't invent false scope because it never
asks "which deals belong to this campaign" — it asks "what's the current state of a
deal we already know belongs here," which is exactly what a refresh should do. Deals
that closed/got deleted since the last CSV drop off (no live opportunity found);
everything else keeps its row, curated niche/suggested_offer/tags/location preserved
untouched (those only exist as hand-curated data, not recoverable from raw GHL
objects), only deal_age_days/deal_value/pipeline/stage/tier recompute live.

Net-new deal discovery (finding entirely new campaign contacts GHL-side) is
deliberately NOT attempted here — there's no reliable read-only way to reconstruct
"which contacts are on this specific warm campaign" from the API surface available,
and guessing wrong (as the first draft proved) silently corrupts the list. New warm
contacts should keep entering the CSV the way they do today (upstream of this file);
this agent's job is keeping the EXISTING list's numbers honest, not growing it.

#168 do-not-call timezone: every row gets a `tz` column inferred from its phone's
area code via tzmap.tz_for_phone() (agents/tzmap.py, this mission's file). Empty
if the phone is missing/foreign/unmapped. warm_block.py (NOT owned by this mission)
can read this column later to enforce a 9am-7pm local-time call window; that
enforcement logic is documented here, not built here (out of scope for this file).

#172 neighborhood clustering: every row gets a `cluster` column, the contact's GHL
city when it looks like a real city name, otherwise "" (the raw city field is dirty
in this account — spot-checked several contacts and found values like "Second
Floor" / "Gemma House" / "New Broad St", clearly address-line-2 import artifacts,
not real cities; a naive pass-through would create fake clusters). Rows sharing a
cluster value can be called back-to-back with less timezone/context switching.

Safety: writes ONLY to WARM-HITLIST.csv (after backing up the current file to
WARM-HITLIST.csv.bak) and prints a diff-sanity summary. Never touches GHL, never
touches warm_dispo.jsonl or any other store this mission doesn't own.

Usage:
  warm_refresh.py             # do it
  warm_refresh.py --dry-run   # pull + score + print the summary, write nothing
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import ghl_social  # noqa: E402
import planner  # noqa: E402
import tzmap  # noqa: E402

CSV_PATH = Path.home() / "Claude" / "WARM-HITLIST.csv"
FIELDS = ["tier", "name", "company", "email", "phone", "location", "niche",
          "suggested_offer", "pipeline", "stage", "deal_age_days", "deal_value",
          "tags", "tz", "cluster"]

# Stage-name substrings that mean "this deal is dead," excluded from the refreshed
# list entirely (a dead deal doesn't belong on an active call hitlist).
DEAD_STAGE_MARKERS = ("not interested", "not intrested", "lost", "no answer")
BOOKED_STAGE_MARKERS = ("booked", "call booked")

# Words that show up in dirty city data in this account (address-line-2 leakage) —
# a city value containing any of these is treated as garbage, not a real cluster.
_BAD_CITY_WORDS = ("floor", "house", "suite", "ste ", " st", "street", "unit ",
                   "building", "apt", "room", "office")


def _loc() -> str:
    try:
        for line in (ghl_social.GHL / ".env").read_text().splitlines():
            if line.startswith("GHL_LOCATION_ID="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _api_json(args: list[str], retries: int = 2) -> dict:
    """Wraps ghl_social._api with a retry-on-ambiguous-response guard. A transient
    network blip or a non-JSON error body must NOT look identical to "confirmed empty
    result" — a bug found live during testing (see warm_refresh module notes /
    mission status file): a single isolated re-check of a dropped row always came
    back correct, but the real full run dropped it anyway, meaning something
    transient during the long run got silently swallowed by the old bare
    except-return-empty-dict pattern and treated as "this contact has no deal."
    Retries with a short backoff before giving up and returning the (still possibly
    genuinely empty) parsed result."""
    last = {}
    for attempt in range(retries + 1):
        out = ghl_social._api(args)
        try:
            j = json.loads(out[out.find("{"):], strict=False)
            return j
        except (ValueError, json.JSONDecodeError):
            last = {"_raw": out}
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
    return last


def pipeline_names(loc: str) -> dict[str, dict[str, str]]:
    """pipelineId -> {"name": ..., "stages": {stageId: stageName}}"""
    j = _api_json(["GET", "/opportunities/pipelines", "--loc"])
    out = {}
    for p in j.get("pipelines", []):
        out[p.get("id")] = {"name": p.get("name", ""),
                            "stages": {s.get("id"): s.get("name", "") for s in p.get("stages", [])}}
    return out


def _clean_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _load_existing() -> list[dict]:
    if not CSV_PATH.exists():
        return []
    with open(CSV_PATH, newline="") as f:
        return list(csv.DictReader(f))


def _looks_like_real_city(city: str) -> bool:
    c = (city or "").strip()
    if not c or len(c) > 40:
        return False
    low = c.lower()
    return not any(w in low for w in _BAD_CITY_WORDS)


def _tier_for(stage: str, deal_age_days: int, deal_value: float) -> str:
    """Tier 1 = booked calls (always the hottest, matches the existing scheme).
    Tier 2 = everything else under 90 days old OR carrying real deal value.
    Tier 3 = aging (90+ days) zero-value deals — still worth a call, but call
    them last; warm_block.py already sorts oldest-first WITHIN a tier, so tier 3
    existing (it references tier "3" in its own loop) finally gets real rows."""
    s = (stage or "").lower()
    if any(m in s for m in BOOKED_STAGE_MARKERS):
        return "1"
    if deal_age_days >= 90 and deal_value <= 0:
        return "3"
    return "2"


def find_contact(loc: str, phone: str, email: str) -> tuple[dict | None, bool]:
    """GET /contacts/?query=<phone or email>, first hit whose phone or email matches
    (case/format-loose). Same lookup shape cold_import.find_contact() and
    proposal_factory.find_contact() already use elsewhere in this codebase. Returns
    (contact_or_none, confirmed) — confirmed=False means the API response itself was
    ambiguous/malformed even after retries, i.e. we genuinely don't know, as opposed
    to confirmed=True + None meaning "we asked and there really is no match." Callers
    MUST treat confirmed=False as "keep the old row, don't drop it" — see the module
    notes on the real transient-drop bug found during testing."""
    q = _clean_phone(phone) or email
    if not q:
        return None, True  # nothing to even query on, this is a real/confirmed miss
    j = _api_json(["GET", f"/contacts/?locationId={loc}&query={q}&limit=5"])
    if "_raw" in j:
        return None, False  # ambiguous: the API call itself didn't come back clean
    want_phone = _clean_phone(phone)
    for c in j.get("contacts", []):
        if want_phone and _clean_phone(c.get("phone", "")) == want_phone:
            return c, True
        if email and (c.get("email") or "").strip().lower() == email:
            return c, True
    return None, True  # well-formed response, genuinely no matching contact


def latest_open_opportunity(loc: str, contact_id: str, pipelines: dict) -> tuple[dict | None, bool]:
    """This contact's most-recently-updated OPEN opportunity. Returns (opp_or_none,
    confirmed) with the same confirmed-vs-ambiguous contract as find_contact()."""
    j = _api_json(["GET", f"/opportunities/search?location_id={loc}&contact_id={contact_id}&limit=10"])
    if "_raw" in j:
        return None, False
    opps = [o for o in j.get("opportunities", []) if o.get("status") == "open"]
    if not opps:
        return None, True
    opps.sort(key=lambda o: o.get("updatedAt") or "", reverse=True)
    return opps[0], True


def refresh_contact(loc: str, old_row: dict, pipelines: dict) -> tuple[dict | None, bool]:
    """Re-look-up ONE existing hitlist row's live state. Returns (row_or_none,
    keep_as_is). keep_as_is=True with row=None means "genuinely confirmed gone, drop
    it." keep_as_is=False means "the API was ambiguous, could not confirm either way
    — caller should keep the OLD row rather than silently losing it." This second
    case is the fix for a real bug found live during testing: a transient/ambiguous
    response during a long run was previously treated identically to "confirmed no
    deal," which silently dropped a real live 'Hot Lead' contact (bijou med spa) that
    a fresh isolated re-check immediately proved was still perfectly live."""
    phone, email = old_row.get("phone", ""), (old_row.get("email") or "").strip().lower()
    contact, confirmed = find_contact(loc, phone, email)
    if not confirmed:
        return None, False
    if not contact:
        return None, True
    opp, confirmed2 = latest_open_opportunity(loc, contact.get("id", ""), pipelines)
    if not confirmed2:
        return None, False
    if not opp:
        return None, True
    stage_id = opp.get("pipelineStageId", "")
    pipe = pipelines.get(opp.get("pipelineId"), {})
    stage_name = pipe.get("stages", {}).get(stage_id, "")
    if any(m in stage_name.lower() for m in DEAD_STAGE_MARKERS):
        return None, True  # confirmed dead stage — a real drop, not an ambiguous one
    created = opp.get("createdAt") or ""
    try:
        age_days = max(0, (datetime.now(timezone.utc)
                           - datetime.fromisoformat(created.replace("Z", "+00:00"))).days)
    except (ValueError, TypeError):
        age_days = int((old_row.get("deal_age_days") or "0") or 0)
    value = opp.get("monetaryValue") or 0
    # tier is a SOURCE SEMANTIC (1=booked a call once, 2=replied once), not a score.
    # 2026-07-03: a re-scoring pass silently re-tiered 388 repliers to '3' and broke
    # warm_block/server/reactivation consumers; restored from .bak. NEVER recompute
    # tier for rows that already carry one; _tier_for only labels NEW rows.
    tier = old_row.get("tier") or _tier_for(stage_name, age_days, value)
    phone_now = contact.get("phone") or phone
    row = {
        "tier": tier,
        "name": old_row.get("name") or contact.get("name") or contact.get("companyName") or "",
        "company": old_row.get("company") or contact.get("companyName") or "",
        "email": email or (contact.get("email") or "").strip().lower(),
        "phone": phone_now,
        # curated columns preserved verbatim — see module docstring on why
        "location": old_row.get("location") or "",
        "niche": old_row.get("niche") or "Local service",
        "suggested_offer": old_row.get("suggested_offer") or "WEB",
        "pipeline": pipe.get("name", ""), "stage": stage_name,
        "deal_age_days": str(age_days), "deal_value": str(int(value)),
        "tags": old_row.get("tags") or "",
        "tz": tzmap.tz_for_phone(phone_now),
        "cluster": old_row.get("cluster") or "",  # filled by enrich_clusters() if still blank
    }
    return row, True


def build_rows(loc: str, old_rows: list[dict], pipelines: dict) -> tuple[list[dict], int, int]:
    """Returns (rows, confirmed_dropped, kept_ambiguous). kept_ambiguous counts rows
    where the API couldn't confirm either way and the OLD row was carried forward
    unchanged rather than risking a silent false drop (see refresh_contact's docstring
    for the real bug this defends against)."""
    rows, dropped, kept_ambiguous = [], 0, 0
    for i, old in enumerate(old_rows):
        updated, confirmed = refresh_contact(loc, old, pipelines)
        if not confirmed:
            # ambiguous API response even after retries: do NOT drop, carry the old
            # row forward as-is so a transient blip can never silently shrink the list
            rows.append(old)
            kept_ambiguous += 1
        elif updated is None:
            dropped += 1
        else:
            rows.append(updated)
        time.sleep(0.35)  # stay well under the public-API burst limit, matches cold_import.py's pacing
        if (i + 1) % 50 == 0:
            print(f"  ...{i + 1}/{len(old_rows)} checked ({len(rows)} live, {dropped} confirmed-dropped, "
                  f"{kept_ambiguous} kept-ambiguous so far)", flush=True)
    return rows, dropped, kept_ambiguous


def enrich_clusters(rows: list[dict], loc: str, max_lookups: int = 60) -> None:
    """#172. Fills `cluster` for rows that don't already have one, by fetching the
    contact and reading its city field (capped lookups per run — this is a live GET
    per contact, keep the weekly refresh from turning into hundreds of API calls).
    Bad-looking city strings (see _looks_like_real_city) are left blank rather than
    creating a fake cluster.

    Prioritizes tier-1 (booked-call) rows first within the lookup budget — this runs
    BEFORE run()'s final tier sort, so without explicit prioritization the 60-lookup
    cap would just enrich whatever order build_rows happened to produce (effectively
    random from clustering's perspective), when the whole point of #172 is grouping
    the highest-value call blocks by timezone/city first."""
    ordered = sorted((r for r in rows if not r["cluster"]), key=lambda r: r["tier"])
    todo = ordered[:max_lookups]
    if not todo:
        return
    email_to_phone_seen = set()
    for r in todo:
        # need a contact id; opportunities carry it, but rows here only have phone/email
        # so re-look-up via contact search on email (cheap, single field) when we have one
        q = r.get("email") or r.get("phone", "")
        if not q or q in email_to_phone_seen:
            continue
        email_to_phone_seen.add(q)
        j = _api_json(["GET", f"/contacts/?locationId={loc}&query={q}&limit=1"])
        cands = j.get("contacts", [])
        if not cands:
            continue
        city = (cands[0].get("city") or "").strip()
        if _looks_like_real_city(city):
            r["cluster"] = city


def diff_sanity(old_rows: list[dict], new_rows: list[dict]) -> dict:
    old_n, new_n = len(old_rows), len(new_rows)
    pct = (abs(new_n - old_n) / old_n * 100) if old_n else 0.0
    old_cols = set(old_rows[0].keys()) if old_rows else set()
    missing_cols = old_cols - set(FIELDS)
    return {"old_count": old_n, "new_count": new_n, "pct_change": round(pct, 1),
            "within_20pct": pct <= 20.0, "missing_original_columns": sorted(missing_cols),
            "all_original_columns_present": not missing_cols}


def run(dry: bool = False, limit: int | None = None, force_write: bool = False) -> dict:
    loc = _loc()
    if not loc:
        print("warm_refresh: no GHL location configured, aborting")
        return {"ok": False, "error": "no location"}
    old_rows = _load_existing()
    if not old_rows:
        print("warm_refresh: WARM-HITLIST.csv is empty or missing, nothing to refresh "
              "(this agent refreshes an existing list, it doesn't build one from scratch — see module docstring)")
        return {"ok": False, "error": "no existing rows"}
    if limit:
        old_rows = old_rows[:limit]

    pipelines = pipeline_names(loc)
    print(f"warm_refresh: re-checking {len(old_rows)} existing hitlist contact(s) against live GHL...")
    rows, dropped, kept_ambiguous = build_rows(loc, old_rows, pipelines)
    print(f"warm_refresh: {len(rows)} rows kept ({dropped} confirmed-dropped as closed/lost/deleted, "
          f"{kept_ambiguous} kept unchanged because the API response was ambiguous — never silently dropped)")
    enrich_clusters(rows, loc)

    sanity = diff_sanity(old_rows, rows)
    print(f"warm_refresh: sanity check -> old={sanity['old_count']} new={sanity['new_count']} "
          f"({sanity['pct_change']}% change, {'OK within +/-20%' if sanity['within_20pct'] else 'FLAG: outside +/-20%'})")
    print(f"warm_refresh: all original columns present: {sanity['all_original_columns_present']}"
          + ("" if sanity["all_original_columns_present"] else f" (missing: {sanity['missing_original_columns']})"))

    from collections import Counter
    tiers = Counter(r["tier"] for r in rows)
    tz_filled = sum(1 for r in rows if r["tz"])
    cluster_filled = sum(1 for r in rows if r["cluster"])
    print(f"warm_refresh: tiers {dict(tiers)} | tz filled {tz_filled}/{len(rows)} | "
          f"cluster filled {cluster_filled}/{len(rows)}")

    # SAFETY GATE: a confirmed-drop rate this high on a real run is exactly the shape
    # of the transient-failure bug found live during testing (see module notes) — a
    # single isolated re-check of a "dropped" contact proved it was still a live Hot
    # Lead, meaning something during a long real run got silently swallowed before the
    # confirmed-vs-ambiguous retry logic existed. Refuse to write past this rate
    # without an explicit override, even outside --dry-run, rather than trust a big
    # drop blindly. ±20% sanity above is informational; this is a hard stop.
    drop_rate = dropped / len(old_rows) if old_rows else 0.0
    if not dry and not force_write and drop_rate > 0.25 and len(old_rows) >= 20:
        print(f"warm_refresh: ABORTING WRITE — {dropped}/{len(old_rows)} ({drop_rate:.0%}) confirmed-dropped "
              "is implausibly high for one refresh cycle. This is the exact signature of a prior transient-"
              "failure bug (see module docstring). Re-run with --dry-run to inspect, or pass --force-write "
              "if you've verified this drop rate is genuinely correct.")
        return {"ok": False, "error": "drop_rate_too_high", "sanity": sanity,
                "dropped": dropped, "kept_ambiguous": kept_ambiguous}

    if dry:
        print("warm_refresh: --dry-run, nothing written")
        return {"ok": True, "dry_run": True, "sanity": sanity, "rows": len(rows),
                "dropped": dropped, "kept_ambiguous": kept_ambiguous}

    if CSV_PATH.exists():
        bak = CSV_PATH.with_suffix(".csv.bak")
        shutil.copy2(CSV_PATH, bak)
        print(f"warm_refresh: backed up old list -> {bak}")

    rows.sort(key=lambda r: (r["tier"], -int(r["deal_age_days"])))
    # R2-54: tmp + os.replace (house atomic-write pattern) instead of a bare "w"
    # open, which truncates the file immediately -- a crash mid-write used to
    # leave WARM-HITLIST.csv empty/partial with only the .bak (taken a moment
    # earlier) to recover from.
    tmp = CSV_PATH.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, CSV_PATH)
    print(f"warm_refresh: wrote {len(rows)} rows -> {CSV_PATH}")
    planner.feed_add("warm", f"hitlist refreshed: {len(rows)} rows ({tiers.get('1', 0)} tier-1 booked)")
    return {"ok": True, "dry_run": False, "sanity": sanity, "rows": len(rows)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None,
                    help="only refresh the first N hitlist rows (fast smoke-test path)")
    ap.add_argument("--force-write", action="store_true",
                    help="bypass the >25%% confirmed-drop safety gate — only use after verifying "
                         "a large drop is genuinely correct, not a transient-failure artifact")
    args = ap.parse_args()
    run(dry=args.dry_run, limit=args.limit, force_write=args.force_write)
