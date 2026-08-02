#!/usr/bin/env python3
"""Postmortem — turns a FAILED/error feed entry into a one-line probable cause
instead of a silent scroll-past.

Why: agents log failures to the feed (e.g. "Daily brief ready" with an API error
buried in detail, or an outright FAILED title) but nothing ever goes back and asks
why. This scans the last 24h of feed entries for failure-shaped titles, skips ones
already written up, and asks one cheap Haiku call for the likely root cause given
the title plus whatever tail of a plausibly-matching agents/*.log it can find.

Read-only against feed.jsonl and agents/*.log; only write is an append to
store/postmortems.jsonl (keyed by title, so a re-run doesn't re-explain the same
failure) plus a feed_add. Run standalone: .venv/bin/python agents/postmortem.py
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
import planner  # noqa: E402
from store_lib import now_iso  # noqa: E402

FEED = ROOT / "store" / "feed.jsonl"
POSTMORTEMS = ROOT / "store" / "postmortems.jsonl"
AGENTS_DIR = ROOT / "agents"
FAIL_RE = re.compile(r"\bfailed\b|\berror\b", re.I)


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


def _recent_failures(hours: int = 24) -> list[dict]:
    cutoff = datetime.now().astimezone() - timedelta(hours=hours)
    out = []
    for r in _read_jsonl(FEED):
        title = r.get("title") or ""
        # Skip this agent's OWN feed_add announcements ("Postmortem: <title>"),
        # otherwise a failure titled "...FAILED..." spawns a postmortem whose
        # feed title still contains "FAILED", which the next run "discovers"
        # as a brand-new failure and re-postmortems forever.
        if title.startswith("Postmortem: "):
            continue
        if not FAIL_RE.search(title):
            continue
        try:
            ts = datetime.fromisoformat(r.get("ts", ""))
        except (ValueError, TypeError):
            continue
        if ts >= cutoff:
            out.append(r)
    return out


def _already_covered() -> set[str]:
    return {r.get("title") for r in _read_jsonl(POSTMORTEMS) if r.get("title")}


def _guess_log(title: str) -> Path | None:
    """Fuzzy-match a feed entry's title/kind against agents/*.log stems. Titles
    rarely name the agent literally, so this is best-effort: strip non-letters
    from every log stem and every word in the title, keep the longest overlap."""
    if not AGENTS_DIR.is_dir():
        return None
    logs = sorted(AGENTS_DIR.glob("*.log"))
    if not logs:
        return None
    words = [re.sub(r"[^a-z]", "", w.lower()) for w in title.split()]
    words = [w for w in words if len(w) >= 4]
    best, best_score = None, 0
    for log in logs:
        stem = re.sub(r"[^a-z]", "", log.stem.lower())
        score = max((len(w) for w in words if w in stem or stem in w), default=0)
        if score > best_score:
            best, best_score = log, score
    return best if best_score >= 4 else None


def _log_tail(path: Path, n: int = 20) -> str:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])


PROMPT = """A step in [OWNER]'s automation system logged this failure:

TITLE: %s
DETAIL: %s

%s

In one or two sentences, give the PROBABLE root cause (be specific if the log
gives you something to point at; otherwise reason from the title/detail alone).
No questions, no hedging disclaimers, just the best-guess cause. Plain text only."""


def build_causes() -> list[dict]:
    covered = _already_covered()
    out = []
    for entry in _recent_failures(24):
        title = entry.get("title") or ""
        if title in covered:
            continue
        log = _guess_log(title)
        log_block = ""
        if log is not None:
            tail = _log_tail(log)
            if tail:
                log_block = f"LAST 20 LINES OF {log.name} (best-guess match, may be unrelated):\n{tail}"
        prompt = PROMPT % (title, entry.get("detail") or "(none)", log_block)
        cause = planner._cli(prompt, timeout=120, feature="plan")
        cause = (cause or "").strip() or "Could not determine a cause (CLI call failed)."
        out.append({"ts": now_iso(), "title": title, "cause": cause})
        covered.add(title)  # don't re-cover duplicate titles within this same run
    return out


def main() -> int:
    causes = build_causes()
    if not causes:
        print("postmortem: no new failures in the last 24h")
        return 0
    POSTMORTEMS.parent.mkdir(parents=True, exist_ok=True)
    with POSTMORTEMS.open("a") as f:
        for rec in causes:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    for rec in causes:
        planner.feed_add("agent", f"Postmortem: {rec['title'][:60]}", rec["cause"][:200])
    print(f"postmortem: wrote {len(causes)} cause(s) -> {POSTMORTEMS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
