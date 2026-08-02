#!/usr/bin/env python3
"""30-day LinkedIn batch (2026-07-11, [OWNER]: "generate thirty days worth of content for
LinkedIn at one time, images with OpenAI, then give me a dated CSV to upload into GHL").

Reuses the WHOLE daily pipeline per post — 12-angle LRU rotation, objection/niche-book
rotation, real-material grounding, voice spec + humanize, score>=7 gate, OpenAI tier-1
image with the vision quality check (re-roll -> crisp text-card fallback) — just 30 of
them in one run, each stamped with a schedule date.

OUTPUT: content/linkedin-30day.csv with GHL Social Planner bulk-import headers
(scheduleDate, content, imageUrls, link — per GHL's Basic CSV doc; dates YYYY-MM-DD HH:mm,
interpreted in the GHL sub-account's timezone). Images are uploaded to the GHL media
library first so imageUrls are public CDN links the importer can fetch.

RESUMABLE: posts are tagged campaign=LI-30D-<stamp>; re-running generates only the
remainder and rewrites the CSV. Uploading the CSV into GHL is [OWNER]'S click (the send
gate); these posts are saved status="exported" so the drawer and the daily engine
ignore them.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import content_gen  # noqa: E402
import ghl_social  # noqa: E402
from store_lib import now_iso  # noqa: E402

TARGET = 30
BATCH = 6
# R2-37 (2026-07-13): was hardcoded "LI-30D-2026-07". This module is one-shot CLI (import
# -> main() -> exit in the same process), never held open across a month boundary by a
# long-lived server, so deriving it at import time is equivalent to deriving it at call
# time. A fixed month-string meant a rerun in ANY other month either collided with, or
# silently ignored, a prior month's in-progress 30-post batch under the same campaign id
# -- this way each calendar month gets its own campaign namespace, and reruns WITHIN the
# same month still resolve to the same one (the docstring's "RESUMABLE" contract).
CAMPAIGN = f"LI-30D-{datetime.now():%Y-%m}"
CSV_OUT = ROOT / "content" / "linkedin-30day.csv"
POST_HOUR = 9   # 09:00 in the GHL sub-account's timezone (naive on purpose)


def _mine() -> list[dict]:
    return [p for p in content_gen.load_posts() if p.get("campaign") == CAMPAIGN]


def generate_pool():
    have = len(_mine())
    print(f"[gen] {have}/{TARGET} already exist for {CAMPAIGN}")
    while have < TARGET:
        n = min(BATCH, TARGET - have)
        print(f"[gen] batch of {n}…", flush=True)
        recs = content_gen.generate(n)
        for r in recs:
            content_gen.save_post({**r, "status": "exported", "campaign": CAMPAIGN})
        have = len(_mine())
        print(f"[gen] {have}/{TARGET} done", flush=True)
        if not recs:
            print("[gen] a batch produced 0 keepers (all scored <7); retrying", flush=True)


def assign_dates():
    """Daily slots in creation order, starting tomorrow (from the FIRST assignment) at
    POST_HOUR. Naive local-style strings; GHL interprets them in the sub-account timezone.

    R2-37 (2026-07-13): used to recompute "tomorrow" from NOW and re-stamp EVERY post on
    every call, so a resumable rerun (topping up stragglers, retrying a failed media
    upload) shifted the WHOLE campaign's dates forward -- including posts from an earlier
    run that may already be imported into GHL, so re-uploading the regenerated CSV
    double-scheduled them at the new dates. Now a post that already has scheduled_for_csv
    keeps it; only posts still missing a date get one, continuing the daily sequence
    right after the latest already-assigned slot (or from tomorrow, on the very first
    assignment for this campaign)."""
    posts = sorted(_mine(), key=lambda p: p.get("created", ""))[:TARGET]

    def _parsed(p):
        v = p.get("scheduled_for_csv")
        if not v:
            return None
        try:
            return datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None  # malformed -- treat as undated rather than crash

    already = [d for d in (_parsed(p) for p in posts) if d is not None]
    undated = [p for p in posts if _parsed(p) is None]

    next_day = (max(already) + timedelta(days=1)) if already else (datetime.now() + timedelta(days=1))
    start = next_day.replace(hour=POST_HOUR, minute=0, second=0, microsecond=0)
    for i, p in enumerate(undated):
        # GHL's postAtSpecificTime column wants seconds: YYYY-MM-DD HH:mm:ss
        when = (start + timedelta(days=i)).strftime("%Y-%m-%d %H:%M:%S")
        content_gen.save_post({**p, "scheduled_for_csv": when})

    all_when = sorted(d.strftime("%Y-%m-%d %H:%M:%S") for d in already) + \
        [(start + timedelta(days=i)).strftime("%Y-%m-%d %H:%M:%S") for i in range(len(undated))]
    if all_when:
        print(f"[dates] {len(posts)} posts dated {min(all_when)[:10]} -> {max(all_when)[:10]} "
              f"at {POST_HOUR:02d}:00 daily ({len(undated)} newly assigned this run)")
    else:
        print("[dates] no posts to date yet")


def upload_images():
    for p in _mine():
        if p.get("ghl_media") or not p.get("image"):
            continue
        fp = content_gen.IMGDIR / Path(p["image"]).name
        if not fp.exists():
            continue
        url = ghl_social.upload_media(str(fp))
        if url:
            content_gen.save_post({**p, "ghl_media": url})
            print(f"[media] uploaded {p.get('angle','?')} -> {url[:60]}", flush=True)
        else:
            print(f"[media] UPLOAD FAILED for {p['id']} (will retry on rerun)", flush=True)


def write_csv() -> int:
    posts = sorted(_mine(), key=lambda p: p.get("scheduled_for_csv", ""))[:TARGET]
    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    # EXACT GHL Social Planner Basic-CSV headers — GHL matches by header string, so the
    # parenthetical suffixes and the empty gif/video columns must be present verbatim or the
    # import errors "missing columns" ([OWNER] hit this 2026-07-11 with the short names).
    with CSV_OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["postAtSpecificTime (YYYY-MM-DD HH:mm:ss)", "content", "imageUrls",
                    "link (OGmetaUrl)", "gifUrl", "videoUrls"])
        for p in posts:
            w.writerow([p.get("scheduled_for_csv", ""), p.get("text", ""),
                        p.get("ghl_media", ""), "", "", ""])
    return len(posts)


def main() -> int:
    generate_pool()
    assign_dates()
    upload_images()
    n = write_csv()
    missing_media = sum(1 for p in _mine() if not p.get("ghl_media"))
    angles = {}
    for p in _mine():
        angles[p.get("angle", "?")] = angles.get(p.get("angle", "?"), 0) + 1
    print(f"\n[csv] {n} rows -> {CSV_OUT}")
    print(f"[csv] angle spread: {dict(sorted(angles.items()))}")
    if missing_media:
        print(f"[csv] WARNING: {missing_media} post(s) without a GHL image URL (rerun to retry uploads)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
