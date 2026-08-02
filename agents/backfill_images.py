#!/usr/bin/env python3
"""Backfill AI split-comparison images for existing posts that don't have one.

For every draft/approved post lacking an AI image, write a style-matched
image_prompt (if missing) via the CLI, then render it with gen_image. Idempotent:
posts already marked image_kind=="ai" are skipped. Safe to re-run.

Run:  uv run python agents/backfill_images.py            # all draft+approved
      uv run python agents/backfill_images.py --approved # approved only
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents", ROOT / "dashboard"):
    sys.path.insert(0, str(p))
import content_gen  # noqa: E402
import gen_image  # noqa: E402
import planner  # noqa: E402

IMGDIR = ROOT / "content" / "images"

STYLE = ("a 1:1 square, photorealistic, cinematic SPLIT-SCREEN comparison. "
         "LEFT HALF = the painful/reactive old way (a stressed agency owner in a dim, "
         "cluttered office, subtle red accent, a red X). RIGHT HALF = the calm/mature "
         "better way (a composed founder or small team in a bright, clean modern office, "
         "subtle green accent, a green check). A small circular VS badge dead center. A "
         "bold UPPERCASE headline across the top (max ~6 words, two lines ok) with ONE key "
         "word emphasized in amber/gold. Three very short labels bottom-LEFT (problems, red) "
         "and three bottom-RIGHT (outcomes, green). Specify the exact headline words and all "
         "six labels, drawn from the post. High contrast, dark-left vs bright-right lighting, "
         "editorial tech aesthetic, no logos, no watermark. Render text crisply, spelled exactly.")

PROMPT = """For each LinkedIn post below, write ONE image_prompt for an AI image model that renders [OWNER]'s signature visual: %s

Posts:
%s

Return ONLY a JSON array: [{"id":"<id>","image_prompt":"..."}]"""


def main() -> int:
    approved_only = "--approved" in sys.argv
    posts = content_gen.load_posts()
    statuses = ("approved",) if approved_only else ("draft", "approved")
    targets = [p for p in posts if p.get("status") in statuses and p.get("image_kind") != "ai"]
    if not targets:
        print("Nothing to backfill — all current posts already have AI images.")
        return 0

    missing = [p for p in targets if not p.get("image_prompt")]
    if missing:
        print(f"Writing image prompts for {len(missing)} post(s)…")
        payload = json.dumps([{"id": p["id"], "text": p["text"]} for p in missing])
        out = planner._cli_json(PROMPT % (STYLE, payload), timeout=220)
        prompts = {r["id"]: r.get("image_prompt", "") for r in out if isinstance(r, dict) and r.get("id")} if isinstance(out, list) else {}
        for p in targets:
            if not p.get("image_prompt") and prompts.get(p["id"]):
                p["image_prompt"] = prompts[p["id"]].strip()

    IMGDIR.mkdir(parents=True, exist_ok=True)
    done = 0
    for p in targets:
        if not p.get("image_prompt"):
            print(f"  ! no prompt for {p['id']}, skipping")
            continue
        outp = str((IMGDIR / (p["id"] + ".png")).resolve())
        ok, msg = gen_image.generate_image(p["image_prompt"], outp)
        if ok and Path(outp).exists():
            p["image"] = "/content-img/" + p["id"] + ".png"
            p["image_kind"] = "ai"
            content_gen.save_post(p)
            done += 1
            print(f"  + AI image: {p['hook'][:56]}")
        else:
            print(f"  ! render failed ({msg}) for {p['hook'][:40]}")
    print(f"\nDone — {done}/{len(targets)} posts now have AI images in your style.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
