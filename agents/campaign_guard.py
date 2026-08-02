#!/usr/bin/env python3
"""Campaign Guard: brand-protection auto-pause for the cold engine. Read-only against
GHL; the only write this file ever makes is a knob going DOWN to 0 in store/config.json
(pausing is always safe — never raises a knob) plus local append-only jsonl logs.

#161 sentiment auto-pause
  Reads store/replies.jsonl, joins each reply's contact_id back to a campaign via
  store/cold_pipeline.jsonl (the only place contact_id<->campaign is recorded), and
  looks at intents from the trailing 7 days. If (negative-ish intents: objection +
  not_now + remove) / (all classified intents) > 30% AND n >= 5 classified replies for
  that campaign in the window, the campaign's enrollment knob (cold_feeder.CAMPAIGNS
  knob field) gets set to 0, a feed line + ntfy notify fires, and the pause is logged
  to store/campaign_guard.jsonl so it's auditable and idempotent (won't re-notify for
  the same pause every run).

#163 unsub/dnd suppress scan
  Any reply with intent "remove" is already suppressed by reply_watch.py itself — this
  file does NOT duplicate that. What THIS adds: a READ scan of GHL contact tags for
  every contact we've ever cold-touched (from cold_pipeline.jsonl), looking for an
  unsubscribe/dnd-style tag (or the platform dnd flag) that may have been set some
  other way (a manual click in GHL, a different workflow, an import). Any hit gets
  appended to store/suppress.jsonl in the exact shape reply_watch._suppress() writes,
  so cold_feeder's existing suppress-list read (it already unions contact_id + email)
  picks it up with zero changes to cold_feeder.

#162 bounce watcher  [E] — partial, see note
  Investigated first: neither CAPABILITIES.md/API-TOOLKIT.md (the gohighlevel-cli docs)
  nor a live GET /conversations/search pull (checked real lastMessageType values: only
  ever TYPE_EMAIL / TYPE_SMS, no bounce/status field) exposes a dedicated bounce or
  email-event REST endpoint. The GHL *internal* workflow system does have an "Email
  Event" trigger family (confirmed via a template account's workflow list — "Email
  Event - Marked SPAM", "Email List Cleaning - Bounced Email" both exist as workflow
  triggers) but that's a canvas trigger, not something this read-only layer can poll.
  So: implemented the documented fallback — parse the same /conversations/search feed
  reply_watch.py already reads for inbound automated mailer-daemon-style bounce
  notifications (sender/body pattern match: mailer-daemon, postmaster, delivery
  status notification, "undelivered", "550", "553", "not found", "no such user",
  combined with lastMessageDirection == inbound and a heuristic "looks automated, not
  a person replying" body shape). 3+ matches in a rolling day -> same knob-to-0 pause
  path as #161. This heuristic is UNTESTED against real bounces (none exist yet, sends
  are still gated at 0) so it's marked [E] awaiting real send volume; it IS unit-tested
  against fixture bounce-shaped messages (see the test run in the mission status file).
  If GHL later exposes a real bounce endpoint, swap detect_bounces()'s source, keep the
  pause path as-is.

Run from the morning chain (agents/morning.sh, after the cold_feeder.py line).
Also runnable by hand: campaign_guard.py [--dry-run]
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
from store_lib import now_iso, LOCAL_TZ  # noqa: E402
import ghl_social  # noqa: E402
import planner  # noqa: E402

REPLIES = ROOT / "store" / "replies.jsonl"
PIPELINE = ROOT / "store" / "cold_pipeline.jsonl"
SUPPRESS = ROOT / "store" / "suppress.jsonl"
GUARD_LOG = ROOT / "store" / "campaign_guard.jsonl"
CONFIG = ROOT / "store" / "config.json"

# Mirrors cold_feeder.CAMPAIGNS (campaign -> knob). Not importing cold_feeder to avoid a
# hard dependency loop / accidentally pulling in its CLI side effects; kept as a small
# constant is the safer coupling. If cold_feeder ever adds a campaign, add it here too.
CAMPAIGN_KNOBS = {"wl": "cold_daily_enroll", "webfix": "webfix_daily_enroll"}

NEGATIVE_INTENTS = {"objection", "not_now", "remove"}
WINDOW_DAYS = 7
NEG_RATIO_THRESHOLD = 0.30
MIN_CLASSIFIED = 5

# Tag substrings that mean "this contact opted out," beyond what reply_watch already
# catches from live replies. Deliberately reuses cold_import.NO_GO's vocabulary style.
UNSUB_TAG_MARKERS = ("unsubscribe", "unsub", "dnd", "do-not-contact", "do not contact", "opt-out", "opt out")

# #162 bounce heuristic: sender/body markers that mean "this is an automated delivery
# failure notice," not a human reply. Deliberately broader than reply_watch.SPAM_MARKERS
# (which screens marketing spam AT [OWNER]) — this looks for the opposite shape, mail
# system chatter ABOUT a message [OWNER]'s system sent.
BOUNCE_SENDER_MARKERS = ("mailer-daemon", "postmaster", "mail delivery subsystem", "mailer subsystem")
BOUNCE_BODY_MARKERS = ("undelivered", "delivery status notification", "delivery has failed",
                       "could not be delivered", "permanent failure", "no such user",
                       "mailbox unavailable", "550 ", "551 ", "553 ", "recipient address rejected",
                       "message wasn't delivered")
BOUNCE_LOG = ROOT / "store" / "bounce_events.jsonl"
BOUNCE_DAILY_THRESHOLD = 3


def _config() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


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


def _api_json(args: list[str]) -> dict:
    out = ghl_social._api(args)
    try:
        return json.loads(out[out.find("{"):])
    except (ValueError, json.JSONDecodeError):
        return {"_raw": out}


def _cold_pipeline_index() -> dict[str, str]:
    """contact_id -> campaign, last-write-wins (a contact can only be one campaign's
    row at a time in practice, but if the store ever has repeats we want the latest)."""
    idx = {}
    for r in _load_jsonl(PIPELINE):
        cid = r.get("contact_id")
        camp = r.get("campaign") or "wl"
        if cid:
            idx[cid] = camp
    return idx


def _within_window(ts: str, cutoff: datetime) -> bool:
    try:
        return datetime.fromisoformat(ts) >= cutoff
    except (ValueError, TypeError):
        return False


def _latest_by_id(records: list[dict]) -> list[dict]:
    """CX6: replies.jsonl is append-only -- the SAME reply id can carry many lines
    (SLA-age refresh, reclassification, status transitions), so a raw line-by-line
    read counts one conversation N times. Keep only the LAST line per id (matching
    how reply_watch._load() itself treats this file) so a single still-pending
    objection re-saved every poll can't trip the 5-reply auto-pause on its own."""
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for r in records:
        rid = r.get("id")
        if not rid:
            continue
        if rid not in by_id:
            order.append(rid)
        by_id[rid] = r
    return [by_id[i] for i in order]


def sentiment_by_campaign() -> dict[str, dict]:
    """{campaign: {"n": int, "neg": int, "ratio": float}} over the trailing window,
    counting only replies whose contact resolves to a known cold-pipeline campaign
    (warm replies, replies with no contact match, etc. are out of scope for this guard)."""
    cid_to_campaign = _cold_pipeline_index()
    cutoff = datetime.now(LOCAL_TZ) - timedelta(days=WINDOW_DAYS)
    stats: dict[str, dict] = {}
    for r in _latest_by_id(_load_jsonl(REPLIES)):
        cid = r.get("contact_id")
        campaign = cid_to_campaign.get(cid) if cid else None
        if not campaign:
            continue
        if not _within_window(r.get("created") or "", cutoff):
            continue
        intent = r.get("intent") or "other"
        if intent not in NEGATIVE_INTENTS and intent not in ("interested", "question", "other", "followup"):
            continue  # unrecognized intent value, skip rather than miscount
        s = stats.setdefault(campaign, {"n": 0, "neg": 0})
        s["n"] += 1
        if intent in NEGATIVE_INTENTS:
            s["neg"] += 1
    for s in stats.values():
        s["ratio"] = (s["neg"] / s["n"]) if s["n"] else 0.0
    return stats


def _already_paused_today(campaign: str) -> bool:
    today = now_iso()[:10]
    for r in _load_jsonl(GUARD_LOG):
        if r.get("campaign") == campaign and r.get("action") == "paused" and (r.get("ts") or "")[:10] == today:
            return True
    return False


def _log_guard(rec: dict):
    GUARD_LOG.parent.mkdir(parents=True, exist_ok=True)
    with GUARD_LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _set_knob_zero(knob: str, dry: bool) -> bool:
    """Write the knob to 0 in config.json. Never writes any OTHER value — pausing only."""
    # F: RMW under lock (matches server.py's config.json writers) -- this was an
    # unlocked read-modify-write, so a concurrent config writer could land between
    # the read and the replace and get silently clobbered, losing whatever it had
    # just set (including a deliverability pause -- a leaking campaign keeps enrolling).
    from store_lib import _flock
    with _flock(CONFIG):
        cfg = _config()
        if int(cfg.get(knob) or 0) == 0:
            return False  # already off, nothing to do
        if dry:
            print(f"  [dry-run] would set {knob} -> 0 (currently {cfg.get(knob)})")
            return True
        cfg[knob] = 0
        import os as _os
        _tmp = CONFIG.with_suffix(".json.tmp")
        _tmp.write_text(json.dumps(cfg, indent=2))
        _os.replace(_tmp, CONFIG)  # atomic: a kill mid-write can't truncate config.json
        return True


def run_sentiment_guard(dry: bool = False) -> list[dict]:
    """#161. Returns the list of campaigns paused this run (empty if none)."""
    stats = sentiment_by_campaign()
    paused = []
    for campaign, s in stats.items():
        if s["n"] < MIN_CLASSIFIED or s["ratio"] <= NEG_RATIO_THRESHOLD:
            continue
        knob = CAMPAIGN_KNOBS.get(campaign)
        if not knob:
            print(f"  campaign_guard: unknown campaign '{campaign}' in replies data, no knob to pause, skipping")
            continue
        if _already_paused_today(campaign):
            print(f"  campaign_guard [{campaign}]: already paused+logged today, not re-notifying")
            continue
        changed = _set_knob_zero(knob, dry)
        pct = round(s["ratio"] * 100)
        msg = (f"{campaign}: {s['neg']}/{s['n']} replies ({pct}%) negative/remove over the last "
               f"{WINDOW_DAYS}d, above the {int(NEG_RATIO_THRESHOLD*100)}% threshold")
        print(f"  campaign_guard [{campaign}]: PAUSING ({msg})" + ("" if changed else " (knob already 0)"))
        # Only persist to the guard log (which idempotency reads back via
        # _already_paused_today) on a REAL run. A --dry-run must be side-effect-free:
        # it must never make a later real run believe today's pause already happened.
        if not dry:
            _log_guard({"ts": now_iso(), "campaign": campaign, "action": "paused", "knob": knob,
                        "n": s["n"], "neg": s["neg"], "ratio": s["ratio"], "dry_run": dry, "reason": msg})
            planner.feed_add("cold", f"{campaign} cold drip auto-paused: {msg}")
            planner.notify("Cold drip auto-paused", msg, tags="warning")
        paused.append({"campaign": campaign, **s})
    if not paused:
        print("  campaign_guard [sentiment]: nothing over threshold, no campaigns paused")
    return paused


def scan_unsub_tags(dry: bool = False) -> list[dict]:
    """#163. READ-only against GHL: for every contact_id we've ever cold-touched, fetch
    the contact and check its tags/dnd flag for an opt-out marker not already caught by
    reply_watch's live-reply suppression. New hits get appended to suppress.jsonl in the
    same shape reply_watch._suppress() uses, so cold_feeder's suppress union picks it up
    with no changes on its side."""
    already_suppressed = {r.get("contact_id") for r in _load_jsonl(SUPPRESS) if r.get("contact_id")}
    contacts = {r.get("contact_id"): r for r in _load_jsonl(PIPELINE) if r.get("contact_id")}
    new_suppressions = []
    for cid, rec in contacts.items():
        if cid in already_suppressed:
            continue
        j = _api_json(["GET", f"/contacts/{cid}"])
        c = j.get("contact") or j
        tags = [t.lower() for t in (c.get("tags") or [])]
        hit_tag = next((t for t in tags if any(m in t for m in UNSUB_TAG_MARKERS)), None)
        is_dnd = bool(c.get("dnd"))
        if not (hit_tag or is_dnd):
            continue
        why = f"GHL tag '{hit_tag}'" if hit_tag else "GHL dnd flag set"
        entry = {"ts": now_iso(), "contact_id": cid, "email": (rec.get("email") or ""), "why": why}
        print(f"  campaign_guard [unsub-scan]: {rec.get('company') or cid} -> suppress ({why})")
        if not dry:
            SUPPRESS.parent.mkdir(parents=True, exist_ok=True)
            with SUPPRESS.open("a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        new_suppressions.append(entry)
    if new_suppressions:
        print(f"  campaign_guard [unsub-scan]: {len(new_suppressions)} new suppression(s) from GHL tags")
        if not dry:
            planner.feed_add("cold", f"{len(new_suppressions)} contact(s) suppressed from GHL unsub/dnd tags")
    else:
        print("  campaign_guard [unsub-scan]: no new opt-outs found in GHL tags")
    return new_suppressions


def _looks_like_bounce(sender: str, body: str) -> bool:
    s, b = (sender or "").lower(), (body or "").lower()
    if any(m in s for m in BOUNCE_SENDER_MARKERS):
        return True
    return any(m in b for m in BOUNCE_BODY_MARKERS)


def detect_bounces(conversations: list[dict]) -> list[dict]:
    """#162 [E]: scan a /conversations/search-shaped list for inbound messages that look
    like automated bounce notices rather than human replies. Pure function (no I/O, no
    GHL calls) so it's directly fixture-testable. `conversations` items are expected to
    carry the same keys reply_watch.py already reads: lastMessageDirection,
    lastMessageType, lastMessageBody, contactName/fullName, email, id."""
    hits = []
    for c in conversations:
        if c.get("lastMessageDirection") != "inbound":
            continue
        if "EMAIL" not in (c.get("lastMessageType") or ""):
            continue
        sender = c.get("email") or c.get("contactName") or c.get("fullName") or ""
        body = c.get("lastMessageBody") or ""
        if _looks_like_bounce(sender, body):
            hits.append({"convo": c.get("id"), "sender": sender, "body": body[:200]})
    return hits


def _bounce_count_today() -> int:
    today = now_iso()[:10]
    return sum(1 for r in _load_jsonl(BOUNCE_LOG) if (r.get("ts") or "")[:10] == today)


def run_bounce_watcher(dry: bool = False) -> dict:
    """#162 [E]. Pulls the same conversations feed reply_watch.py reads, runs
    detect_bounces() over it, logs any new hits to store/bounce_events.jsonl (dedup'd by
    convo id so re-running mid-day doesn't double count), and pauses BOTH cold knobs
    (a bounce doesn't carry a campaign tag the way a reply does, so we can't isolate
    which campaign caused it — pause everything cold, the safer default) if today's
    running total hits BOUNCE_DAILY_THRESHOLD. This is the fallback heuristic path
    (see module docstring #162 note); mark [E], untested against real bounces."""
    loc = ""
    try:
        for line in (ghl_social.GHL / ".env").read_text().splitlines():
            if line.startswith("GHL_LOCATION_ID="):
                loc = line.split("=", 1)[1].strip()
    except OSError:
        pass
    if not loc:
        print("  campaign_guard [bounce-watch]: no GHL location configured, skipping")
        return {"new_hits": [], "today_total": 0}
    out = ghl_social._api(["GET", f"/conversations/search?locationId={loc}&limit=60&sortBy=last_message_date"])
    try:
        convos = json.loads(out[out.find("{"):]).get("conversations", [])
    except (ValueError, json.JSONDecodeError):
        print("  campaign_guard [bounce-watch]: could not read conversations")
        return {"new_hits": [], "today_total": 0}
    already = {r.get("convo") for r in _load_jsonl(BOUNCE_LOG)}
    hits = [h for h in detect_bounces(convos) if h["convo"] not in already]
    if hits and not dry:
        BOUNCE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with BOUNCE_LOG.open("a") as f:
            for h in hits:
                f.write(json.dumps({"ts": now_iso(), **h}, ensure_ascii=False) + "\n")
    elif hits:
        print(f"  [dry-run] would log {len(hits)} bounce hit(s)")
    today_total = _bounce_count_today() + (len(hits) if dry else 0)
    print(f"  campaign_guard [bounce-watch]: {len(hits)} new bounce-shaped message(s), "
          f"{today_total} total today (threshold {BOUNCE_DAILY_THRESHOLD})")
    if today_total >= BOUNCE_DAILY_THRESHOLD:
        already_paused = _already_paused_today("bounce-all")
        if already_paused:
            print("  campaign_guard [bounce-watch]: already paused+logged today, not re-notifying")
        else:
            for knob in CAMPAIGN_KNOBS.values():
                _set_knob_zero(knob, dry)
            msg = f"{today_total} bounce-shaped inbound message(s) today, at/over the {BOUNCE_DAILY_THRESHOLD}/day limit"
            print(f"  campaign_guard [bounce-watch]: PAUSING ALL COLD KNOBS ({msg})")
            if not dry:
                _log_guard({"ts": now_iso(), "campaign": "bounce-all", "action": "paused",
                            "knob": "all", "n": today_total, "reason": msg, "dry_run": dry})
                planner.feed_add("cold", f"Cold drip auto-paused (all campaigns): {msg}")
                planner.notify("Cold drip auto-paused: bounces", msg, tags="warning")
    return {"new_hits": hits, "today_total": today_total}


def run(dry: bool = False) -> dict:
    print("campaign_guard: sentiment auto-pause (#161)")
    paused = run_sentiment_guard(dry)
    print("campaign_guard: unsub/dnd tag scan (#163)")
    suppressed = scan_unsub_tags(dry)
    print("campaign_guard: bounce watcher (#162) [E]")
    bounces = run_bounce_watcher(dry)
    return {"paused": paused, "suppressed": suppressed, "bounces": bounces}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="log/print what would happen, write nothing")
    args = ap.parse_args()
    run(dry=args.dry_run)
