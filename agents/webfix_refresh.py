#!/usr/bin/env python3
"""#174 enrichment refresh job: webfix cold-pipeline contacts enrolled 85+ days ago
with no reply on file get their site re-audited (qa.py) and a fresh "90 days later"
site_note DRAFTED. Nothing here writes to GHL.

HARD RAIL NOTE (read before touching this file): H174's original brief said "update
site_note" — but this mission's rails are explicit: "No GHL WRITES except: none at
all in this mission — everything you build READS GHL or writes LOCAL staging/reports."
A live PUT to the contact's Site Note custom field would be a GHL write, which the
rails forbid outright (the only writes this mission may make anywhere are the H161/162
knob-to-0 pause and local jsonl/CSV files). So: this file drafts the updated site_note
text and appends it to store/webfix_refresh_staging.jsonl (LOCAL staging, allowed) —
last-write-wins BY EMAIL, exactly the way cold_import.py's own pipeline records work.
Applying the staged text to the real GHL custom field is a separate, explicit,
write-permitted step for [OWNER] or a future mission to wire up; this file only
prepares it.

Pipeline: for each eligible contact -> fetch its `website` field directly off the GHL
contact record (confirmed live: this is where the real URL lives, not in any local
store) -> run qa.py against it via the second-brain venv (qa.py's own docstring says
to: "~/Claude/second-brain/.venv/bin/python qa.py <url>", macOS system python's TLS
stack fails on TLS-1.3-only hosts) -> turn the findings into a fresh, human "90 days
later" site_note line in the same voice cold_import.py's original hooks used ("your
homepage links to 3 blog posts that 404" style, not a raw report dump).

Usage:
  webfix_refresh.py                    # find eligible + stage fresh notes
  webfix_refresh.py --dry-run          # show what would be staged, write nothing
  webfix_refresh.py --min-age-days 85  # override the eligibility window
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso, humanize, LOCAL_TZ  # noqa: E402
import ghl_social  # noqa: E402
import planner  # noqa: E402

PIPELINE = ROOT / "store" / "cold_pipeline.jsonl"
REPLIES = ROOT / "store" / "replies.jsonl"
STAGING = ROOT / "store" / "webfix_refresh_staging.jsonl"
QA_PY = Path.home() / "Claude" / "elementor-recoder" / "qa.py"
VENV_PY = ROOT / ".venv" / "bin" / "python"
MIN_AGE_DAYS = 85


def _load_jsonl(path: Path) -> list[dict]:
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


def _already_staged_emails() -> set[str]:
    """last-write-wins by email means: has THIS email already got a staged note from
    a PRIOR run within the same eligibility cycle? We still allow re-staging once
    another 85 days pass (min-age gate handles that), this set is just for
    same-run/same-day de-dupe so a re-run today doesn't stage the same contact twice."""
    today = now_iso()[:10]
    return {r.get("email") for r in _load_jsonl(STAGING)
            if r.get("email") and (r.get("ts") or "")[:10] == today}


def _replied_contact_ids() -> set[str]:
    return {r.get("contact_id") for r in _load_jsonl(REPLIES) if r.get("contact_id")}


def eligible_contacts(min_age_days: int) -> list[dict]:
    """webfix cold_pipeline rows, status == enrolled, enrolled_ts older than the
    threshold, contact_id not present in replies.jsonl (no reply on file). Right now
    (2026-07-03) webfix_daily_enroll is still 0 and every webfix row in the real store
    is status=="staged" (never enrolled) -> this correctly returns nothing yet; that's
    accurate, not a bug. Fixture-tested against synthetic 90-day-old enrolled rows to
    prove the logic, see the mission status file."""
    cutoff = datetime.now(LOCAL_TZ) - timedelta(days=min_age_days)
    replied = _replied_contact_ids()
    out = []
    for r in _load_jsonl(PIPELINE):
        if (r.get("campaign") or "wl") != "webfix" or r.get("status") != "enrolled":
            continue
        if r.get("contact_id") in replied:
            continue
        try:
            ts = datetime.fromisoformat(r.get("enrolled_ts") or "")
        except (ValueError, TypeError):
            continue
        if ts > cutoff:
            continue
        out.append(r)
    return out


def _contact_website(contact_id: str) -> str:
    out = ghl_social._api(["GET", f"/contacts/{contact_id}"])
    try:
        j = json.loads(out[out.find("{"):])
    except (ValueError, json.JSONDecodeError):
        return ""
    return ((j.get("contact") or j).get("website") or "").strip()


def run_qa(url: str) -> tuple[str, dict]:
    """Runs qa.py via the second-brain venv (per qa.py's own docstring — macOS system
    python's TLS stack fails on TLS-1.3-only hosts). Returns (markdown_report, stats)."""
    if not url:
        return "", {}
    if not url.startswith("http"):
        url = "https://" + url
    try:
        r = subprocess.run([str(VENV_PY), str(QA_PY), url, "--max-pages", "12"],
                           capture_output=True, text=True, timeout=90)
        report = r.stdout or ""
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"qa.py failed: {e}", {}
    stats = {}
    m = re.search(r"pages (\d+).*?links (\d+).*?images (\d+).*?\*\*(\d+) FAIL / (\d+) WARN\*\*", report)
    if m:
        stats = {"pages": int(m.group(1)), "links": int(m.group(2)), "images": int(m.group(3)),
                 "fails": int(m.group(4)), "warns": int(m.group(5))}
    return report, stats


def _summarize_findings(report: str, max_lines: int = 3) -> list[str]:
    """Pull the top FAIL lines out of the qa.py markdown report and turn each into a
    short human clause, the same register as the original hooks
    ("your homepage links to 3 blog posts that 404")."""
    fails = [ln for ln in report.splitlines() if ln.strip().startswith("- **FAIL**")]
    clean = []
    for ln in fails[:max_lines]:
        # strip the markdown bold/backtick scaffolding, keep the human message
        msg = re.sub(r"^-\s*\*\*FAIL\*\*\s*`[^`]*`\s*", "", ln).strip()
        if msg:
            clean.append(msg)
    return clean


def draft_site_note(company: str, report: str, stats: dict) -> str:
    """'90 days later' framing — brutal but factual, VOICE-SPEC register (short,
    direct, numbers doing the talking, no throat-clearing)."""
    if not stats:
        return humanize(f"Went back to check {company}'s site 90 days later and couldn't load it at all.")
    findings = _summarize_findings(report)
    if stats.get("fails", 0) == 0:
        return humanize(f"Checked {company}'s site again, 90 days later. Clean this time, nice.")
    lead = findings[0] if findings else f"{stats.get('fails', 0)} broken things on the site"
    extra = f" and {stats['fails'] - 1} more" if stats.get("fails", 0) > 1 else ""
    return humanize(f"Went back to {company}'s site, 90 days later. Still {lead}{extra}.")


def _staged_row(pipeline_rec: dict, company: str, note: str, stats: dict, url: str) -> dict:
    return {"ts": now_iso(), "email": pipeline_rec.get("email", ""), "contact_id": pipeline_rec.get("contact_id", ""),
            "company": company, "url": url, "site_note_draft": note, "qa_stats": stats,
            "applied_to_ghl": False}


def run(dry: bool = False, min_age_days: int = MIN_AGE_DAYS) -> list[dict]:
    items = eligible_contacts(min_age_days)
    if not items:
        print(f"webfix_refresh: nothing eligible (no webfix enrollments {min_age_days}+ days old "
              "with no reply on file)")
        return []
    already = _already_staged_emails()
    staged = []
    for rec in items:
        email = (rec.get("email") or "").lower()
        if email in already:
            print(f"  {rec.get('company')}: already staged today, skipping")
            continue
        cid = rec.get("contact_id", "")
        url = _contact_website(cid) if cid else ""
        report, stats = run_qa(url)
        note = draft_site_note(rec.get("company", "the site"), report, stats)
        row = _staged_row(rec, rec.get("company", ""), note, stats, url)
        print(f"  {rec.get('company')}: {note}")
        if not dry:
            STAGING.parent.mkdir(parents=True, exist_ok=True)
            with STAGING.open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        staged.append(row)
    if staged and not dry:
        planner.feed_add("cold", f"{len(staged)} webfix site_note refresh(es) staged for review "
                                 "(NOT written to GHL — apply manually, this mission is read-only)")
    return staged


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-age-days", type=int, default=MIN_AGE_DAYS)
    args = ap.parse_args()
    run(dry=args.dry_run, min_age_days=args.min_age_days)
