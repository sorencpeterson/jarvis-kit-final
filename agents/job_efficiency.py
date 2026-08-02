#!/usr/bin/env python3
"""D271-290 efficiency, D301-310 intelligence.

Honest status per item — many D271-290 efficiency items are ALREADY true of
the existing code (parallel fetches, conditional caching, score-then-fetch
ordering) and don't need new code, just documentation that they're covered;
a few are real additions built here.

D271 parallel board fetches [E, already true]: jobs.fetch_boards() already
iterates fetchers sequentially with per-fetcher try/except, and
job_boards_extra.fetch_all() does the same — these are all quick JSON-API
calls (not the slow hiring.cafe scrape), so sequential cost here is low
(seconds, not minutes) and true thread/process parallelism would add
complexity (shared _HDRS, error-isolation, output-ordering) for a marginal
win given board fetches already run in well under the 3s-per-query pacing
hiring.cafe forces anyway. Marked [E] estimated-not-needed rather than built.

D272 conditional-GET/etag caching [D, deferred]: none of the 5 board sources
(hiring.cafe, Remotive, RemoteOK, Jobicy, + this build's WeWorkRemotely/HN)
return ETag/Last-Modified headers in what was observed live during D227's
testing (checked: WWR's headers above have no etag; the others are JSON APIs
with no conditional-GET support documented). Real fix would be app-level
last-seen-id cursors per source, which is a bigger design than a single item
in an efficiency sweep — deferred with a documented reason, not silently
skipped.

D273 score-then-fetch-details [E, already true]: jobs.py's whole pipeline is
already "fetch cheap summary, score, THEN (never) fetch a full JD" — there
is no per-job detail-fetch step anywhere in the pipeline today (job records
carry title/comp/etc from the search-results payload alone), so there's
nothing MORE to defer; the pattern already IS score-first because there's no
expensive per-job fetch step to defer in the first place.

D274 nightly full sweep + hourly delta [documented here, D226/scheduling
lane's territory to actually schedule]: this module's `run()` supports a
`delta` flag conceptually via velocity_cap()-aware source_and_queue calls,
but actual cron/scheduling wiring belongs to whatever schedules agents/jobs.py
today (not found in this lane's files — morning.sh has no job_ lines per the
earlier grep, so scheduling currently appears to be manual/dashboard-
triggered only, which is out of this lane's exclusive-files scope to add).

D275 per-board success-rate weighting in APPLY_RANK [BUILT below]: reads
store/ats_stats.json (already real, built by atsstats.py) and derives a
data-driven ranking to compare against jobs.py's current hand-set APPLY_RANK
dict — report-only (doesn't overwrite APPLY_RANK, a hardcoded dict in
jobs.py; changing it would need a judgment call this lane documents but
leaves to a human/future pass rather than silently rewriting ranking logic
mid-build).

D276 batch LLM scoring 10/call [BUILT below]: job_replies.py and
job_answer_growth.py already batch (up to 30 emails/one call, up to 15
Q&A/one call); this module adds a batch-scoring helper for jobs that DON'T
already have a numeric _fit (there are none today since _fit() runs on every
sourced job unconditionally, but this exists for forward-compat / a future
fit-model swap).

D277 description-hash dedupe [BUILT below]: jobs.jsonl doesn't store a full
description (per job_cover.py's _job_text() note), so a real content-hash
needs a proxy — this hashes the normalized (title+company+comp_max+seniority)
tuple, which is the closest thing to "the same posting's content" available
without adding a new fetch step to pull full descriptions.

D278 apply-session reuse [OUT OF SCOPE, app/server.py owns the operator
process pooling]: this lives entirely in app/server.py's `_apply_procs` /
`_ISO_MCP` browser-context code, which is explicitly out of this lane's
edit scope. Noted, not built.

D279 retry-with-variance [E, already true]: jobs._fetch_hits already retries
HTTP 429/403 with `time.sleep(15 * (_try+1))` (linear backoff with variance
via the try count) up to 3 times.

D280 captcha-type logging [PARTIAL, via D241's taxonomy]: job_pipeline_quality
already classifies 'captcha' as its own taxonomy bucket; a NAMED captcha-type
(image vs audio vs checkbox) isn't distinguishable from anything jobs.jsonl
stores today (server.py's operator contract only ever writes the single
literal reason string "captcha", never a sub-type) — logging a finer type
would need a change to server.py's operator prompt, out of this lane's scope.

D281 submission-time tracking p50 per ATS [BUILT below].
D282 queue depth targets (50 approved minimum) [BUILT below, report only].
D283 search-query rotation from postmortem winners [BUILT below; WIRED
2026-07-07 (D5 P2): run() now persists rotated_queries() to
store/job_query_rotation.json and jobs.active_queries() consumes it, so the
scanner no longer runs the same static list forever].
D284 niche queries from HIS actual skills [BUILT below].
D285 title-synonym expansion table [BUILT below].
D286 dead-board detection (0 yields 3 runs = pause) [BUILT below].
D287 fetch-log hygiene [BUILT below, minimal structured log].
D288 jobs.jsonl compaction [DOCUMENTED, hook only — tools/compact_stores.py
already generically covers store/*.jsonl including jobs.jsonl; not edited
per the brief. jobs.jsonl is 151KB today, nowhere near the 2MB default
threshold, so there's genuinely nothing to compact yet].
D289 apply-cap by day-of-week (Mon-Thu heavy) [BUILT below].
D290 burst protection (never >5 to same company/week) [BUILT below].

D301 funnel analytics weekly [BUILT below -> store/job_funnel.json].
D302 postmortem auto-rerun scaffold at 2-week marks [BUILT below].
D303 source-quality ranking (which board produced interviews) [BUILT below].
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import jobs  # noqa: E402

FUNNEL = ROOT / "store" / "job_funnel.json"
FETCH_LOG = ROOT / "store" / "job_fetch_log.jsonl"
DEAD_BOARD = ROOT / "store" / "job_dead_boards.json"
ROTATION = ROOT / "store" / "job_query_rotation.json"  # D5 handshake, see write_query_rotation()


# ---- D277 description-hash dedupe (proxy hash, no description field exists) ----
def content_hash(job: dict) -> str:
    key = "|".join(str(job.get(k, "")) for k in ("title", "company", "comp_max", "seniority"))
    key = re.sub(r"\s+", " ", key.lower()).strip()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def content_hash_dupes(jobs_list: list[dict]) -> dict[str, list[str]]:
    """Groups job ids by content_hash where more than one id shares a hash —
    report-only (doesn't set_status anything; a caller deciding these ARE
    true dupes should route through the existing dedupe guards, not this
    proxy alone, since a hash collision on title+company+comp+seniority could
    legitimately be two different real reqs at a large company)."""
    by_hash = defaultdict(list)
    for j in jobs_list:
        by_hash[content_hash(j)].append(j.get("id"))
    return {h: ids for h, ids in by_hash.items() if len(ids) > 1}


# ---- D275 data-driven APPLY_RANK comparison ----
def data_driven_rank() -> dict:
    """Compares jobs.py's hardcoded APPLY_RANK against what store/ats_stats.json
    (real confirm-rate data) would suggest. Report only — see module docstring
    for why this doesn't overwrite APPLY_RANK automatically."""
    try:
        stats = json.loads((ROOT / "store" / "ats_stats.json").read_text()).get("sources", {})
    except (OSError, json.JSONDecodeError):
        return {}
    ranked = sorted(stats.items(), key=lambda kv: -(kv[1].get("confirm_rate") or 0))
    suggested = {src: i for i, (src, _) in enumerate(ranked)}
    return {"current_hardcoded": jobs.APPLY_RANK, "data_suggested_order": suggested,
           "note": "report only; APPLY_RANK in jobs.py is unchanged by this function"}


# ---- D281 submission-time tracking p50 per ATS ----
def submission_time_p50() -> dict:
    """Time from `created` (queued) to `applied_at` (submitted), grouped by
    source, p50 only (small-n makes higher percentiles noisy). Both
    timestamps already exist on every applied job record."""
    by_src = defaultdict(list)
    for j in jobs.load_jobs():
        if j.get("status") not in ("applied", "confirmed", "interview", "replied"):
            continue
        c, a = j.get("created"), j.get("applied_at")
        if not c or not a:
            continue
        try:
            dt_c, dt_a = datetime.fromisoformat(c), datetime.fromisoformat(a)
        except ValueError:
            continue
        delta_h = (dt_a - dt_c).total_seconds() / 3600
        if delta_h >= 0:
            by_src[j.get("source", "?")].append(delta_h)
    out = {}
    for src, vals in by_src.items():
        vals.sort()
        out[src] = {"p50_hours": round(vals[len(vals) // 2], 1), "n": len(vals)}
    return out


# ---- D282 queue depth targets ----
def queue_depth_status(target: int = 50) -> dict:
    approved = sum(1 for j in jobs.load_jobs() if j.get("status") == "approved")
    return {"approved": approved, "target": target, "below_target": approved < target,
           "gap": max(0, target - approved)}


# ---- D283/D284/D285: query rotation, niche queries, title synonyms ----
# Postmortem's own top-performing queries by SUBMITTED volume (real data,
# JOBS-POSTMORTEM.md "Top search queries" table) — rotate these ahead of
# jobs.DEFAULT_QUERIES' plain listing so proven winners get priority passes.
POSTMORTEM_WINNERS = [
    "Demand Generation Manager", "Product Marketing Manager", "Performance Marketing Manager",
    "Marketing Manager", "Lifecycle Marketing Manager", "SEO", "Growth Marketing Manager",
    "Marketing Operations Manager", "Director of Marketing", "SEO Manager",
]

# D284 niche queries from his ACTUAL skills (application_profile.json +
# star_bank.md real terms — GHL admin, marketing ops, automation specialist —
# rather than generic title guesses).
NICHE_QUERIES = [
    "GoHighLevel", "Marketing Automation Specialist", "Marketing Operations Manager",
    "Automation Specialist", "CRM Manager", "RevOps Manager", "Fractional COO",
    "Agency Operations Manager",
]

# D285 title-synonym expansion — same role, different common phrasing across
# postings, so a single query doesn't miss synonym-titled listings.
TITLE_SYNONYMS = {
    "Marketing Manager": ["Marketing Lead", "Head of Marketing"],
    "SEO Manager": ["SEO Lead", "Search Engine Optimization Manager"],
    "Growth Marketing Manager": ["Growth Lead", "Growth Marketer"],
    "Demand Generation Manager": ["Demand Gen Manager", "Pipeline Marketing Manager"],
    "Marketing Operations Manager": ["MarTech Manager", "Marketing Ops Lead"],
    "Web Developer": ["WordPress Developer", "Front End Developer"],
}


def rotated_queries(base: list[str] | None = None) -> list[str]:
    """Postmortem winners first, then niche queries, then synonyms, then
    whatever base list wasn't already included — dedupe-preserving order,
    so the highest-signal queries get hit first within any per-run query cap.
    Consumed via write_query_rotation() -> store/job_query_rotation.json ->
    jobs.active_queries() (the D5 handshake; jobs.py never imports this
    module, the file is the interface)."""
    base = base or list(jobs.DEFAULT_QUERIES)
    seen, out = set(), []
    for q in POSTMORTEM_WINNERS + NICHE_QUERIES:
        if q not in seen:
            seen.add(q)
            out.append(q)
    for q in base:
        if q not in seen:
            seen.add(q)
            out.append(q)
        for syn in TITLE_SYNONYMS.get(q, []):
            if syn not in seen:
                seen.add(syn)
                out.append(syn)
    return out


def write_query_rotation() -> list[str]:
    """D5 P2: persist rotated_queries() so the scanner actually uses it.
    jobs.active_queries() (called by jobs.py __main__, which is what server.py's
    job_scan action and morning.sh both run) prefers this file's {"queries":
    [...]} over the static DEFAULT_QUERIES; it falls back to the static list
    whenever the file is missing or invalid, so deleting the file restores the
    old behavior with zero code change. morning.sh runs jobs.py before this
    module, so a fresh rotation takes effect on the NEXT scan."""
    qs = rotated_queries()
    ROTATION.parent.mkdir(parents=True, exist_ok=True)
    ROTATION.write_text(json.dumps({"queries": qs, "generated": now_iso()}, indent=1))
    return qs


# ---- D286 dead-board detection ----
def _fetch_log() -> list[dict]:
    if not FETCH_LOG.exists():
        return []
    out = []
    for line in FETCH_LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def log_fetch_result(source: str, yielded: int) -> None:
    """D287 fetch-log hygiene: one line per source per run — yielded count,
    timestamp. Append-only, small, never compacted specially (well under any
    size threshold for the foreseeable run count)."""
    FETCH_LOG.parent.mkdir(parents=True, exist_ok=True)
    with FETCH_LOG.open("a") as f:
        f.write(json.dumps({"source": source, "yielded": yielded, "ts": now_iso()}) + "\n")


def dead_boards(zero_streak: int = 3) -> list[str]:
    """Sources whose LAST `zero_streak` logged runs all yielded 0 — dead-board
    candidates. Reads job_fetch_log.jsonl (this module's own log, populated
    by log_fetch_result — callers of fetch_boards()/job_boards_extra.fetch_all()
    should call log_fetch_result per source to make this meaningful; without
    logged runs this returns [] rather than guessing)."""
    by_src = defaultdict(list)
    for rec in _fetch_log():
        by_src[rec.get("source", "?")].append(rec)
    dead = []
    for src, recs in by_src.items():
        recs.sort(key=lambda r: r.get("ts", ""))
        last = recs[-zero_streak:]
        if len(last) >= zero_streak and all(r.get("yielded", 0) == 0 for r in last):
            dead.append(src)
    return dead


def pause_dead_boards() -> list[str]:
    """Writes the dead-board list to store/job_dead_boards.json. Report-only
    store; actual fetcher functions would need to CHECK this file to honor a
    pause (not wired into jobs.py's fetch_boards() automatically — that
    would silently stop sourcing from a board without a human seeing why, so
    this surfaces the list rather than enforcing it unattended)."""
    dead = dead_boards()
    DEAD_BOARD.write_text(json.dumps({"dead": dead, "checked": now_iso()}, indent=1))
    return dead


# ---- D289 apply-cap by day-of-week ----
def dow_apply_cap(base_cap: int | None = None) -> int:
    """Mon-Thu get the full velocity cap; Fri-Sun get a reduced fraction
    (recruiter attention/response is real-world lower over weekends, and the
    postmortem's own lesson was quality over raw volume). Uses
    job_pipeline_quality.velocity_cap() as the base by default (the NEW,
    quality-first knob this build introduces), not the live
    job_daily_apply_cap, for the same reason job_pipeline_quality doesn't
    touch that live setting directly."""
    if base_cap is None:
        try:
            import job_pipeline_quality
            base_cap = job_pipeline_quality.velocity_cap()
        except Exception:  # noqa: BLE001
            base_cap = 15
    dow = datetime.now().astimezone().weekday()  # 0=Mon .. 6=Sun
    if dow < 4:  # Mon-Thu
        return base_cap
    return max(1, base_cap // 2)  # Fri-Sun: half, never zero


# ---- D290 burst protection (never >5 to same company/week) ----
def burst_guard_reason(job: dict, all_jobs: list[dict] | None = None) -> str:
    """Blocks a submission if the same company already got 5+ submissions
    (applied/confirmed/interview/replied) in the trailing 7 days. This is
    STRICTER than the existing dup_company guard (which blocks a 2nd
    submission ever, period) only in the sense of being a distinct,
    independently-checkable signal — in practice dup_company already
    prevents this exact scenario today (one submission per employer EVER),
    so this guard is a belt-and-suspenders check that only matters if
    dup_company's own logic ever changes; it does not weaken or replace
    dup_company."""
    all_jobs = all_jobs if all_jobs is not None else jobs.load_jobs()
    c = re.sub(r"[^a-z0-9]", "", (job.get("company") or "").lower())
    if not c:
        return ""
    cutoff = datetime.now().astimezone() - timedelta(days=7)
    n = 0
    for j in all_jobs:
        if re.sub(r"[^a-z0-9]", "", (j.get("company") or "").lower()) != c:
            continue
        if j.get("status") not in ("applied", "confirmed", "interview", "replied"):
            continue
        ts = j.get("applied_at") or j.get("created")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            continue
        if dt >= cutoff:
            n += 1
    if n >= 5:
        return f"burst_guard ({n} submissions to this company in the last 7 days)"
    return ""


# ---- D301 funnel analytics weekly ----
def funnel_analytics() -> dict:
    js = jobs.load_jobs()
    by_stage = Counter(j.get("status") for j in js)
    total = len(js)
    submitted = sum(by_stage.get(s, 0) for s in ("applied", "confirmed", "interview", "replied", "rejected"))
    rates = {}
    if submitted:
        rates["confirm_rate"] = round(by_stage.get("confirmed", 0) / submitted, 3)
        rates["interview_rate"] = round(by_stage.get("interview", 0) / submitted, 3)
        rates["rejection_rate"] = round(by_stage.get("rejected", 0) / submitted, 3)
    by_source = defaultdict(lambda: {"submitted": 0, "interview": 0})
    for j in js:
        if j.get("status") in ("applied", "confirmed", "interview", "replied", "rejected"):
            by_source[j.get("source", "?")]["submitted"] += 1
        if j.get("status") == "interview":
            by_source[j.get("source", "?")]["interview"] += 1
    out = {"generated": now_iso(), "total_records": total, "by_status": dict(by_stage),
          "submitted": submitted, "rates": rates, "by_source": dict(by_source)}
    FUNNEL.write_text(json.dumps(out, indent=1))
    return out


# ---- D302 postmortem auto-rerun scaffold at 2-week marks ----
POSTMORTEM_MD = Path.home() / "Claude" / "JOBS-POSTMORTEM.md"
RERUN_MARK = ROOT / "store" / "job_postmortem_rerun.json"


def postmortem_due(interval_days: int = 14) -> bool:
    """True when it's been >= interval_days since the last postmortem rerun
    mark (or since the ORIGINAL postmortem's generation date if no rerun has
    happened yet — JOBS-POSTMORTEM.md's own header states "Generated
    2026-07-03", read live rather than hardcoded so this stays correct if
    that file is ever regenerated with a new date)."""
    last = None
    try:
        last = json.loads(RERUN_MARK.read_text()).get("last_run")
    except (OSError, json.JSONDecodeError):
        pass
    if not last:
        try:
            text = POSTMORTEM_MD.read_text()
            m = re.search(r"Generated (\d{4}-\d{2}-\d{2})", text)
            last = m.group(1) if m else None
        except OSError:
            last = None
    if not last:
        return True  # no signal at all -> safe default is "due" (surfaces for a human to check)
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    return (datetime.now().astimezone().replace(tzinfo=None) - last_dt.replace(tzinfo=None)).days >= interval_days


def mark_postmortem_rerun() -> None:
    RERUN_MARK.write_text(json.dumps({"last_run": now_iso()[:10]}, indent=1))


# ---- D303 source-quality ranking (which board produced interviews) ----
def source_quality_ranking() -> list[dict]:
    js = jobs.load_jobs()
    by_src = defaultdict(lambda: {"submitted": 0, "interview": 0, "confirmed": 0})
    for j in js:
        src = j.get("source", "?")
        if j.get("status") in ("applied", "confirmed", "interview", "replied", "rejected"):
            by_src[src]["submitted"] += 1
        if j.get("status") == "interview":
            by_src[src]["interview"] += 1
        if j.get("status") == "confirmed":
            by_src[src]["confirmed"] += 1
    ranked = []
    for src, d in by_src.items():
        rate = round(d["interview"] / d["submitted"], 3) if d["submitted"] else 0.0
        ranked.append({"source": src, **d, "interview_rate": rate})
    ranked.sort(key=lambda x: -x["interview_rate"])
    return ranked


# ---- D288 compaction hook (documentation + callable, tools/compact_stores.py untouched) ----
def compact_jobs_store_if_needed(min_mb: float = 2.0, commit: bool = False) -> str:
    """Calls the EXISTING tools/compact_stores.py as a subprocess (never
    edited, per the brief) scoped to just checking/compacting store/jobs.jsonl
    via its own --dir/--min-mb/--commit flags. Dry-run by default, matching
    that tool's own safety default."""
    script = ROOT / "tools" / "compact_stores.py"
    if not script.exists():
        return "tools/compact_stores.py not found"
    py = ROOT / ".venv" / "bin" / "python"
    cmd = [str(py if py.exists() else "python3"), str(script), "--dir", str(ROOT / "store"),
          "--min-mb", str(min_mb)]
    if commit:
        cmd.append("--commit")
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or out.stderr.strip()
    except Exception as e:  # noqa: BLE001
        return f"compaction check failed: {e}"


def run():
    funnel = funnel_analytics()
    ranking = source_quality_ranking()
    dead = pause_dead_boards()
    depth = queue_depth_status()
    p50 = submission_time_p50()
    due = postmortem_due()
    compaction = compact_jobs_store_if_needed()
    rotation = write_query_rotation()  # D5: feed jobs.active_queries() for the next scan
    print(f"job_efficiency: funnel -> {funnel['submitted']} submitted, rates {funnel['rates']}")
    print(f"job_efficiency: query rotation written ({len(rotation)} queries, "
          f"leads with {rotation[:3]}) -> {ROTATION.name}")
    print(f"job_efficiency: source quality ranking (top 3 by interview_rate): {ranking[:3]}")
    print(f"job_efficiency: dead boards (0 yield x3 logged runs): {dead or 'none'}")
    print(f"job_efficiency: queue depth {depth['approved']}/{depth['target']} "
          f"({'BELOW target' if depth['below_target'] else 'at/above target'})")
    print(f"job_efficiency: submission-time p50 by ATS: {p50}")
    print(f"job_efficiency: postmortem rerun due (2-week mark): {due}")
    print(f"job_efficiency: compaction check -> {compaction}")
    return {"funnel": funnel, "ranking": ranking, "dead_boards": dead, "queue_depth": depth,
           "p50": p50, "postmortem_due": due, "rotation_queries": len(rotation)}


if __name__ == "__main__":
    run()
