#!/usr/bin/env python3
"""B88 + B89 + B90 + B97: content-signal detectors over already-classified mail.
Reads store/mail_triage.jsonl (never re-hits Gmail search), fetches full bodies only
for candidates that clear a cheap first filter.

B88 attachment intelligence: has:attachment mail with invoice/contract/csv signals ->
  routed to store/mail_attachment_suggestions.jsonl {id,kind:"invoice"|"contract"|"csv",
  note}. Invoice-flavored -> a finance-note SUGGESTION (never appends to store/ledger.jsonl
  directly — that store is server.py-owned; this only proposes, its owner's UI/click
  decides whether to promote a suggestion into a real ledger entry).

B89 meeting-request detection -> store/mail_meeting_suggestions.jsonl
  {id,from,subject,note} — a "draft a calendar event" suggestion, not an actual
  calendar write (no calendar API touched anywhere in this fleet).

B90 payment/receipt detection -> store/mail_ledger_suggestions.jsonl
  {id,from,subject,amount,note,status:"suggested"} — same suggestions-only contract
  as B88's invoice note. Amount is best-effort regex extraction from the subject/snippet
  (a $ or currency-coded figure), null if none found; never guessed.

B97 bulk-archive suggestions: 30d-old items in the noise/newsletter lanes, clustered
  by sender, -> store/mail_archive_suggestions.jsonl {sender,count,oldest,newest,
  sample_subjects}. SUGGESTIONS ONLY (per the mission's explicit rail: "auto-archive =
  suggestions list only") — no archive/label-remove call exists anywhere in this
  module or gmail_api.py.

READ-ONLY against Gmail (only get_message() body fetches for pre-filtered candidates).
No sends, no archives, no label writes.

Run:  .venv/bin/python agents/mail_signals.py             # all four passes, real data
      .venv/bin/python agents/mail_signals.py --fixture    # sample rows, no Gmail calls
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", Path.home() / "Claude" / "gmail"):
    sys.path.insert(0, str(p))
from store_lib import now_iso, _flock  # noqa: E402
import planner  # noqa: E402
import gmail_api  # noqa: E402
from runlog import track  # noqa: E402

TRIAGE = ROOT / "store" / "mail_triage.jsonl"
ATTACH_OUT = ROOT / "store" / "mail_attachment_suggestions.jsonl"
MEETING_OUT = ROOT / "store" / "mail_meeting_suggestions.jsonl"
LEDGER_OUT = ROOT / "store" / "mail_ledger_suggestions.jsonl"
ARCHIVE_OUT = ROOT / "store" / "mail_archive_suggestions.jsonl"

ARCHIVE_AGE_DAYS = 30
ARCHIVE_MIN_CLUSTER = 3  # only suggest a cluster, not a one-off (B97: "clusters")

# Word-boundary matched (real bug found+fixed: bare "nda"/"terms" false-matched inside
# "standard"/generic "terms & conditions" marketing-footer boilerplate on live mail —
# see tests/test_mail_signals.py for the regression cases). "bill"/"terms" dropped
# entirely: too generic even with word boundaries ("bill" hits "billing", "terms" hits
# footer boilerplate on nearly every commercial email).
INVOICE_HINTS = ("invoice", "statement", "amount due")
CONTRACT_HINTS = ("agreement", "contract", "\\bnda\\b", "sow", "scope of work")
CSV_ATTACHMENT_EXTS = (".csv", ".xlsx", ".xls", ".tsv")
DOC_ATTACHMENT_EXTS = (".pdf", ".doc", ".docx", ".pages")

MEETING_HINTS = ("schedule a call", "schedule a meeting", "book a time", "book a call",
                  "available for a", "set up a meeting", "set up a call", "calendly.com",
                  "calendar.google.com", "find a time", "grab 15 min", "grab a time",
                  "hop on a call")

PAYMENT_HINTS = ("payment received", "receipt", "you received a payment", "money added",
                  "transfer sent", "transfer received", "your receipt", "paid invoice",
                  "subscription renewed", "charged")

# D4 P2 fix: the old pattern required exactly two cent digits ("$500.5" truncated
# to 500) and had no k-shorthand ("$12k" parsed as 12.0, "1.5k" missed entirely).
# Three alternatives: $-prefixed, USD/dollars-suffixed, and bare k-shorthand.
# "401k"/"401(k)" is retirement-plan boilerplate, never a payment amount, so the
# bare-k form skips it explicitly ($-prefixed "$401k" still parses: the $ is a
# strong money signal).
_AMOUNT_RE = re.compile(
    r"(?:USD\s?)?\$\s?([\d,]+(?:\.\d+)?)\s?([kK]\b)?"
    r"|([\d,]+(?:\.\d+)?)\s?([kK]\b)?\s?(?:USD|dollars)"
    r"|(?<![\d.$])(?!401[kK]\b)([\d,]+(?:\.\d+)?)([kK])\b",
    re.I,
)


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


def _append(path: Path, rec: dict, key: str = "id") -> bool:
    """Append under store_lib._flock, repeating the duplicate check on `key` INSIDE
    the lock. Each detector computes its 'done' set up front then spends time in
    per-message Gmail body fetches before writing, so the up-front check goes stale
    under concurrent runs (same race that produced a live duplicate mail draft,
    2026-07 P0). Returns False on duplicate."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _flock(path):
        if rec.get(key) in {r.get(key) for r in _read_jsonl(path)}:
            return False
        with path.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def _already(path: Path, key: str = "id") -> set:
    return {r[key] for r in _read_jsonl(path) if r.get(key)}


def _extract_amount(text: str) -> float | None:
    """Best-effort money amount from subject/snippet text. Handles "$1,234.56",
    "$ 500", "500.00 USD", "500 dollars", and k-shorthand ("$12k" -> 12000,
    "1.5k" -> 1500). Returns None when nothing money-shaped is present, never
    a guess (this feeds ledger SUGGESTIONS, so a wrong number is worse than none)."""
    m = _AMOUNT_RE.search(text or "")
    if not m:
        return None
    num, k = next(((m.group(i), m.group(i + 1)) for i in (1, 3, 5) if m.group(i)),
                  (None, None))
    if not num:
        return None
    try:
        val = float(num.replace(",", ""))
    except ValueError:
        return None
    return val * 1000 if k else val


# --- B88: attachment intelligence -------------------------------------------

_INVOICE_RE = re.compile(r"\b(?:" + "|".join(re.escape(h) for h in INVOICE_HINTS) + r")\b", re.I)
_CONTRACT_RE = re.compile(r"\b(?:" + "|".join(CONTRACT_HINTS) + r")\b", re.I)


def detect_attachments(fixture: bool = False) -> int:
    """Gates on REAL attachment presence first (real bug found+fixed: an earlier
    version keyword-matched email BODY text with no attachment-presence check at all,
    flagging plain marketing newsletters as "invoice-flavored attachments" just because
    boilerplate footer text like "terms & conditions" happened to appear). Attachment
    FILENAME extension is the primary signal (cheap, reliable); body language is only
    used to pick invoice vs contract for a doc-type attachment, never to invent an
    attachment that isn't there."""
    done = _already(ATTACH_OUT)
    if fixture:
        rows = [{"id": "fxa1", "subject": "Your invoice #4521", "from": "billing@vendor.com",
                  "_attachments": [{"filename": "invoice_4521.pdf"}],
                  "_body": "Attached is your invoice for this month's services.", "lane": "receipts"}]
    else:
        rows = [r for r in _read_jsonl(TRIAGE) if r.get("id") not in done]

    n = 0
    for r in rows:
        attachments = r.get("_attachments")
        text = r.get("_body")
        if attachments is None:
            if fixture:
                continue
            try:
                full = gmail_api.get_message(r["id"])
                attachments = full.get("attachments", [])
                text = (full.get("subject", "") + " " + full.get("body", ""))
            except Exception:
                continue
        if not attachments:
            continue  # no real attachment on this message -> nothing to suggest

        filenames_lo = " ".join(a.get("filename", "") for a in attachments).lower()
        lo = (text or "").lower()
        kind = None
        if any(filenames_lo.endswith(ext) for ext in CSV_ATTACHMENT_EXTS):
            kind = "csv"
        elif _INVOICE_RE.search(lo) or _INVOICE_RE.search(filenames_lo):
            kind = "invoice"
        elif _CONTRACT_RE.search(lo) or _CONTRACT_RE.search(filenames_lo):
            kind = "contract"
        elif any(filenames_lo.endswith(ext) for ext in DOC_ATTACHMENT_EXTS):
            kind = "document"  # has a real doc attachment, no stronger signal on type
        if not kind:
            continue
        note = {"invoice": "possible finance note (invoice-flavored attachment)",
                 "contract": "possible legal/contract doc, worth a filed copy",
                 "csv": "data export attached, possible data-drop candidate",
                 "document": "document attachment, no specific type signal"}[kind]
        if _append(ATTACH_OUT, {"id": r["id"], "from": r.get("sender_email", r.get("from", "")),
                                 "subject": r.get("subject", ""), "kind": kind,
                                 "attachment_names": [a.get("filename", "") for a in attachments],
                                 "note": note, "created": now_iso()}):
            n += 1
    return n


# --- B89: meeting-request detection -----------------------------------------

def detect_meetings(fixture: bool = False) -> int:
    done = _already(MEETING_OUT)
    if fixture:
        rows = [{"id": "fxm1", "subject": "Quick call?", "from": "prospect@example.com",
                  "_body": "Can we schedule a call this week to discuss?", "lane": "response_needed"}]
    else:
        rows = [r for r in _read_jsonl(TRIAGE)
                if r.get("id") not in done and r.get("lane") != "noise"]

    n = 0
    for r in rows:
        text = r.get("_body")
        if text is None:
            if fixture:
                continue
            try:
                full = gmail_api.get_message(r["id"])
                text = (full.get("subject", "") + " " + full.get("body", ""))
            except Exception:
                continue
        lo = text.lower()
        hit = next((h for h in MEETING_HINTS if h in lo), None)
        if not hit:
            continue
        if _append(MEETING_OUT, {"id": r["id"], "from": r.get("sender_email", r.get("from", "")),
                                  "subject": r.get("subject", ""),
                                  "note": f"meeting-request language detected ({hit!r})",
                                  "created": now_iso()}):
            n += 1
    return n


# --- B90: payment/receipt -> ledger suggestion ------------------------------

def detect_payments(fixture: bool = False) -> int:
    done = _already(LEDGER_OUT)
    if fixture:
        rows = [{"id": "fxp1", "subject": "Payment received - $500.00", "from": "service@paypal.com",
                  "_body": "You received a payment of $500.00.", "lane": "receipts"}]
    else:
        rows = [r for r in _read_jsonl(TRIAGE) if r.get("id") not in done and r.get("lane") == "receipts"]

    n = 0
    for r in rows:
        text = r.get("_body")
        if text is None:
            if fixture:
                continue
            try:
                full = gmail_api.get_message(r["id"])
                text = (full.get("subject", "") + " " + full.get("body", ""))
            except Exception:
                continue
        lo = text.lower()
        if not any(h in lo for h in PAYMENT_HINTS):
            continue
        amount = _extract_amount(text)
        if _append(LEDGER_OUT, {"id": r["id"], "from": r.get("sender_email", r.get("from", "")),
                                 "subject": r.get("subject", ""), "amount": amount,
                                 "note": "payment/receipt detected, review before adding to ledger",
                                 "status": "suggested", "created": now_iso()}):
            n += 1
    return n


# --- B97: bulk-archive suggestions ------------------------------------------

def _parse_date(date_str) -> datetime | None:
    """Triage `date` values across store generations (D4): new rows are ISO
    (mail_brain._to_iso normalizes at write time), legacy rows are raw RFC 2822
    Date headers, and Gmail internalDate epoch-milliseconds may appear in older
    stores. Accept all three; None for anything unparseable."""
    from email.utils import parsedate_to_datetime
    s = str(date_str or "").strip()
    if not s:
        return None
    if s.isdigit():  # Gmail internalDate: epoch milliseconds
        try:
            return datetime.fromtimestamp(int(s) / 1000.0, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    try:  # ISO 8601 (the normalized write-time format)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:  # legacy RFC 2822 header form
        dt = parsedate_to_datetime(s)
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def suggest_archives(fixture: bool = False) -> int:
    if fixture:
        rows = [
            {"sender_email": "promo@shop.com", "subject": "Sale!", "lane": "newsletter",
             "date": "Mon, 01 Jun 2026 10:00:00 +0000"},
            {"sender_email": "promo@shop.com", "subject": "Bigger Sale!", "lane": "newsletter",
             "date": "Tue, 02 Jun 2026 10:00:00 +0000"},
            {"sender_email": "promo@shop.com", "subject": "Last Chance!", "lane": "newsletter",
             "date": "Wed, 03 Jun 2026 10:00:00 +0000"},
        ]
    else:
        rows = _read_jsonl(TRIAGE)

    cutoff = datetime.now(timezone.utc) - timedelta(days=ARCHIVE_AGE_DAYS)
    by_sender = defaultdict(list)
    for r in rows:
        if r.get("lane") not in ("noise", "newsletter"):
            continue
        dt = _parse_date(r.get("date", ""))
        if not dt:
            continue
        if not fixture and dt > cutoff:
            continue  # real mode: only 30d+ old; fixture dates are deliberately old already
        by_sender[r.get("sender_email", "")].append((dt, r.get("subject", "")))

    existing = {r["sender"] for r in _read_jsonl(ARCHIVE_OUT)}
    n = 0
    for sender, items in by_sender.items():
        if not sender or len(items) < ARCHIVE_MIN_CLUSTER or sender in existing:
            continue
        items.sort()
        if _append(ARCHIVE_OUT, {
            "sender": sender,
            "count": len(items),
            "oldest": items[0][0].isoformat(),
            "newest": items[-1][0].isoformat(),
            "sample_subjects": [s for _, s in items[:3]],
            "created": now_iso(),
        }, key="sender"):
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fixture", action="store_true")
    args = ap.parse_args()

    with track("mail_signals"):
        att = detect_attachments(fixture=args.fixture)
        meet = detect_meetings(fixture=args.fixture)
        pay = detect_payments(fixture=args.fixture)
        arch = suggest_archives(fixture=args.fixture)

    print(f"mail_signals: attachments={att} meetings={meet} payments={pay} archive_clusters={arch}")
    if att + meet + pay + arch:
        planner.feed_add("agent", "Mail signals pass",
                          f"attach={att} meet={meet} pay={pay} archive={arch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
