#!/usr/bin/env python3
"""Voice drift check — samples recent posts and asks whether they still sound
like [OWNER] or have drifted toward generic AI-content-mill voice.

Why: content_gen.py writes new posts against content/voice.md every day, but
nothing ever checks the OUTPUT against the RULES over time. A slow drift (a
stray em-dash slipping through, a hookier-but-hollower opener, listicle habits
creeping in) is exactly the kind of thing that's invisible day to day and
obvious in a 5-post sample. One cheap Haiku call does the comparison; this
agent's only job is sampling, prompting, and logging.

Read-only against content/posts.jsonl and content/voice.md; only writes are an
append to store/insights.jsonl and, if drift is high, a feed_add warn.
Run standalone: .venv/bin/python agents/voice_drift.py
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

POSTS = ROOT / "content" / "posts.jsonl"
VOICE_MD = ROOT / "content" / "voice.md"
INSIGHTS = ROOT / "store" / "insights.jsonl"
SAMPLE_N = 5
DRIFT_WARN_AT = 6


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


def _recent_posts(n: int = SAMPLE_N) -> list[dict]:
    posts = _read_jsonl(POSTS)
    # posts.jsonl is append-only per id like every other store here; last
    # occurrence per id wins, then take the N most-recently-created of those.
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for p in posts:
        pid = p.get("id")
        if not pid:
            continue
        if pid not in by_id:
            order.append(pid)
        by_id[pid] = p
    current = [by_id[i] for i in order]
    current.sort(key=lambda p: p.get("created", ""))
    return current[-n:]


PROMPT = """Compare these recent posts against [OWNER]'s voice rules below. Score
DRIFT from 0 (perfectly on-voice) to 10 (reads like generic AI content, nothing
like the rules). Be specific in notes about what's drifting, if anything.

VOICE RULES:
%s

RECENT POSTS (most recent last):
%s

Return ONLY JSON: {"drift": <0-10 int>, "notes": "<1-3 sentences, specific>"}"""


def check_drift() -> dict | None:
    posts = _recent_posts()
    if not posts:
        return None
    try:
        rules = VOICE_MD.read_text()
    except OSError:
        rules = "(voice.md not found)"
    texts = "\n\n---\n\n".join(p.get("text", "") for p in posts)
    result = planner._cli_json(PROMPT % (rules, texts), timeout=120, feature="plan")
    if not isinstance(result, dict) or "drift" not in result:
        return None
    try:
        drift = int(result.get("drift"))
    except (TypeError, ValueError):
        return None
    drift = max(0, min(10, drift))
    return {"drift": drift, "notes": str(result.get("notes") or ""), "sample_n": len(posts)}


def main() -> int:
    result = check_drift()
    if result is None:
        print("voice_drift: no posts to sample yet or CLI call failed")
        return 0
    text = (f"Voice drift check on {result['sample_n']} recent post(s): "
            f"{result['drift']}/10. {result['notes']}").strip()
    rec = {"ts": now_iso(), "text": text}
    INSIGHTS.parent.mkdir(parents=True, exist_ok=True)
    with INSIGHTS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if result["drift"] >= DRIFT_WARN_AT:
        planner.feed_add("warn", f"Voice drift {result['drift']}/10", result["notes"][:200])
    print(f"voice_drift: {result['drift']}/10 on {result['sample_n']} post(s) -> {INSIGHTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
