#!/usr/bin/env python3
"""E405: knowledge-decay checker — playbook facts vs reality, starting with
the one pairing the mission calls out explicitly: does
agents/proposal_factory.py's hardcoded PRICING dict still match
business-library/playbooks/pricing-tree.md's "single source of pricing
truth" table? pricing-tree.md's OWN header says "Change a number here,
change it there" — this file is the automated version of remembering to
actually do that.

WHAT: parses pricing-tree.md's "## The ladder" markdown table (SKU, Price,
      When columns) into {sku_label: price_int}, and reads
      proposal_factory.PRICING (imported directly, not re-parsed from
      source text, so this always sees the REAL dict Python actually uses,
      not a regex guess at it) into {tier_key: price_int}. Matches the two
      by SKU NAME (loosely, case-insensitive substring, since
      "Landing page" in the doc vs "Landing page" in PRICING["landing"]
      aren't guaranteed identical strings) and flags any SKU whose price
      differs, plus any SKU present in one source but not matched in the
      other (a genuine "these two need reconciling" signal either way).
WHEN: run periodically (quarterly matches the mission's own cadence word
      for E405/406-style checks) or after any pricing change, real or
      planned. Pure local file/import reads, no LLM call, no network.
RAILS: read-only against business-library/playbooks/pricing-tree.md and
      agents/proposal_factory.PRICING (import, not exec). Only write is
      store/knowledge_decay_report.json (full overwrite each run). No GHL
      writes, no sends. This file does NOT reconcile a mismatch itself
      (never edits pricing-tree.md or proposal_factory.py) — it only
      reports; a human decides which side is stale.

Run:  .venv/bin/python agents/knowledge_decay.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402
from runlog import track  # noqa: E402

PRICING_TREE_MD = Path.home() / "Claude" / "business-library" / "playbooks" / "pricing-tree.md"
OUT = ROOT / "store" / "knowledge_decay_report.json"

# Matches a markdown table row: | SKU | $Price | When |  (tolerant of extra
# whitespace, requires the price cell to start with a literal '$')
_TABLE_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*\$([\d,]+)(?:/mo)?[^|]*\|\s*(.*?)\s*\|\s*$")


def parse_pricing_tree(text: str) -> dict[str, int]:
    """SKU label -> price (int dollars). Only rows under '## The ladder'
    with a numeric $ price are kept; monthly SKUs ($75/mo etc.) keep just
    the number (the /mo is stripped by the regex, not silently dropped —
    it's simply not distinguishable in this int-only comparison, which is
    fine since PRICING's tiers are one-time builds, not recurring, so a
    recurring SKU wouldn't have a PRICING match anyway)."""
    out: dict[str, int] = {}
    in_ladder = False
    for line in text.splitlines():
        if line.strip().startswith("## "):
            in_ladder = line.strip().lower().startswith("## the ladder")
            continue
        if not in_ladder:
            continue
        m = _TABLE_ROW_RE.match(line)
        if not m:
            continue
        sku, price_str, _when = m.groups()
        if sku.strip().lower() in ("sku", "---"):
            continue
        try:
            price = int(price_str.replace(",", ""))
        except ValueError:
            continue
        out[sku.strip()] = price
    return out


def get_pricing_factory() -> dict[str, int]:
    """SKU label -> price, straight from the REAL Python dict (import, not
    a text re-parse), so this always reflects what the code actually does."""
    import proposal_factory
    out = {v["name"]: v["price"] for v in proposal_factory.PRICING.values()}
    for k, v in proposal_factory.CARE.items():
        out[v["name"]] = v["price"]
    return out


def _norm(label: str) -> str:
    return re.sub(r"[^a-z0-9]", "", label.lower())


def compare(doc_prices: dict[str, int], code_prices: dict[str, int]) -> dict:
    """Loose-match doc SKU labels against code SKU labels (normalized,
    substring-tolerant either direction) and report mismatches/unmatched."""
    matched, mismatched, doc_only, code_only = [], [], [], []
    code_used = set()

    for doc_label, doc_price in doc_prices.items():
        doc_norm = _norm(doc_label)
        found = None
        for code_label, code_price in code_prices.items():
            if code_label in code_used:
                continue
            code_norm = _norm(code_label)
            if doc_norm == code_norm or doc_norm in code_norm or code_norm in doc_norm:
                found = (code_label, code_price)
                break
        if found is None:
            doc_only.append({"label": doc_label, "price": doc_price})
            continue
        code_label, code_price = found
        code_used.add(code_label)
        if doc_price == code_price:
            matched.append({"doc_label": doc_label, "code_label": code_label, "price": doc_price})
        else:
            mismatched.append({"doc_label": doc_label, "code_label": code_label,
                              "doc_price": doc_price, "code_price": code_price})

    for code_label, code_price in code_prices.items():
        if code_label not in code_used:
            code_only.append({"label": code_label, "price": code_price})

    # secondary signal: a doc-only and code-only entry sharing the EXACT
    # same price is likely the same SKU under a different name (e.g.
    # "Webfix bundle" in the doc vs "Site fix bundle" in PRICING both $450)
    # rather than two genuinely-different unmatched SKUs — surfaced as a
    # suggestion, not auto-merged, since price-coincidence alone isn't proof.
    likely_same_by_price = []
    for d in doc_only:
        for c in code_only:
            if d["price"] == c["price"]:
                likely_same_by_price.append({"doc_label": d["label"], "code_label": c["label"],
                                            "price": d["price"]})

    return {"matched": matched, "mismatched": mismatched,
            "doc_only": doc_only, "code_only": code_only,
            "likely_same_by_price": likely_same_by_price}


def run() -> dict:
    try:
        doc_text = PRICING_TREE_MD.read_text()
    except OSError as e:
        return {"ok": False, "error": f"could not read {PRICING_TREE_MD}: {e}"}
    doc_prices = parse_pricing_tree(doc_text)
    try:
        code_prices = get_pricing_factory()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"could not import proposal_factory.PRICING: {e}"}
    result = compare(doc_prices, code_prices)
    return {"ok": True, "generated": now_iso(),
            "doc_path": str(PRICING_TREE_MD), "doc_sku_count": len(doc_prices),
            "code_sku_count": len(code_prices), **result}


def main() -> int:
    with track("knowledge_decay"):
        data = run()
        if data["ok"]:
            OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    if not data["ok"]:
        print(f"knowledge_decay: FAIL — {data['error']}")
        return 2

    n_mismatch = len(data["mismatched"])
    n_doc_only = len(data["doc_only"])
    n_code_only = len(data["code_only"])
    print(f"knowledge_decay: {len(data['matched'])} SKU(s) matched, {n_mismatch} PRICE MISMATCH, "
          f"{n_doc_only} doc-only, {n_code_only} code-only -> {OUT}")
    for m in data["mismatched"]:
        print(f"  MISMATCH: {m['doc_label']!r} says ${m['doc_price']}, but "
              f"proposal_factory's {m['code_label']!r} says ${m['code_price']}")
    for d in data["doc_only"]:
        print(f"  doc-only (pricing-tree.md has it, PRICING dict doesn't): {d['label']} ${d['price']}")
    for c in data["code_only"]:
        print(f"  code-only (PRICING dict has it, pricing-tree.md doesn't match it): {c['label']} ${c['price']}")
    for s in data.get("likely_same_by_price", []):
        print(f"  SUGGESTION: {s['doc_label']!r} (doc) and {s['code_label']!r} (code) share the same "
              f"${s['price']} price, probably the same SKU under a different name, not a real gap")

    if n_mismatch:
        try:
            planner.feed_add("warn", f"{n_mismatch} pricing mismatch(es) vs pricing-tree.md",
                            "run agents/knowledge_decay.py for details")
        except Exception:  # noqa: BLE001
            pass
    return 1 if n_mismatch else 0


if __name__ == "__main__":
    raise SystemExit(main())
