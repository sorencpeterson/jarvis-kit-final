#!/usr/bin/env python3
"""Warm Sprint block: each morning, pick today's 10 calls from the warm hitlist.
Tier 1 first, undispo'd only, oldest deals first (they decay fastest). Writes
store/warm_block.json; /api/warm floats the block to the top so call mode starts
with them. Read-only against GHL. Runs in the morning chain; idempotent per day.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import planner  # noqa: E402

CSV = Path.home() / "Claude" / "WARM-HITLIST.csv"
DISPO = ROOT / "store" / "warm_dispo.jsonl"
OUT = ROOT / "store" / "warm_block.json"
N = 10


def _rid(phone: str, name: str) -> str:
    return "w_" + hashlib.sha1((phone or name).encode()).hexdigest()[:10]


def _csv_hash() -> str:
    try:
        import hashlib as _h
        return _h.sha1(CSV.read_bytes()).hexdigest()[:12] if CSV.exists() else ""
    except OSError:
        return ""


def run() -> dict:
    today = date.today().isoformat()
    chash = _csv_hash()
    if OUT.exists():
        try:
            cur = json.loads(OUT.read_text())
            # rebuild if the day changed OR the hitlist CSV changed (a mid-day warm_refresh
            # regenerates ids, and stale block ids won't match server _warm_rows / call_prep).
            if cur.get("date") == today and cur.get("ids") and cur.get("csv_hash") == chash:
                print(f"warm block already built for {today} ({len(cur['ids'])} calls)")
                return cur
        except (ValueError, json.JSONDecodeError):
            pass
    done = set()
    if DISPO.exists():
        for line in DISPO.read_text().splitlines():
            try:
                done.add(json.loads(line).get("id"))
            except (ValueError, json.JSONDecodeError):
                continue
    picks = []
    if CSV.exists():
        rows = list(csv.DictReader(open(CSV, newline="")))
        for tier in ("1", "2", "3"):
            if len(picks) >= N:
                break
            tier_rows = [r for r in rows if (r.get("tier") or "").strip() == tier]
            # oldest deals first: they decay fastest
            tier_rows.sort(key=lambda r: -int(r.get("deal_age_days") or 0))
            for r in tier_rows:
                phone = (r.get("phone") or "").strip()
                name = (r.get("name") or "").strip() or (r.get("company") or "").strip()
                rid = _rid(phone, name)
                if rid in done or not (phone or (r.get("email") or "").strip()):
                    continue
                picks.append({"id": rid, "name": name.title(), "niche": (r.get("niche") or "").strip()})
                if len(picks) >= N:
                    break
    out = {"date": today, "csv_hash": chash, "ids": [p["id"] for p in picks], "picks": picks}
    # K: atomic write (tmp + os.replace) instead of a direct truncating write.
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    os.replace(tmp, OUT)
    if picks:
        names = ", ".join(p["name"].split()[0] for p in picks[:4])
        planner.feed_add("warm", f"today's 10-block is loaded: {names}" + ("..." if len(picks) > 4 else ""))
        print(f"warm block built: {len(picks)} calls, starting with {names}")
    else:
        print("warm block: nothing left to pick (hitlist exhausted or missing)")
    return out


if __name__ == "__main__":
    run()
