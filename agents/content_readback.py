#!/usr/bin/env python3
"""Content publish readback — GHL Social Planner is the source of truth.

Our store used to mark posts 'scheduled' forever; GHL actually publishes (or fails)
them and we never heard back. This closes that loop: pull the planner's post list,
match our records by ghl_id, and flip scheduled -> posted (or -> failed, with a push).
Runs in the morning routine; safe (read GHL, write local store only).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import ghl_social  # noqa: E402
import planner  # noqa: E402

POSTS = ROOT / "content" / "posts.jsonl"


def _loc() -> str:
    for line in (ghl_social.GHL / ".env").read_text().splitlines():
        if line.startswith("GHL_LOCATION_ID="):
            return line.split("=", 1)[1].strip()
    return ""


def _load() -> dict:
    recs = {}
    if POSTS.exists():
        for line in POSTS.read_text().splitlines():
            try:
                r = json.loads(line)
                recs[r["id"]] = r
            except (json.JSONDecodeError, KeyError):
                continue
    return recs


def _save(rec: dict):
    with POSTS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def ghl_posts() -> dict:
    out = ghl_social._api(["POST", f"/social-media-posting/{_loc()}/posts/list",
                           "--json", json.dumps({"type": "all", "limit": "100", "skip": "0"})])
    try:
        j = json.loads(out[out.find("{"):], strict=False)
    except (ValueError, json.JSONDecodeError):
        return {}
    posts = (j.get("results") or {}).get("posts") or j.get("posts") or []
    return {(p.get("_id") or p.get("id")): p for p in posts}


def run():
    recs = _load()
    sched = [r for r in recs.values() if r.get("status") == "scheduled" and r.get("ghl_id")]
    if not sched:
        print("readback: nothing scheduled locally")
        return 0
    remote = ghl_posts()
    if not remote:
        print("readback: could not fetch GHL posts (kept local state)")
        return 0
    # GHL sometimes re-creates posts under fresh ids (reschedules), so match by
    # stored ghl_id first, then by normalized text prefix; refresh stale ids.
    def norm(s):
        return " ".join((s or "").split())[:60]
    by_text = {norm(p.get("summary")): p for p in remote.values()}
    posted, failed, waiting = [], [], 0
    for r in sched:
        g = remote.get(r["ghl_id"]) or by_text.get(norm(r.get("text")))
        if g and (g.get("_id") or g.get("id")) != r.get("ghl_id"):
            r = {**r, "ghl_id": g.get("_id") or g.get("id")}
        st = (g or {}).get("status", "")
        if st == "published":
            eng = {k: g.get(k) for k in ("likes", "comments", "shares", "stats", "analytics") if g.get(k) is not None}
            _save({**r, "status": "posted", "posted_at": (g.get("publishedAt")
                   or g.get("scheduleDate") or now_iso()), **({"engagement": eng} if eng else {})})
            posted.append(r["id"])
        elif st == "failed":
            _save({**r, "status": "failed", "failed_at": now_iso()})
            failed.append(r["id"])
        else:
            waiting += 1
    print(f"readback: {len(posted)} confirmed posted, {len(failed)} failed, {waiting} still scheduled")
    if posted:
        planner.feed_add("built", f"LinkedIn confirmed live: {len(posted)} post(s) published")
    if failed:
        planner.notify("Post FAILED in GHL", f"{len(failed)} scheduled post(s) failed to publish. Check the planner.")
        planner.feed_add("agent", f"{len(failed)} scheduled post(s) FAILED in GHL")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
