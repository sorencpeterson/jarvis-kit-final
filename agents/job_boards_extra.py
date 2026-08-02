#!/usr/bin/env python3
"""D227: board expansion beyond jobs.py's hiring.cafe + Remotive/RemoteOK/Jobicy set.

Two new ToS-safe PUBLIC feeds, both tested live 2026-07-03 while building this:

1. WeWorkRemotely RSS. The per-CATEGORY feed URLs (e.g.
   /categories/remote-marketing-jobs.rss) 403 even when following the
   redirect they issue (tested live: HTTP 301 -> HTTP 403 on the resolved
   URL). The COMBINED feed (/remote-jobs.rss, all categories, ~100 items)
   works cleanly (HTTP 200, confirmed live) and carries a per-item
   <category> tag, so this filters client-side the same way jobs.py's own
   _fetch_remotive() already filters Remotive's category param results --
   consistent pattern, just filtering after fetch instead of before.
   Item shape confirmed live: <title> is "Company: Job Title" (split on
   the FIRST ": "), <link>/<guid> both point at the WWR listing page (a
   board_listing=True case, same as jobs.py's existing remotive/remoteok
   entries -- no <link> per item goes straight to an ATS form), <region>
   carries the geo string (NOT <country>, which is empty on every item
   tested), <pubDate> is RFC-822.

2. Hacker News "who is hiring" monthly thread via the Algolia HN Search API
   (hn.algolia.com/api/v1, public, no key, no ToS issue -- it's HN's own
   official read API). Finding the OFFICIAL thread (not a copycat "Ask HN:
   who's hiring" post) requires filtering to tag `author_whoishiring`
   (confirmed live: this correctly surfaced "Ask HN: Who is hiring? (July
   2026)", story id 48747976, as the top hit; an unfiltered query returns
   decoy/unofficial threads first). Each top-level comment on that story is
   one job post (confirmed live: 200 top-level comments on the July 2026
   thread, 37 of which mention marketing/SEO/growth/wordpress/content/brand/
   demand-gen/digital-marketing keywords -- a real, worthwhile yield). Posts
   have no structured apply_url (this is a comments thread, not a job
   board), so this always sets board_listing=True and apply_url = the HN
   comment's own permalink; the actual apply instructions live in the
   comment text itself for a human/operator to read.

Both functions catch and swallow all fetch errors (matches jobs.py's own
`except Exception: continue` discipline in fetch_boards()) so a feed outage
never breaks a sourcing run; they return [] on any failure.
"""
from __future__ import annotations

import html
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import jobs  # noqa: E402  (reuse _norm/_HDRS so records match the existing board shape exactly)

_MARKETING_KW = ("marketing", "seo", "growth", "wordpress", "web dev", "web developer",
                 "content", "brand", "demand gen", "digital marketing", "email marketing",
                 "paid media", "ppc", "performance marketing", "product marketing",
                 "ecommerce", "e-commerce", "social media", "crm", "revops")


def _wwr_rss_text() -> str | None:
    req = urllib.request.Request("https://weworkremotely.com/remote-jobs.rss", headers=jobs._HDRS)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return None


_WWR_ITEM = re.compile(r"<item>(.*?)</item>", re.S)
_WWR_TAG = lambda tag: re.compile(rf"<{tag}>(.*?)</{tag}>", re.S)  # noqa: E731
_TITLE_RE = _WWR_TAG("title")
_LINK_RE = _WWR_TAG("link")
_REGION_RE = _WWR_TAG("region")
_CATEGORY_RE = _WWR_TAG("category")
_PUBDATE_RE = _WWR_TAG("pubDate")


def fetch_weworkremotely() -> list[dict]:
    """Combined WWR feed, filtered client-side to Sales and Marketing category
    OR a marketing keyword in the title (some genuinely-relevant roles land
    in 'All Other Remote' or 'Product', so title keywords catch what the
    category filter alone would miss)."""
    body = _wwr_rss_text()
    if not body:
        return []
    out = []
    for m in _WWR_ITEM.finditer(body):
        chunk = m.group(1)
        title_m, region_m = _TITLE_RE.search(chunk), _REGION_RE.search(chunk)
        if not title_m:
            continue
        raw_title = html.unescape(title_m.group(1).strip())
        category_m = _CATEGORY_RE.search(chunk)
        category = (category_m.group(1).strip() if category_m else "")
        if category != "Sales and Marketing" and not any(k in raw_title.lower() for k in _MARKETING_KW):
            continue
        company, _, job_title = raw_title.partition(": ")
        if not job_title:  # no ": " separator found -- treat whole string as title, company unknown
            company, job_title = "?", raw_title
        link_m = _LINK_RE.search(chunk)
        url = link_m.group(1).strip() if link_m else None
        if not url:
            continue
        region = html.unescape(region_m.group(1).strip()) if region_m else ""
        pubdate_m = _PUBDATE_RE.search(chunk)
        posted = None
        if pubdate_m:
            try:
                dt = datetime.strptime(pubdate_m.group(1).strip(), "%a, %d %b %Y %H:%M:%S %z")
                posted = dt.astimezone(timezone.utc).isoformat()
            except ValueError:
                posted = None
        jid = re.sub(r"[^a-z0-9]+", "-", url.lower())[-80:]
        out.append(jobs._norm("weworkremotely", jid, job_title, company, url,
                              None, region, posted))
    return out


# ---- HN "who is hiring" monthly thread (Algolia HN Search API, public) ----
_HN_HDRS = {"User-Agent": jobs._HDRS["User-Agent"], "Accept": "application/json"}


def _hn_latest_thread_id() -> int | None:
    req = urllib.request.Request(
        "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring"
        "&query=Who%20is%20hiring&hitsPerPage=5", headers=_HN_HDRS)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError):
        return None
    for hit in data.get("hits", []):
        title = (hit.get("title") or "")
        # "Who wants to be hired?" is the job-SEEKER thread, not the hiring one -- skip it
        if "who is hiring" in title.lower() and "wants to be hired" not in title.lower():
            try:
                return int(hit.get("objectID"))
            except (TypeError, ValueError):
                continue
    return None


def fetch_hn_whoishiring() -> list[dict]:
    """Top-level comments on the latest official 'Who is hiring?' thread,
    filtered to marketing-relevant keyword hits. apply_url is the comment's
    own HN permalink (board_listing=True -- there's no structured ATS here,
    just a comments thread; an operator/human reads the comment text for the
    real apply instructions, exactly like remotive/remoteok listing pages
    already work in the existing fetch_boards() flow)."""
    tid = _hn_latest_thread_id()
    if not tid:
        return []
    req = urllib.request.Request(f"https://hn.algolia.com/api/v1/items/{tid}", headers=_HN_HDRS)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            thread = json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError):
        return []
    out = []
    for c in thread.get("children") or []:
        text = c.get("text") or ""
        plain = re.sub(r"<[^>]+>", " ", html.unescape(text)).lower()
        if not any(k in plain for k in _MARKETING_KW):
            continue
        cid = c.get("id")
        if not cid:
            continue
        # Posts on this thread conventionally read "Company | Role | Location | Type | ...body".
        # Split on <p> (paragraph breaks) and literal "|" to get clean segments, then take the
        # first as company and the first SUBSEQUENT segment that reads like a role (not a bare
        # location/employment-type/URL token) as the title. Best-effort throughout -- a miss on
        # either just falls back to a generic label, never blocks the record from being queued.
        segs = [re.sub(r"<[^>]+>", "", s).strip()
                for s in re.split(r"<p>|\|", html.unescape(text)) if s.strip()]
        segs = [s for s in segs if s and len(s) < 80]
        company = segs[0] if segs else "?"
        _NON_ROLE = re.compile(
            r"^(remote|onsite|hybrid|full[\s-]?time|part[\s-]?time|contract|https?://|"
            r"US(A)?\b|USA?\s+only|\$[\d,]|[\d,]+k[\s-]|worldwide|global)", re.I)
        title = next((s for s in segs[1:5] if s and not _NON_ROLE.match(s)), None) or "Marketing role"
        posted = None
        ts = c.get("created_at_i")
        if ts:
            try:
                posted = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
            except (ValueError, OSError):
                posted = None
        out.append(jobs._norm("hn_whoishiring", cid, title,
                              company, f"https://news.ycombinator.com/item?id={cid}",
                              None, "", posted))
    return out


def fetch_all() -> list[dict]:
    """Both new boards, deduped by apply_url, same discipline as jobs.fetch_boards()."""
    jobs_out, seen = [], set()
    for fn in (fetch_weworkremotely, fetch_hn_whoishiring):
        try:
            for j in fn():
                u = j.get("apply_url")
                if u and u not in seen:
                    seen.add(u)
                    jobs_out.append(j)
        except Exception:  # noqa: BLE001
            continue
    return jobs_out


if __name__ == "__main__":
    wwr = fetch_weworkremotely()
    hn = fetch_hn_whoishiring()
    print(f"weworkremotely: {len(wwr)} marketing-relevant job(s)")
    for j in wwr[:5]:
        print(f"  {j['company']} - {j['title']} ({j['apply_url']})")
    print(f"hn_whoishiring: {len(hn)} marketing-relevant post(s)")
    for j in hn[:5]:
        print(f"  {j['company']} - {j['title']} ({j['apply_url']})")
