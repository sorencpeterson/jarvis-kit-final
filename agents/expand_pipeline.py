#!/usr/bin/env python3
"""#177 cold list expansion pipeline: read a CSV of scraped agencies, dedupe against
everything already touched, generate hooks in cheap batches, append staged rows.
DRY-RUN by default (no writes) — pass --commit to actually append to cold_pipeline.jsonl.
Local-only writes; never touches GHL (staging here is the same pre-GHL stage
cold_import.py's CSV inputs sit at — this produces MORE rows for cold_import.py to
later import, it doesn't call GHL itself).

Note on hook quality vs. the existing crawl-based pipeline: this mission's brief is
explicit — "hooks via claude -p (planner._cli, feature 'default', batch of 10 per
call)". There's a heavier, evidence-graded pipeline elsewhere in the repo
(playwright-project/automations/agency-enrichment/hooks_cli.py, NOT this mission's
file, studied only as read-only reference) that crawls each agency's live site and
requires quoted on-page evidence per hook. This file deliberately does the LIGHTER
thing the brief asked for: a batched claude -p call working off whatever fields the
input CSV already has (company/niche/city), no live crawl. Every generated hook gets
tagged confidence:"low" in the output (never "send"-ready the way the crawl pipeline's
high/medium rows are) so cold_import.py / a human reviewing this file never mistakes
a batch-guessed hook for an evidence-grounded one.

Dedupe sources (a row is skipped if it matches ANY of these by email, lowercased):
  1. store/cold_pipeline.jsonl — every email that has EVER appeared there, any status
     (staged/enrolled/skipped/error), this campaign or any other. "ALL history" per
     the brief, not just today's campaign.
  2. store/suppress.jsonl — anyone who opted out or bounced.
  3. ~/Claude/NO_GO.csv or NO_GO.txt, if present (one email per line, or an "email"
     column). Optional — the brief says "if present"; this file works fine without it,
     just prints that it wasn't found rather than failing.

Usage:
  expand_pipeline.py scraped.csv                # dry-run: show what WOULD be staged
  expand_pipeline.py scraped.csv --commit        # actually append to cold_pipeline.jsonl
  expand_pipeline.py scraped.csv --commit --limit 50
"""
from __future__ import annotations

import argparse
import csv
import os
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso, humanize  # noqa: E402
import planner  # noqa: E402

PIPELINE = ROOT / "store" / "cold_pipeline.jsonl"
SUPPRESS = ROOT / "store" / "suppress.jsonl"
NO_GO_CANDIDATES = [ROOT / "store" / "NO_GO.csv", ROOT / "store" / "NO_GO.txt"]
BATCH_SIZE = 10

HOOK_PROMPT = """You write short, honest cold-outreach personalization hooks for a
white-label web-dev/marketing operator ([OWNER], sells $1K 48-72hr website builds to
agencies with dev overflow). For EACH agency below, write a ONE-SENTENCE hook a human
could plausibly have noticed about them, using ONLY the fields given (company name,
city, niche/source if given) — do NOT invent specifics you don't have (no fake project
names, no fake client names, no fake awards). If nothing genuine is inferable beyond
the company name/location, write "NO STRONG HOOK" instead of fabricating one.

VOICE (hard rules): no em-dashes, short, direct, no marketing fluff, no "I noticed"
throat-clearing, no exclamation points.

Return ONLY a JSON array, one string per input in the same order:
["hook or NO STRONG HOOK", "hook or NO STRONG HOOK", ...]

AGENCIES:
"""


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


def _field(row: dict, *names: str) -> str:
    for n in names:
        v = (row.get(n) or "").strip()
        if v:
            return v
    return ""


def load_no_go() -> set[str]:
    for p in NO_GO_CANDIDATES:
        if not p.exists():
            continue
        emails = set()
        if p.suffix == ".csv":
            with open(p, newline="") as f:
                for r in csv.DictReader(f):
                    e = _field(r, "email", "Email")
                    if e:
                        emails.add(e.lower())
        else:
            emails = {ln.strip().lower() for ln in p.read_text().splitlines() if ln.strip()}
        print(f"expand_pipeline: loaded {len(emails)} NO_GO email(s) from {p.name}")
        return emails
    print("expand_pipeline: no NO_GO file found (checked NO_GO.csv, NO_GO.txt) — fine, optional, skipping")
    return set()


def already_touched_emails() -> set[str]:
    """Every email that's EVER appeared in cold_pipeline.jsonl, any campaign, any status."""
    out = set()
    for r in _load_jsonl(PIPELINE):
        e = (r.get("email") or "").strip().lower()
        if e:
            out.add(e)
    return out


def suppressed_emails() -> set[str]:
    out = set()
    for r in _load_jsonl(SUPPRESS):
        e = (r.get("email") or "").strip().lower()
        if e:
            out.add(e)
    return out


def load_scraped_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        email = _field(r, "email", "Email").lower()
        if not email:
            continue  # nothing to enroll without an email, same gate cold_import.py enforces
        out.append({
            "company": _field(r, "company", "Company", "Name", "agency"),
            "email": email,
            "phone": _field(r, "phone", "Phone"),
            "city": _field(r, "city", "City"),
            "state": _field(r, "state", "State"),
            "website": _field(r, "website", "Website"),
            "niche": _field(r, "niche", "Niche", "segment", "Segment") or "agency",
        })
    return out


def generate_hooks(rows: list[dict], batch_size: int = BATCH_SIZE) -> list[str]:
    """Batched claude -p calls, BATCH_SIZE agencies per call, per the mission brief.
    Returns one hook string per input row (same order), "" for any that fail to parse."""
    hooks = []
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        listing = "\n".join(
            f"{j}. {r['company']} | {r['city']}{', ' + r['state'] if r['state'] else ''} | niche: {r['niche']}"
            for j, r in enumerate(batch))
        out = planner._cli(HOOK_PROMPT + listing, timeout=90, feature="default")
        data = planner._extract_json(out or "") or []
        for j in range(len(batch)):
            h = data[j] if j < len(data) and isinstance(data[j], str) else ""
            h = h.strip()
            hooks.append("" if h.upper() == "NO STRONG HOOK" else humanize(h))
        print(f"  hooks batch {i // batch_size + 1}: {len(batch)} agencies -> "
              f"{sum(1 for h in hooks[-len(batch):] if h)} usable hooks")
    return hooks


def build_staged_rows(rows: list[dict], hooks: list[str]) -> list[dict]:
    out = []
    for r, hook in zip(rows, hooks):
        out.append({
            "email": r["email"], "company": r["company"] or r["email"].split("@")[0],
            "ts": now_iso(), "campaign": "wl", "status": "staged_pending_import",
            "kind": "expand_pipeline", "phone": r["phone"], "city": r["city"],
            "state": r["state"], "website": r["website"], "niche": r["niche"],
            "personalization": hook, "confidence": "low",
        })
    return out


def run(csv_path: str, commit: bool = False, limit: int | None = None) -> dict:
    scraped = load_scraped_csv(csv_path)
    print(f"expand_pipeline: {len(scraped)} row(s) with an email in {csv_path}")
    if limit:
        scraped = scraped[:limit]

    touched = already_touched_emails()
    suppressed = suppressed_emails()
    no_go = load_no_go()
    seen_this_run = set()
    fresh, skipped = [], 0
    for r in scraped:
        e = r["email"]
        if e in touched or e in suppressed or e in no_go or e in seen_this_run:
            skipped += 1
            continue
        seen_this_run.add(e)
        fresh.append(r)
    print(f"expand_pipeline: {len(fresh)} genuinely new (after dedupe vs cold_pipeline history + "
          f"suppress + NO_GO + in-file dupes), {skipped} skipped")

    if not fresh:
        return {"ok": True, "staged": 0, "skipped": skipped}

    hooks = generate_hooks(fresh)
    staged_rows = build_staged_rows(fresh, hooks)
    usable = sum(1 for r in staged_rows if r["personalization"])
    print(f"expand_pipeline: {usable}/{len(staged_rows)} got a real hook "
          f"(the rest came back NO STRONG HOOK -> empty personalization, still stageable, just weaker outreach)")

    if not commit:
        print("expand_pipeline: DRY-RUN (default) — nothing written. Pass --commit to append for real.")
        for r in staged_rows[:5]:
            print(f"  [dry] {r['company']} <{r['email']}> hook: {r['personalization'][:70] or '(none)'}")
        if len(staged_rows) > 5:
            print(f"  [dry] ...and {len(staged_rows) - 5} more")
        return {"ok": True, "staged": 0, "would_stage": len(staged_rows), "skipped": skipped}

    PIPELINE.parent.mkdir(parents=True, exist_ok=True)
    with PIPELINE.open("a") as f:
        for r in staged_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"expand_pipeline: appended {len(staged_rows)} row(s) -> {PIPELINE}")
    planner.feed_add("cold", f"list expansion: {len(staged_rows)} new agenc{'y' if len(staged_rows)==1 else 'ies'} "
                             f"staged ({usable} with a real hook)")
    return {"ok": True, "staged": len(staged_rows), "skipped": skipped}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path", help="CSV of scraped agencies (needs an email column)")
    ap.add_argument("--commit", action="store_true", help="actually write to cold_pipeline.jsonl (default: dry-run)")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    run(args.csv_path, commit=args.commit, limit=args.limit)
