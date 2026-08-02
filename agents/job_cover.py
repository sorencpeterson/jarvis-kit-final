#!/usr/bin/env python3
"""D222: per-job cover-letter personalization.

The apply operator (app/server.py `_build_prompt`) currently bases every cover
letter on `application_profile.json`'s single static `default_cover`, "lightly
fitted to the role" by whatever the LLM operator improvises in the moment. That
means the SAME base pitch goes out everywhere, and any role-specific fit has to
be reinvented per-application at apply time instead of being decided once,
cheaply, at scoring time.

cover_for(job) builds: default_cover + up to 2 extra lines --
  1. a role-keyword line (which of his real skills line up with THIS title/desc)
  2. an honest company-fact line (freeform, ONLY when a real fact is on hand;
     never invented -- see _company_fact())
...and caches the result on the job record as `cover_override`, so:
  - it's computed once (cheap, deterministic, no LLM call needed for the
    keyword line; the company-fact line is opportunistic and best-effort)
  - it's stable across re-runs (same job -> same cover, no drift)
  - the apply operator has a per-job override to reach for instead of always
    falling back to the generic default_cover

OPERATOR CONTRACT (documented here since app/server.py is out of this lane's
edit scope): `_build_prompt()` in app/server.py should prefer
`job.get("cover_override")` over `profile["default_cover"]` when present,
still "lightly fitted to the role" per its existing instruction. Until that
one-line change lands in server.py, this module still delivers value: it's
useful standalone (job_cover.run() backfills the field on the queue; the
value is visible in the dashboard / any manual-apply flow that reads the job
record), and the future server.py hook is a single `job.get("cover_override")
or profile.get("default_cover")` fallback with zero risk if the field is
absent.

Nothing here sends anything. Read-only against jobs.jsonl except for
`_save()` re-writing the SAME job with the added field (matches the append-
only-by-id discipline jobs.py already uses everywhere).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import humanize  # noqa: E402
import jobs  # noqa: E402

# Skills he genuinely has (mirrors application_profile.json's experience_stance +
# the STAR bank) -- keyword -> the human phrase to use when it matches the role.
# Ordered roughly by how much of his real story each one carries.
_SKILL_PHRASES = [
    ("wordpress", "WordPress builds"), ("elementor", "Elementor builds"),
    ("web dev", "web development"), ("web design", "web design"),
    ("seo", "SEO"), ("search engine", "SEO"),
    ("demand gen", "demand generation"), ("growth", "growth marketing"),
    ("lifecycle", "lifecycle marketing"), ("crm", "CRM/marketing ops"),
    ("marketing automation", "marketing automation"), ("automation", "AI-assisted automation"),
    ("paid media", "paid media"), ("paid search", "paid search"), ("ppc", "paid media"),
    ("google ads", "Google Ads"), ("meta ads", "Meta ads"),
    ("cro", "conversion rate optimization"), ("conversion", "conversion optimization"),
    ("content", "content strategy"), ("email", "email marketing"), ("sms", "SMS marketing"),
    ("brand", "brand marketing"), ("product marketing", "product marketing"),
    ("marketing operations", "marketing operations"), ("revops", "revenue operations"),
    ("agency", "agency operations"), ("gohighlevel", "GoHighLevel"), ("ghl", "GoHighLevel"),
    ("ecommerce", "ecommerce marketing"), ("e-commerce", "ecommerce marketing"),
    ("analytics", "analytics/reporting"),
]

_STOP = {"the", "and", "for", "with", "you", "our", "are", "will", "this", "that",
         "have", "has", "your", "from", "who", "role", "team", "work", "join"}


def _job_text(job: dict) -> str:
    """Whatever text a job record actually carries to keyword-match against.
    jobs.jsonl records don't store a full description (only title/salary/etc
    per the schema documented at the top of jobs.py), so this is title +
    query (the search term that surfaced it, a real signal of intent) +
    seniority/commitment when present. Best-effort by design."""
    bits = [job.get("title") or "", job.get("query") or "", job.get("seniority") or ""]
    c = job.get("commitment")
    if isinstance(c, list):
        bits.extend(str(x) for x in c)
    elif c:
        bits.append(str(c))
    return " ".join(bits).lower()


def role_keyword_line(job: dict, max_skills: int = 3) -> str:
    """One honest line naming which of his REAL skills line up with this role's
    own title/query text. Never invents a skill; only surfaces overlap."""
    text = _job_text(job)
    hits, seen_phrase = [], set()
    for kw, phrase in _SKILL_PHRASES:
        if kw in text and phrase not in seen_phrase:
            hits.append(phrase)
            seen_phrase.add(phrase)
        if len(hits) >= max_skills:
            break
    if not hits:
        return ""
    if len(hits) == 1:
        joined = hits[0]
    elif len(hits) == 2:
        joined = f"{hits[0]} and {hits[1]}"
    else:
        joined = ", ".join(hits[:-1]) + f", and {hits[-1]}"
    return f"For this {job.get('title') or 'role'}, the direct overlap is {joined}."


# ---- honest company fact (opportunistic, cached, never invented) ----
_FACT_CACHE = ROOT / "store" / "company_facts.json"


def _load_fact_cache() -> dict:
    try:
        return json.loads(_FACT_CACHE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_fact_cache(d: dict) -> None:
    try:
        _FACT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _FACT_CACHE.write_text(json.dumps(d, indent=1))
    except OSError:
        pass


def _company_fact(company: str) -> str:
    """A short, honest, ALREADY-KNOWN fact about the company, or '' if none is
    on hand. Deliberately does NOT call an LLM or the web to invent one --
    that's a fabrication risk (a wrong "fact" in a cover letter is worse than
    no fact) and this lane's MCP access is rate-limited (see
    agents/job_mcp_notes.md), so this only reads a small manually-seedable
    cache. get_company_data enrichment (job_mcp_enrich.py) is the intended
    future writer of real entries here once the MCP is reachable; until then
    this degrades to '' everywhere, which cover_for() handles cleanly (no
    fact line, just the default_cover + keyword line)."""
    if not company:
        return ""
    key = re.sub(r"[^a-z0-9]", "", company.lower())
    cache = _load_fact_cache()
    entry = cache.get(key)
    if entry and entry.get("fact"):
        return str(entry["fact"])
    return ""


def cover_for(job: dict, profile: dict | None = None) -> str:
    """default_cover + up to 2 generated lines (role keywords, honest company
    fact when known). Deterministic given the same job+profile+fact-cache, so
    safe to call repeatedly / cache."""
    profile = profile if profile is not None else jobs.load_profile()
    base = (profile.get("default_cover") or "").strip()
    extra = []
    # role_keyword_line intentionally NOT appended to the outgoing cover (2026-07-07 copy
    # audit): "For this <title>, the direct overlap is X, Y, and Z" is a fixed rule-of-three
    # frame that repeats verbatim across every application -- a visible template seam. The
    # default_cover already carries the relevance; a real company fact still personalizes.
    fact = _company_fact(job.get("company") or "")
    if fact:
        extra.append(fact)
    if not extra:
        return base
    return humanize(base + ("\n\n" if base else "") + " ".join(extra))


def _save_cover_override(job_id: str, cov: str) -> bool:
    """Load-under-lock + compare-and-swap append (R2-47, 2026-07-13 hunt).

    backfill() used to jobs._save() a job dict CAPTURED from its own load_jobs() snapshot,
    taken once before its loop started. jobs._save() only locks the append itself, not a
    read-modify-write -- so if an apply-operator advanced that SAME job approved -> applying
    -> applied while backfill() was still iterating, the stale-status append (still carrying
    status="approved" from backfill's old snapshot) landed as a LATER line in the append-only
    store than the real transition, silently REVERTING an in-flight/submitted job back to
    "approved" and opening it to a duplicate re-application.

    Fixed the same way jobs.set_status()/mark_applying() already are: re-read the CURRENT
    record under the store's own lock, and only append if the job is still in a state this
    enrichment is safe to touch. Returns True on a real write, False if skipped (moved on,
    already covered, or gone)."""
    from store_lib import _flock
    with _flock(jobs.QUEUE):
        rec = next((x for x in jobs.load_jobs() if x.get("id") == job_id), None)
        if not rec:
            return False
        if rec.get("status") not in ("pending", "approved"):
            return False  # moved on since backfill() decided to cover it -- don't clobber
        if rec.get("cover_override"):
            return False  # a concurrent run already set it -- don't overwrite
        rec["cover_override"] = cov
        jobs.QUEUE.parent.mkdir(parents=True, exist_ok=True)
        with jobs.QUEUE.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True


def backfill(limit: int = 500) -> int:
    """Compute + cache cover_override on every approved/pending job that
    doesn't have one yet. Cheap (no LLM call in the common no-fact-cache
    case), safe to re-run (idempotent: skips jobs that already have the
    field, matching the pattern D251/dedupe code already uses)."""
    profile = jobs.load_profile()
    n = 0
    for j in jobs.load_jobs():
        if j.get("status") not in ("pending", "approved"):
            continue
        if j.get("cover_override"):
            continue
        cov = cover_for(j, profile)
        if not cov:
            continue
        if _save_cover_override(j["id"], cov):
            n += 1
        if n >= limit:
            break
    return n


def run():
    n = backfill()
    print(f"job_cover: personalized {n} job(s) -> cover_override cached in jobs.jsonl")
    return n


if __name__ == "__main__":
    run()
