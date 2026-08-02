#!/usr/bin/env python3
"""Daily drip-enroller for the cold WL campaign. OFF by default.

Adds the workflow trigger tag `wl-cold` to a small batch of staged contacts per day
(config `cold_daily_enroll`, default 0). Refuses to touch anyone unless ALL of:

  1. cold_daily_enroll > 0             — [OWNER]'s explicit go switch
  2. deliverability preflight is green — cold_preflight.check_all()["ready"]
  3. the workflow exists AND is published — he publishes the draft himself in GHL

Idempotent per day: counts today's enrollments first, so the morning self-heal
re-running the routine can't double-feed. GHL also skips DND contacts platform-side,
and any reply hits the workflow's reply-exit goal and lands in the REPLIES drawer.

Send-window humanizer (#165): today's quota is split into 2-3 mini-batches, and each
tag call inside a batch waits a random 20-90s before the next one (never a flat :00
machine-gun burst). A wall-clock budget caps total jitter at 20 minutes so this can't
stall the rest of the morning chain; --rehearse and a tiny remainder never jitter.
"""
from __future__ import annotations

import json
import random
from datetime import datetime
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import LOCAL_TZ, now_iso  # noqa: E402
import cold_preflight  # noqa: E402
import ghl_social  # noqa: E402
import planner  # noqa: E402

PIPELINE = ROOT / "store" / "cold_pipeline.jsonl"
# One entry per campaign; records without a campaign field default to "wl".
CAMPAIGNS = [
    {"campaign": "wl", "prefix": "[2026-07] Cold Agencies - WL Sites",
     "tag": "wl-cold", "knob": "cold_daily_enroll", "live_knob": "cold_workflow_live"},
    {"campaign": "webfix", "prefix": "[2026-07] Webfix - Agencies With Broken Sites",
     "tag": "wl-webfix", "knob": "webfix_daily_enroll", "live_knob": "webfix_workflow_live"},
]

# #165 send-window humanizer knobs
JITTER_MIN_S, JITTER_MAX_S = 20, 90   # random pause between individual tag calls
JITTER_BUDGET_S = 20 * 60             # hard wall-clock ceiling on total jitter per run


def _api_json(args: list[str]) -> dict:
    out = ghl_social._api(args)
    try:
        return json.loads(out[out.find("{"):])
    except (ValueError, json.JSONDecodeError):
        return {"_raw": out}


def _tag_ok(j: dict) -> bool:
    """R: did a GHL /contacts/{id}/tags POST actually succeed? `_api_json` only sets
    `_raw` when the response FAILED to parse as JSON at all -- a well-formed GHL
    error body (404/429/401, e.g. {"statusCode":404,"message":"Contact not found"})
    parses cleanly and has no `_raw` key, so `not j.get("_raw")` was True for an
    error response too, and the contact got marked 'enrolled' with no tag ever
    applied (never sent to). Fails CLOSED: a raw-text fallback, an explicit error
    status, or the absence of a real success signal (tags/tagsAdded) all count as
    failure."""
    if not isinstance(j, dict) or j.get("_raw") is not None:
        return False
    status = j.get("statusCode")
    try:
        if status is not None and int(status) >= 400:
            return False
    except (ValueError, TypeError):
        pass
    return j.get("tags") is not None or bool(j.get("tagsAdded"))


def _live_state_ok(cc: dict) -> bool:
    """R2#4: did the pre-tag contact GET (CX3's live dnd/tag re-check) actually
    return a usable, verifiable contact shape? Mirrors _tag_ok's fail-closed
    logic: a raw-text fallback (unparseable body), an explicit 4xx/5xx status,
    or a body with none of the real contact-shaped fields (e.g. a JSON error
    body like {"statusCode":404,"message":"Contact not found"}, which parses
    cleanly and has no `_raw` key) all count as "can't verify current state" --
    the caller must treat that as a reason to SKIP tagging, not as "no dnd, no
    no-go tags". The previous version defaulted to the latter: an errored or
    malformed live-recheck read as a clean contact and got tagged anyway."""
    if not isinstance(cc, dict) or cc.get("_raw") is not None:
        return False
    status = cc.get("statusCode")
    try:
        if status is not None and int(status) >= 400:
            return False
    except (ValueError, TypeError):
        pass
    return any(k in cc for k in ("id", "tags", "dnd", "email"))


def _config() -> dict:
    try:
        return json.loads((ROOT / "store" / "config.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def load_pipeline() -> dict:
    recs = {}
    if PIPELINE.exists():
        for line in PIPELINE.read_text().splitlines():
            try:
                r = json.loads(line)
                recs[r["email"]] = r
            except (json.JSONDecodeError, KeyError):
                continue
    return recs


def workflow_published(prefix: str) -> bool:
    from cold_import import _loc
    j = _api_json(["GET", f"/workflows/?locationId={_loc()}"])
    for w in j.get("workflows", []):
        if (w.get("name") or "").startswith(prefix):
            return (w.get("status") or "").lower() == "published"
    return False


def _ramp_cap(first_enrolled_ts: str) -> int:
    """Deliverability ramp: day 1 sends 10, +5 per day since the first enrollment. Protects
    the warmed domain (a cold domain jumping to 30/day looks like spam). Extracted from run()
    so it's tested against the REAL formula, not a drifting hand-copied replica (test audit).
    A missing/naive/garbage ts falls back to day-1 (10), never crashes the ramp."""
    if first_enrolled_ts and first_enrolled_ts != "9999":
        try:
            days_in = max(0, (datetime.now().astimezone()
                              - datetime.fromisoformat(first_enrolled_ts)).days)
        except (ValueError, TypeError):
            days_in = 0
        return 10 + 5 * days_in
    return 10  # first day ever


def run():
    if "--rehearse" in sys.argv:
        print(f"[rehearse] {'cold feeder'}: would run with live config; nothing touched")
        # fall through with a no-op API layer
        global _api_json
        real = _api_json
        _api_json = lambda a: (print("  [rehearse] API:", a[0], a[1]) or {})

    cfg = _config()
    active = [c for c in CAMPAIGNS if int(cfg.get(c["knob"]) or 0) > 0]
    if not active:
        print("cold feeder: off (no campaign knobs set)")
        return 0

    pre = cold_preflight.check_all()
    if not pre["ready"]:
        print("cold feeder: BLOCKED, deliverability preflight is red:", pre)
        planner.notify("Cold drip blocked", "Deliverability preflight failed. Check the COLD panel.")
        return 0

    recs = load_pipeline()
    today = now_iso()[:10]
    for c in active:
        n = int(cfg.get(c["knob"]) or 0)
        if (ROOT / "store" / ".travel-mode").exists():
            n = max(1, n // 2)  # abroad: half volume (#86)
        # CX4: cold_workflow_live (and its webfix sibling) used to let a stale or
        # manually-set config flag substitute for actually checking GHL -- if that
        # flag is ever True while the workflow is paused/unpublished/deleted, this
        # bypassed the 3rd go-live gate the module docstring promises ("the workflow
        # exists AND is published"). workflow_published() against the live GHL API
        # is now the only source of truth; the config flag is no longer read here.
        if not workflow_published(c["prefix"]):
            print(f"cold feeder [{c['campaign']}]: workflow not published yet; nothing enrolled")
            continue
        mine = [r for r in recs.values() if (r.get("campaign") or "wl") == c["campaign"]]
        done_today = sum(1 for r in mine if r.get("status") == "enrolled"
                         and (r.get("enrolled_ts") or "")[:10] == today)
        # deliverability ramp: day 1 sends 10, +5/day until the knob cap. Protects the
        # domain we warmed; a cold domain jumping straight to 30/day looks like spam.
        first_ts = min((r.get("enrolled_ts") or "9999" for r in mine if r.get("status") == "enrolled"),
                       default="")
        ramp_cap = _ramp_cap(first_ts)
        if ramp_cap < n:
            print(f"cold feeder [{c['campaign']}]: ramp day cap {ramp_cap} (knob {n})")
            n = ramp_cap
        room = n - done_today
        if room <= 0:
            print(f"cold feeder [{c['campaign']}]: daily cap reached ({done_today}/{n})")
            continue
        # suppress list: anyone who ever said "remove me" (reply_watch writes it) never enrolls
        supp_f = ROOT / "store" / "suppress.jsonl"
        suppressed = set()
        if supp_f.exists():
            for line in supp_f.read_text().splitlines():
                try:
                    x = json.loads(line)
                    suppressed.add(x.get("contact_id") or "")
                    suppressed.add((x.get("email") or "").lower())
                except (ValueError, json.JSONDecodeError):
                    continue
            suppressed.discard("")
        staged = sorted((r for r in mine if r.get("status") == "staged" and r.get("contact_id")
                         and r.get("contact_id") not in suppressed
                         and (r.get("email") or "").lower() not in suppressed),
                        key=lambda r: r.get("ts", ""))[:room]
        if not staged:
            print(f"cold feeder [{c['campaign']}]: nothing staged")
            continue
        # #165: never a flat :00 machine-gun burst. Split into 2-3 mini-batches and jitter
        # 20-90s between individual tag calls, budget-capped so this can't blow out the
        # morning chain. Rehearse mode (and tiny remainders) skip the sleeps entirely.
        rehearsing = "--rehearse" in sys.argv
        n_batches = 1 if (rehearsing or len(staged) <= 1) else random.choice([2, 3])
        batches, bsize = [], -(-len(staged) // n_batches)  # ceil division
        for i in range(0, len(staged), bsize):
            batches.append(staged[i:i + bsize])
        enrolled = 0
        jitter_spent = 0.0
        run_start = time.monotonic()
        from store_lib import _flock
        from cold_import import NO_GO as _NO_GO
        for bi, batch in enumerate(batches):
            for ri, r in enumerate(batch):
                # CX3: `staged` above reflects cold_import.py's dnd/tag check at
                # STAGING time -- hours to days before the daily ramp actually
                # reaches this contact. A contact who became a client, booked a
                # call, or got DND'd/unsubscribed in GHL since staging must not be
                # cold-tagged on stale information. Re-fetch and re-check live,
                # immediately before the tag call, with the same NO_GO vocabulary
                # cold_import used at staging (dnd flag + unsub/client/booked tags).
                cur = _api_json(["GET", f"/contacts/{r['contact_id']}"])
                cc = cur.get("contact") or cur
                verify_failed = not _live_state_ok(cc)
                # R2#4: fail CLOSED. A malformed/errored GET (verify_failed) must not
                # read as "no dnd, no tags" and let the contact through un-verified --
                # short-circuits before cc.get(...) below, so a garbage `cc` shape is
                # never inspected either.
                cc_tags = [] if verify_failed else [t.lower() for t in (cc.get("tags") or [])]
                stale_no_go = verify_failed or bool(cc.get("dnd")) or any(
                    s in t for t in cc_tags for s in _NO_GO)

                if stale_no_go:
                    detail = ("live-recheck failed/unverifiable" if verify_failed
                             else "dnd" if cc.get("dnd") else ",".join(cc_tags)[:120])
                    with _flock(PIPELINE), PIPELINE.open("a") as f:
                        f.write(json.dumps({**r, "status": "skipped_no_go", "detail": detail},
                                           ensure_ascii=False) + "\n")
                    print(f"  skipped {r['email']}: "
                         + ("live-recheck failed, failing closed" if verify_failed
                            else f"contact state changed since staging "
                                 f"({'dnd' if cc.get('dnd') else 'no-go tag'})"))
                elif rehearsing:
                    enrolled += 1  # count for the preview total, but write nothing
                else:
                    j = _api_json(["POST", f"/contacts/{r['contact_id']}/tags",
                                   "--json", json.dumps({"tags": [c["tag"]]})])
                    if _tag_ok(j):
                        # H: shared _flock with cold_import.record() -- cold_import
                        # already claims this lock on its own appends to the same
                        # file; this writer wasn't actually taking it, so a
                        # janitor-compact (or cold_import) landing mid-write could
                        # drop an enrollment record (double-send risk). Taken
                        # per-append, not held across the jitter sleeps below, so
                        # this can't also starve those other writers for the whole
                        # multi-minute batch run.
                        with _flock(PIPELINE), PIPELINE.open("a") as f:
                            f.write(json.dumps({**r, "status": "enrolled", "enrolled_ts": now_iso()},
                                               ensure_ascii=False) + "\n")
                        enrolled += 1
                    else:
                        print(f"  tag failed for {r['email']}: {str(j)[:120]}")
                is_last_call = (bi == len(batches) - 1) and (ri == len(batch) - 1)
                if is_last_call:
                    continue
                if rehearsing or jitter_spent >= JITTER_BUDGET_S:
                    time.sleep(0.4)  # budget exhausted or rehearsing: fall back to the old pacing
                    continue
                pause = min(random.uniform(JITTER_MIN_S, JITTER_MAX_S),
                           JITTER_BUDGET_S - jitter_spent)
                jitter_spent += pause
                time.sleep(pause)
        elapsed = time.monotonic() - run_start
        print(f"cold feeder [{c['campaign']}]: enrolled {enrolled} (today {done_today + enrolled}/{n}) "
              f"across {len(batches)} mini-batch(es), {elapsed:.0f}s wall time")
        if enrolled:
            planner.feed_add("cold", f"{c['campaign']}: {enrolled} agencies entered the sequence")
            planner.notify("Cold drip ran", f"{c['campaign']}: {enrolled} enrolled today.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
