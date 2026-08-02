#!/usr/bin/env python3
"""Template learn (#54) — extract style rules from how [OWNER] edits reply drafts.

Why: reply_watch.py drafts replies and [OWNER] approves/edits/sends them, but
nothing ever looks BACK at the gap between what the model drafted and what
[OWNER] actually sent, the single richest signal for "here's what his voice
actually wants that the prompt isn't capturing." This compares 'draft' against
'sent_text' on approved/sent replies.jsonl records and, once there's enough
signal (>=3 edited pairs), asks ONE Haiku call to extract 3 concrete style
rules and appends them to store/draft_style_rules.md.

Honest about state: replies.jsonl currently has no 'sent_text' field at all
(reply_watch.py only ever writes 'draft' + 'status'), so until something starts
recording the edited final text, this has nothing to learn from. Rather than
silently no-op, it writes store/template_learn.json {status:"collecting",
pairs:0} and says so on stdout, so the gap is visible instead of invisible.

Read-only against replies.jsonl; writes store/template_learn.json (always,
overwrite) and appends store/draft_style_rules.md only once pairs>=3.
Run standalone: .venv/bin/python agents/template_learn.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402

REPLIES = ROOT / "store" / "replies.jsonl"
STATE = ROOT / "store" / "template_learn.json"
RULES_MD = ROOT / "store" / "draft_style_rules.md"
MIN_PAIRS = 3


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


def _edited_pairs() -> list[dict]:
    """approved/sent records that carry BOTH a draft and a distinct edited final.
    Accepts 'sent_text' as the edited-final field name per the spec; also checks
    the more generic 'final' in case a future writer names it differently, since
    either would represent the same signal (draft vs what actually went out)."""
    pairs = []
    for r in _read_jsonl(REPLIES):
        if r.get("status") not in ("approved", "sent"):
            continue
        draft = (r.get("draft") or "").strip()
        final = (r.get("sent_text") or r.get("final") or "").strip()
        if draft and final and draft != final:
            pairs.append({"id": r.get("id"), "draft": draft, "sent_text": final})
    return pairs


RULES_PROMPT = """Below are pairs of (AI-drafted reply, what [OWNER] actually sent
instead) for his warm-reply drafting system. Compare each pair and extract EXACTLY
3 concrete, actionable style rules that explain the pattern in his edits, things
a prompt could enforce next time (e.g. "cuts the closing line to one word",
"never opens with a greeting", "shortens sentences under 12 words"). Be specific
to what you actually see in the diffs, not generic advice.

Return ONLY a JSON array of exactly 3 short rule strings:
["rule one", "rule two", "rule three"]

PAIRS:
%s"""


def _extract_rules(pairs: list[dict]) -> list[str]:
    blob = "\n\n".join(
        f"PAIR {i+1}:\nDRAFT: {p['draft']}\nSENT: {p['sent_text']}"
        for i, p in enumerate(pairs)
    )
    data = planner._cli_json(RULES_PROMPT % blob, timeout=120, feature="plan")
    if isinstance(data, list):
        return [str(r).strip() for r in data if str(r).strip()][:3]
    return []


def _write_state(status: str, pairs: int, extra: dict | None = None):
    rec = {"status": status, "pairs": pairs, "ts": now_iso(), **(extra or {})}
    STATE.write_text(json.dumps(rec, indent=2))


def _append_rules_md(rules: list[str], pair_count: int):
    RULES_MD.parent.mkdir(parents=True, exist_ok=True)
    header_needed = not RULES_MD.exists()
    with RULES_MD.open("a") as f:
        if header_needed:
            f.write("# Draft style rules (learned from [OWNER]'s edits)\n\n")
        f.write(f"## Learned {now_iso()} (from {pair_count} edited pair(s))\n")
        for r in rules:
            f.write(f"- {r}\n")
        f.write("\n")


def run() -> int:
    pairs = _edited_pairs()
    n = len(pairs)

    if n < MIN_PAIRS:
        _write_state("collecting", n)
        print(f"template_learn: collecting — {n}/{MIN_PAIRS} edited draft/sent pairs found "
              f"in replies.jsonl (no 'draft' vs 'sent_text' pairs yet means nothing has "
              f"recorded the edited-final text). Wrote {STATE}")
        return 0

    rules = _extract_rules(pairs)
    if not rules:
        _write_state("collecting", n, {"note": "had enough pairs but extraction failed"})
        print(f"template_learn: had {n} pairs but the extraction call returned nothing usable; "
              f"left status collecting so a future run retries. Wrote {STATE}")
        return 0

    _append_rules_md(rules, n)
    _write_state("learned", n, {"last_rules": rules})
    print(f"template_learn: extracted {len(rules)} style rule(s) from {n} pairs -> {RULES_MD}")
    planner.feed_add("agent", f"Learned {len(rules)} draft style rule(s) from {n} edits")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
