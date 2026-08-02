#!/usr/bin/env python3
"""Organizer agent — the smart parser.

Reads raw inputs (aggregated projects + open todos) and uses one cheap Haiku call
to classify every item into a clean board:
  domain  : topical bucket (webdev, outreach, systems, career, finance, health,
            relationships, mind, personal)
  type    : status  (current state — info, not actionable)
            task    (remaining actionable work)
            reminder(time-bound nudge)
            info    (reference — tuck away)
Plus a synthesized one-line CURRENT STATUS per domain.

Output -> store/board.json  (domains + typed items). todos.jsonl is untouched;
task/reminder items carry ref_id so the dashboard can complete the live todo.

E331 (organize.py learning: his manual re-domains feed the classifier prompt):
app/server.py's PATCH /api/board/item/{iid} sets locked=True the instant [OWNER]
edits a board item by hand (domain/type/priority/due), and this file already
preserved locked items untouched on every run. What was missing: nothing ever
noticed WHEN a lock happened because of a domain change specifically, or fed
that correction back in. This adds store/domain_history.jsonl (append-only,
owned by this file): every AUTO-classified item's domain is recorded once, so
on the NEXT run, if that same item is now locked with a DIFFERENT domain than
its last recorded auto-classification, that's real evidence of a manual
re-domain. Up to FEWSHOT_MAX of the most recent such corrections are rendered
as few-shot examples ("[OWNER] has previously corrected...") prepended to the
classifier prompt, so the model learns his actual domain boundaries over time
instead of only ever seeing the same static rules.

Run:  uv run python agents/organize.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "dashboard"):
    sys.path.insert(0, str(p))
from store_lib import load_todos, new_id, now_iso  # noqa: E402
import planner  # noqa: E402
from runlog import track  # noqa: E402  (E353: runlog adoption)

BOARD = ROOT / "store" / "board.json"
PROJECTS = ROOT / "store" / "projects.json"
DOMAINS_FILE = ROOT / "store" / "domains.json"
DOMAIN_HISTORY = ROOT / "store" / "domain_history.jsonl"
FEWSHOT_MAX = 5  # cap on how many learned corrections get injected per run

DEFAULT_DOMAINS = [
    {"key": "webdev",        "label": "Web Dev",          "icon": "🌐"},
    {"key": "outreach",      "label": "Outreach & Leads", "icon": "📣"},
    {"key": "systems",       "label": "Systems & AI",     "icon": "🤖"},
    {"key": "career",        "label": "Career",           "icon": "💼"},
    {"key": "finance",       "label": "Finance",          "icon": "💰"},
    {"key": "health",        "label": "Health",           "icon": "💪"},
    {"key": "relationships", "label": "Relationships",    "icon": "❤️"},
    {"key": "mind",          "label": "Mind",             "icon": "🧘"},
    {"key": "personal",      "label": "Personal & Dreams","icon": "🌱"},
]


def load_domains():
    try:
        d = json.loads(DOMAINS_FILE.read_text())
        return d if isinstance(d, list) and d else DEFAULT_DOMAINS
    except (OSError, json.JSONDecodeError):
        return DEFAULT_DOMAINS


def _item_key(it: dict) -> str:
    """Stable join key for a board item across runs: ref_id when we have one
    (todo/project ref), else the normalized text (same fallback organize.py's
    own 'skip' set already uses for locked items without a ref_id)."""
    return it.get("ref_id") or (it.get("text") or "").strip().lower()


def _read_domain_history() -> list[dict]:
    if not DOMAIN_HISTORY.exists():
        return []
    out = []
    for line in DOMAIN_HISTORY.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _last_auto_domain_by_key(history: list[dict]) -> dict[str, str]:
    """Last-write-wins: key -> most recently recorded AUTO-classified domain."""
    out: dict[str, str] = {}
    for r in history:
        if r.get("key"):
            out[r["key"]] = r.get("domain", "")
    return out


def _append_domain_history(records: list[dict]) -> None:
    if not records:
        return
    DOMAIN_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with DOMAIN_HISTORY.open("a") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def detect_corrections(locked_items: list[dict], last_auto: dict[str, str]) -> list[dict]:
    """Locked items whose CURRENT domain differs from the last domain this
    file itself auto-classified them into, before [OWNER] locked them — i.e.
    a real manual re-domain, not just any locked item (most locks don't
    change the domain at all, e.g. a priority-only edit)."""
    corrections = []
    for it in locked_items:
        key = _item_key(it)
        prior = last_auto.get(key)
        current = it.get("domain")
        if prior and current and prior != current:
            corrections.append({"text": it.get("text", ""), "from": prior, "to": current})
    return corrections


def _fewshot_block(corrections: list[dict]) -> str:
    if not corrections:
        return ""
    recent = corrections[-FEWSHOT_MAX:]
    lines = [f'- "{c["text"][:80]}" -> [OWNER] moved this from {c["from"]} to {c["to"]}' for c in recent]
    return ("\n[OWNER] has previously CORRECTED these classifications by hand (learn the pattern, "
            "don't just repeat the same items verbatim):\n" + "\n".join(lines) + "\n")


PROMPT = """You are [OWNER]'s information architect. Organize his raw items into a clean board.

DOMAINS (use these exact keys): %s
- webdev: website builds & dev work
- outreach: lead gen, cold/warm outreach, agency lists, GHL campaigns
- systems: automation, AI, the second-brain, scheduled agents
- career: Upwork, Indeed, COO/job applications
- finance: money, revenue, payments, subscriptions
- health: fitness, gym, sleep, diet, body
- relationships: partner (Maddy), family, friends
- mind: mental health, inner work, reset
- personal: learning, languages, travel, dreams

For EACH raw item assign:
- domain: one key above
- type: one of
    status  = a statement of CURRENT STATE, not actionable (e.g. "Ops campaign published, 0 enrollments")
    task    = remaining actionable work (a verb — send, build, fix, finish)
    reminder= time-bound nudge (has or implies a date/deadline)
    info    = reference to keep but tuck away (not actionable, not a live status)
- priority: 1 (high) / 2 / 3 — only for task/reminder, else null. Rank by EXPECTED VALUE: 1 only when it moves money this week or unblocks something that does; effort-heavy low-dollar items rank lower
- due: for REMINDERS only, infer a best-effort date as YYYY-MM-DD (today is %s). Use any explicit (due ...) marker; otherwise estimate from context (e.g. "this week"→a few days out). null for non-reminders or if truly unknown.
- note: any extra context/detail worth keeping when this is opened (e.g. the text after "—", the why, specifics). null if none.
- keep the text short and clean

Also write a ONE-LINE current-status summary per domain that has items (what's the state right now, in [OWNER]'s direct voice).

Return ONLY this JSON (no prose):
{"items":[{"text":"...","domain":"webdev","type":"task","priority":2,"due":null,"note":null,"ref":"<ref if given>"}],
 "status":{"webdev":"one-line status","finance":"..."}}
%s
RAW ITEMS:
%s"""


def _run() -> int:
    DOMAINS = load_domains()
    KEYS = [d["key"] for d in DOMAINS]
    FALLBACK = "personal" if "personal" in KEYS else (KEYS[0] if KEYS else "personal")
    todos = [t for t in load_todos() if t["status"] in ("inbox", "scheduled", "doing")]
    try:
        projects = json.loads(PROJECTS.read_text())
    except (OSError, json.JSONDecodeError):
        projects = []

    # preserve the user's manual edits: keep locked items, never re-add removed ones
    try:
        existing = json.loads(BOARD.read_text())
    except (OSError, json.JSONDecodeError):
        existing = {}
    locked = [it for it in existing.get("items", []) if it.get("locked")]
    removed = set(existing.get("removed", []))
    skip = set(removed)
    for it in locked:
        skip.add(it.get("ref_id") or it.get("text", "").strip().lower())

    # E331: has [OWNER] re-domained any of these locked items since we last
    # auto-classified them? If so, that's a real correction signal to teach
    # the classifier, not just an opaque "leave it alone."
    history = _read_domain_history()
    last_auto = _last_auto_domain_by_key(history)
    corrections = detect_corrections(locked, last_auto)
    fewshot = _fewshot_block(corrections)
    if corrections:
        print(f"organize: learned from {len(corrections)} manual re-domain(s) this run")

    raw = []
    for t in todos:
        if t["id"] in skip:
            continue
        tag = "[TODO]" + (f"(due {t['scheduled_time'][:16]})" if t.get("scheduled_time") else "")
        raw.append(f'{tag} ref={t["id"]}: {t["text"]}')
    for p in projects:
        if p["name"].strip().lower() in skip:
            continue
        raw.append(f'[PROJECT status={p.get("status","")}] ({p.get("area","")}) {p["name"]}'
                   + (f' — {p["note"]}' if p.get("note") else ''))

    auto, status = [], {}
    if raw:
        # Chunk the classifier: one call carrying 76 items blew the 150s alarm inside
        # planner._cli and returned None, leaving EVERYTHING unclassified that run
        # (2026-07-11 audit: "returned nothing usable for 76 raw item(s)" daily). Batches
        # of 25 keep each call comfortably inside its own timeout; a failed batch now
        # loses only its 25, and the merge below is order-preserving.
        _BATCH = 25
        merged_items, merged_status, failed_batches = [], {}, 0
        for bi in range(0, len(raw), _BATCH):
            chunk = raw[bi:bi + _BATCH]
            data = planner._cli_json(
                PROMPT % (", ".join(KEYS), now_iso()[:10], fewshot, "\n".join(chunk)), timeout=160)
            if isinstance(data, dict) and "items" in data:
                merged_items.extend(data.get("items", []))
                merged_status.update({k: v for k, v in (data.get("status") or {}).items() if k in KEYS})
            else:
                failed_batches += 1
        if failed_batches:
            print(f"organize: {failed_batches} classifier batch(es) of {_BATCH} failed "
                  f"(timeout/bad JSON); classified {len(merged_items)} of {len(raw)} items")
        data = {"items": merged_items, "status": merged_status} if merged_items else None
        if isinstance(data, dict) and "items" in data:
            for it in data.get("items", []):
                dom = it.get("domain") if it.get("domain") in KEYS else FALLBACK
                typ = it.get("type") if it.get("type") in ("status", "task", "reminder", "info") else "info"
                due = it.get("due")
                auto.append({
                    "id": new_id(it.get("text", "") + dom + typ),
                    "text": (it.get("text") or "").strip(),
                    "domain": dom, "type": typ,
                    "priority": it.get("priority") if it.get("priority") in (1, 2, 3) else None,
                    "due": due if (isinstance(due, str) and len(due) >= 8) else None,
                    "note": (it.get("note") or None), "source": "todo" if it.get("ref") else "project",
                    "ref_id": it.get("ref") or None, "locked": False,
                })
            status = {k: v for k, v in (data.get("status") or {}).items() if k in KEYS}
        elif not locked:
            print("Organizer returned nothing usable.")
            return 1
        else:
            # PRE-EXISTING GAP, fixed in passing: locked items being non-empty
            # used to make a classifier failure (timeout/bad JSON) print
            # NOTHING and silently keep only the locked items, indistinguishable
            # from "there was genuinely nothing new to classify." With 50+ raw
            # items the classifier call can exceed its own alarm-based timeout
            # (perl alarm at timeout-10s in planner._cli) and return None; that's
            # now surfaced instead of hidden.
            print(f"organize: classifier call returned nothing usable for {len(raw)} raw "
                  f"item(s) (timeout or bad JSON) — keeping {len(locked)} locked item(s) only, "
                  f"{len(raw)} item(s) NOT classified this run.")

    # E331: record this run's auto-classifications so a FUTURE run can detect
    # if [OWNER] locks one of these items with a different domain than we gave it.
    _append_domain_history([
        {"key": _item_key(it), "domain": it["domain"], "ts": now_iso()} for it in auto
    ])

    items = locked + auto
    status = {**existing.get("status", {}), **status}  # keep status for locked-only domains
    # The classifier call above can run 160s+; [OWNER] may have locked/edited items in the
    # dashboard meanwhile. Under the lock, re-read and let HIS fresh edits win, then write
    # atomically (2026-07-07 audit: the unlocked write_text silently ate manual edits).
    import os as _os
    from store_lib import _flock
    with _flock(BOARD):
        try:
            fresh = json.loads(BOARD.read_text())
            fresh_locked = {i.get("id"): i for i in fresh.get("items", []) if i.get("locked")}
            fresh_removed = set(fresh.get("removed", []))
            items = [fresh_locked.pop(i.get("id"), i) for i in items
                     if _item_key(i) not in fresh_removed and i.get("id") not in fresh_removed]
            items += list(fresh_locked.values())  # locked while we ran: keep them
            removed = removed | fresh_removed
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
        board = {"domains": DOMAINS, "items": items, "status": status,
                 "removed": list(removed), "generated": now_iso()}
        _tmp = BOARD.with_suffix(".json.tmp")
        _tmp.write_text(json.dumps(board, indent=2, ensure_ascii=False))
        _os.replace(_tmp, BOARD)

    by = {}
    for it in items:
        by.setdefault(it["domain"], {}).setdefault(it["type"], 0)
        by[it["domain"]][it["type"]] += 1
    print(f"Organized {len(items)} items across {len(by)} domains:")
    for k in KEYS:
        if k in by:
            print(f"  {k:<14} " + " ".join(f"{t}:{n}" for t, n in by[k].items()))
    planner.feed_add("agent", f"Reorganized board — {len(items)} items, {len(by)} domains")
    return 0


def main() -> int:
    with track("organize"):  # E353: runlog adoption
        return _run()


if __name__ == "__main__":
    raise SystemExit(main())
