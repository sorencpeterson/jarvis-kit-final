#!/usr/bin/env python3
"""B3: portfolio teardown. The proposal factory is reactive (a contact appears, it
builds). This is the proactive feeder: walk the candidate lists we ALREADY have
websites for, audit each site for concrete weaknesses, and rank the worst as fresh
proposal candidates, so "who should get the next teardown proposal" is a ranked
list instead of a guess.

WHAT: pulls candidates with website fields from the enrichment CSVs this repo
      already stages cold contacts from (cold_import.py's own sources):
        ~/Claude/playwright-project/automations/agency-enrichment/out/wl-hooks.csv
          (website column; rows with status=send first, they have verified emails)
        ~/Claude/wl-webdev-import-master.csv (Website column)
      Skips: suppressed contacts (store/suppress.jsonl), domains that already have
      a proposal (store/proposals.jsonl site_url), and domains already audited
      (store/teardown_candidates.jsonl is its own dedup state, so runs resume where
      the last one stopped). Fetches up to FETCH_CAP sites per run, read-only GETs
      through net_guard (proposal_factory.fetch_site when importable, which is
      already net_guard-gated and parses title/viewport/weight; a basic gated fetch
      otherwise). Audits each for: unreachable/dead (weight 4, mirrors the
      pricing-tree rule that 4+ structural faults means full rebuild), no https,
      missing title, missing mobile viewport tag, heavy page (> SLOW_BYTES, the
      "over ~3s on a phone" heuristic), thin content, DIY template builder
      (Wix/Weebly/GoDaddy class), missing h1. Every audited site is appended to
      store/teardown_candidates.jsonl as {name, site, faults: [..], score, ts}
      (clean sites land with score 0 so they are never refetched); the run's
      ranked worst goes out as one feed line.
WHEN: any cadence (morning chain or ad hoc); FETCH_CAP per run keeps it polite.
      Fresh install with none of the CSVs present prints and exits 0.
RAILS: read-only GETs against public sites only (net_guard-gated, redirect-safe).
      NO contact is ever made: no emails, no GHL writes, no enrollment, no sends.
      Only writes are the candidates store and one feed line. If proposal_factory
      is importable it is used ONLY for its fetch_site parser, never build().

HONEST LIMITS: this is a curb-side audit of ONE page (the homepage as served).
It cannot see JS-rendered content (a React site can look "thin"), Core Web
Vitals, SEO rankings, or booking flows. Faults are pitch angles, not a lab report.

Tunables (change here, nowhere else):
  FETCH_CAP    = 8          max site fetches per run
  SLOW_BYTES   = 1_500_000  page weight above this is called heavy (~3s+ on 4G)
  THIN_CHARS   = 400        visible text below this is called thin
  DEAD_WEIGHT  = 4          an unreachable site counts like 4 faults (rebuild lane)
  FEED_TOP     = 3          how many names the feed line carries

Run:  .venv/bin/python agents/portfolio_teardown.py [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402

HOOKS_CSV = Path.home() / "Claude/playwright-project/automations/agency-enrichment/out/wl-hooks.csv"
MASTER_CSV = Path.home() / "Claude/wl-webdev-import-master.csv"
PROPOSALS = ROOT / "store" / "proposals.jsonl"
SUPPRESS = ROOT / "store" / "suppress.jsonl"
OUT = ROOT / "store" / "teardown_candidates.jsonl"

FETCH_CAP = 8
SLOW_BYTES = 1_500_000
THIN_CHARS = 400
DEAD_WEIGHT = 4
FEED_TOP = 3

BUILDERS = ("wix.com", "weebly.com", "godaddysites", "website-builder", "site123",
            "jimdo", "webs.com", "wixsite")


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


def _domain(url: str) -> str:
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.split("/")[0].split("?")[0].strip()


def candidates() -> list[dict]:
    """{name, site} rows from the CSVs, deduped by domain, verified-email rows
    (wl-hooks status=send) first so the best-reachable prospects get audited first."""
    rows: list[dict] = []
    if HOOKS_CSV.exists():
        try:
            for r in csv.DictReader(open(HOOKS_CSV, newline="")):
                site = (r.get("website") or "").strip()
                if not site:
                    continue
                rows.append({"name": (r.get("company") or "").strip() or _domain(site),
                             "site": site, "email": (r.get("email") or "").strip().lower(),
                             "_pri": 0 if (r.get("status") or "").strip() == "send" else 1})
        except (OSError, csv.Error) as e:
            print(f"  wl-hooks.csv unreadable, skipped: {e}")
    if MASTER_CSV.exists():
        try:
            for r in csv.DictReader(open(MASTER_CSV, newline="")):
                site = (r.get("Website") or "").strip()
                if not site:
                    continue
                rows.append({"name": (r.get("Company Name") or "").strip() or _domain(site),
                             "site": site, "email": (r.get("Email") or "").strip().lower(),
                             "_pri": 2})
        except (OSError, csv.Error) as e:
            print(f"  master csv unreadable, skipped: {e}")
    rows.sort(key=lambda r: r["_pri"])
    seen, out = set(), []
    for r in rows:
        d = _domain(r["site"])
        if not d or d in seen:
            continue
        seen.add(d)
        out.append(r)
    return out


def _suppressed_emails() -> set[str]:
    return {(r.get("email") or "").strip().lower()
            for r in _read_jsonl(SUPPRESS) if r.get("email")}


def _known_domains() -> set[str]:
    """Domains that already have a proposal or a teardown row: never refetched."""
    known = set()
    for r in _read_jsonl(PROPOSALS):
        d = _domain(r.get("site_url") or "")
        if d:
            known.add(d)
    for r in _read_jsonl(OUT):
        d = _domain(r.get("site") or "")
        if d:
            known.add(d)
    return known


def _fetch_site(url: str) -> dict:
    """proposal_factory.fetch_site when importable (net_guard-gated, parses title/
    viewport/imgs/bytes/raw_html), else a minimal net_guard-gated fallback with the
    same output keys. Tests monkeypatch this."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        import proposal_factory
        return proposal_factory.fetch_site(url)
    except ImportError:
        pass
    try:
        import net_guard
        resp = net_guard.safe_urlopen(url, timeout=12,
                                      headers={"User-Agent": "Mozilla/5.0 ([OWNER]Digital audit)"})
        raw = resp.read(400_000).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return {"url": url, "error": str(e)[:100]}
    title = (re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I) or [None, ""])[1]
    body = re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw,
                                          flags=re.S | re.I))
    return {"url": url, "title": (title or "").strip()[:120],
            "text": re.sub(r"\s+", " ", body).strip()[:2600],
            "viewport": 'name="viewport"' in raw, "imgs": len(re.findall(r"<img", raw, re.I)),
            "bytes": len(raw), "raw_html": raw[:120_000]}


def fetch_failed(site: dict) -> bool:
    """A transient/blocked fetch (DNS/TLS blip, net_guard refusal, empty {} from the
    factory) — NOT a real reading of the page. These must never be persisted: a blip
    would brand a prospect 'unreachable' score DEAD_WEIGHT forever, add its domain to
    the known set, and feed a false 'your site returns nothing' pitch (letter #1: never
    invent evidence). A real fetch that returned an empty page is different: it has no
    'error' and carries response keys, and stays as legitimate thin-site evidence."""
    if site.get("error"):
        return True
    # factory returns {} for a guard-blocked URL, or a dict with only 'url' on trouble:
    # no response fields at all means no real fetch happened.
    return not any(k in site for k in ("title", "text", "raw_html", "viewport", "bytes"))


def audit(site: dict) -> tuple[list[str], int]:
    """(faults, score). Score is a weighted fault count: unreachable weighs
    DEAD_WEIGHT (rebuild lane per pricing-tree), everything else weighs 1."""
    faults, score = [], 0
    if site.get("error") or not (site.get("text") or site.get("raw_html")):
        faults.append(f"unreachable or empty ({(site.get('error') or 'no content')[:60]})")
        return faults, DEAD_WEIGHT
    if (site.get("url") or "").startswith("http://"):
        faults.append("no https")
        score += 1
    if not (site.get("title") or "").strip():
        faults.append("no page title")
        score += 1
    if not site.get("viewport"):
        faults.append("no mobile viewport tag")
        score += 1
    b = int(site.get("bytes") or 0)
    if b > SLOW_BYTES:
        faults.append(f"heavy page ({b // 1000}KB, slow on a phone)")
        score += 1
    if len(site.get("text") or "") < THIN_CHARS:
        faults.append("thin content (almost no visible text)")
        score += 1
    raw = (site.get("raw_html") or "").lower()
    for b_name in BUILDERS:
        if b_name in raw:
            faults.append(f"DIY template builder ({b_name.split('.')[0]})")
            score += 1
            break
    if raw and "<h1" not in raw:
        faults.append("no h1 heading")
        score += 1
    return faults, score


def run(*, dry_run: bool = False) -> int:
    cands = candidates()
    if not cands:
        print("portfolio teardown: no candidate CSVs with website fields found, nothing to do")
        return 0
    suppressed = _suppressed_emails()
    known = _known_domains()
    queue = [c for c in cands
             if _domain(c["site"]) not in known
             and (not c.get("email") or c["email"] not in suppressed)]
    if not queue:
        print(f"portfolio teardown: all {len(cands)} candidate domain(s) already "
              "audited or covered by a proposal")
        return 0

    batch = queue[:FETCH_CAP]
    print(f"portfolio teardown: {len(queue)} unaudited candidate(s), fetching {len(batch)}")
    results, unreachable = [], 0
    for c in batch:
        site = _fetch_site(c["site"])
        if fetch_failed(site):
            # transient failure or guard block: do NOT persist. Leaving it out of the
            # candidates store AND the known-domains set means it refetches next run
            # instead of becoming permanent false "unreachable" pitch evidence.
            unreachable += 1
            print(f"  [skip] {c['name']} ({_domain(c['site'])}): fetch failed "
                  f"({(site.get('error') or 'blocked/empty response')[:60]}), will refetch")
            continue
        faults, score = audit(site)
        results.append({"name": c["name"], "site": site.get("url") or c["site"],
                        "faults": faults, "score": score, "ts": now_iso()})
    results.sort(key=lambda r: -r["score"])
    if unreachable:
        print(f"portfolio teardown: {unreachable} candidate(s) unreachable this run, "
              "not persisted (refetch next run)")

    if dry_run:
        print(f"[dry-run] {len(results)} audited, nothing written:")
        for r in results:
            print(f"  [{r['score']}] {r['name']} ({_domain(r['site'])}): "
                  + ("; ".join(r["faults"]) or "clean"))
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    faulty = [r for r in results if r["score"] > 0]
    for r in results:
        print(f"  [{r['score']}] {r['name']} ({_domain(r['site'])}): "
              + ("; ".join(r["faults"]) or "clean"))
    if faulty:
        tops = ", ".join(f"{r['name']} ({r['score']})" for r in faulty[:FEED_TOP])
        try:
            planner.feed_add("agent", f"Teardown: {len(faulty)} weak site(s) ranked, "
                                      f"worst: {tops}")
        except Exception:  # noqa: BLE001
            pass
    print(f"portfolio teardown: {len(results)} audited ({len(faulty)} with faults) -> {OUT}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="audit candidate prospect sites for weakness")
    ap.add_argument("--dry-run", action="store_true", help="fetch + audit, write nothing")
    args = ap.parse_args()
    from runlog import track
    with track("portfolio_teardown"):
        return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
