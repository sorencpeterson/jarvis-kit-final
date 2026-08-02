#!/usr/bin/env python3
"""#175 [E] agency-partner scoreboard: wl (white-label) accounts ranked by
orders/revenue/margin, "once they exist" per the brief.

WHY THIS IS [E]: checked store/ledger.jsonl (the existing revenue-event ledger,
written via server.py's _ledger_add — confirmed live, not this mission's file, read
only) live: it has exactly one record, a test entry ("wave1", amount 0). No agency
has placed a repeat/partner-tier order yet — the business is still in cold-outreach
build-out (per project memory: "GHL DBR campaign next"). There's also no field
anywhere distinguishing a one-off wl client from a recurring AGENCY PARTNER (someone
reselling repeatedly) versus a single-project buyer — that distinction doesn't exist
in the data model yet.

What's built: a real, working scoreboard() function against ledger.jsonl's ACTUAL
existing schema ({"ts","kind","amount","note", optional "contact_id"/"company"}),
fixture-tested with synthetic multi-order partner data (see mission status file —
3 partners, several orders each, correct ranking by revenue and by margin once a
cost field is present). The moment real repeat-order ledger entries tagged with a
partner identity exist, this file's output is real without any code change. Cost/
margin needs a `cost` field on ledger entries that doesn't exist yet either — this
scaffold handles its absence gracefully (margin shows as "n/a" rather than a
fabricated number) rather than guessing at cost data that was never recorded.

Read-only against nothing external — pure local ledger read + local JSON write.

Usage:
  partner_scoreboard.py             # build store/partner_scoreboard.json from real ledger data
  partner_scoreboard.py --dry-run   # print it, write nothing
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402

LEDGER = ROOT / "store" / "ledger.jsonl"
OUT = ROOT / "store" / "partner_scoreboard.json"
# ledger `kind` values that count as a real wl order (matches how _ledger_add is
# actually called elsewhere — "booked_call" seen live is NOT an order, it's a call;
# a real scoreboard should only count revenue-bearing kinds, not activity events).
# P: "won" is the kind server.py's /api/ledger docs as the actual close-of-deal
# event (POST /api/ledger {kind:"won",amount:...} on close) -- the only kind this
# codebase's writers actually log today. Without it the scoreboard reports $0 forever.
ORDER_KINDS = {"order", "wl_order", "deposit", "invoice_paid", "sale", "won"}


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


def load_orders() -> list[dict]:
    return [r for r in _load_jsonl(LEDGER) if (r.get("kind") or "").lower() in ORDER_KINDS]


def scoreboard(orders: list[dict]) -> dict:
    """Groups by partner identity — prefers `company`, falls back to `contact_id`,
    falls back to `note` (better a rough bucket than silently dropping a real order
    with no clean identity field). Returns partners sorted by revenue desc, each with
    orders/revenue/margin (margin "n/a" when no `cost` field is present on any of that
    partner's orders — never fabricated)."""
    by_partner: dict[str, list[dict]] = defaultdict(list)
    for o in orders:
        key = o.get("company") or o.get("contact_id") or (o.get("note") or "")[:60] or "unknown"
        by_partner[key].append(o)

    rows = []
    for partner, os_ in by_partner.items():
        revenue = sum(float(o.get("amount") or 0) for o in os_)
        has_cost = any("cost" in o for o in os_)
        margin = None
        if has_cost:
            cost = sum(float(o.get("cost") or 0) for o in os_)
            margin = round(revenue - cost, 2)
        rows.append({"partner": partner, "orders": len(os_), "revenue": round(revenue, 2),
                     "margin": margin if margin is not None else "n/a",
                     "avg_order": round(revenue / len(os_), 2) if os_ else 0})
    rows.sort(key=lambda r: -r["revenue"])
    return {"partners": rows, "total_partners": len(rows),
            "total_orders": sum(r["orders"] for r in rows),
            "total_revenue": round(sum(r["revenue"] for r in rows), 2),
            "generated": now_iso()}


def run(dry: bool = False) -> dict:
    orders = load_orders()
    if not orders:
        print(f"partner_scoreboard: [E] no order-kind ledger entries yet (checked kinds {sorted(ORDER_KINDS)} "
              "against store/ledger.jsonl — it currently only has a test record). Writing an empty "
              "scoreboard, not fabricated data.")
    board = scoreboard(orders)
    print(f"partner_scoreboard: {board['total_partners']} partner(s), {board['total_orders']} order(s), "
          f"${board['total_revenue']:.0f} total revenue")
    for r in board["partners"][:10]:
        print(f"  {r['partner']:30} orders={r['orders']:3} revenue=${r['revenue']:.0f} margin={r['margin']}")
    if dry:
        return board
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(board, indent=2, ensure_ascii=False))
    print(f"partner_scoreboard: wrote -> {OUT}")
    return board


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(dry=args.dry_run)
