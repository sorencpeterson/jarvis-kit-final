#!/usr/bin/env python3
"""E339: cross-store referential integrity check — do the contact_id
references scattered across proposals.jsonl / replies.jsonl actually resolve
to a real GHL contact, or are they pointing at nothing (a deleted contact, a
typo'd id, leftover test/fixture data)?

WHAT: samples every distinct non-empty contact_id from store/proposals.jsonl
      and store/replies.jsonl, then calls proposal_factory.find_contact(cid=)
      for each (the SAME live GHL lookup app/server.py's dossier endpoint
      uses — reusing it rather than re-deriving a lookup, one source of
      truth). A cid that resolves to {} is a candidate orphan.
HONEST CAVEAT (load-bearing, read before trusting a red flag): find_contact
      returns {} on BOTH "this contact truly doesn't exist in GHL anymore"
      AND "the GHL API call itself failed" (network blip, rate limit, auth
      hiccup) — proposal_factory.py's own docstring says so ("Read-only")
      but doesn't distinguish the two outcomes in its return value. This
      checker can't tell them apart from one failed lookup alone, so it
      NEVER reports a single-lookup miss as a confirmed orphan: it retries
      each miss once (RETRY_DELAY_S apart) and only flags a cid as orphaned
      if BOTH attempts come back empty. Even then, the report is phrased as
      "did not resolve" rather than "confirmed deleted" — the honest ceiling
      of what a live API call can tell you.
WHEN: run weekly (matches the mission's "weekly" cadence ask) or ad hoc.
      SAMPLE_LIMIT caps how many distinct cids get checked per run (real GHL
      API calls, not free/instant) so this stays a light weekly job, not a
      slow full-table scan every time.
RAILS: read-only against GHL (via proposal_factory.find_contact, itself
      read-only per its own docstring), store/proposals.jsonl,
      store/replies.jsonl. Only write is store/integrity_report.json (full
      overwrite each run). No GHL writes, no sends.

Run:  .venv/bin/python agents/integrity_checker.py
      .venv/bin/python agents/integrity_checker.py --fixture   (no GHL calls)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402
from runlog import track  # noqa: E402  (E353: runlog adoption)

PROPOSALS = ROOT / "store" / "proposals.jsonl"
REPLIES = ROOT / "store" / "replies.jsonl"
OUT = ROOT / "store" / "integrity_report.json"
SAMPLE_LIMIT = 25  # cap real GHL lookups per run — a weekly job, not a scan
RETRY_DELAY_S = 2  # gap between the two lookup attempts for a candidate miss


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


def _distinct_contact_ids() -> dict[str, list[str]]:
    """contact_id -> list of "source:id" strings referencing it, so a report
    line can say exactly which records point at a possibly-orphaned id."""
    refs: dict[str, list[str]] = {}
    for r in _read_jsonl(PROPOSALS):
        cid = (r.get("contact_id") or "").strip()
        if cid:
            refs.setdefault(cid, []).append(f"proposal:{r.get('id', '?')}")
    for r in _read_jsonl(REPLIES):
        cid = (r.get("contact_id") or "").strip()
        if cid:
            refs.setdefault(cid, []).append(f"reply:{r.get('id', '?')}")
    return refs


def _lookup(cid: str) -> dict:
    import proposal_factory
    return proposal_factory.find_contact(cid=cid)


def check_ids(refs: dict[str, list[str]], *, limit: int = SAMPLE_LIMIT,
              lookup_fn=None, retry_delay_s: float = RETRY_DELAY_S) -> dict:
    """Returns {"checked": n, "resolved": [...], "did_not_resolve": [...],
    "skipped_over_limit": n}. lookup_fn injectable for tests/fixtures (pass
    retry_delay_s=0 in tests so a fixture run doesn't actually sleep)."""
    lookup_fn = lookup_fn or _lookup
    ids = sorted(refs.keys())[:limit]
    skipped = max(0, len(refs) - limit)

    resolved, did_not_resolve = [], []
    for cid in ids:
        first = lookup_fn(cid)
        if first:
            resolved.append({"contact_id": cid, "refs": refs[cid]})
            continue
        if retry_delay_s:
            time.sleep(retry_delay_s)
        second = lookup_fn(cid)
        if second:
            resolved.append({"contact_id": cid, "refs": refs[cid]})
        else:
            did_not_resolve.append({"contact_id": cid, "refs": refs[cid]})

    return {"checked": len(ids), "resolved": resolved,
            "did_not_resolve": did_not_resolve, "skipped_over_limit": skipped}


def _fixture_lookup(cid: str) -> dict:
    """Deterministic fixture: cids starting with 'real_' resolve, everything
    else (including obvious test/fixture-looking ids) doesn't — no network."""
    if cid.startswith("real_"):
        return {"id": cid, "name": "Fixture Person"}
    return {}


def run(*, fixture: bool = False) -> dict:
    if fixture:
        refs = {"real_001": ["proposal:p1"], "orphan_001": ["proposal:p2"],
                "versiontest001": ["reply:r1"]}
        result = check_ids(refs, lookup_fn=_fixture_lookup, retry_delay_s=0)
    else:
        refs = _distinct_contact_ids()
        result = check_ids(refs)
    return {"generated": now_iso(), "fixture": fixture,
            "total_distinct_ids": len(refs), **result}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fixture", action="store_true")
    args = ap.parse_args()

    with track("integrity_checker"):  # E353: runlog adoption
        data = run(fixture=args.fixture)
        OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    tag = " [FIXTURE]" if data["fixture"] else ""
    n_orphan = len(data["did_not_resolve"])
    print(f"integrity_checker{tag}: {data['total_distinct_ids']} distinct contact_id(s) on file, "
          f"{data['checked']} checked this run ({data['skipped_over_limit']} skipped over sample limit), "
          f"{n_orphan} did NOT resolve -> {OUT}")
    if n_orphan:
        print("did-not-resolve (each verified twice, still no guarantee it's a confirmed delete "
              "vs a GHL API hiccup, see this file's docstring):")
        for r in data["did_not_resolve"]:
            print(f"  {r['contact_id']}  <- {', '.join(r['refs'])}")
    if n_orphan and not data["fixture"]:
        try:
            planner.feed_add("warn", f"{n_orphan} contact_id(s) did not resolve in GHL",
                            "run agents/integrity_checker.py for details")
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
