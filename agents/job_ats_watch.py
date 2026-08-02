#!/usr/bin/env python3
"""Curated-company ATS watcher — the highest-signal job source.

The 2026-07 sourcing sweep proved the real remote-US marketing-ops/growth/RevOps roles
live on companies' own ATS feeds (Lever/Greenhouse/Ashby public JSON), not on aggregators
that surface stale/closed listings. This watches a curated list of companies that hire
[OWNER]'s exact archetype, pulls their live feeds, filters to relevant + remote-US +
salary-floor, and feeds the jobs queue through the SAME intake guards (employer dedupe,
YOE gate, fit floor) jobs.py already enforces. Nothing applies; it only stages candidates.

Companies come from store/ats_watch.json (seeded below on first run). Add more as you find
employers that hire the archetype. Runs in the morning chain.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import jobs  # noqa: E402

WATCH = ROOT / "store" / "ats_watch.json"

# Seed: the companies the sourcing sweep verified hire this archetype, + the watch-list.
# ats: lever | greenhouse | ashby. slug: the company's board slug.
SEED = [
    {"co": "OpenHands", "ats": "ashby", "slug": "openhands"},
    {"co": "Mento", "ats": "ashby", "slug": "mento"},
    {"co": "SmartBug Media", "ats": "lever", "slug": "SmartBugOperatingLLC"},
    {"co": "GoHighLevel", "ats": "lever", "slug": "gohighlevel"},
    {"co": "RevPartners", "ats": "greenhouse", "slug": "revpartners"},
    {"co": "QuestDB", "ats": "greenhouse", "slug": "questdb"},
    {"co": "Vendasta", "ats": "greenhouse", "slug": "vendasta"},
    {"co": "ActiveCampaign", "ats": "greenhouse", "slug": "activecampaign"},
    {"co": "Boulevard", "ats": "greenhouse", "slug": "boulevard"},
    {"co": "ServiceTitan", "ats": "greenhouse", "slug": "servicetitan"},
    {"co": "Duda", "ats": "greenhouse", "slug": "duda"},
]

# (old FLOOR=110_000 removed 2026-07-07: it hard-clamped the curated-ATS pool to a [SALARY_ANCHOR]-lane
# floor even when config lowered job_min_yearly. [OWNER]'s goal shifted to max opportunities, so
# this path now respects the single job_min_yearly knob like every other source.)


def _load_watch() -> list[dict]:
    try:
        return json.loads(WATCH.read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        WATCH.parent.mkdir(parents=True, exist_ok=True)
        WATCH.write_text(json.dumps(SEED, indent=2))
        return SEED


def _get(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers=jobs._HDRS)
        return urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None


def _comp_from_text(*parts) -> int | None:
    """Best-effort salary parse from any text the feed gives us.

    CONSERVATIVE by design (D5 P2 sweep): every form requires a literal $ so
    "401k match" / bare ids like "128000" can never parse as a salary. A wrong
    number poisons jobs._fit scoring; when ambiguous, return None."""
    import re
    blob = " ".join(str(p) for p in parts if p)
    nums = []
    for m in re.findall(r"\$\s?(\d{2,3}),?(\d{3})\b", blob):
        nums.append(int(m[0] + m[1]))
    for m in re.findall(r"\$\s?(\d{2,3}(?:\.\d)?)\s?[kK]\b", blob):
        nums.append(int(float(m) * 1000))
    for m in re.findall(r"\$\s?(\d{2,3})(?:\.\d+)?\s?(?:/|per\s?)(?:hr|hour)\b", blob, re.I):
        nums.append(int(m) * 2080)  # hourly -> annual, matches jobs._comp's convention
    return max(nums) if nums else None


def _norm(ats: str, board: str, jid, title, co, url, comp, loc, posted) -> dict:
    """jobs._norm hardcodes easy=True (correct for the operator-clicks-through
    boards it was written for, WRONG here: it auto-approved every ATS listing
    into the apply pipeline). Derive easy the same way jobs._extract does --
    membership in jobs.EASY_ATS -- so greenhouse (not automatable) and any
    future non-easy ATS stage as pending for [OWNER]'s eyes, never auto-approve."""
    rec = jobs._norm(board, jid, title, co, url, comp, loc, posted)
    rec["easy"] = (ats or "").lower() in jobs.EASY_ATS
    return rec


def _iso_from_ms(ts):
    """Lever's createdAt is epoch milliseconds; jobs._age_days expects ISO and
    returns None on anything else, which silently disabled the freshness gate
    for every Lever row. Convert; pass anything non-numeric through untouched."""
    if ts is None:
        return None
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(float(ts) / 1000.0, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError, OverflowError):
        return ts


def fetch_lever(slug: str, co: str) -> list[dict]:
    raw = _get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if not raw:
        return []
    try:
        posts = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return []
    out = []
    for p in posts:
        cat = p.get("categories") or {}
        title, loc = p.get("text", ""), (cat.get("location") or "")
        comp = _comp_from_text(p.get("descriptionPlain", "")[:1200], cat.get("commitment"))
        out.append(_norm("lever", "ats-lever", p.get("id"), title, co, p.get("hostedUrl", ""),
                         comp, loc, _iso_from_ms(p.get("createdAt"))))
    return out


def fetch_greenhouse(slug: str, co: str) -> list[dict]:
    raw = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return []
    out = []
    for j in (data.get("jobs") or []):
        loc = ((j.get("location") or {}).get("name")) or ""
        comp = _comp_from_text((j.get("content") or "")[:1500])
        out.append(_norm("greenhouse", "ats-greenhouse", j.get("id"), j.get("title", ""), co,
                         j.get("absolute_url", ""), comp, loc, j.get("updated_at")))
    return out


def fetch_ashby(slug: str, co: str) -> list[dict]:
    raw = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return []
    out = []
    for j in (data.get("jobs") or []):
        loc = j.get("locationName") or ("Remote" if j.get("isRemote") else "")
        comp = None
        c = j.get("compensation") or {}
        tiers = (c.get("compensationTierSummary") or "") if isinstance(c, dict) else ""
        comp = _comp_from_text(tiers)
        out.append(_norm("ashby", "ats-ashby", j.get("id") or j.get("jobId"), j.get("title", ""),
                         co, j.get("applyUrl") or j.get("jobUrl", ""), comp, loc,
                         j.get("publishedAt")))
    return out


FETCH = {"lever": fetch_lever, "greenhouse": fetch_greenhouse, "ashby": fetch_ashby}


def run() -> dict:
    watch = _load_watch()
    seen = {j["id"] for j in jobs.load_jobs()}
    seen_co = {jobs._ckey(j.get("company"), j.get("title")) for j in jobs.load_jobs()}
    minyr = jobs._min_yearly()
    staged, scanned, reasons = 0, 0, {}
    for w in watch:
        fn = FETCH.get(w.get("ats"))
        if not fn:
            continue
        rows = fn(w["slug"], w["co"])
        scanned += len(rows)
        for j in rows:
            tl = j["title"].lower()
            if any(x in tl for x in ("freelance", "contract", "part-time", "part time",
                                     "intern", "bilingual", "french", "spanish", "german",
                                     "(emea", "apac", "latam")):
                reasons["disqualified-title"] = reasons.get("disqualified-title", 0) + 1
                continue
            if not jobs._relevant(j["title"]):
                reasons["off-title"] = reasons.get("off-title", 0) + 1
                continue
            if not j.get("is_us"):
                reasons["not-us"] = reasons.get("not-us", 0) + 1
                continue
            if j.get("comp_max") and j["comp_max"] < minyr:
                reasons["below-floor"] = reasons.get("below-floor", 0) + 1
                continue
            # Same freshness cutoff jobs.source_and_queue applies: recruiter
            # attention lives in week one; a stale ATS row is a near-dead lead.
            age = jobs._age_days(j.get("posted"))
            if age is not None and age > jobs._MAX_AGE:
                reasons["stale"] = reasons.get("stale", 0) + 1
                continue
            ck = jobs._ckey(j.get("company"), j.get("title"))
            if j["id"] in seen or ck in seen_co:
                continue
            seen.add(j["id"])
            seen_co.add(ck)
            # Auto-approve ONLY easy-apply ATS rows (mirrors jobs.source_and_queue):
            # the bot fills those cleanly; everything else stages pending for [OWNER].
            rec = {**j, "status": "approved" if (jobs.auto_on() and j.get("easy")) else "pending",
                   "query": f"ats:{w['co']}", "created": jobs.now_iso(), "fit": jobs._fit(j),
                   "source_kind": "ats-watch"}
            jobs._save(rec)
            staged += 1
    print(f"ats_watch: scanned {scanned} across {len(watch)} companies, staged {staged} "
          f"relevant remote-US archetype role(s). filtered: {reasons}")
    return {"scanned": scanned, "staged": staged, "reasons": reasons}


if __name__ == "__main__":
    run()
