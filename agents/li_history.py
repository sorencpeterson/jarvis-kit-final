#!/usr/bin/env python3
"""LinkedIn full-history dedupe + cooldown + stale sweep — A4, A9, A19.

networking.py's own _seen_urls() only dedupes against what's CURRENTLY in the
queue (any status). That's fine for "don't queue the same URL twice right now,"
but it does NOT stop re-sourcing someone who was already attempted, declined, or
connected in the past and later fell out of the queue file's dedup window (there
isn't one today, but this module is what makes "check ALL history" an explicit,
testable operation rather than an assumption).

This module is read-only against store/network.jsonl (via networking.load_queue(),
never opens the file directly, so it always sees the same last-write-wins view
networking.py itself uses) plus its own append-only companion store for company-
level cooldowns (A9) and stale-expiry audit trail (A19). It does not mutate
network.jsonl directly for A4 (that's just a filter function) but DOES call
networking.set_status() for A19 (marking pending items expired), which is the
existing, safe, additive-only mutation path networking.py already exposes.

Run standalone: .venv/bin/python agents/li_history.py --sweep-stale
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import networking  # noqa: E402
import planner  # noqa: E402

COOLDOWNS = ROOT / "store" / "li_company_cooldowns.jsonl"
STALE_DAYS_DEFAULT = 21  # A19: pending >21d auto-expires


def _url_key(url: str) -> str:
    """Normalize a LinkedIn profile/post URL for stable dedupe: strip query
    string + trailing slash + protocol/www so 'linkedin.com/in/x/?a=1' and
    'https://www.linkedin.com/in/x' are recognized as the same target."""
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://(www\.)?", "", u)
    u = u.split("?", 1)[0]
    return u.rstrip("/")


def _company_from_target_text(target_text: str, headline: str = "") -> str:
    """Best-effort company extraction from headline text: '... @ Acme Inc' or
    '... at Acme Inc'. No invented data — returns '' if no pattern matches,
    NEVER guesses a company from a name or generic text.

    Uses a (?:^|\\s) lookbehind-style anchor rather than \\b before the '@'
    alternative: \\b requires a word-char/non-word-char transition, and '@'
    preceded by a space has NO such transition (space and @ are both non-word
    chars), so \\b silently never matched the '@' case at all. '\\s' still
    works fine for the 'at' alternative since word boundaries around plain
    letters behave normally."""
    blob = f"{headline} {target_text}".strip()
    m = re.search(r"(?:^|\s)(?:@|at)\s+([A-Z][\w&.,' -]{2,60})", blob)
    return m.group(1).strip().rstrip(".,") if m else ""


def full_history_index() -> dict:
    """(A4) One pass over ALL history (every status, not just pending/current),
    keyed by normalized URL. Returns {url_key: {"status": ..., "kind": ...,
    "created": ..., "count": n}} — the LAST status seen for that URL across every
    record networking.load_queue() returns (which is already last-write-wins by
    id, but the SAME person can have been queued under multiple record ids over
    time — e.g. sourced twice months apart — so this re-aggregates by url_key on
    top of that)."""
    idx: dict[str, dict] = {}
    for rec in networking.load_queue():
        uk = _url_key(rec.get("url", ""))
        if not uk:
            continue
        prev = idx.get(uk)
        if not prev or (rec.get("created", "") >= prev.get("created", "")):
            idx[uk] = {"status": rec.get("status"), "kind": rec.get("kind"),
                       "created": rec.get("created", ""), "author": rec.get("author", "")}
        idx.setdefault(uk, {}).setdefault("_count", 0)
    counts = Counter(_url_key(r.get("url", "")) for r in networking.load_queue() if r.get("url"))
    for uk, c in counts.items():
        if uk in idx:
            idx[uk]["count"] = c
    return idx


def already_touched(url: str, history: dict | None = None) -> dict | None:
    """(A4) Returns the history record if this URL has EVER appeared in the
    queue (any status: pending/approved/done/skipped), else None. Callers use
    this at SOURCING time to drop a candidate before it's even scored, not just
    before it's queued (networking._seen_urls() already covers 'not currently
    queued'; this covers 'never touched, ever')."""
    history = history if history is not None else full_history_index()
    return history.get(_url_key(url))


def filter_unattempted(targets: list[dict], history: dict | None = None) -> list[dict]:
    """(A4) Drop any sourced target whose URL has EVER been in the queue, of
    ANY status. This is the sourcing-time counterpart to networking.py's
    per-run _seen_urls() (which only guards the current save)."""
    history = history if history is not None else full_history_index()
    return [t for t in targets if not already_touched(t.get("url", ""), history)]


# ---- A9: decline/ignore decay -> company cooldown ----

def _load_cooldowns() -> list[dict]:
    if not COOLDOWNS.exists():
        return []
    out = []
    for line in COOLDOWNS.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _save_cooldown(rec: dict):
    COOLDOWNS.parent.mkdir(parents=True, exist_ok=True)
    with COOLDOWNS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def active_cooldowns(today: date | None = None) -> dict[str, dict]:
    """Company (lowercased) -> latest cooldown record, filtered to still-active
    (until date >= today). Last-write-wins by company, same discipline as every
    other store here."""
    today = today or date.today()
    by_company: dict[str, dict] = {}
    for rec in _load_cooldowns():
        c = (rec.get("company") or "").strip().lower()
        if not c:
            continue
        by_company[c] = rec  # append-only, last line wins
    active = {}
    for c, rec in by_company.items():
        try:
            until = datetime.fromisoformat(rec.get("until", "")[:10]).date()
        except ValueError:
            continue
        if until >= today:
            active[c] = rec
    return active


def compute_company_ignore_counts() -> Counter:
    """(A9) 'declined' isn't a status networking.py's executor writes today
    (connects either go to done or stay pending/approved — LinkedIn doesn't
    expose 'they declined' to the sender), so this counts SKIPPED connect
    items per company as the ignore signal (a skip is [OWNER] or the operator
    saying 'not this one,' which is the closest real proxy this system has to
    'ignored'; when/if an operator run starts writing an explicit declined
    status, add it to the status tuple below, additive, no other change
    needed)."""
    counts = Counter()
    for rec in networking.load_queue():
        if rec.get("kind") != "connect" or rec.get("status") not in ("skipped",):
            continue
        company = _company_from_target_text(rec.get("target", ""))
        if company:
            counts[company.lower()] += 1
    return counts


def apply_decline_decay(threshold: int = 2, cooldown_days: int = 90) -> list[dict]:
    """(A9) 2+ ignored (skipped) connect requests to the same company -> write a
    90-day cooldown record for that company, IF one isn't already active. Returns
    the list of newly-written cooldown records (empty if nothing new). Additive
    only (append to li_company_cooldowns.jsonl), never touches network.jsonl."""
    counts = compute_company_ignore_counts()
    active = active_cooldowns()
    new_records = []
    for company, n in counts.items():
        if n >= threshold and company not in active:
            until = (date.today() + timedelta(days=cooldown_days)).isoformat()
            rec = {"company": company, "ignored_count": n, "until": until,
                   "created": now_iso(), "reason": f"{n} skipped connect(s)"}
            _save_cooldown(rec)
            new_records.append(rec)
    return new_records


def company_on_cooldown(company: str, active: dict | None = None) -> bool:
    active = active if active is not None else active_cooldowns()
    return (company or "").strip().lower() in active


def filter_cooldown_companies(targets: list[dict]) -> list[dict]:
    """Drop sourced targets whose company (extracted from headline) is currently
    on cooldown (A9). Targets with no extractable company pass through untouched
    (never guessed, never penalized for a field we couldn't parse)."""
    active = active_cooldowns()
    out = []
    for t in targets:
        company = _company_from_target_text(t.get("target", ""), t.get("headline", ""))
        if company and company_on_cooldown(company, active):
            continue
        out.append(t)
    return out


# ---- A19: stale-queue sweeper ----

def find_stale(days: int = STALE_DAYS_DEFAULT, today: date | None = None) -> list[dict]:
    """Pending items older than `days`. Read-only, no mutation."""
    today = today or date.today()
    out = []
    for rec in networking.load_queue():
        if rec.get("status") != "pending":
            continue
        created = (rec.get("created") or "")[:10]
        if not created:
            continue
        try:
            d = datetime.fromisoformat(created).date()
        except ValueError:
            continue
        if (today - d).days > days:
            out.append(rec)
    return out


def sweep_stale(days: int = STALE_DAYS_DEFAULT, dry: bool = True) -> list[dict]:
    """(A19) Pending items older than `days` -> status 'expired', reason logged.
    Uses networking.set_status() (the existing safe mutation path — appends a
    new line with the same id, additive-only) so this needs NO new write path
    into network.jsonl. dry=True (default) only reports, writes nothing."""
    stale = find_stale(days)
    if dry:
        return stale
    swept = []
    for rec in stale:
        updated = networking.set_status(rec["id"], "expired")
        if updated:
            swept.append(updated)
    if swept:
        planner.feed_add("network", f"{len(swept)} stale LinkedIn queue item(s) expired (>{days}d pending)")
    return swept


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep-stale", action="store_true", help="expire pending items older than --days")
    ap.add_argument("--days", type=int, default=STALE_DAYS_DEFAULT)
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--decline-decay", action="store_true", help="apply A9 company cooldown decay")
    args = ap.parse_args()

    if args.decline_decay:
        new = apply_decline_decay()
        print(f"li_history: {len(new)} new company cooldown(s) written")
        for r in new:
            print(f"  {r['company']}: {r['ignored_count']} ignored -> cooldown until {r['until']}")

    if args.sweep_stale:
        stale = find_stale(args.days)
        print(f"li_history: {len(stale)} pending item(s) older than {args.days}d")
        if not args.dry_run and stale:
            swept = sweep_stale(args.days, dry=False)
            print(f"li_history: expired {len(swept)} item(s)")
        elif stale:
            print("(dry run, use --sweep-stale without --dry-run to actually expire)")

    if not args.sweep_stale and not args.decline_decay:
        idx = full_history_index()
        print(f"li_history: {len(idx)} distinct URL(s) in full history")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
