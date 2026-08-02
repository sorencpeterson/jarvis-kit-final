#!/usr/bin/env python3
"""D235: answer-bank growth loop with an explicit approve gate.

agents/answer_bank.py (existing, NOT owned by this D-lane build but read here)
already mines agents/launch.log wholesale into store/answer_bank.json — it
extracts up to 20 recurring Q&A pairs and merges them straight in, with no
per-question review step. That's fine for what it does (keep the bank fresh
automatically) but it means every question that ever gets extracted goes
live into the "answer VERBATIM on every form" bank with zero human check,
which is a real risk for anything more specific than the boilerplate
work-auth/salary/remote questions it was built around (a wrong or
overconfident verbatim answer to a nuanced screener question is worse than
asking the LLM to reason about it fresh each time).

D235 asks for a DIFFERENT loop: unresolved screener questions land in a
NEEDS-QUEUE (store/answer_suggestions.jsonl) with a drafted answer, and only
[OWNER]'s explicit approval promotes a suggestion into the real answer_bank.json.
This module is that queue + the approve mechanics, layered ALONGSIDE (not
replacing) answer_bank.py's existing auto-merge behavior — the two can
coexist: answer_bank.py keeps doing its cheap wholesale extraction for the
bank's boilerplate core, and this module is the slower, gated path for
NEW/uncertain questions that shouldn't autopromote without a look.

NEEDS-QUEUE CONTRACT (the "approve mechanics" the brief asks to document):
  - `mine_new_suggestions()` reads agents/launch.log, extracts (question,
    drafted_answer) pairs the SAME way answer_bank.py does (same _cli prompt
    shape, same model), but ONLY for questions not already present (fuzzy,
    same first-60-chars-normalized key answer_bank.py itself uses) in either
    store/answer_bank.json (already-approved) OR store/answer_suggestions.jsonl
    (already-pending) — so nothing gets suggested twice.
  - Each new suggestion is appended to store/answer_suggestions.jsonl as
    {"id": "...", "q": "...", "a": "...", "status": "pending", "ts": "..."}.
    Append-only, id-keyed, same last-write-wins-by-id discipline as every
    other jsonl store in this codebase (store_lib.load_todos is the
    canonical pattern; this file mirrors it locally since it's a small,
    job-lane-specific store not worth adding a cross-cutting dependency for).
  - `approve(suggestion_id)`: flips status to "approved" AND copies the
    {q, a} pair into store/answer_bank.json's own qa list (so it starts
    getting used verbatim immediately, same as any other bank entry) --
    THIS is the one-tap action a future dashboard "Approve" button calls.
  - `reject(suggestion_id, note="")`: flips status to "rejected", never
    touches answer_bank.json. A rejected suggestion's normalized question key
    is remembered so the SAME question text won't be re-suggested on a later
    mine (does not, however, block a meaningfully DIFFERENT question that
    happens to be about the same topic).
  - Nothing in this module ever writes to answer_bank.json except through an
    explicit approve() call -- mining alone never promotes anything.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import new_id, now_iso  # noqa: E402
import planner  # noqa: E402

LOG = ROOT / "agents" / "launch.log"
BANK = ROOT / "store" / "answer_bank.json"
SUGGESTIONS = ROOT / "store" / "answer_suggestions.jsonl"

MINE_PROMPT = (
    "Below are job-application operator transcripts. Extract screener questions "
    "[OWNER] was asked that are NOT simple boilerplate (work authorization, "
    "sponsorship, salary, notice period, remote, years of experience, portfolio/"
    "links, referral source, address) -- those are already handled elsewhere. "
    "Focus on role-specific or unusual questions (e.g. 'describe a time you...', "
    "industry-specific tool/experience questions, culture-fit questions) and the "
    "answer given. Answers must be exactly as truthful as what was written, never "
    "stronger. Return ONLY JSON {\"qa\":[{\"q\":\"...\",\"a\":\"...\"}]} with at most "
    "15 pairs, each answer under 300 chars.\n\nTRANSCRIPTS:\n")


def _qkey(q: str) -> str:
    return " ".join((q or "").lower().split())[:60]


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


def _load_suggestions() -> list[dict]:
    """Last-write-wins by id, same discipline store_lib.load_todos uses."""
    by_id, order = {}, []
    for r in _read_jsonl(SUGGESTIONS):
        rid = r.get("id")
        if not rid:
            continue
        if rid not in by_id:
            order.append(rid)
        by_id[rid] = r
    return [by_id[i] for i in order]


def _append_suggestion(rec: dict) -> None:
    SUGGESTIONS.parent.mkdir(parents=True, exist_ok=True)
    with SUGGESTIONS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _bank_qa() -> list[dict]:
    try:
        return json.loads(BANK.read_text()).get("qa", [])
    except (OSError, json.JSONDecodeError):
        return []


def _known_keys() -> set[str]:
    """Every question already covered: approved bank entries + any pending OR
    rejected suggestion (rejected stays remembered so mining doesn't loop on
    a question [OWNER] already said no to -- see module docstring)."""
    keys = {_qkey(x.get("q")) for x in _bank_qa()}
    keys |= {_qkey(x.get("q")) for x in _load_suggestions()}
    return keys


def mine_new_suggestions(limit_new: int = 15) -> list[dict]:
    """Extract questions from launch.log NOT already known (bank or pending/
    rejected suggestions), append each as a pending suggestion. Returns the
    newly-appended records. Safe to re-run often (idempotent by question key,
    same as answer_bank.py's own dedupe approach)."""
    if not LOG.exists():
        return []
    known = _known_keys()
    tail = LOG.read_text(errors="replace")[-120_000:]
    out = planner._cli(MINE_PROMPT + tail, timeout=150, feature="plan") or ""
    j = planner._extract_json(out) or {}
    qa = [x for x in (j.get("qa") or []) if x.get("q") and x.get("a")]
    new = []
    for x in qa:
        key = _qkey(x["q"])
        if key in known:
            continue
        known.add(key)
        rec = {"id": new_id("ans_" + key), "q": x["q"].strip()[:300],
               "a": x["a"].strip()[:300], "status": "pending", "ts": now_iso()}
        _append_suggestion(rec)
        new.append(rec)
        if len(new) >= limit_new:
            break
    return new


def pending() -> list[dict]:
    return [x for x in _load_suggestions() if x.get("status") == "pending"]


def approve(suggestion_id: str) -> dict | None:
    """Promotes a pending suggestion into store/answer_bank.json's qa list AND
    flips its own status to approved. Returns the promoted {q,a} dict, or None
    if the id wasn't found/wasn't pending."""
    subs = _load_suggestions()
    rec = next((x for x in subs if x.get("id") == suggestion_id), None)
    if not rec or rec.get("status") != "pending":
        return None
    rec = {**rec, "status": "approved", "approved_at": now_iso()}
    _append_suggestion(rec)
    try:
        bank = json.loads(BANK.read_text())
    except (OSError, json.JSONDecodeError):
        bank = {"ts": "", "qa": []}
    qa = bank.get("qa") or []
    qa.append({"q": rec["q"], "a": rec["a"]})
    # de-dupe by key, same 60-char-normalized approach answer_bank.py uses, keep newest
    seen = {}
    for x in qa:
        seen[_qkey(x.get("q", ""))] = x
    bank["qa"] = list(seen.values())[:30]
    bank["ts"] = now_iso()
    BANK.write_text(json.dumps(bank, indent=1))
    planner.feed_add("agent", f"Answer bank: approved new suggestion ({rec['q'][:50]})")
    return {"q": rec["q"], "a": rec["a"]}


def reject(suggestion_id: str, note: str = "") -> dict | None:
    subs = _load_suggestions()
    rec = next((x for x in subs if x.get("id") == suggestion_id), None)
    if not rec or rec.get("status") != "pending":
        return None
    rec = {**rec, "status": "rejected", "rejected_at": now_iso(), "note": note}
    _append_suggestion(rec)
    return rec


def run():
    new = mine_new_suggestions()
    pend = pending()
    print(f"job_answer_growth: {len(new)} new suggestion(s) mined, {len(pend)} total pending approval")
    if new:
        planner.feed_add("agent", f"{len(new)} new screener answer(s) awaiting your approval",
                         "; ".join(x["q"][:40] for x in new[:3]))
    return {"new": len(new), "pending": len(pend)}


if __name__ == "__main__":
    run()
