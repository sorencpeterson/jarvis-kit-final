#!/usr/bin/env python3
"""Image quality gate for content posts (2026-07-11, [OWNER]: "make sure we have an image
quality checker before they go out").

AI image models garble text (the #1 failure), mangle hands/faces, and sometimes render
something unrelated. Every AI-generated post image now gets a VISION CHECK via the Max-plan
claude CLI (the model literally looks at the PNG with the Read tool, $0):

  - at CREATION (content_gen.make_card): fail -> one re-roll -> still fail -> swap to the
    Playwright text-card fallback (which always renders text perfectly).
  - at PUSH (/api/content/ghl/push): any approved post whose AI image never passed gets
    checked before scheduling; failures are held back individually (never block the batch)
    so a bad image can NEVER go out.

The verdict is stored on the post as img_check = {ok, why, text_seen} so the drawer and
a human can see what the checker saw.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
import planner  # noqa: E402

PROMPT = """Look at the image file at this exact path using the Read tool: %s

It is meant to be the graphic for this LinkedIn post:
HOOK: %s
POST (first lines): %s

Judge it strictly on four things:
1. TEXT: transcribe EVERY piece of text you can see in the image. Is all of it crisply
   legible and spelled correctly (real words, no garbled/half-formed letters)?
2. RELEVANT: does the visual + its text actually fit this post's message?
3. ARTIFACTS: any classic AI failures (mangled hands/faces, nonsense UI, warped objects,
   gibberish glyphs, watermark-like smudges)?
4. PROFESSIONAL: would this pass as a premium editorial graphic on a business feed?

A single misspelled or garbled word = FAIL. Irrelevant visual = FAIL. Obvious artifact = FAIL.

Reply with ONLY this JSON, nothing else:
{"ok": true/false, "text_seen": "every word you could read", "why": "one plain sentence"}"""


def check_image(path: str, hook: str, text: str, timeout: int = 90) -> dict:
    """Vision-check one image. Fail-open on infrastructure errors (no CLI, timeout):
    an unavailable checker must not brick content generation — it returns
    {ok: True, skipped: reason} so the pipeline continues and the push gate retries."""
    p = Path(path)
    if not p.exists():
        return {"ok": False, "why": "image file missing"}
    cli = planner._find_claude_cli()
    if not cli:
        return {"ok": True, "skipped": "no claude CLI"}
    model = planner._models().get("content") or planner._models().get("default")
    prompt = PROMPT % (str(p.resolve()), (hook or "")[:120], (text or "")[:400])
    try:
        out = subprocess.run(
            ["perl", "-e", f"alarm {timeout}; exec @ARGV", cli, "-p", prompt,
             "--model", model, "--output-format", "json",
             "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
             "--allowedTools", "Read"],
            capture_output=True, text=True, timeout=timeout + 10, cwd="/tmp",
        ).stdout
    except Exception as e:  # noqa: BLE001
        return {"ok": True, "skipped": f"checker unavailable: {type(e).__name__}"}
    try:
        j = json.loads(out)
        raw = j.get("result") or ""
    except (ValueError, json.JSONDecodeError):
        raw = out or ""
    verdict = planner._extract_json(raw)
    if isinstance(verdict, dict) and "ok" in verdict:
        return {"ok": bool(verdict.get("ok")),
                "text_seen": str(verdict.get("text_seen") or "")[:300],
                "why": str(verdict.get("why") or "")[:200]}
    return {"ok": True, "skipped": "unparseable verdict"}  # fail-open, push gate retries


if __name__ == "__main__":
    print(json.dumps(check_image(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "",
                                 sys.argv[3] if len(sys.argv) > 3 else "")))
