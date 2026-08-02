#!/usr/bin/env python3
"""B14: booked-call dropoff audit. A booked call is the most expensive thing the
funnel produces, and the factory only fires a proposal when a dispo lands or a
build is asked for, so a booked call that never got a proposal is pure leak.
This joins the two stores and names the leak, contact by contact.

WHAT: builds the BOOKED set from both places a booked call is recorded:
        store/warm_dispo.jsonl rows with dispo=booked (joined back to the hitlist
          row via warm_block.py's id formula), and
        ~/Claude/WARM-HITLIST.csv rows whose stage mentions "booked" (the
          GHL-recorded booked-call cohort; today that is the whole tier 1, and
          warm_dispo is empty, so this lane carries the real data).
      Then marks each booked contact covered or leaked against store/proposals.jsonl:
      covered means a proposal with status in COVERED_STATUSES exists for them,
      matched by email first, name-token subset second (either side's 4+ char
      word set contained in the other's, so a "+ Wellness" inserted mid-name
      still joins while short generic names never wildcard).
      skipped and superseded proposals do NOT count as covered (skipped means the
      factory output was discarded, superseded means replaced; if the replacement
      is live it matches on its own). Writes store/dropoff_audit.json with the FULL
      leak list {name, company, email, phone, stage, age_days} plus the covered
      pairs, and one feed line: "N booked calls never got a proposal: <top 3>".
WHEN: any cadence (morning chain or before a call block). Pure local joins, no
      LLM, sub-second. Fresh install (no hitlist, no dispo rows) prints, exits 0.
RAILS: read-only against every source. Only writes are the output JSON (full
      overwrite) and one feed line. No drafts, no sends, no GHL, no pushes.

HONEST LIMITS: the join is only as good as the identifiers. A proposal built with
a different email and a reworded company name will look like a leak (false
positive possible, silent-close false negative not: covered requires a real match).
Leak order is oldest-booked-first, since those decay fastest.

Tunables (change here, nowhere else):
  COVERED_STATUSES = ("staged", "sending", "sent", "accepted")
  FEED_TOP         = 3     names carried in the feed line

Run:  .venv/bin/python agents/dropoff_audit.py [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402

HITLIST = Path.home() / "Claude" / "WARM-HITLIST.csv"
DISPO = ROOT / "store" / "warm_dispo.jsonl"
PROPOSALS = ROOT / "store" / "proposals.jsonl"
OUT = ROOT / "store" / "dropoff_audit.json"

COVERED_STATUSES = ("staged", "sending", "sent", "accepted")
FEED_TOP = 3


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


def _rid(phone: str, name: str) -> str:
    """warm_block.py's id formula, verbatim (dispo rows carry these ids)."""
    return "w_" + hashlib.sha1((phone or name).encode()).hexdigest()[:10]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def booked_contacts() -> list[dict]:
    """Every contact with a booked call on record, deduped, oldest deal first."""
    rows = []
    if HITLIST.exists():
        try:
            rows = list(csv.DictReader(open(HITLIST, newline="")))
        except (OSError, csv.Error) as e:
            print(f"  hitlist unreadable: {e}")
    booked_ids = {r.get("id") for r in _read_jsonl(DISPO) if r.get("dispo") == "booked"}
    out, seen = [], set()
    for r in rows:
        phone = (r.get("phone") or "").strip()
        name = (r.get("name") or "").strip() or (r.get("company") or "").strip()
        stage = (r.get("stage") or "").strip()
        rid = _rid(phone, name)
        if not ("booked" in stage.lower() or rid in booked_ids):
            continue
        key = (r.get("email") or "").strip().lower() or _norm(name)
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            age = int(r.get("deal_age_days") or 0)
        except (TypeError, ValueError):
            age = 0
        out.append({"name": name, "company": (r.get("company") or "").strip(),
                    "email": (r.get("email") or "").strip().lower(),
                    "phone": phone, "stage": stage, "age_days": age})
    out.sort(key=lambda x: -x["age_days"])  # oldest booked first: they decay fastest
    return out


def _tokens(s: str) -> frozenset:
    """The 4+ character word set of a business name. Subset matching on these
    joins "Transcend Aging Medspa" to "Transcend Aging + Wellness Medspa" while
    "Spa" (no 4+ char words after the generic drop) can never wildcard-match."""
    return frozenset(w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) >= 4)


def _covered_index() -> tuple[set[str], list[tuple[frozenset, frozenset, str]]]:
    """(emails, [(company_tokens, name_tokens, pid)]) for proposals that count."""
    by_id: dict[str, dict] = {}
    for r in _read_jsonl(PROPOSALS):
        if r.get("id"):
            by_id[r["id"]] = r
    emails, names = set(), []
    for r in by_id.values():
        if r.get("status") not in COVERED_STATUSES:
            continue
        em = (r.get("email") or "").strip().lower()
        if em:
            emails.add(em)
        names.append((_tokens(r.get("company") or ""), _tokens(r.get("name") or ""),
                      r.get("id", "")))
    return emails, names


def match_proposal(contact: dict, emails: set[str],
                   names: list[tuple[frozenset, frozenset, str]]) -> str:
    """The covering proposal id, or ''. Email exact first; then a name-token
    subset match either direction (one side's whole word set inside the other's)."""
    em = contact.get("email") or ""
    if em and em in emails:
        return "email"
    cand_sets = [t for t in (_tokens(contact.get("company") or ""),
                             _tokens(contact.get("name") or "")) if t]
    for comp_t, name_t, pid in names:
        for target in (comp_t, name_t):
            if not target:
                continue
            for cand in cand_sets:
                if cand <= target or target <= cand:
                    return pid
    return ""


def build() -> dict:
    booked = booked_contacts()
    if not booked:
        return {}
    emails, names = _covered_index()
    leak, covered = [], []
    for c in booked:
        pid = match_proposal(c, emails, names)
        if pid:
            covered.append({"name": c["name"], "company": c["company"],
                            "matched_by": "email" if pid == "email" else pid})
        else:
            leak.append(c)
    return {"generated": now_iso(), "booked_total": len(booked),
            "with_proposal": len(covered), "without_proposal": len(leak),
            "leak": leak, "covered": covered}


def run(*, dry_run: bool = False) -> int:
    data = build()
    if not data:
        print("dropoff audit: no booked-call records found (hitlist stages + warm "
              "dispo both empty), nothing to join")
        return 0

    n = data["without_proposal"]
    tops = ", ".join((c["company"] or c["name"]) for c in data["leak"][:FEED_TOP])
    line = (f"{n} of {data['booked_total']} booked calls never got a proposal"
            + (f": {tops}" if tops else ""))
    print(f"dropoff audit: {line}")
    for c in data["leak"]:
        print(f"  LEAK {c['company'] or c['name']} ({c['email'] or c['phone'] or 'no contact'}, "
              f"booked {c['age_days']}d ago)")
    if dry_run:
        print(f"[dry-run] nothing written ({data['with_proposal']} covered, {n} leaked)")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(OUT)
    try:
        planner.feed_add("agent", f"Dropoff audit: {line}")
    except Exception:  # noqa: BLE001
        pass
    print(f"dropoff audit: {data['with_proposal']} covered, {n} leaked -> {OUT}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="booked-call to proposal conversion audit")
    ap.add_argument("--dry-run", action="store_true", help="print the leak, write nothing")
    args = ap.parse_args()
    from runlog import track
    with track("dropoff_audit"):
        return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
