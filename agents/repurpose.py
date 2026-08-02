#!/usr/bin/env python3
"""Content repurposing (#77) — a LinkedIn post that landed just dies there today;
nothing turns it into the other formats (Twitter/X thread, newsletter blurb) it
could also feed. This closes that gap for whatever the newest posted piece is.

Reads content/posts.jsonl (last-write-wins by id, same discipline
content_readback.py uses when it flips a record to 'posted'), takes the single
newest 'posted' record not yet repurposed, and asks one CLI call for a JSON
repurpose pack: a 3-5 tweet thread + a newsletter blurb, both in [OWNER]'s voice.
These are drafts only — nothing here posts or sends anything.

Read-only against content/posts.jsonl; writes are store/repurposed.jsonl (append)
+ a feed_add. Run standalone: .venv/bin/python agents/repurpose.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import humanize, now_iso  # noqa: E402
import planner  # noqa: E402

POSTS = ROOT / "content" / "posts.jsonl"
REPURPOSED = ROOT / "store" / "repurposed.jsonl"


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


def _load_posts() -> dict[str, dict]:
    """last-write-wins by id, matching content_readback.py's own _load()."""
    by_id: dict[str, dict] = {}
    for r in _read_jsonl(POSTS):
        if r.get("id"):
            by_id[r["id"]] = r
    return by_id


def _already_repurposed() -> set[str]:
    return {r.get("post_id") for r in _read_jsonl(REPURPOSED) if r.get("post_id")}


def _newest_unrepurposed() -> dict | None:
    posts = _load_posts()
    posted = [r for r in posts.values() if r.get("status") == "posted"]
    posted.sort(key=lambda r: r.get("posted_at") or r.get("created") or "")
    covered = _already_repurposed()
    for r in reversed(posted):  # newest first
        if r.get("id") not in covered:
            return r
    return None


PROMPT = """This LinkedIn post of [OWNER]'s just went live:

%s

Repurpose it into other formats, in the same direct, punchy, no-fluff voice
(NO em-dashes). Output ONLY this JSON, nothing else:
{"tweet_thread": ["tweet 1", "tweet 2", "tweet 3"], "newsletter_blurb": "2-3 sentence blurb"}
tweet_thread must have 3 to 5 short tweets (under 280 chars each) that carry the
post's core idea as a thread, not just chopped-up sentences. newsletter_blurb is
a teaser paragraph that would work as one item in a roundup email."""


def build_repurpose(post: dict) -> dict | None:
    text = post.get("text") or post.get("hook") or ""
    if not text:
        return None
    data = planner._cli_json(PROMPT % text, timeout=120, feature="content")
    if not isinstance(data, dict):
        return None
    thread = data.get("tweet_thread")
    blurb = data.get("newsletter_blurb")
    if not isinstance(thread, list) or not thread or not isinstance(blurb, str) or not blurb:
        return None
    thread = [humanize(str(t).strip()) for t in thread if str(t).strip()][:5]
    if len(thread) < 3:
        return None
    return {"post_id": post["id"], "tweet_thread": thread, "blurb": humanize(blurb.strip()), "ts": now_iso()}


def main() -> int:
    post = _newest_unrepurposed()
    if not post:
        print("repurpose: no new posted content to repurpose")
        return 0
    rec = build_repurpose(post)
    if not rec:
        print(f"repurpose: CLI call failed or returned unusable output for {post.get('id')}")
        return 0
    REPURPOSED.parent.mkdir(parents=True, exist_ok=True)
    with REPURPOSED.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    planner.feed_add("agent", f"Repurposed post ready: {len(rec['tweet_thread'])}-tweet thread + newsletter blurb (draft)")
    print(f"repurpose: repurposed {rec['post_id']} -> {REPURPOSED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
