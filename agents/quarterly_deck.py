#!/usr/bin/env python3
"""The quarterly board deck (tech #272) — 8-section markdown "slides" so [OWNER]
presents to himself like an owner should. Pulls real numbers where they exist,
says "no data yet" honestly where they don't. Runs FOR REAL against Q3-so-far:
this is meant to produce an honest tiny-numbers deck today, not wait for scale.

Sections (the item says 8; owner_report.py's MONEY/PIPELINE/WAITING breakdown
plus retro/kill/lane/cost/target framing covers the brief's "numbers, wins,
kills, lanes, costs, lessons from retro_history, next-quarter targets from
operating-model ramp" list exactly):
1. THE NUMBER (closed vs plan, this quarter)
2. WINS (ledger won/payment rows this quarter)
3. KILLS (channels/experiments that produced $0 with real volume -- pulled
   from channel_cac.json if it's been run, else a note that it hasn't)
4. LANES (source_scores.json / niche_db.json rollup if present)
5. COSTS (cac.json total token cost vs revenue if present)
6. LESSONS (store/retro_history.jsonl entries this quarter)
7. NEXT-QUARTER TARGET (business-library/operating-model.md ramp table, parsed
   for the next month/quarter row)
8. THE ONE THING (single named priority, taken from store/goals.json top item
   if present, else left blank for [OWNER] to fill)

Writes store/deck-YYYYQN.md. Read-only everywhere; will happily read the
other agents' JSON outputs (niche_db.json, source_scores.json, cac.json) IF
they've been run first, but doesn't require it -- missing inputs render as
"not yet run" lines, not errors.

Run standalone: .venv/bin/python agents/quarterly_deck.py
                 .venv/bin/python agents/quarterly_deck.py --fixture
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from store_lib import now_iso  # noqa: E402

LEDGER = ROOT / "store" / "ledger.jsonl"
RETRO_HISTORY = ROOT / "store" / "retro_history.jsonl"
CONFIG = ROOT / "store" / "config.json"
GOALS = ROOT / "store" / "goals.json"
CAC = ROOT / "store" / "cac.json"
SOURCE_SCORES = ROOT / "store" / "source_scores.json"
NICHE_DB = ROOT / "store" / "niche_db.json"
OPERATING_MODEL = Path.home() / "Claude" / "business-library" / "operating-model.md"


def _quarter(d: date) -> tuple[int, int]:
    return d.year, (d.month - 1) // 3 + 1


def _quarter_bounds(year: int, q: int) -> tuple[str, str]:
    start_month = (q - 1) * 3 + 1
    end_month = start_month + 2
    start = f"{year}-{start_month:02d}-01"
    # crude end-of-quarter: last day varies, but a ">=start, <next-quarter-start" range is enough
    next_q_month = end_month + 1
    next_year = year if next_q_month <= 12 else year + 1
    next_q_month = next_q_month if next_q_month <= 12 else 1
    end_exclusive = f"{next_year}-{next_q_month:02d}-01"
    return start, end_exclusive


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


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _in_range(ts: str, start: str, end_exclusive: str) -> bool:
    d = (ts or "")[:10]
    return bool(d) and start <= d < end_exclusive


def _section_numbers(ledger_rows: list[dict], start: str, end_exclusive: str) -> list[str]:
    q_rows = [r for r in ledger_rows if _in_range(r.get("ts", ""), start, end_exclusive)]
    closed_kinds = ("won", "payment", "closed")
    closed_total = sum(float(r.get("amount") or 0) for r in q_rows if r.get("kind") in closed_kinds)
    close_count = sum(1 for r in q_rows if r.get("kind") in closed_kinds)

    cfg = _read_json(CONFIG) or {}
    plan = cfg.get("plan") or {}
    quarter_target = 0
    for month_key, amt in plan.items():
        if start[:7] <= month_key < end_exclusive[:7]:
            quarter_target += int(amt or 0)

    lines = [f"closed this quarter: ${closed_total:,.0f} across {close_count} event(s)"]
    if quarter_target:
        gap = quarter_target - closed_total
        lines.append(f"target this quarter: ${quarter_target:,.0f} ({'ahead' if gap <= 0 else f'${gap:,.0f} short'})")
    else:
        lines.append("target this quarter: no plan number set in store/config.json for these months")
    if close_count == 0:
        lines.append("(n=0 closed events this quarter -- this is the honest number, not a placeholder)")
    return lines


def _section_wins(ledger_rows: list[dict], start: str, end_exclusive: str) -> list[str]:
    q_rows = [r for r in ledger_rows if _in_range(r.get("ts", ""), start, end_exclusive)
             and r.get("kind") in ("won", "payment", "closed") and r.get("kind") != "test"]
    if not q_rows:
        return ["no closed wins recorded this quarter yet (n=0) -- honest, not padded."]
    return [f"- ${float(r.get('amount') or 0):,.0f}: {r.get('note') or '(no note)'} ({(r.get('ts') or '')[:10]})"
           for r in q_rows]


def _section_kills(cac: dict | None) -> list[str]:
    if not cac:
        return ["channel_cac.py hasn't been run yet this quarter -- run it before the next deck "
               "to get real kill-criteria evidence per operating-model.md's rule "
               "('a channel that produces $0 for 60 days with real volume gets paused')."]
    lines = []
    for lane, data in (cac.get("lanes") or {}).items():
        cost = data.get("token_cost_usd") or 0
        rev = data.get("closed_revenue") or 0
        # field is "activity_proxy" in cac.json (channel_cac.py's actual output
        # key) -- a stale "activity_proxy_count" read here silently zeroed
        # every touches value via the `or 0` fallback and suppressed every
        # kill-flag on the first real run. Fixed.
        touches = data.get("activity_proxy") or 0
        if cost > 0 and rev == 0 and touches > 0:
            lines.append(f"- {lane}: ${cost:.2f} spent, $0 revenue, {touches:g} touches -- "
                         "watch against the 60-day kill rule, not there yet if this is early days")
    return lines or ["no lane currently shows spend with zero revenue -- nothing flagged for kill review."]


def _section_lanes(source_scores: dict | None, niche_db: dict | None) -> list[str]:
    lines = []
    if source_scores:
        top = sorted((source_scores.get("sources") or {}).items(), key=lambda kv: -kv[1]["revenue"])[:3]
        if top:
            lines.append("top sources by revenue: " + ", ".join(f"{k} (${v['revenue']:,.0f})" for k, v in top))
    else:
        lines.append("source_scorecard.py hasn't been run yet -- no lane revenue rollup available.")
    if niche_db:
        top_n = sorted((niche_db.get("niches") or {}).items(), key=lambda kv: -kv[1]["proposals"])[:3]
        if top_n:
            lines.append("top niches by proposal volume: " + ", ".join(f"{k} ({v['proposals']}p)" for k, v in top_n))
    else:
        lines.append("niche_db.py hasn't been run yet -- no niche rollup available.")
    return lines


def _section_costs(cac: dict | None) -> list[str]:
    if not cac:
        return ["channel_cac.py hasn't been run yet -- no cost data available."]
    total_cost = cac.get("total_token_cost_usd") or 0
    total_rev = cac.get("total_closed_revenue") or 0
    return [f"total token cost this run: ${total_cost:,.2f}",
           f"total matched closed revenue: ${total_rev:,.2f}",
           f"net: ${total_rev - total_cost:,.2f} ({'positive' if total_rev >= total_cost else 'negative, expected pre-scale'})"]


def _section_lessons(retro_rows: list[dict], start: str, end_exclusive: str) -> list[str]:
    q_rows = [r for r in retro_rows if _in_range(r.get("ts", ""), start, end_exclusive)]
    if not q_rows:
        return ["no retro_history entries this quarter yet."]
    lines = []
    for r in q_rows:
        applied = r.get("applied") or {}
        if isinstance(applied, dict) and applied:
            for k, v in applied.items():
                lines.append(f"- applied: {k} = {v} ({(r.get('ts') or '')[:10]})")
        else:
            lines.append(f"- {json.dumps(applied)} ({(r.get('ts') or '')[:10]})")
    return lines


def _next_quarter_target(operating_model_text: str, today: date) -> list[str]:
    """Parses the ramp table in operating-model.md and surfaces the row for
    the CURRENT period (what [OWNER] is chasing right now) plus the very next
    row in table order (what's coming up after). The table format is
    '| Month | Closed target | Focus |' markdown rows -- best-effort regex
    match, falls back to a plain note if the file is missing or the format
    changes.

    Deliberately prefers the CURRENT month/quarter row over a blind "next
    calendar month" computation: on 2026-07-03 (mid-Jul, mid-Q3) the useful
    number is "Jul 2026: $10k" (the target actively being chased), not "Aug
    2026: $15k" (a month that hasn't started) -- a pure next-month calc gave
    the wrong answer here on the first real run, this fixes that."""
    if not operating_model_text:
        return ["business-library/operating-model.md not found -- no ramp target available."]
    rows = re.findall(r"\|\s*([A-Za-z0-9 /]+?)\s*\|\s*\*\*(\$[0-9,.km/]+)\*\*\s*\|\s*(.+?)\s*\|",
                      operating_model_text)
    if not rows:
        return ["couldn't parse a ramp table row from operating-model.md -- check the format hasn't changed."]

    this_month_name = today.strftime("%b %Y")  # e.g. "Jul 2026"
    quarter_label = f"Q{(today.month - 1) // 3 + 1} {today.year}"  # e.g. "Q3 2026"

    def _matches_current(label: str) -> bool:
        low = label.lower()
        return this_month_name.lower() in low or quarter_label.lower() in low

    current_idx = next((i for i, (label, _, _) in enumerate(rows) if _matches_current(label)), None)
    if current_idx is None:
        # today's month/quarter isn't named explicitly (e.g. a half-year row covers it) --
        # fall back to the first row in table order rather than guessing wrong.
        label, target, focus = rows[0]
        return [f"{label}: target {target}, focus: {focus} "
               f"(today's exact period not named in the table; showing the first ramp row)"]

    lines = [f"CURRENT ({rows[current_idx][0]}): target {rows[current_idx][1]}, focus: {rows[current_idx][2]}"]
    if current_idx + 1 < len(rows):
        nxt = rows[current_idx + 1]
        lines.append(f"NEXT ({nxt[0]}): target {nxt[1]}, focus: {nxt[2]}")
    return lines


def _section_one_thing(goals: dict | None) -> list[str]:
    if not goals:
        return ["store/goals.json not found or empty -- name the ONE thing by hand for this deck."]
    # goals.json shape is loosely defined elsewhere in this repo; be defensive
    if isinstance(goals, dict):
        for key in ("top", "priority", "one_thing"):
            if goals.get(key):
                return [str(goals[key])]
        # otherwise just surface however many top-level goal entries exist, first one
        for v in goals.values():
            if isinstance(v, str) and v.strip():
                return [v.strip()]
    return ["no single named priority found in store/goals.json -- name the ONE thing by hand."]


def build_deck(ledger_rows: list[dict], retro_rows: list[dict], cac: dict | None,
               source_scores: dict | None, niche_db: dict | None, goals: dict | None,
               operating_model_text: str, today: date) -> tuple[str, str]:
    year, q = _quarter(today)
    label = f"{year}Q{q}"
    start, end_exclusive = _quarter_bounds(year, q)

    lines = [f"# Quarterly Board Deck — {label} (generated {now_iso()})", "",
             f"_Covers {start} through {end_exclusive} (exclusive), so-far as of today._", ""]

    sections = [
        ("1. THE NUMBER", _section_numbers(ledger_rows, start, end_exclusive)),
        ("2. WINS", _section_wins(ledger_rows, start, end_exclusive)),
        ("3. KILLS", _section_kills(cac)),
        ("4. LANES", _section_lanes(source_scores, niche_db)),
        ("5. COSTS", _section_costs(cac)),
        ("6. LESSONS (from retro_history)", _section_lessons(retro_rows, start, end_exclusive)),
        ("7. NEXT-QUARTER TARGET (operating-model.md ramp)", _next_quarter_target(operating_model_text, today)),
        ("8. THE ONE THING", _section_one_thing(goals)),
    ]
    for title, body_lines in sections:
        lines.append(f"## {title}")
        lines.extend(body_lines if body_lines else ["(nothing to report)"])
        lines.append("")

    return label, "\n".join(lines)


def _fixture_inputs():
    ledger_rows = [
        {"ts": "2026-07-02T10:00:00", "kind": "won", "amount": 2500, "note": "Acme HVAC signed"},
        {"ts": "2026-07-10T10:00:00", "kind": "won", "amount": 900, "note": "landing page rush"},
    ]
    retro_rows = [{"ts": "2026-07-05T10:00:00", "applied": {"job_blacklist_source": "lever"}}]
    cac = {"lanes": {"cold": {"token_cost_usd": 12.50, "closed_revenue": 2500, "activity_proxy": 40},
                     "warm": {"token_cost_usd": 3.10, "closed_revenue": 0, "activity_proxy": 15}},
           "total_token_cost_usd": 15.60, "total_closed_revenue": 2500}
    source_scores = {"sources": {"cold:webfix": {"revenue": 2500}, "warm:tier1": {"revenue": 0}}}
    niche_db = {"niches": {"hvac": {"proposals": 8}, "salon": {"proposals": 3}}}
    goals = {"top": "close the first 3 white-label agency accounts"}
    return ledger_rows, retro_rows, cac, source_scores, niche_db, goals


def run(fixture: bool = False) -> dict:
    today = date.today()
    if fixture:
        ledger_rows, retro_rows, cac, source_scores, niche_db, goals = _fixture_inputs()
        operating_model_text = OPERATING_MODEL.read_text() if OPERATING_MODEL.exists() else ""
        source = "FIXTURE"
        out_path = ROOT / "store" / "deck-FIXTURE.md"
    else:
        ledger_rows = _read_jsonl(LEDGER)
        retro_rows = _read_jsonl(RETRO_HISTORY)
        cac = _read_json(CAC)
        source_scores = _read_json(SOURCE_SCORES)
        niche_db = _read_json(NICHE_DB)
        goals = _read_json(GOALS)
        operating_model_text = OPERATING_MODEL.read_text() if OPERATING_MODEL.exists() else ""
        source = "REAL"
        year, q = _quarter(today)
        out_path = ROOT / "store" / f"deck-{year}Q{q}.md"

    label, deck = build_deck(ledger_rows, retro_rows, cac, source_scores, niche_db, goals,
                             operating_model_text, today)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(deck)
    print(f"quarterly_deck [{source}]: {label} deck written, {len(deck.splitlines())} lines -> {out_path}")
    return {"source": source, "label": label, "path": str(out_path), "deck": deck}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", action="store_true")
    args = ap.parse_args()
    run(fixture=args.fixture)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
