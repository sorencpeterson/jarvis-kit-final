#!/usr/bin/env python3
"""A10 (FABLE-BUILD-QUEUE Section 5, MED): the offer comparison calculator.
CacheFly and 8x8 are live interview paths; an offer call can land with days
of notice and the worst time to build a comparison tool is while one is
exploding. This is the ready-before-needed version.

WHAT: reads store/offers.jsonl (shape: {id, company, base, bonus, equity_note,
      remote, pto_days, start, notes, ts}, per-line reads, last-write-wins by
      id) and prints:
        - a side-by-side table of every offer on file
        - an effective-comp line per offer (base + bonus; equity is noted,
          never priced, options in a private company are not cash)
        - each offer against the salary anchor (application_profile
          salary_expectation, fallback [SALARY_ANCHOR])
        - ONE blunt recommendation line
      --add appends an offer from args (--company and --base required; money
      accepts "$140,000", "140k", "140000"). Empty store prints how to add.
WHEN: on demand, the moment an offer (or a verbal number) exists. Also safe
      any time: read-only unless --add.
RAILS: writes ONLY store/offers.jsonl and only via --add (append under
      _flock). No LLM, no pushes, no sends, nothing outward. Recording an
      offer is not accepting one; the standing rule stays in the output:
      never accept same-day. Fresh install exits 0 with usage.

Run:  .venv/bin/python agents/offer_compare.py
      .venv/bin/python agents/offer_compare.py --add --company CacheFly --base 140k \
          --bonus 10k --equity "options, 4y vest" --remote yes --pto 20 \
          --start 2026-08-01 --notes "verbal, letter pending" [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import _flock, new_id, now_iso  # noqa: E402
import jobs  # noqa: E402

# ---- tunables ----
STORE = ROOT / "store" / "offers.jsonl"
ANCHOR_FALLBACK = 135000  # application_profile salary_expectation wins

HOW_TO_ADD = """offer_compare: no offers on file yet. Add the first one the moment a number exists:

  .venv/bin/python agents/offer_compare.py --add --company CacheFly --base 140k \\
      --bonus 10k --equity "options, 4y vest" --remote yes --pto 20 \\
      --start 2026-08-01 --notes "verbal on the call, letter pending"

Rules that do not change: never accept same-day, anchor high, get it in writing."""


def _anchor() -> int:
    try:
        exp = jobs.load_profile().get("salary_expectation") or ""
        digits = re.sub(r"[^0-9]", "", exp.split("/")[0])
        if digits and int(digits) >= 40000:
            return int(digits)
    except Exception:  # noqa: BLE001 - profile surprises never block a comparison
        pass
    return ANCHOR_FALLBACK


def _money(v) -> int:
    """Forgiving money parse: '$140,000' / '140k' / '140000' / 140000 -> 140000. 0 on blank."""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().lower().replace("$", "").replace(",", "")
    if not s:
        return 0
    try:
        if s.endswith("k"):
            return int(float(s[:-1]) * 1000)
        return int(float(s))
    except ValueError:
        return 0


def load_offers() -> list[dict]:
    if not STORE.exists():
        return []
    by_id, order = {}, []
    for line in STORE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("id"):
            if r["id"] not in by_id:
                order.append(r["id"])
            by_id[r["id"]] = r
    return [by_id[i] for i in order]


def effective(o: dict) -> int:
    return _money(o.get("base")) + _money(o.get("bonus"))


def add_offer(company: str, base, bonus=None, equity: str = "", remote: str = "",
              pto=None, start: str = "", notes: str = "", dry_run: bool = False) -> dict:
    rec = {"id": new_id(f"offer_{company}_{base}_{now_iso()}"),
           "company": company, "base": _money(base), "bonus": _money(bonus),
           "equity_note": equity or "", "remote": remote or "",
           "pto_days": int(pto) if pto not in (None, "") else None,
           "start": start or "", "notes": notes or "", "ts": now_iso()}
    if dry_run:
        print(f"[dry-run] would append: {json.dumps(rec, ensure_ascii=False)}")
        return rec
    STORE.parent.mkdir(parents=True, exist_ok=True)
    with _flock(STORE), STORE.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"offer_compare: recorded {company} at ${_money(base):,} base -> {STORE}")
    return rec


def _fmt(v) -> str:
    if v in (None, ""):
        return "?"
    return str(v)


def table(offers: list[dict], anchor: int) -> str:
    cols = [str(o.get("company") or "?")[:18] for o in offers]
    w = max(12, *(len(c) for c in cols)) + 2
    rows: list[tuple[str, list[str]]] = [
        ("company", cols),
        ("base", [f"${_money(o.get('base')):,}" for o in offers]),
        ("bonus", [f"${_money(o.get('bonus')):,}" for o in offers]),
        ("effective", [f"${effective(o):,}" for o in offers]),
        (f"vs ${anchor // 1000}k anchor",
         [f"{'+' if effective(o) >= anchor else '-'}${abs(effective(o) - anchor):,}"
          for o in offers]),
        ("equity", [_fmt(o.get("equity_note"))[:w - 2] for o in offers]),
        ("remote", [_fmt(o.get("remote")) for o in offers]),
        ("pto_days", [_fmt(o.get("pto_days")) for o in offers]),
        ("start", [_fmt(o.get("start")) for o in offers]),
        ("notes", [_fmt(o.get("notes"))[:w - 2] for o in offers]),
    ]
    label_w = max(len(r[0]) for r in rows) + 2
    lines = []
    for label, vals in rows:
        lines.append(f"{label:<{label_w}}" + "".join(f"{v:<{w}}" for v in vals))
    return "\n".join(lines)


def recommendation(offers: list[dict], anchor: int) -> str:
    """One blunt line. Effective comp decides; the anchor is the floor test."""
    ranked = sorted(offers, key=effective, reverse=True)
    best = ranked[0]
    b_eff = effective(best)
    name = best.get("company") or "?"
    if len(ranked) == 1:
        if b_eff >= anchor:
            return (f"One offer on the table: {name} at ${b_eff:,} effective clears your "
                    f"${anchor:,} anchor by ${b_eff - anchor:,}. Never accept same-day; "
                    "sleep on it and get it in writing.")
        return (f"One offer on the table: {name} at ${b_eff:,} effective is "
                f"${anchor - b_eff:,} UNDER your ${anchor:,} anchor. Counter before "
                "anything else, and never accept same-day.")
    second = ranked[1]
    gap = b_eff - effective(second)
    if b_eff < anchor:
        return (f"Best effective is {name} at ${b_eff:,}, still ${anchor - b_eff:,} under "
                f"your ${anchor:,} anchor. Counter both before comparing further.")
    return (f"On money it is {name}: ${b_eff:,} effective, ${gap:,} over "
            f"{second.get('company') or 'the next best'} and ${b_eff - anchor:,} over your "
            f"anchor. Money is not the whole call, but the number is not close.")


def run() -> int:
    offers = load_offers()
    if not offers:
        print(HOW_TO_ADD)
        return 0
    anchor = _anchor()
    print(table(offers, anchor))
    print()
    print(recommendation(offers, anchor))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Side-by-side job offer comparison")
    ap.add_argument("--add", action="store_true", help="append one offer from the flags below")
    ap.add_argument("--company")
    ap.add_argument("--base", help="base salary: 140000 / 140k / $140,000")
    ap.add_argument("--bonus", default=None)
    ap.add_argument("--equity", default="", help="equity note (never priced into effective comp)")
    ap.add_argument("--remote", default="", help="yes / no / hybrid")
    ap.add_argument("--pto", default=None, help="PTO days")
    ap.add_argument("--start", default="", help="start date")
    ap.add_argument("--notes", default="")
    ap.add_argument("--dry-run", action="store_true", help="with --add: print the record, write nothing")
    args = ap.parse_args()
    if args.add:
        if not args.company or not args.base:
            print("offer_compare: --add needs at least --company and --base")
            return 2
        if args.dry_run:
            add_offer(args.company, args.base, args.bonus, args.equity, args.remote,
                      args.pto, args.start, args.notes, dry_run=True)
            return 0
        from runlog import track
        with track("offer_compare"):
            add_offer(args.company, args.base, args.bonus, args.equity, args.remote,
                      args.pto, args.start, args.notes)
            run()
        return 0
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
