#!/usr/bin/env python3
"""Strip the PREVIOUS owner's identity out of an inherited copy of this kit.

Why this exists as a separate step from owner.py: owner.py resolves `[OWNER]`-style
TOKENS at runtime. It does nothing about a previous owner's real name, domain,
company, clients, or partner sitting in the source as literal prose, because those
were never tokens to begin with. Inheriting a copy means inheriting those.

That residue is not cosmetic. An agent working in a tree full of a DIFFERENT real
person's name, clients, and private relationships is being asked to act as one
person while reading another's life, and a careful agent responds to that exactly
the way it should: by hedging, by refusing, by treating ambiguous instructions as
suspect. Clearing the residue is what makes the system unambiguously YOURS, which
is what makes an agent willing to act on it.

    python3 tools/depersonalize.py --scan             what is still in here
    python3 tools/depersonalize.py --from "Old Name" --dry-run
    python3 tools/depersonalize.py --from "Old Name" --apply

WHAT IT REWRITES (mechanical, unambiguous):
  the previous owner's name, handle, email, site, and company -> yours, from
  config/owner.json. Identity is a substitution; there is one right answer.

WHAT IT ONLY REPORTS (needs your judgment, never auto-edited):
  business model, offers, pricing, ICP, named clients, private relationships.
  Those are decisions about YOUR business, not find-and-replace. Run
  tools/retarget_audit.py for the ranked list.

Never touches store/, .git/, .venv/, or skills/third-party/. --apply writes a
timestamped backup of every file it changes to ../jarvis-depersonalize-<stamp>/.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SKIP_DIRS = ("store/", ".git/", ".venv/", "skills/third-party/", "node_modules/",
             "__pycache__/", ".playwright-mcp/")
TEXT_SUFFIXES = (".py", ".md", ".json", ".sh", ".txt", ".html", ".js", ".yml", ".yaml")

# Personal-life and client vocabulary: REPORTED, never rewritten. A name here is a
# real person or a real company, and guessing at a replacement would be worse than
# leaving it. These are the hits most likely to make an agent balk, so they are
# surfaced loudly even though the tool refuses to touch them.
SENSITIVE_HINTS = (
    (r"\b(partner|girlfriend|boyfriend|wife|husband|spouse)\b", "a private relationship"),
    (r"\bclient[s]?\b.{0,40}\.(com|io|net|co)\b", "a named client domain"),
    (r"\b[A-Z][a-z]+(?:'s)? (?:dad|mom|father|mother|brother|sister)\b", "a family member"),
)


def _files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True, timeout=30).stdout.splitlines()
        rels = [f for f in out if f]
    except Exception:  # noqa: BLE001
        rels = []
    if not rels:
        rels = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file()]
    return [ROOT / r for r in rels
            if not any(r.startswith(d) or f"/{d}" in r for d in SKIP_DIRS)
            and Path(r).suffix in TEXT_SUFFIXES]


def _owner_now() -> dict:
    try:
        import owner
        return {k: owner.get(k, "") for k in
                ("name", "handle", "email", "site", "company", "linkedin")}
    except Exception:  # noqa: BLE001
        return {}


def _variants(full_name: str) -> list[str]:
    """Name spellings to catch, LONGEST FIRST.

    Ordering is the whole trick. Replacing 'soren' before 'sorenpeterson.io'
    turns the domain into '<newname>peterson.io' -- a broken string that looks
    like a successful edit. Longest-first makes every replacement terminal.
    """
    parts = [p for p in re.split(r"\s+", full_name.strip()) if p]
    out = {full_name, full_name.lower(), "".join(parts).lower()}
    if parts:
        out |= {parts[0], parts[0].lower(), parts[-1], parts[-1].lower()}
        if len(parts) > 1:
            out.add(f"{parts[0][0]}{parts[-1]}".lower())
    return sorted({v for v in out if len(v) > 2}, key=len, reverse=True)


def scan(old_name: str | None) -> dict:
    """path -> list of (kind, sample). kind is 'identity' or a sensitive hint."""
    found: dict[str, list] = {}
    pats = []
    if old_name:
        pats = [(re.compile(rf"(?<![A-Za-z0-9]){re.escape(v)}(?![A-Za-z0-9])", re.I),
                 "identity") for v in _variants(old_name)]
    for f in _files():
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        rel = str(f.relative_to(ROOT))
        hits = []
        for pat, kind in pats:
            m = pat.search(text)
            if m:
                line = text[:m.start()].count("\n") + 1
                hits.append((kind, f"line {line}: {m.group(0)}"))
                break
        for pat, why in SENSITIVE_HINTS:
            m = re.search(pat, text)
            if m:
                line = text[:m.start()].count("\n") + 1
                hits.append((why, f"line {line}: {m.group(0)[:48]}"))
        if hits:
            found[rel] = hits
    return found


def apply(old_name: str, new: dict, dry: bool) -> int:
    if not new.get("name"):
        print("\n  No owner configured. Run setup.py first: there is nothing to")
        print("  replace the old identity WITH, and a blank substitution is worse.\n")
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    backup = ROOT.parent / f"jarvis-depersonalize-{stamp}"
    changed, total = [], 0

    name_parts = [p for p in re.split(r"\s+", new["name"].strip()) if p]
    first_new = name_parts[0] if name_parts else new["name"]
    handle_new = (new.get("handle") or "".join(name_parts).lower())

    for f in _files():
        try:
            text = original = f.read_text(errors="replace")
        except OSError:
            continue
        for v in _variants(old_name):            # LONGEST FIRST, see _variants
            if " " in old_name and v.lower() == old_name.strip().lower():
                repl = new["name"]
            elif v.lower() == "".join(re.split(r"\s+", old_name.strip())).lower():
                repl = handle_new
            else:
                repl = first_new
            text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(v)}(?![A-Za-z0-9])",
                          repl, text, flags=re.I)
        if text != original:
            n = sum(1 for a, b in zip(original.splitlines(), text.splitlines()) if a != b)
            total += n
            changed.append((str(f.relative_to(ROOT)), n))
            if not dry:
                dest = backup / f.relative_to(ROOT)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
                f.write_text(text)

    verb = "would change" if dry else "changed"
    print(f"\n  {verb} {len(changed)} file(s), {total} line(s)\n")
    for rel, n in sorted(changed, key=lambda r: -r[1])[:25]:
        print(f"    {n:>4} lines  {rel}")
    if len(changed) > 25:
        print(f"           ... {len(changed) - 25} more")
    if dry:
        print("\n  Dry run. Nothing written. Re-run with --apply to commit.\n")
    else:
        print(f"\n  Backup of every changed file: {backup}")
        print("  Now run the suite before trusting it:  make test\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="old", help="the PREVIOUS owner's full name")
    ap.add_argument("--scan", action="store_true", help="report only, no changes")
    ap.add_argument("--dry-run", action="store_true", help="show what --apply would do")
    ap.add_argument("--apply", action="store_true", help="write the changes")
    args = ap.parse_args()

    new = _owner_now()
    if args.scan or not (args.apply or args.dry_run):
        found = scan(args.old)
        ident = {k: v for k, v in found.items() if any(x[0] == "identity" for x in v)}
        sens = {k: v for k, v in found.items() if any(x[0] != "identity" for x in v)}
        print(f"\n  Configured owner: {new.get('name') or '(none: run setup.py)'}\n")
        if args.old:
            print(f"  IDENTITY residue for {args.old!r}: {len(ident)} file(s)")
            for rel in sorted(ident)[:12]:
                print(f"    {rel}")
            if len(ident) > 12:
                print(f"    ... {len(ident) - 12} more")
            print("\n  Fix: python3 tools/depersonalize.py --from "
                  f"{args.old!r} --dry-run\n")
        else:
            print("  Pass --from 'Previous Owner Name' to scan for identity residue.\n")
        print(f"  PRIVATE-LIFE / CLIENT mentions (reported, never auto-edited): "
              f"{len(sens)} file(s)")
        for rel in sorted(sens)[:10]:
            why = next(x for x in found[rel] if x[0] != "identity")
            print(f"    {rel:<46} {why[0]}")
        if len(sens) > 10:
            print(f"    ... {len(sens) - 10} more")
        print("\n  Those are judgment calls. Business model and offers: "
              "tools/retarget_audit.py\n")
        return 0

    if not args.old:
        print("\n  --from 'Previous Owner Name' is required to rewrite.\n")
        return 1
    return apply(args.old, new, dry=not args.apply)


if __name__ == "__main__":
    sys.exit(main())
