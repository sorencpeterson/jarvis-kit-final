#!/usr/bin/env python3
"""Meeting prep cards (#55) — walking into a call cold because nobody pulled
together who it's with and what's been happening with them is an avoidable
miss. This builds a one-shot prep card for every calendar event in the next 36
hours that doesn't have one yet.

Pulls events from /api/gcal ({date, text, when} per gcal_write.read_events()),
filters to the next 36h, and for each new one builds a card in a single CLI call
seeded with the event title, any contact_graph.json person whose name/company
fuzzy-matches the title, and recent feed.jsonl mentions of that name. Contact
matching and feed matching are both best-effort string containment (the graph
has phone-number-only entries and no company field yet, so this is intentionally
loose rather than silently prepping nothing).

E333 (meeting-prep v2): classifies each event as SALES or INTERVIEW (or
GENERIC if neither pattern matches) and pulls a richer, kind-specific context
bundle before the single CLI call, porting the pattern agents/interview_prep.py
already proved out for job interviews (JD research via WebSearch + STAR bank)
so SALES calls get the equivalent depth:
  SALES:     contact_graph.json match (as before) + store/niche_db.json's
             stats for that contact's niche (open/close rate, avg price, what
             actually works) + any open objections.jsonl entries for that
             niche + agents/thread_memory.dossier_summaries_for_contact() if
             the matched contact has a contact_id.
  INTERVIEW: detected via a title match against jobs.jsonl (status in
             ("interview","applied") + fuzzy title containment) or the word
             "interview" in the event title -> company/title seed + [OWNER]'s
             star_bank() STAR stories (store_lib.star_bank(), the SAME source
             interview_prep.py uses) + a WebSearch-backed company blurb, one
             CLI call with --allowedTools WebSearch (same tool interview_prep
             already uses for real research, not invented).
  GENERIC:   unchanged from v1 (contact match + feed mentions, no WebSearch).

Read-only against /api/gcal, store/contact_graph.json, store/feed.jsonl,
store/niche_db.json, store/objections.jsonl, store/jobs.jsonl,
store/thread_summaries.jsonl (via thread_memory's helper); writes are
store/prep_cards.jsonl (append, now also carries a "kind" field) + a
feed_add per card. If /api/gcal is empty or unreachable, prints that and
exits 0 (not an error — no calendar wired yet is a valid state).
Run standalone: .venv/bin/python agents/meeting_prep.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso, secret, star_bank  # noqa: E402
import planner  # noqa: E402
import thread_memory  # noqa: E402
from runlog import track  # noqa: E402  (E353: runlog adoption)

PREP_CARDS = ROOT / "store" / "prep_cards.jsonl"
CONTACT_GRAPH = ROOT / "store" / "contact_graph.json"
FEED = ROOT / "store" / "feed.jsonl"
NICHE_DB = ROOT / "store" / "niche_db.json"
OBJECTIONS = ROOT / "store" / "objections.jsonl"
JOBS = ROOT / "store" / "jobs.jsonl"
WINDOW_HOURS = 36
INTERVIEW_KEYWORDS = ("interview", "screen", "phone screen", "onsite", "hiring manager")

# Generic-word stopfilter for title-matching (E333 hardening — found while
# verifying for real: v1's plain "4+ char word" filter let common English
# words like "about"/"site"/"doctor"/"call" false-match unrelated contacts
# and jobs by pure substring coincidence, e.g. "Call with the team about site
# updates" matched a contact named "all about me laser medspa" purely via the
# word "about". A real minimum-length bump doesn't fix this — "site"/"call"
# are already 4-5 chars, same as many real proper nouns — so this is a
# stopword list, not a length threshold.
_MATCH_STOPWORDS = {
    "about", "after", "again", "call", "calls", "check", "could", "doctor",
    "during", "email", "event", "from", "have", "hello", "into", "meet",
    "meeting", "onsite", "over", "phone", "please", "quick", "site", "sync",
    "that", "their", "them", "then", "there", "these", "they", "this",
    "time", "today", "tomorrow", "update", "updates", "very", "week",
    "weekly", "were", "what", "when", "where", "which", "will", "with",
    "your",
}


def _match_words(title: str, min_len: int = 4) -> list[str]:
    """Words worth matching a title against contacts/jobs: min_len+ chars,
    lowercased, generic-English stopwords excluded (see _MATCH_STOPWORDS)."""
    return [w for w in (w.lower() for w in title.split())
            if len(w) >= min_len and w not in _MATCH_STOPWORDS]


def _get(path: str) -> dict:
    req = urllib.request.Request("http://127.0.0.1:8765" + path,
                                 headers={"X-Brain-Token": secret("brain_token")})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


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


def _already_prepped() -> set[str]:
    return {r.get("event") for r in _read_jsonl(PREP_CARDS) if r.get("event")}


def _upcoming(events: list[dict]) -> list[dict]:
    now = datetime.now().astimezone()
    cutoff = now + timedelta(hours=WINDOW_HOURS)
    out = []
    for e in events:
        when = e.get("when") or ""
        try:
            dt = datetime.fromisoformat(when)
        except ValueError:
            continue
        if not dt.tzinfo:
            dt = dt.astimezone()
        if now <= dt <= cutoff:
            out.append(e)
    return out


def _matching_contact(title: str, *, exclude_job_only: bool = False) -> dict | None:
    """Loose fuzzy match: does any word of the event title (4+ chars, minus
    generic stopwords) appear in a contact's name? The graph is phone-heavy
    with no company field yet, so this is intentionally forgiving rather than
    strict.

    exclude_job_only: when True, skip a Person whose ONLY source is "jobs"
    (contact_graph.py's tag for a company [OWNER] applied TO, not a sales
    prospect — sources=["jobs"] means no GHL/warm/replies signal ever backed
    this entity, it's purely a job-posting company name). Found this matters
    for real while verifying E333: "829 Studios" is BOTH a real company [OWNER]
    applied to for a job AND happens to share a name pattern that would
    otherwise get treated as a sales lead with zero actual sales history."""
    try:
        people = json.loads(CONTACT_GRAPH.read_text()).get("people", [])
    except (OSError, json.JSONDecodeError):
        return None
    words = _match_words(title)
    if not words:
        return None
    for person in people:
        name = (person.get("name") or "").lower()
        if not name:
            continue
        if exclude_job_only and person.get("sources") == ["jobs"]:
            continue
        if any(w in name for w in words):
            return person
    return None


def _feed_mentions(title: str, n: int = 5) -> list[str]:
    words = _match_words(title)
    if not words:
        return []
    hits = []
    for r in reversed(_read_jsonl(FEED)):
        t = (r.get("title") or "") + " " + (r.get("detail") or "")
        if any(w in t.lower() for w in words):
            hits.append(f"{r.get('kind', '?')}: {r.get('title', '')}")
            if len(hits) >= n:
                break
    return hits


def _matching_job(title: str) -> dict | None:
    """Loose fuzzy match against jobs.jsonl for an INTERVIEW-kind event: any
    4+ char word of the event title appearing in a job's company or title,
    restricted to jobs actually in an interview-adjacent status so a random
    'applied' entry from months ago doesn't false-match a generic meeting."""
    jobs_rows = _read_jsonl(JOBS)
    words = _match_words(title)
    if not words or not jobs_rows:
        return None
    candidates = [j for j in jobs_rows if j.get("status") in ("interview", "applied")]
    for j in candidates:
        blob = f"{j.get('company', '')} {j.get('title', '')}".lower()
        if any(w in blob for w in words):
            return j
    return None


def classify_event(title: str) -> str:
    """SALES if a real contact_graph match exists (excluding job-company-only
    entries, see _matching_contact's exclude_job_only), INTERVIEW if the
    title contains an interview keyword OR matches a jobs.jsonl row, else
    GENERIC. Checked in this order because a contact match is the stronger,
    more specific signal (an actual known prospect) versus a keyword guess."""
    t = title.lower()
    if any(kw in t for kw in INTERVIEW_KEYWORDS) or _matching_job(title):
        return "interview"
    if _matching_contact(title, exclude_job_only=True):
        return "sales"
    return "generic"


def _niche_for_contact(contact: dict | None) -> str | None:
    """contact_graph.json v2 (E323) stashes 'niche' directly on a Person when
    it came from the warm-hitlist source; None for any other source."""
    return (contact or {}).get("niche")


def _niche_stats(niche: str | None) -> dict | None:
    if not niche:
        return None
    try:
        db = json.loads(NICHE_DB.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return db.get("niches", {}).get(niche)


def _open_objections(niche: str | None, n: int = 5) -> list[str]:
    """store/objections.jsonl entries for this niche (or unfiltered if the
    contact has no niche on record — better a few generic objections than
    none). Empty file (real current state) -> [] honestly, not an error."""
    rows = _read_jsonl(OBJECTIONS)
    if niche:
        rows = [r for r in rows if (r.get("niche") or "").lower() == niche.lower()] or rows
    return [r.get("text") or r.get("objection") or "" for r in rows[-n:] if (r.get("text") or r.get("objection"))]


SALES_PROMPT = """[OWNER] has a SALES call/meeting coming up ([OWNER_COMPANY] /
[OWNER_COMPANY], white-label web builds + agency ops). Build a tight prep
card (plain text, NO em-dashes) covering:
1. Who it's with and what's known about them (from CONTACT below)
2. Niche performance context: how this niche typically converts (open/close
   rate, avg price) if the stats below have real numbers, otherwise say the
   data's too thin and don't invent a rate
3. 2-3 objections this niche commonly raises and a one-line counter for each
4. Anything from past conversation threads worth referencing
5. The obvious next move to push this deal forward
Keep it under 180 words total. If the info below is thin, say so briefly and
give your best generic sales prep rather than refusing.

EVENT TITLE: %s
START: %s

CONTACT (may be empty): %s
NICHE STATS for "%s" (may be empty/insufficient data): %s
COMMON OBJECTIONS for this niche (may be empty): %s
PAST THREAD SUMMARIES with this contact (may be empty): %s
RECENT FEED MENTIONS (may be empty/irrelevant): %s

Output ONLY the prep card."""

INTERVIEW_PROMPT = """[OWNER] has a JOB INTERVIEW coming up. Build a tight prep
card (plain text, NO em-dashes) covering:
1. What the company does and any relevant context (use WebSearch on the
   company name below if it's not empty)
2. What this specific role is probably about, inferred from the title
3. 3-4 likely interview questions for this role, each with a one-line angle
   [OWNER] should hit, weaving in his real STAR stories below where they fit
4. Two sharp questions for [OWNER] to ask them
Keep it under 200 words total. If the company name is empty/unclear, do your
best generic interview prep rather than refusing.

EVENT TITLE: %s
START: %s

MATCHED JOB (may be empty): %s
[OWNER]'S BACKGROUND: %s
HIS REAL STAR STORIES (weave in where relevant, may be empty): %s

Output ONLY the prep card."""

GENERIC_PROMPT = """[OWNER] has a meeting coming up. Build a tight prep card (plain text,
NO em-dashes) covering:
1. Who it's likely with and what's known about them (from the info below)
2. What the meeting is probably about, inferred from the title
3. 2-3 talking points or questions worth raising
Keep it under 150 words total. If the info below is thin, say so briefly and give
your best generic prep rather than refusing.

EVENT TITLE: %s
START: %s

KNOWN CONTACT MATCH (may be empty/irrelevant): %s

RECENT FEED MENTIONS (may be empty/irrelevant): %s

Output ONLY the prep card."""

# kept for backward compatibility with anything that imported the old name
PROMPT = GENERIC_PROMPT


def _contact_id_for(contact: dict | None) -> str | None:
    """contact_graph.json's Person records don't carry a contact_id directly
    (see agents/contact_graph.py — it's built from GHL contacts/warm rows/
    replies, none of which stash the GHL contact_id on the merged Person).
    Best-effort recovery: replies.jsonl DOES carry contact_id alongside name
    and email, so match this contact's name or any of its emails against
    replies.jsonl to recover one. None (not a crash) if nothing matches —
    that's a valid state (a contact who's never had a reply thread)."""
    if not contact:
        return None
    name = (contact.get("name") or "").strip().lower()
    emails = {e.lower() for e in contact.get("emails", [])}
    for r in _read_jsonl(ROOT / "store" / "replies.jsonl"):
        if not r.get("contact_id"):
            continue
        if (r.get("name") or "").strip().lower() == name and name:
            return r["contact_id"]
        if (r.get("email") or "").strip().lower() in emails and emails:
            return r["contact_id"]
    return None


def _build_sales_card(title: str, start: str) -> str | None:
    contact = _matching_contact(title)
    niche = _niche_for_contact(contact)
    stats = _niche_stats(niche)
    objections = _open_objections(niche)
    mentions = _feed_mentions(title)
    contact_id = _contact_id_for(contact)
    thread_summaries = thread_memory.dossier_summaries_for_contact(contact_id) if contact_id else []
    return planner._cli(
        SALES_PROMPT % (title, start, json.dumps(contact) if contact else "none",
                        niche or "(unknown)", json.dumps(stats) if stats else "insufficient data",
                        "; ".join(objections) or "none on file",
                        "; ".join(s.get("summary", "") for s in thread_summaries) or "none",
                        "; ".join(mentions) or "none"),
        timeout=100, feature="plan")


def _build_interview_card(title: str, start: str) -> str | None:
    job = _matching_job(title)
    sb = star_bank()
    blurb = "Full-stack marketer/operator, ~6 yrs. SEO, WordPress/web, Google Ads + Analytics, paid media, CRO, marketing automation, ops/leadership. Remote."
    try:
        import jobs as jobs_mod
        p = jobs_mod.load_profile()
        if p:
            blurb = (f"{p.get('current_title', blurb)}, ~{p.get('years_experience', 6)} yrs. "
                    f"Remote. Salary target {p.get('salary_expectation', '[SALARY_ANCHOR]')}.")
    except Exception:  # noqa: BLE001
        pass
    cli = planner._find_claude_cli()
    prompt = INTERVIEW_PROMPT % (title, start, json.dumps(job) if job else "none", blurb, sb or "none on file")
    if not cli:
        # fall back to the normal (non-WebSearch) CLI path if we can't locate
        # the binary directly — still produces a card, just without live research
        return planner._cli(prompt, timeout=100, feature="plan")
    try:
        out = subprocess.run(
            ["perl", "-e", "alarm 115; exec @ARGV", cli, "-p", prompt,
             "--model", "claude-sonnet-4-6", "--allowedTools", "WebSearch"],
            capture_output=True, text=True, timeout=130, cwd="/tmp").stdout
        return (out or "").strip() or None
    except Exception:  # noqa: BLE001
        return planner._cli(prompt, timeout=100, feature="plan")  # last-resort fallback


def build_cards(events: list[dict]) -> list[dict]:
    upcoming = _upcoming(events)
    covered = _already_prepped()
    out = []
    for e in upcoming:
        key = e.get("when") or e.get("text") or ""
        if not key or key in covered:
            continue
        title = e.get("text") or "(untitled)"
        start = e.get("when") or ""
        kind = classify_event(title)
        if kind == "sales":
            card = _build_sales_card(title, start)
        elif kind == "interview":
            card = _build_interview_card(title, start)
        else:
            contact = _matching_contact(title)
            mentions = _feed_mentions(title)
            card = planner._cli(
                GENERIC_PROMPT % (title, start, json.dumps(contact) if contact else "none",
                                  "; ".join(mentions) or "none"),
                timeout=90, feature="plan")
        card = (card or "").strip()
        if not card:
            continue
        out.append({"event": key, "start": start, "title": title, "kind": kind,
                    "card": card, "ts": now_iso()})
        covered.add(key)
    return out


def _run() -> int:
    try:
        events = _get("/api/gcal").get("events", [])
    except Exception as e:  # noqa: BLE001
        print(f"meeting_prep: /api/gcal unavailable ({e})")
        return 0
    if not events:
        print("meeting_prep: /api/gcal returned no events")
        return 0
    cards = build_cards(events)
    if not cards:
        print("meeting_prep: no new events in the next 36h need a prep card")
        return 0
    PREP_CARDS.parent.mkdir(parents=True, exist_ok=True)
    with PREP_CARDS.open("a") as f:
        for rec in cards:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    for rec in cards:
        planner.feed_add("agent", f"Prep card ready: {rec['title'][:60]}")
    print(f"meeting_prep: built {len(cards)} prep card(s) -> {PREP_CARDS}")
    return 0


def main() -> int:
    with track("meeting_prep"):  # E353: runlog adoption
        return _run()


if __name__ == "__main__":
    from runlog import track
    with track("meeting_prep"):
        raise SystemExit(main())
