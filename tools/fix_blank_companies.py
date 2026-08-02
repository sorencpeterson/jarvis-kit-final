#!/usr/bin/env python3
"""B13 (FABLE-BUILD-QUEUE Section 5): blank-company proposal fixer.

~9 of 15 staged proposals have company="" (a known bug blocking sends: the UI and
attention router show "a prospect" instead of the business, and any surface keyed on
company renders blank). The email drafts themselves are fine; only the record field
is empty. Fix, in order of trust:
  1. Re-resolve from the GHL contact (rung 1, read-only): companyName on the contact.
  2. Fallback: when the record's `name` field is obviously the BUSINESS (contains a
     med/spa/wellness/clinic word), promote a title-cased copy of it.
  3. Anything else (a person name, no signal) is flagged for manual review, never guessed.
Also flags staged records with a blank email: those cannot send at all.

Writes go through proposal_factory.patch() (atomic, flocked, append-only history).

Run:  .venv/bin/python tools/fix_blank_companies.py            # dry run, prints the plan
      .venv/bin/python tools/fix_blank_companies.py --apply    # apply the patches
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import proposal_factory  # noqa: E402

_BIZ_WORDS = {"spa", "medspa", "med", "wellness", "aesthetics", "aesthetic", "health",
              "clinic", "clinical", "llc", "center", "centre", "medical", "iv", "dental",
              "salon", "rejuvenation", "doctors", "dermatology"}


def _biz_title(s: str) -> str:
    """Title-case a lowercase business name without mangling LLC / IV / men's."""
    words = []
    for w in s.split():
        lw = w.lower()
        if lw in ("llc", "iv", "md", "np", "pa"):
            words.append(lw.upper())
        elif lw in ("and", "of", "the", "for") and words:
            words.append(lw)
        else:
            words.append(w[:1].upper() + w[1:])
    return re.sub(r"(['’])S\b", r"\1s", " ".join(words))


def _name_is_business(name: str) -> bool:
    return bool(_BIZ_WORDS & set(re.split(r"[^a-z]+", name.lower())))


def plan() -> tuple[list[dict], list[dict]]:
    """Returns (patches, flags). Each patch: {id, company, source}. Each flag: {id, why}."""
    patches, flags = [], []
    for r in proposal_factory.load_queue():
        if r.get("status") != "staged":
            continue
        pid = r.get("id", "")
        if not (r.get("email") or "").strip():
            flags.append({"id": pid, "name": r.get("name", ""),
                          "why": "blank EMAIL: this proposal cannot send at all"})
        if (r.get("company") or "").strip():
            continue
        company, source = "", ""
        c = proposal_factory.find_contact(cid=r.get("contact_id", ""),
                                          email=r.get("email", ""), name=r.get("name", ""))
        if (c.get("company") or "").strip():
            company, source = c["company"].strip(), "ghl"
        elif _name_is_business(r.get("name", "")):
            company, source = _biz_title(r["name"]), "name-field"
        if company:
            patches.append({"id": pid, "company": company, "source": source})
        else:
            flags.append({"id": pid, "name": r.get("name", ""),
                          "why": "no company on GHL contact and name is not a business; needs a human"})
    return patches, flags


def main() -> int:
    apply = "--apply" in sys.argv
    patches, flags = plan()
    print(f"blank-company fixer: {len(patches)} patchable, {len(flags)} flagged")
    for p in patches:
        print(f"  [{p['source']:<10}] {p['id']} -> company = {p['company']!r}")
    for f in flags:
        print(f"  [FLAG] {f['id']} ({f['name']}): {f['why']}")
    if not apply:
        print("dry run: nothing written. Re-run with --apply to patch.")
        return 0
    n = 0
    for p in patches:
        if proposal_factory.patch(p["id"], {"company": p["company"],
                                            "company_fixed": p["source"]}):
            n += 1
    print(f"applied {n}/{len(patches)} patches via proposal_factory.patch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
