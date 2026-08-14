#!/usr/bin/env python3
"""Find business assumptions this kit inherited from its ORIGINAL owner.

owner.py retargets IDENTITY (name, site, company, email). It does not retarget the
BUSINESS: what you sell, to whom, at what price, and the vocabulary that comes with
it. Those are baked into agent prompts and business-library/ as plain prose, so a
fresh install keeps reasoning from the original owner's business model while
correctly signing the output with YOUR name. The symptom is not an error. It is
plausible, well-written output aimed at the wrong market -- the hardest failure to
notice, because nothing ever breaks.

    python3 tools/retarget_audit.py              ranked summary
    python3 tools/retarget_audit.py --files      every file, with hit counts
    python3 tools/retarget_audit.py --terms      what it looks for, and why

Ranking is by RUNTIME IMPACT, not hit count. An agent prompt that reaches an LLM on
every run matters more than a doc nobody reads, even if the doc mentions the old
business fifty times. Fix tier 1 first; tier 4 is cosmetic.

This tool only reads and prints. It never edits anything.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Vocabulary that carries a business MODEL, not an identity. Each entry is
# (regex, what it implies). Identity tokens are deliberately absent: owner.py
# already handles those, and flagging them here would bury the real signal.
TERMS = [
    (r"white[- ]label", "sells production capacity to other agencies"),
    (r"\bmedspa?s?\b", "targets medical spas"),
    (r"agency owners?\b", "sells TO agencies, not to end businesses"),
    (r"fractional CO[OT]", "positions as fractional executive"),
    (r"\bWebfix\b", "a specific productized offer"),
    (r"care plans?\b", "a specific monthly retainer product"),
    (r"Ops Partner|AI Ops Install", "specific productized offers"),
    (r"\$\d[\d,]*(?:/mo| flat| build)", "hardcoded price points"),
    (r"speed[- ]to[- ]lead", "a specific productized offer"),
    (r"\bGoHighLevel\b|\bGHL\b", "assumes a specific CRM"),
    (r"local (?:business|biz)(?:es)?\b", "assumes a local-services market"),
]

# Tier 1 first: the earlier a pattern matches, the more it matters.
TIERS = [
    (1, "LLM PROMPTS (reaches the model on every run)", [
        re.compile(r"^agents/.*\.py$"), re.compile(r"^app/(server|planner|brain|executive)\.py$")]),
    (2, "BUSINESS LIBRARY (agents read these at runtime)", [
        re.compile(r"^business-library/")]),
    (3, "SKILLS AND KITS (used when invoked)", [
        re.compile(r"^skills/yours/"), re.compile(r"^kits/"), re.compile(r"^browser-agent/")]),
    (4, "DOCS AND TESTS (cosmetic; fix last or never)", [
        re.compile(r".*")]),
]

TIER_ACTION = {
    1: "Rewrite the prompt text to describe YOUR business. These are what make the "
       "system sound like someone else.",
    2: "Replace the content with your own offers, pricing, ICP, and objections. "
       "business-library/ IS the system's business knowledge; wrong content here "
       "propagates everywhere.",
    3: "Retarget when you next use that skill. Harmless until invoked.",
    4: "Ignore unless it confuses you later. No runtime effect.",
}


def _tracked_files() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True, timeout=30).stdout
        files = [f for f in out.splitlines() if f]
    except Exception:  # noqa: BLE001
        files = []
    if not files:  # not a git checkout: walk instead
        files = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
                 if p.is_file() and ".git" not in p.parts and ".venv" not in p.parts]
    skip = ("skills/third-party/", "store/", ".venv/", "node_modules/")
    return [f for f in files
            if not f.startswith(skip)
            and Path(f).suffix in (".py", ".md", ".json", ".sh", ".txt", ".html")]


def _tier(path: str) -> tuple[int, str]:
    for num, label, pats in TIERS:
        if any(p.match(path) for p in pats):
            return num, label
    return 4, TIERS[-1][1]


def scan() -> dict:
    """path -> {term: count} for every file carrying inherited business vocabulary."""
    hits: dict[str, dict[str, int]] = {}
    for rel in _tracked_files():
        try:
            text = (ROOT / rel).read_text(errors="replace")
        except OSError:
            continue
        found = {}
        for pat, _why in TERMS:
            n = len(re.findall(pat, text, re.IGNORECASE))
            if n:
                found[pat] = n
        if found:
            hits[rel] = found
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--files", action="store_true", help="list every file with hit counts")
    ap.add_argument("--terms", action="store_true", help="show what is searched for, and why")
    args = ap.parse_args()

    if args.terms:
        print("\n  Business-model vocabulary this looks for:\n")
        for pat, why in TERMS:
            print(f"    {pat:<34} {why}")
        print("\n  Identity (name, site, company) is NOT listed: owner.py already")
        print("  retargets that at runtime. This tool finds what it CANNOT.\n")
        return 0

    hits = scan()
    if not hits:
        print("\n  Clean: no inherited business vocabulary found.\n")
        return 0

    by_tier: dict[int, list] = {}
    for path, found in hits.items():
        num, _label = _tier(path)
        by_tier.setdefault(num, []).append((path, sum(found.values())))

    total_files = len(hits)
    print(f"\n  {total_files} files still describe the ORIGINAL owner's business.")
    print("  Ranked by runtime impact, not by hit count.\n")

    for num, label, _pats in TIERS:
        rows = sorted(by_tier.get(num, []), key=lambda r: -r[1])
        if not rows:
            continue
        print(f"  {'=' * 66}")
        print(f"  TIER {num}: {label}   [{len(rows)} files]")
        print(f"  {TIER_ACTION[num]}")
        print()
        shown = rows if args.files else rows[:8]
        for path, n in shown:
            print(f"    {n:>4} hits  {path}")
        if not args.files and len(rows) > len(shown):
            print(f"           ... {len(rows) - len(shown)} more (--files to see all)")
        print()

    t1 = len(by_tier.get(1, []))
    t2 = len(by_tier.get(2, []))
    print(f"  {'=' * 66}")
    print(f"  Start here: {t1} prompt file(s), then {t2} business-library file(s).")
    print("  Nothing below tier 2 changes what the system says to a human.\n")
    print("  Suggested prompt for your Claude, one tier at a time:")
    print("    \"Retarget tier 1 from tools/retarget_audit.py to my business:")
    print("     <what you sell> to <who buys it>. Keep the mechanics identical,")
    print("     change only the business assumptions. Show me each diff.\"\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
