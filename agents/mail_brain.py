#!/usr/bin/env python3
"""B118/#2: the email classifier core. One pass over new messages (from
mail_sync.peek(), R2-46: cursor advances only after this run actually classifies
them, see run()) -> {lane, why, response_needed, deadline?, entities} per
message, written id-keyed to
store/mail_triage.jsonl. Everything else in the B-fleet (drafts, digest, hygiene report,
unsubscribe candidates) reads this store rather than re-classifying.

Folds in the detectors that don't need real volume to build (B121-140), computed
DETERMINISTICALLY before the LLM call (regex/config lookups, not another model call —
cheaper, and more reliable than asking an LLM to spot a legal keyword):
  - B123 tone hint (rough heuristic word list; LLM does the real read)
  - B124 deadline extraction handed to the LLM (needs language understanding)
  - B125 intro-email hint ("introducing" pattern)
  - B126 recruiter/ATS lane routing (same domain list job_replies.py already trusts)
  - B130 duplicate-thread collapse (same normalized subject within the batch)
  - B133 legal/notice keyword escalation
  - B134 client watchlist match (store/mail_watchlist.json)
  - B84  VIP match (store/mail_vip.json)
  - B85  newsletter heuristic (List-Unsubscribe header / common bulk senders)

Lanes: vip | response_needed | business | jobs | receipts | newsletter | noise

Model: planner._cli_json with feature="plan" (routes to Haiku per B118 + config.json's
models.plan, batch of 20 messages/call per B118's explicit ask).

READ-ONLY against Gmail except label writes, and those are scoped to brain/* only
(via gmail_api.apply_label, called only when --apply-labels is passed — off by default
so a first run never mutates the mailbox without an explicit flag).

Run:  .venv/bin/python agents/mail_brain.py                  # classify pass, print counts
      .venv/bin/python agents/mail_brain.py --limit 30        # cap messages this run
      .venv/bin/python agents/mail_brain.py --apply-labels    # also write brain/<lane> labels
      .venv/bin/python agents/mail_brain.py --fixture         # classify a few sample dicts,
                                                                 no Gmail calls (for tests)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", Path.home() / "Claude" / "gmail"):
    sys.path.insert(0, str(p))
from store_lib import now_iso, _flock  # noqa: E402
import planner  # noqa: E402
import gmail_api  # noqa: E402
import mail_sync  # noqa: E402
import mail_sender_scores  # noqa: E402
from runlog import track  # noqa: E402

TRIAGE = ROOT / "store" / "mail_triage.jsonl"
VIP_CFG = ROOT / "store" / "mail_vip.json"
WATCHLIST_CFG = ROOT / "store" / "mail_watchlist.json"

LANES = ("vip", "response_needed", "business", "jobs", "receipts", "newsletter", "noise")
BATCH_SIZE = 20  # B118: batch of 20 per LLM call

# Same ATS domains job_replies.py already trusts (B126: feed jobs system, not comms).
# Duplicated intentionally rather than imported — job_replies.py is jobs-fleet-owned
# and this module must not create a coupling that breaks if they refactor their file.
ATS_DOMAINS = ("ashbyhq.com", "hire.lever.co", "greenhouse.io", "workablemail.com",
               "rippling.com", "careerplug.com", "jazz.co", "breezy.hr",
               "applytojob.com", "myworkday.com")

LEGAL_KEYWORDS = ("lawsuit", "cease and desist", "chargeback", "subpoena", "legal notice",
                   "attorney", "litigation", "small claims", "dispute filed", "arbitration")

RECEIPT_HINTS = ("invoice", "receipt", "payment received", "your order", "subscription renew",
                  "billing statement", "payment confirmation")

NEWSLETTER_HINTS = ("unsubscribe", "view in browser", "you're receiving this because",
                     "manage your preferences")

# B91: obvious cold pitches AT him, classified out into a monthly-check pile rather
# than mixed into the business lane with real client/vendor mail. This is a FLAG, not
# a lane, so the existing 7-lane taxonomy (relied on elsewhere: mail_digest.py's
# ITEM_SECTIONS, mail_hygiene.py's jobs-lane exclusion) never has to change shape —
# a "sales pitches" view is just `[r for r in triage if r["sales_pitch"]]`.
SALES_PITCH_HINTS = ("boost your", "grow your business", "increase your leads",
                      "guaranteed results", "book a call", "free audit", "free consultation",
                      "reply rates", "cold email", "lead generation", "% off", "limited time offer",
                      "our services can help", "i noticed your website", "i came across your")

_ADDR_RE = re.compile(r"<([^>]+)>")
_INTRO_RE = re.compile(r"\bintroduc(?:e|ing|es)\b.{0,40}\bto\b", re.I)


def _extract_email(addr: str) -> str:
    m = _ADDR_RE.search(addr or "")
    return (m.group(1) if m else (addr or "")).lower().strip()


def _extract_domain(email: str) -> str:
    return email.split("@")[-1].lower() if "@" in email else ""


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _vip_emails() -> set[str]:
    cfg = _load_json(VIP_CFG, {"senders": []})
    return {s.get("email", "").lower() for s in cfg.get("senders", []) if s.get("email")}


def _watchlist_names() -> list[str]:
    cfg = _load_json(WATCHLIST_CFG, {"client_names": []})
    return [n.lower() for n in cfg.get("client_names", []) if n]


def _already_triaged() -> set[str]:
    """id-keyed last-write-wins read, matching store_lib's JSONL convention."""
    if not TRIAGE.exists():
        return set()
    ids = set()
    for line in TRIAGE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ids.add(json.loads(line)["id"])
        except (json.JSONDecodeError, KeyError):
            continue
    return ids


def _append_triage_batch(recs: list[dict]) -> list[dict]:
    """Append one classify batch under store_lib._flock, re-checking already-triaged
    ids INSIDE the lock. The run-start check goes stale across the minutes-long LLM
    batch calls, so a concurrent run (server rerun + 6:30 cron) could double-append
    the same message (same jsonl race that produced a live duplicate draft, 2026-07
    P0). One lock + one id re-read per batch, not per record, keeps this O(batches).
    Returns the records actually written."""
    TRIAGE.parent.mkdir(parents=True, exist_ok=True)
    with _flock(TRIAGE):
        done = _already_triaged()
        written = [r for r in recs if r["id"] not in done]
        if written:
            with TRIAGE.open("a") as f:
                for rec in written:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return written


def _to_iso(raw) -> str:
    """D4: normalize the message date to ISO 8601 at write time. Gmail hands back
    RFC 2822 Date headers (and internalDate is epoch milliseconds); the triage
    store used to carry those raw, so every consumer grew its own parser and
    server.py's string sort on `date` was meaningless. New rows are ISO; readers
    keep accepting the legacy formats (mail_signals._parse_date). Unparseable
    input passes through untouched, never destroyed."""
    s = str(raw or "").strip()
    if not s:
        return ""
    if s.isdigit():  # Gmail internalDate: epoch milliseconds
        try:
            return datetime.fromtimestamp(int(s) / 1000.0, tz=timezone.utc).isoformat()
        except (ValueError, OSError, OverflowError):
            return s
    try:  # already ISO: normalized passthrough
        return datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()
    except ValueError:
        pass
    try:  # RFC 2822 header form
        dt = parsedate_to_datetime(s)
        if dt is None:
            return s
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (TypeError, ValueError):
        return s


def _normalized_subject(subject: str) -> str:
    """Strip Re:/Fwd:/Fw: prefixes + collapse whitespace, for B130 duplicate detection."""
    s = re.sub(r"(?i)^\s*(re|fwd?|fw)\s*:\s*", "", subject or "")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _deterministic_hints(msg: dict, vip: set[str], watchlist: list[str], seen_subjects: dict) -> dict:
    """Pre-classification signal computed with regex/config lookups, no LLM call.
    Returned dict is folded into the prompt as context AND used as a hard override
    for lanes the LLM shouldn't be trusted to get wrong (ATS domain -> jobs is a sender
    fact, not a judgment call)."""
    email = _extract_email(msg.get("from", ""))
    domain = _extract_domain(email)
    subject = msg.get("subject", "") or ""
    snippet = (msg.get("snippet", "") or "")
    body_lo = (snippet + " " + subject).lower()

    norm_subj = _normalized_subject(subject)
    is_dup = norm_subj in seen_subjects and norm_subj != ""
    seen_subjects.setdefault(norm_subj, 0)
    seen_subjects[norm_subj] += 1

    sales_pitch_hits = sum(1 for h in SALES_PITCH_HINTS if h in body_lo)

    return {
        "is_vip": email in vip,
        "is_ats": any(d in domain for d in ATS_DOMAINS),
        "watchlist_hit": next((n for n in watchlist if n in body_lo), None),
        "has_legal_kw": next((k for k in LEGAL_KEYWORDS if k in body_lo), None),
        "looks_receipt": any(h in body_lo for h in RECEIPT_HINTS),
        "looks_newsletter": any(h in body_lo for h in NEWSLETTER_HINTS),
        "looks_intro": bool(_INTRO_RE.search(snippet or "")),
        "is_dup_subject": is_dup,
        "norm_subject": norm_subj,
        "sender_score": mail_sender_scores.get_score(email),
        "sender_email": email,
        "sales_pitch_hits": sales_pitch_hits,
    }


# 5 GOOD few-shot examples (B115), hand-written from generic patterns since real
# override history doesn't exist yet — SEEDS. Once store/mail_triage.jsonl accumulates
# enough of [OWNER]'s actual corrections (moved-lane = signal, per E117/B116), a future
# pass should replace these with real examples mined from his overrides.
FEW_SHOT = """EXAMPLES (seed patterns, not real mail):
1. From: a real client asking "can you resend the invoice from last week?" -> lane=response_needed, why="direct question needing an answer", response_needed=true, entities=["invoice"]
2. From: noreply@ashbyhq.com, subject "Your application was received" -> lane=jobs, why="automated ATS confirmation", response_needed=false
3. From: a newsletter with List-Unsubscribe header, generic marketing content -> lane=newsletter, why="bulk newsletter, no personal ask", response_needed=false
4. From: a cold agency pitching SEO services unsolicited -> lane=business, why="unsolicited sales pitch, not urgent", response_needed=false
5. From: PayPal, subject "Payment received - $500.00" -> lane=receipts, why="automated payment receipt", response_needed=false
"""

CLASSIFY_PROMPT = """Classify each email in [OWNER]'s inbox ([OWNER_COMPANY] — white-label
web builds + agency ops for agencies). For each, return an object with:
  lane: one of vip, response_needed, business, jobs, receipts, newsletter, noise
  why: one short phrase (under 12 words)
  response_needed: true/false — does [OWNER] personally need to reply?
  deadline: a short phrase like "by Friday" / "EOD today" if the email states or implies
            one, else null
  entities: short list of key nouns (company names, amounts, products) mentioned, else []

Lane guide:
  vip = from someone on his VIP list (already flagged in HINTS below)
  response_needed = a real person asking him something / needs his reply, not automated
  business = client/prospect/vendor business mail that isn't urgent
  jobs = job-application-related (ATS confirmations, recruiter outreach, interview mail)
  receipts = payment/invoice/subscription confirmations, no action needed
  newsletter = bulk/marketing mail he's subscribed to
  noise = spam, irrelevant, nothing to do

%s

Return ONLY a JSON array, same order as input, one object per email:
[{"lane":"...","why":"...","response_needed":true,"deadline":null,"entities":[]}]

EMAILS (each includes deterministic HINTS your classification should respect,
especially is_vip/is_ats/has_legal_kw which are sender/keyword FACTS, not guesses):
%s"""


def _fixture_messages() -> list[dict]:
    """A few representative fake messages for --fixture mode (no Gmail calls)."""
    return [
        {"id": "fx1", "from": "client@example.com", "subject": "Can you resend the invoice?",
         "snippet": "Hey, can you resend the invoice from last week? Need it for my books.",
         "date": now_iso()},
        {"id": "fx2", "from": "noreply@ashbyhq.com", "subject": "Your application was received",
         "snippet": "Thanks for applying to Acme Corp. We'll be in touch.", "date": now_iso()},
        {"id": "fx3", "from": "news@somenewsletter.com", "subject": "This week's roundup",
         "snippet": "unsubscribe here | view in browser", "date": now_iso()},
        {"id": "fx4", "from": "attorney@lawfirm.com", "subject": "Cease and desist notice",
         "snippet": "This is a legal notice regarding a dispute filed against your company.",
         "date": now_iso()},
        {"id": "fx5", "from": "service@paypal.com", "subject": "Payment received - $500.00",
         "snippet": "You received a payment of $500.00.", "date": now_iso()},
    ]


def classify_batch(messages: list[dict]) -> list[dict]:
    """messages: list of metadata dicts (id/from/subject/snippet/date at minimum).
    Returns one triage record per message, in the same order, ALWAYS (falls back to
    a safe 'business'/response_needed=false record on any LLM/parse failure so one
    bad batch never drops messages silently — B120 error isolation)."""
    vip = _vip_emails()
    watchlist = _watchlist_names()
    seen_subjects: dict[str, int] = {}

    hints = [_deterministic_hints(m, vip, watchlist, seen_subjects) for m in messages]

    listing_lines = []
    for i, (m, h) in enumerate(zip(messages, hints)):
        listing_lines.append(
            f"{i}. From: {m.get('from','')[:60]} | Subject: {m.get('subject','')[:80]} | "
            f"Snippet: {(m.get('snippet','') or '')[:140]} | "
            f"HINTS: is_vip={h['is_vip']} is_ats={h['is_ats']} "
            f"has_legal_kw={h['has_legal_kw'] or 'none'} looks_receipt={h['looks_receipt']} "
            f"looks_newsletter={h['looks_newsletter']} sender_score={h['sender_score']}"
        )
    listing = "\n".join(listing_lines)

    raw = planner._cli_json(CLASSIFY_PROMPT % (FEW_SHOT, listing), timeout=150, feature="plan")
    llm_results = raw if isinstance(raw, list) else []

    out = []
    for i, (m, h) in enumerate(zip(messages, hints)):
        r = llm_results[i] if i < len(llm_results) and isinstance(llm_results[i], dict) else {}
        lane = r.get("lane") if r.get("lane") in LANES else "business"

        # Deterministic overrides: sender/keyword FACTS beat the LLM's judgment call.
        if h["is_ats"]:
            lane = "jobs"
        if h["has_legal_kw"]:
            lane = "response_needed"  # legal notices always need his eyes, never auto-filed
        if h["is_vip"] and lane not in ("receipts",):
            lane = "vip"
        if h["watchlist_hit"] and lane not in ("receipts", "jobs"):
            lane = "vip"  # active-client mention surfaces same as VIP (B134)

        why = r.get("why", "") or ("legal keyword detected" if h["has_legal_kw"] else
                                     "ATS sender" if h["is_ats"] else "auto-fallback classify")
        # B91: keyword hits OR the LLM's own "why" naming it a pitch (its judgment call
        # on tone/intent often catches phrasing the keyword list doesn't) -> flagged,
        # never auto-elevated to response_needed even if phrased as a question.
        # Threshold is >=1, not >=2: real testing against a live cold pitch (leadgenjay.com,
        # subject "5X your reply rates with these 2 tricks") showed body/snippet content is
        # often junk (tracking-pixel whitespace) with the real signal living ONLY in the
        # subject line, so a single strong-phrase hit is the honest bar, not two.
        pitch_language = h["sales_pitch_hits"] >= 1 or "sales pitch" in why.lower() or "unsolicited" in why.lower()
        # D4 decouple: sender standing and content class are INDEPENDENT axes.
        # pitch_language records the content read on its own; sales_pitch (the
        # monthly-check/archive bucket AND the response_needed suppressor below)
        # never fires for a VIP or watchlist-elevated sender: a known client
        # writing "book a call" or "free audit" is talking business, not cold
        # pitching, and a VIP's mail must never get buried by content phrasing.
        sales_pitch = pitch_language and not h["is_vip"] and lane != "vip"

        rec = {
            "id": m["id"],
            "thread_id": m.get("threadId", ""),
            "from": m.get("from", ""),
            "sender_email": h["sender_email"],
            "subject": m.get("subject", ""),
            "date": _to_iso(m.get("date", "")),  # D4: ISO at write time, legacy readable
            "lane": lane,
            "why": why,
            "response_needed": (bool(r.get("response_needed", False)) or bool(h["has_legal_kw"])) and not sales_pitch,
            "deadline": r.get("deadline"),
            "entities": r.get("entities", []) if isinstance(r.get("entities"), list) else [],
            "tone_flag": None,  # B123: filled by a keyword pass below, LLM doesn't score tone reliably at Haiku tier
            "legal_flag": bool(h["has_legal_kw"]),
            "watchlist_hit": h["watchlist_hit"],
            "intro_email": h["looks_intro"],
            "dup_subject": h["is_dup_subject"],
            "sender_score": h["sender_score"],
            "pitch_language": pitch_language,  # D4: raw content axis, independent of sender standing
            "sales_pitch": sales_pitch,  # B91: filter store/mail_triage.jsonl on this for the monthly-check pile (never true for VIP senders)
            "classified_at": now_iso(),
        }
        out.append(rec)
    return out


ANGRY_WORDS = ("unacceptable", "furious", "disappointed", "ridiculous", "worst", "terrible",
               "refund now", "waste of", "never again", "scam")


def _tone_flag(subject: str, snippet: str) -> str | None:
    """B123: cheap keyword heuristic for angry/hot tone (the LLM call already ran;
    this is a zero-cost second pass over text already in memory, not a second call)."""
    text = f"{subject} {snippet}".lower()
    hits = sum(1 for w in ANGRY_WORDS if w in text)
    if hits >= 2:
        return "hot"
    if hits == 1:
        return "warm"
    return None


def run(limit: int = 100, fixture: bool = False, apply_labels: bool = False) -> dict:
    if fixture:
        messages = _fixture_messages()
    else:
        # R2-46: peek(), don't sync() -- sync() would ack the Gmail cursor for the
        # WHOLE delta immediately, before the [:limit] slice below is even fetched,
        # let alone classified. `full` tracks whether this run's `ids` covers the
        # entire delta; the cursor only advances (below) when it does, so a
        # truncated remainder stays re-fetchable next run instead of being
        # permanently acked. peek() is idempotent against the old cursor, and
        # re-seeing an id already written is a no-op (_already_triaged() below).
        delta = mail_sync.peek()
        ids = delta["message_ids"][:limit]
        full = len(ids) >= len(delta["message_ids"])
        if not ids:
            if full:  # genuinely nothing new, not a truncation -- safe to advance
                mail_sync.advance_cursor(delta)
            return {"classified": 0, "lanes": {}, "mode": delta["mode"]}
        messages = gmail_api.get_messages_metadata(ids, fields=("From", "Subject", "Date"))

    done = _already_triaged()
    fresh = [m for m in messages if m["id"] not in done]
    if not fresh:
        if not fixture and full:
            mail_sync.advance_cursor(delta)
        return {"classified": 0, "lanes": {}, "mode": "fixture" if fixture else "already_triaged"}

    all_recs = []
    for i in range(0, len(fresh), BATCH_SIZE):
        batch = fresh[i:i + BATCH_SIZE]
        recs = classify_batch(batch)
        for rec in recs:
            rec["tone_flag"] = _tone_flag(rec["subject"], "")
        # only records that actually landed count / get labels — a concurrent run
        # that beat us to an id owns it.
        all_recs.extend(_append_triage_batch(recs))

    # R2-46: every fetched id was actually run through classify_batch above (a
    # per-batch LLM/parse failure still yields a safe fallback record per B120,
    # never a silent skip) -- only NOW is it safe to ack the cursor, and only up
    # to what this run actually covered (`full`).
    if not fixture and full:
        mail_sync.advance_cursor(delta)

    lane_counts: dict[str, int] = {}
    for r in all_recs:
        lane_counts[r["lane"]] = lane_counts.get(r["lane"], 0) + 1

    if apply_labels:
        by_lane: dict[str, list[str]] = {}
        for r in all_recs:
            by_lane.setdefault(r["lane"], []).append(r["id"])
        for lane, lane_ids in by_lane.items():
            gmail_api.apply_labels_batch(lane_ids, f"brain/{lane}")

    return {"classified": len(all_recs), "lanes": lane_counts,
            "mode": "fixture" if fixture else "live"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--limit", type=int, default=100, help="max messages this run")
    ap.add_argument("--apply-labels", action="store_true", help="write brain/<lane> Gmail labels")
    ap.add_argument("--fixture", action="store_true", help="classify sample dicts, no Gmail calls")
    args = ap.parse_args()

    with track("mail_brain"):
        result = run(limit=args.limit, fixture=args.fixture, apply_labels=args.apply_labels)

    print(f"mail_brain: classified {result['classified']} message(s), mode={result['mode']}")
    for lane in LANES:
        n = result["lanes"].get(lane, 0)
        if n:
            print(f"  {lane:<17} {n}")
    if result["classified"]:
        planner.feed_add("agent", f"Mail classified: {result['classified']} message(s)",
                          ", ".join(f"{k}={v}" for k, v in result["lanes"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
