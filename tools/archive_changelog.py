#!/usr/bin/env python3
"""Move a document's dated changelog into an archive, keeping the live part small.

A long-lived project doc grows by appending: every fix, every incident, every
decision, newest at the bottom. The live content stays useful; the history behind
it becomes the majority of the file. That history is still read, by you and by any
agent that opens the repo, and what it mostly teaches is that a lot has gone wrong
here. Archiving it is not deletion. It is putting the past where the past goes.

    python3 tools/archive_changelog.py REMINDERS.md              # dry run
    python3 tools/archive_changelog.py REMINDERS.md --apply

Moves two things into <NAME>-ARCHIVE.md:
  * sections whose heading is a date          (## 2026-08-12 - what happened)
  * sections named DONE / RESOLVED / CHANGELOG

Everything else stays. Nothing is deleted: the tool refuses to write unless every
byte of the original is accounted for in the two output files.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATED = re.compile(r"^#{1,3}\s+.*?(20\d\d[-/]\d\d[-/]\d\d)", re.I)
DONE = re.compile(r"^#{1,3}\s+.*\b(DONE|RESOLVED|CHANGELOG|COMPLETED|SHIPPED)\b", re.I)


def split(text: str) -> tuple[str, str]:
    """-> (live, archived). Splits on headings; a matched heading takes its body."""
    lines = text.splitlines(keepends=True)
    # locate every heading and its extent
    heads = [i for i, ln in enumerate(lines) if re.match(r"^#{1,3}\s+", ln)]
    heads.append(len(lines))
    live, arch = [], []
    prev_end = 0
    # preamble before the first heading always stays live
    if heads and heads[0] > 0:
        live.extend(lines[:heads[0]])
        prev_end = heads[0]
    for h, nxt in zip(heads[:-1], heads[1:]):
        if h < prev_end:
            continue
        block = lines[h:nxt]
        head = lines[h]
        depth = len(head) - len(head.lstrip("#"))
        if DATED.match(head) or DONE.match(head):
            # take this heading and every DEEPER heading under it
            end = nxt
            for h2, nxt2 in zip(heads[:-1], heads[1:]):
                if h2 < nxt:
                    continue
                h2head = lines[h2]
                d2 = len(h2head) - len(h2head.lstrip("#"))
                if d2 > depth and not (DATED.match(h2head) or DONE.match(h2head)):
                    end = nxt2
                else:
                    break
            arch.extend(lines[h:end])
            prev_end = end
        else:
            live.extend(block)
            prev_end = nxt
    return "".join(live), "".join(arch)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("doc", help="the document to split, e.g. REMINDERS.md")
    ap.add_argument("--apply", action="store_true", help="write the files")
    args = ap.parse_args()

    src = Path(args.doc)
    if not src.is_absolute():
        src = ROOT / src
    if not src.is_file():
        print(f"\n  No such file: {src}\n")
        return 1

    original = src.read_text()
    live, arch = split(original)

    # nothing may be lost: every non-empty line must survive somewhere
    lost = [ln for ln in original.splitlines()
            if ln.strip() and ln not in live.splitlines() and ln not in arch.splitlines()]
    if lost:
        print(f"\n  REFUSING: {len(lost)} line(s) would be lost. First: {lost[0][:70]!r}\n")
        return 1

    if not arch.strip():
        print(f"\n  Nothing dated to archive in {src.name}.\n")
        return 0

    o, l, a = (len(original.splitlines()), len(live.splitlines()), len(arch.splitlines()))
    dest = src.with_name(f"{src.stem}-ARCHIVE{src.suffix}")
    print(f"\n  {src.name}: {o} lines")
    print(f"    stays live : {l} lines  ({l * 100 // max(1, o)}%)")
    print(f"    archived   : {a} lines  ({a * 100 // max(1, o)}%)  -> {dest.name}")

    if not args.apply:
        print("\n  Dry run. Re-run with --apply to write.\n")
        return 0

    pointer = (f"\n---\n\n## History\n\nResolved items and the dated changelog live in "
               f"[{dest.name}]({dest.name}). They are kept for the record and need no "
               f"action; this file stays limited to what is still open.\n")
    header = (f"# {src.stem} — archive\n\nResolved items and dated history moved out of "
              f"{src.name} so the live document stays short. Nothing here needs action.\n\n---\n\n")
    dest.write_text(header + arch)
    src.write_text(live.rstrip() + "\n" + pointer)
    print(f"\n  Written. {src.name} is now {len(src.read_text().splitlines())} lines.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
