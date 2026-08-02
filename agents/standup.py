#!/usr/bin/env python3
"""E334: standup content from real deltas — turns the yesterday-vs-today
numeric diff agents/snapshot.py already computes into short, plain-English
standup sentences ("applied to 22 more jobs, warm pipeline value dropped
$11k") instead of [OWNER] having to read raw "key: old -> new" diff lines
himself every morning.

WHAT: imports snapshot.build_snapshot() + diff_with_yesterday() directly
      (no HTTP re-fetch, no re-deriving the diff logic — reuses the exact
      same numbers snapshot.py already computed) and renders the changed
      lines into standup sentences via ONE cheap Haiku call. Falls back to
      the raw diff lines (still readable, just not prose) if the CLI call
      fails, rather than returning nothing.
WHEN: run after agents/snapshot.py in the morning chain (or standalone any
      time — it calls build_snapshot() itself if no diff is cached, so it
      never depends on snapshot.py having run first in the SAME session,
      just on yesterday's snapshot file already existing on disk).
RAILS: read-only against the live server endpoints (same ones snapshot.py
      hits) and store/snapshots/*.json. Only write is store/standup.md
      (full overwrite each run) + a feed_add. No GHL writes, no sends.

Run:  .venv/bin/python agents/standup.py
      .venv/bin/python agents/standup.py --fixture   (deterministic fixture
      diff, no live server call, no LLM call skipped either — still real)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import humanize, now_iso  # noqa: E402
import planner  # noqa: E402
import snapshot  # noqa: E402

STANDUP = ROOT / "store" / "standup.md"

PROMPT = """Turn these raw yesterday-vs-today number changes into a short
standup update for [OWNER], written like a sharp chief-of-staff giving a
30-second verbal update. Plain text, NO em-dashes, 3-5 short sentences max.
Group related numbers into one sentence where it makes sense (e.g. jobs
counts together, money numbers together). Skip anything that looks like
noise (a field that appeared/disappeared due to an API shape change, not a
real change worth mentioning) rather than reporting every single line
literally. If NOTHING here looks like real signal, say plainly that nothing
meaningfully moved.

RAW CHANGES (key: yesterday -> today):
%s

Output ONLY the standup update, no preamble."""


def _fixture_lines() -> list[str]:
    """Frozen scenario for --fixture: deterministic, no store/network I/O
    needed to GET the lines (the LLM call itself still happens for real)."""
    return [
        "  jobs.counts.applied: 110 -> 113 (+3)",
        "  jobs.counts.approved: 0 -> 22 (+22)",
        "  money.warm_total: 58 -> 47 (-11)",
        "  usage.total_calls: 8 -> 170 (+162)",
    ]


def build(*, fixture: bool = False) -> dict:
    """Returns {"lines": [...], "text": str, "fixture": bool}. Pure enough to
    unit-test the non-LLM parts by inspecting 'lines' before the CLI call."""
    if fixture:
        lines = _fixture_lines()
    else:
        snap = snapshot.build_snapshot()
        lines = snapshot.diff_with_yesterday(snap)

    if not lines:
        return {"lines": [], "text": "Nothing meaningfully moved since yesterday.", "fixture": fixture}

    text = planner._cli(PROMPT % "\n".join(lines), timeout=60, feature="plan")
    text = (text or "").strip()
    if not text:
        # honest fallback: still useful, just not prose
        text = "Raw deltas (standup prose unavailable this run):\n" + "\n".join(lines)
    else:
        text = humanize(text)  # hard voice filter: the prompt says NO em-dashes but models
                               # don't always comply (proved it: a real run produced one) —
                               # this is the same mechanical strip every other [OWNER]-facing
                               # agent output in this repo goes through.
    return {"lines": lines, "text": text, "fixture": fixture}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fixture", action="store_true",
                     help="use a frozen scenario instead of the real snapshot diff")
    args = ap.parse_args()

    from runlog import track
    with track("standup"):
        result = build(fixture=args.fixture)
        doc = (f"# Standup — {now_iso()[:10]}\n\n{result['text']}\n\n"
              f"_{'FIXTURE run, ' if result['fixture'] else ''}"
              f"{len(result['lines'])} raw delta(s) considered, generated {now_iso()}_\n")
        STANDUP.write_text(doc)

    tag = " [FIXTURE]" if result["fixture"] else ""
    print(f"standup{tag}: {len(result['lines'])} delta(s) -> {STANDUP}")
    print()
    print(result["text"])
    if result["lines"] and not result["fixture"]:
        try:
            planner.feed_add("agent", "Standup ready", result["text"][:90])
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
