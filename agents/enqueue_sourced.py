#!/usr/bin/env python3
"""Enqueue a sourcing run's JSON into the networking queue (2026-07-15).

The 6:02am networking-daily-source scheduled task used to carry this logic as an
embedded python -c one-liner in its prompt; when sourcing went commenter-first
([OWNER]: "point the sourcing at agency-owner commenters on other posts") the logic
outgrew a one-liner, so it lives here where it can be tested.

Input file (argv[1], default /tmp/network_sourced.json), sections all optional:
  comments:    [{author, text, url}]                     -> networking.queue_comments
  replies:     [{author, text, url}]                     -> networking.queue_replies
  likes:       [{author, text, url}]                     -> networking.queue_likes
  connections: [{name, headline, url}]                   -> networking.queue_connections
  commenters:  [{commenter_name, commenter_headline, commenter_url, comment_text,
                 post_author, post_url, post_context}]   -> reshaped through
                li_commenters.commenter_to_candidate (is_commenter scoring bonus +
                context preserved), a short "commented on post by X" tag folded into
                the headline so the NETWORK tab shows WHY they were sourced, then
                merged ahead of plain connections into queue_connections.

Commenters are listed FIRST in the merged connect batch on purpose: queue_
connections ranks by li_scoring but the merge order breaks ties, and the whole
point of the commenter-first push is that engaged commenters beat cold search hits.
Merged batch is deduped by url here (queue_connections' own seen-check only guards
against urls already IN the store, not the same url twice in one input batch).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import li_commenters  # noqa: E402
import networking  # noqa: E402


def merge_connect_batch(data: dict) -> list[dict]:
    """Commenter rows (reshaped, context-tagged) first, plain connections after,
    deduped by url keeping the first (i.e. the commenter version wins)."""
    merged = []
    for r in data.get("commenters", []) or []:
        c = li_commenters.commenter_to_candidate(r)
        who = (r.get("post_author") or "").strip() or "a niche creator"
        c["headline"] = ((c.get("headline") or "") + " · commented on post by " + who)[:200]
        merged.append(c)
    merged += [c for c in (data.get("connections", []) or []) if isinstance(c, dict)]
    by_url: dict[str, dict] = {}
    for c in merged:
        u = c.get("url")
        if u and u not in by_url:
            by_url[u] = c
    return list(by_url.values())


def run(path: str = "/tmp/network_sourced.json") -> dict:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"enqueue_sourced: cannot read {path}: {e}")
        return {}
    counts = {
        "comments": len(networking.queue_comments(data.get("comments", []) or [])),
        "connections": len(networking.queue_connections(merge_connect_batch(data))),
        "replies": len(networking.queue_replies(data.get("replies", []) or [])),
        "likes": len(networking.queue_likes(data.get("likes", []) or [])),
    }
    print("queued", " ".join(f"{k} {v}" for k, v in counts.items()))
    return counts


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "/tmp/network_sourced.json")
