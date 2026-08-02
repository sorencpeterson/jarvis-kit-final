#!/usr/bin/env python3
"""Accepted-connection follow-up conveyor — A3 (★), A35, A36, A61.

THE CONTRACT (operator side): when a Chrome operator runs the LinkedIn networking
executor skill and sees a pending 'connect' item has been ACCEPTED (the person is
now a 1st-degree connection), it appends one line to store/li_accepted.jsonl:

    {"url": "<profile url>", "name": "<their name>", "accepted_at": "<ISO ts>",
     "connect_item_id": "<the original connect queue item's id>",
     "headline": "<their headline, optional>", "context": "<why-them line if known>"}

That file does not exist yet on this machine (no operator run has produced one)
— this module builds the store schema, the reader, AND the drafting machinery
against it, but until an operator run appends real rows, run_conveyor() has
nothing to act on. This is the [E] boundary the mission calls out: the BRAIN
side (this file) is fully built; the DATA (accepted.jsonl rows) is operator-fed.

What this module does with real rows, once they exist:
  A3  (★) day-2+ conveyor: accepted -> a VALUE DM draft (not a pitch), staged
      into networking's queue as kind="dm", status="pending", same one-tap
      approval flow as every other queue item. Only fires once >= 2 days have
      passed since accepted_at (a same-day DM reads as bot-fast).
  A35 follow-up ladder: day 4 / day 12 no-reply nudges (drafts only), keyed off
      the DM conveyor's OWN queue items (a dm item with status="done" and no
      reply signal after N days gets a follow-up draft chained to it).
  A36 dead-thread closer: 21d past the day-12 follow-up with still no reply ->
      one final soft-close draft, then the thread is marked closed (no more
      drafts generated for it).
  A61 accepted-but-silent re-engage: overlaps A35/A36 conceptually but is
      specifically for connections who accepted and NEVER got a day-2 DM in
      the first place (conveyor never ran, or ran and errored) — a monthly
      sweep that finds those and gives them one more shot at day-2 draft.

networking.py's queue schema is NOT modified for this — a "dm" kind item has
identical shape to every existing kind (id/kind/author/target/url/draft/status/
created), it's just a new value in the `kind` field. server.py's KIND_ORDER
dict doesn't list "dm" so it sorts after comment/reply/connect/like via its
existing `.get(x.get("kind"), 9)` fallback — no crash, just "last" in the
pending sort, which is fine (server.py is out of scope to edit, and this needed
zero changes there for "dm" items to already display correctly in the queue).

Nothing here sends anything. A dm item lands as status="pending" exactly like
every other queue kind; the human approves, then the SAME executor skill
(browser-agent/skills/linkedin-networking-execute.md) sends it via DOM once its
"kind": "dm" case is added there (a browser-agent-owned file, out of this
lane's scope — documented in the CONTRACTS section of the status file instead
of edited directly).
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import new_id, now_iso, humanize  # noqa: E402
import networking  # noqa: E402
import planner  # noqa: E402
import li_quality  # noqa: E402

ACCEPTED = ROOT / "store" / "li_accepted.jsonl"
CONVEYOR_STATE = ROOT / "store" / "li_conveyor_state.jsonl"

DAY2_MIN_DAYS = 2       # A3: don't DM same-day, reads as bot
FOLLOWUP_DAY4 = 4        # A35
FOLLOWUP_DAY12 = 12      # A35
DEAD_THREAD_DAY = 21     # A36, measured from the day-12 follow-up
STALE_NO_DM_DAYS = 30    # A61: accepted this long ago with no day-2 DM ever sent


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


def _append_jsonl(path: Path, rec: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_accepted() -> list[dict]:
    """Last-write-wins by url (an operator could re-append if it re-detects
    the same acceptance across runs; url is the natural key here, matching
    li_history._url_key's normalization so accepted.jsonl and network.jsonl
    agree on what 'the same person' means)."""
    import li_history
    by_url: dict[str, dict] = {}
    order: list[str] = []
    for rec in _read_jsonl(ACCEPTED):
        uk = li_history._url_key(rec.get("url", ""))
        if not uk:
            continue
        if uk not in by_url:
            order.append(uk)
        by_url[uk] = rec
    return [by_url[u] for u in order]


def _conveyor_state() -> dict[str, dict]:
    """url_key -> latest state record. State tracks what stage of the ladder
    (day2/day4/day12/dead) has already been drafted for this contact, so
    run_conveyor() is idempotent (safe to run daily without re-drafting)."""
    import li_history
    out: dict[str, dict] = {}
    for rec in _read_jsonl(CONVEYOR_STATE):
        uk = li_history._url_key(rec.get("url", ""))
        if uk:
            out[uk] = rec
    return out


def _save_state(url: str, stage: str, extra: dict | None = None):
    rec = {"url": url, "stage": stage, "ts": now_iso()}
    if extra:
        rec.update(extra)
    _append_jsonl(CONVEYOR_STATE, rec)


def _days_since(iso_ts: str, today: date | None = None) -> int | None:
    if not iso_ts:
        return None
    today = today or date.today()
    try:
        d = datetime.fromisoformat(iso_ts[:10]).date()
    except ValueError:
        return None
    return max(0, (today - d).days)


DAY2_PROMPT = """You are [OWNER], sending a SHORT first DM to someone who just accepted his
LinkedIn connection request. This is NOT a pitch, NOT a sales message. It's a genuine
day-2 "good to connect" note that offers one concrete piece of value or references
something real about them, never generic.

Voice: direct, punchy, first person, no fluff, no em-dashes, no emojis, contractions
always ("don't", "I'll"). Under 300 characters. At most ONE question, and only if
you're genuinely curious, never as filler. NEVER include a link. NEVER pitch anything.

Context on them:
%s

Write ONE draft DM. If there's not enough real context to say something specific and
non-generic, write a SHORT warm generic-but-not-templated note instead (still no pitch,
still no link) rather than forcing a fake specific.

Return ONLY a JSON object: {"draft": "..."}"""


def draft_day2_dm(accepted_rec: dict) -> str:
    """(A3) One LLM call per contact (small, cheap — Sonnet per config's
    'networking' feature routing, same tier the existing comment/reply drafts
    use). Falls back to '' on any failure, never a placeholder that could
    accidentally queue."""
    context_bits = []
    if accepted_rec.get("name"):
        context_bits.append(f"Name: {accepted_rec['name']}")
    if accepted_rec.get("headline"):
        context_bits.append(f"Headline: {accepted_rec['headline']}")
    if accepted_rec.get("context"):
        context_bits.append(f"Why he connected: {accepted_rec['context']}")
    context = "\n".join(context_bits) or "(no extra context captured at connect time)"
    out = planner._cli_json(DAY2_PROMPT % context, timeout=90, feature="networking")
    if isinstance(out, dict):
        return humanize((out.get("draft") or "").strip())
    return ""


FOLLOWUP_PROMPT = """You are [OWNER]. He sent this LinkedIn DM to a connection %s days ago
and got no reply yet. Write a SHORT, low-pressure follow-up, never pushy, never
"just checking in" or "just following up" (those are banned filler phrases). Give it a
reason to exist (new angle, one more concrete thing) rather than just poking them again.
Under 300 characters, no em-dashes, no emojis, at most one question.

ORIGINAL MESSAGE SENT:
%s

Return ONLY a JSON object: {"draft": "..."}"""


def draft_followup(original_draft: str, days_elapsed: int) -> str:
    out = planner._cli_json(FOLLOWUP_PROMPT % (days_elapsed, original_draft[:280]),
                             timeout=90, feature="networking")
    if isinstance(out, dict):
        return humanize((out.get("draft") or "").strip())
    return ""


CLOSER_PROMPT = """You are [OWNER]. He messaged this LinkedIn connection twice with no reply
over 3 weeks. Write ONE short, graceful closing line that leaves the door open without
being needy or guilt-tripping. No em-dashes, no emojis, no question (this is a closer,
not an opener). Under 200 characters.

Return ONLY a JSON object: {"draft": "..."}"""


def draft_dead_thread_closer() -> str:
    out = planner._cli_json(CLOSER_PROMPT, timeout=80, feature="networking")
    if isinstance(out, dict):
        return humanize((out.get("draft") or "").strip())
    return ""


def _queue_dm(accepted_rec: dict, draft: str, stage: str) -> dict | None:
    """Stage a dm-kind item into networking's EXISTING queue store via its own
    save_item() (the safe, already-tested append path) — this module never
    writes to network.jsonl directly. Runs the SAME validate_draft() gate
    (A6/A21-A40) every other draft in the system passes through before it's
    allowed to queue."""
    v = li_quality.validate_draft(draft, kind="dm", first_touch=(stage == "day2"),
                                   name=accepted_rec.get("name", ""))
    if not v["ok"]:
        return None  # never queue a draft that fails the quality gate
    rec = {
        "id": new_id("dm_" + accepted_rec.get("url", "") + stage),
        "kind": "dm",
        "author": accepted_rec.get("name", ""),
        "target": accepted_rec.get("headline", "") or accepted_rec.get("context", ""),
        "url": accepted_rec.get("url", ""),
        "draft": v["text"],
        "status": "pending",
        "created": now_iso(),
        "conveyor_stage": stage,  # extra field, additive — existing readers ignore unknown keys
    }
    networking.save_item(rec)
    return rec


def _queue_dm_and_advance(rec: dict, draft: str, dm_stage: str, state_stage: str | None = None) -> dict | None:
    """Queue the dm item + advance conveyor state as ONE locked unit (R2-36, 2026-07-13).

    These used to be two separate unlocked appends to two different files: _queue_dm ->
    networking.save_item writes network.jsonl, then a separate _save_state call writes
    li_conveyor_state.jsonl. A crash between them, or two conveyor sweeps overlapping,
    could leave a queued dm with no matching state record — the next sweep then sees
    'not yet at this stage' for that contact and redrafts + re-queues a duplicate dm.
    Locking both writes under CONVEYOR_STATE's lock closes the concurrent-sweep race and
    shrinks the crash window to just these two adjacent statements (no LLM call or other
    slow I/O happens between them)."""
    from store_lib import _flock
    with _flock(CONVEYOR_STATE):
        item = _queue_dm(rec, draft, dm_stage)
        if item:
            _save_state(rec.get("url", ""), state_stage or dm_stage, {"queue_item_id": item["id"]})
        return item


def run_conveyor(dry: bool = False) -> dict:
    """The main sweep: for every accepted contact, figure out what stage of
    the ladder they're due for (if any) and draft + queue it. Idempotent
    (state-tracked), additive-only, drafts nothing twice for the same stage.

    dry=True: compute what WOULD be drafted/queued without calling the LLM or
    writing anything (useful for --dry-run and for tests/fixture mode).
    """
    accepted = load_accepted()
    if not accepted:
        return {"accepted_count": 0, "queued": [], "note": "no store/li_accepted.jsonl rows yet "
                 "(operator hasn't fed accepted-connection data — this is the [E] gap; "
                 "machinery is ready, waiting on operator runs)"}

    import li_history
    state = _conveyor_state()
    today = date.today()
    queued = []
    would_queue = []

    for rec in accepted:
        url = rec.get("url", "")
        uk = li_history._url_key(url)
        st = state.get(uk, {})
        stage_done = st.get("stage", "")
        days = _days_since(rec.get("accepted_at", ""), today)
        if days is None:
            continue

        # A3: day-2 DM, only once, only after DAY2_MIN_DAYS have passed
        if not stage_done and days >= DAY2_MIN_DAYS:
            if dry:
                would_queue.append({"url": url, "stage": "day2", "days_since_accepted": days})
                continue
            draft = draft_day2_dm(rec)
            if draft:
                item = _queue_dm_and_advance(rec, draft, "day2")
                if item:
                    queued.append(item)
            continue

        # A35/A36: follow-up ladder, keyed off the day2 item's OWN status.
        # Only chase a day2 dm that's marked "done" (i.e. it was actually sent)
        # with no separate "replied" signal — this system has no inbound-DM
        # detection yet (that's an operator-fed signal too, same [E] boundary),
        # so the ladder currently fires purely on elapsed time since send,
        # which is honest: it will draft a follow-up even if they DID reply
        # and a human just hasn't marked it. That's a SAFE failure mode (an
        # unnecessary drafted item the human skips) rather than an unsafe one
        # (never following up because reply-detection silently never came).
        day2_item_id = st.get("queue_item_id", "")
        day2_item = next((x for x in networking.load_queue() if x.get("id") == day2_item_id), None)
        if not day2_item or day2_item.get("status") != "done":
            continue
        sent_days = _days_since(day2_item.get("acted_at", day2_item.get("created", "")), today)
        if sent_days is None:
            continue

        if stage_done == "day2" and sent_days >= FOLLOWUP_DAY4:
            if dry:
                would_queue.append({"url": url, "stage": "day4", "days_since_sent": sent_days})
                continue
            draft = draft_followup(day2_item.get("draft", ""), sent_days)
            if draft:
                item = _queue_dm_and_advance(rec, draft, "day4")
                if item:
                    queued.append(item)
            continue

        if stage_done == "day4" and sent_days >= FOLLOWUP_DAY12:
            if dry:
                would_queue.append({"url": url, "stage": "day12", "days_since_sent": sent_days})
                continue
            draft = draft_followup(day2_item.get("draft", ""), sent_days)
            if draft:
                item = _queue_dm_and_advance(rec, draft, "day12")
                if item:
                    queued.append(item)
            continue

        # R2-36 (2026-07-13): was `sent_days >= (FOLLOWUP_DAY12 + DEAD_THREAD_DAY)`. The
        # "day2_item" lookup above (despite its name) refreshes every loop to whichever
        # item queue_item_id currently points at — i.e. the LATEST stage's own dm, which
        # at this point (stage_done == "day12") is the day-12 follow-up itself — so
        # sent_days here is already "days since the day-12 dm was sent." Adding
        # FOLLOWUP_DAY12 again double-counted that gap, pushing the closer ~12 days later
        # than the docstring's "21d past the day-12 follow-up," and the drift compounded
        # further whenever an earlier stage's send was delayed by approval lag (the wrong
        # base timestamp this fixes). Each stage's wait is measured from the message
        # actually sent immediately before it, so the threshold here is just DEAD_THREAD_DAY.
        if stage_done == "day12" and sent_days >= DEAD_THREAD_DAY:
            if dry:
                would_queue.append({"url": url, "stage": "closer", "days_since_sent": sent_days})
                continue
            draft = draft_dead_thread_closer()
            if draft:
                item = _queue_dm_and_advance(rec, draft, "closer", state_stage="closed")
                if item:
                    queued.append(item)

    if dry:
        return {"accepted_count": len(accepted), "would_queue": would_queue}
    if queued:
        planner.feed_add("network", f"{len(queued)} LinkedIn follow-up DM draft(s) staged")
    return {"accepted_count": len(accepted), "queued": [q["id"] for q in queued]}


# ---- A61: accepted-but-silent re-engage (monthly sweep) ----

def find_accepted_but_silent(today: date | None = None) -> list[dict]:
    """(A61) Connections accepted >= STALE_NO_DM_DAYS ago with NO conveyor
    state at all (the day-2 DM never got drafted — conveyor didn't run, or
    errored, or the row was appended after the fact). Distinct from the
    normal day-2 path above, which handles the common case; this is the
    safety-net sweep for gaps."""
    accepted = load_accepted()
    state = _conveyor_state()
    today = today or date.today()
    import li_history
    out = []
    for rec in accepted:
        uk = li_history._url_key(rec.get("url", ""))
        if uk in state:
            continue  # already has SOME conveyor history, not silent
        days = _days_since(rec.get("accepted_at", ""), today)
        if days is not None and days >= STALE_NO_DM_DAYS:
            out.append(rec)
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    result = run_conveyor(dry=args.dry_run)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    silent = find_accepted_but_silent()
    if silent:
        print(f"\nA61: {len(silent)} accepted-but-silent connection(s) (>{STALE_NO_DM_DAYS}d, no conveyor history)")
