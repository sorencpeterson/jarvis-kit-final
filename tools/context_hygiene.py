#!/usr/bin/env python3
"""Audit what an agent reads on entry, and what that reading teaches it.

A coding agent opening this repo does not read it neutrally. It reads the root
documents first, and those documents set a prior before any work begins. When the
entry context is mostly incident logs, leak post-mortems, UNVERIFIED tags on
things that were later settled, and sentences like "assume more remains", the
prior it sets is: this system is compromised, its own claims are unreliable, and
caution is the correct response. The agent then hedges, refuses, and over-checks.
That is not malfunction. It is the documentation working exactly as written.

Two separate costs:

  VOLUME   entry docs are re-read every session. On a $20 plan that is real money
           spent before any work happens.
  TONE     doubt in an always-read document does not stay put. It propagates into
           generated copy and into the agent's willingness to act at all.

    python3 tools/context_hygiene.py            summary and recommendations
    python3 tools/context_hygiene.py --detail   show the flagged lines

THE DISTINCTION THIS TOOL CANNOT MAKE FOR YOU. Honest uncertainty about a genuinely
OPEN question is good engineering and should stay. Stale doubt about something
since settled is corrosive and should be deleted, not softened. Both look identical
to a regex. What the tool can do is show you where doubt is concentrated, and how
much of it a model is reading before it does anything.

Read-only. Never edits.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Documents an agent reads on entry: the instruction files, plus whatever is
# sitting in the repo root where a directory listing surfaces it.
ENTRY_ALWAYS = ("CLAUDE.md", "AGENTS.md")

PATTERNS = [
    (re.compile(r"\[?UNVERIFIED\]?|\[?SUSPECTED\]?|\bunverified\b", re.I),
     "unverified-tag",
     "An UNVERIFIED tag is right in a dated report and corrosive in a document "
     "read every session. If it has since been settled, say so flatly and delete "
     "the doubt; if it is still open, move it to the open-questions list."),
    (re.compile(r"assume more (remain|exist)|still baked in|more remains", re.I),
     "distrust-the-tree",
     "Tells the agent the whole repo is unreliable. True once, during a migration; "
     "after that it is a standing instruction to distrust everything it reads."),
    (re.compile(r"self-report is not evidence|not evidence|cannot be verified|"
                r"unfalsifiable|may be (producing )?wrong", re.I),
     "distrust-the-output",
     "Teaches the agent that the system's own outputs cannot be trusted. Keep it "
     "scoped to the one mechanism it applies to, never as a general statement."),
    (re.compile(r"\bleak(ed|s)?\b|credential.{0,20}(revok|expos)|incident|"
                r"red[- ]team|breach", re.I),
     "incident-narrative",
     "Security HISTORY belongs in an archive; security GUIDANCE belongs in entry "
     "context. 'Red-team your own output before calling it done' is worth every "
     "token. 'On 2026-08-13 a credential leaked and was revoked' is not, and a "
     "catalogue of those teaches the agent the environment is compromised. This "
     "pattern cannot tell the two apart, so read the flagged lines: keep the "
     "instructions, move the incident log."),
    (re.compile(r"\b(refus|declin|would not|blocked me|kept asking)\w*\b", re.I),
     "refusal-narrative",
     "Descriptions of an AGENT refusing become a template for refusing. Note this "
     "also matches a SAFETY FEATURE that refuses (a preflight gate, a send guard), "
     "which is correct behaviour and should stay. Check which one you have."),
]

# A dated changelog is the single biggest archive win: large, append-only, and
# nothing in it needs action.
CHANGELOG_HINT = re.compile(r"^#{1,3}\s*(20\d\d-\d\d-\d\d|PART \d|Changelog)", re.I | re.M)


def _entry_docs() -> list[Path]:
    docs = []
    for name in ENTRY_ALWAYS:
        p = ROOT / name
        if p.is_file():
            docs.append(p)
    try:
        tracked = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT,
                                 capture_output=True, text=True, timeout=20).stdout.split()
    except Exception:  # noqa: BLE001
        tracked = []
    for rel in tracked:
        if "/" in rel:
            continue                      # root only: that is what a listing shows
        p = ROOT / rel
        if p.is_file() and p not in docs:
            docs.append(p)
    return docs


def audit() -> list[dict]:
    out = []
    for p in _entry_docs():
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        flags: dict[str, list] = {}
        for pat, kind, _why in PATTERNS:
            for i, ln in enumerate(lines, 1):
                if pat.search(ln):
                    flags.setdefault(kind, []).append((i, ln.strip()[:96]))
        out.append({
            "path": str(p.relative_to(ROOT)),
            "lines": len(lines),
            "words": len(text.split()),
            "flags": flags,
            "hits": sum(len(v) for v in flags.values()),
            "changelog": len(CHANGELOG_HINT.findall(text)),
            "always": p.name in ENTRY_ALWAYS,
        })
    return sorted(out, key=lambda d: -d["hits"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--detail", action="store_true", help="show flagged lines")
    args = ap.parse_args()

    rows = audit()
    if not rows:
        print("\n  No entry documents found.\n")
        return 0

    words = sum(r["words"] for r in rows)
    tok = words * 4 // 3
    print(f"\n  ENTRY CONTEXT: {len(rows)} root document(s), {words:,} words "
          f"(~{tok:,} tokens)")
    print("  This is what an agent reads before it does anything.\n")
    if tok > 40000:
        print("  That is a large prior. Most of it is probably history rather than")
        print("  instruction, and history belongs in an archive.\n")

    flagged = [r for r in rows if r["hits"]]
    if not flagged:
        print("  No doubt-teaching patterns found.\n")
        return 0

    print(f"  {'=' * 68}")
    print("  DOUBT CONCENTRATION (what the entry context teaches)\n")
    for r in flagged[:12]:
        mark = "  [always read]" if r["always"] else ""
        per = r["hits"] * 100 / max(1, r["lines"])
        print(f"    {r['hits']:>4} hits  {per:>5.1f}/100 lines  {r['path']}{mark}")
        print(f"              {', '.join(sorted(r['flags']))}")
    print()

    kinds = {k: w for _p, k, w in PATTERNS}
    seen = {k for r in flagged for k in r["flags"]}
    print(f"  {'=' * 68}")
    print("  WHY EACH MATTERS\n")
    for k in sorted(seen):
        print(f"    {k}")
        for line in _wrap(kinds[k], 66):
            print(f"        {line}")
        print()

    print(f"  {'=' * 68}")
    print("  RECOMMENDED, most effective first\n")
    big = [r for r in rows if r["changelog"] >= 3 and r["lines"] > 300]
    for r in big:
        print(f"    1. Split {r['path']} ({r['lines']} lines): move the dated")
        print("       changelog to an -ARCHIVE.md. Nothing in it needs action, and")
        print("       it carries most of the incident language.")
    print("    2. Close settled flags FLATLY. Delete the reasoning that made them")
    print("       look uncertain; a 'closed, but' entry re-teaches the doubt.")
    print("    3. Keep open questions, in one clearly-labelled open list.")
    print("    4. Anything describing a PREVIOUS owner's system: rewrite as yours")
    print("       (tools/depersonalize.py) or archive it.\n")

    if args.detail:
        print(f"  {'=' * 68}")
        print("  FLAGGED LINES\n")
        for r in flagged[:8]:
            print(f"    --- {r['path']}")
            for kind, hits in sorted(r["flags"].items()):
                for ln, txt in hits[:4]:
                    print(f"      {kind:<20} :{ln:<5} {txt}")
            print()
    else:
        print("  Run with --detail to see the lines.\n")
    return 0


def _wrap(s: str, w: int) -> list[str]:
    words, out, cur = s.split(), [], ""
    for x in words:
        if len(cur) + len(x) + 1 > w:
            out.append(cur)
            cur = x
        else:
            cur = f"{cur} {x}".strip()
    if cur:
        out.append(cur)
    return out


if __name__ == "__main__":
    sys.exit(main())
