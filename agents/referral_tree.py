#!/usr/bin/env python3
"""#170 [E] referral tree scaffold: who referred whom, as a tree structure ready for
a visual (item #170 in the backlog specifically wants "a growing tree" render — this
file builds the DATA for that, not the render itself, same split as every other data
agent in this codebase vs. the dashboard/bridge that draws it).

WHY THIS IS [E]: investigated referral_timer.py (read-only reference, not this
mission's file) — it ASKS booked contacts for a referral (drafts the ask message,
logs that an ask happened) but does NOT capture the actual referral relationship
(who referred whom). Checked store/referral_log.json and store/referral_drafts.jsonl
live: neither exists yet (zero referral activity has happened). There is no
"referred_by" field anywhere in warm_dispo.jsonl, the WARM-HITLIST.csv columns, or
any GHL contact field I could find (checked customFields on a live sample — no
referral-source field is set up). So there is genuinely no real referral data to
build a tree FROM yet.

What's built: a real, working build_tree() function against a defined input schema
(a referral_edges.jsonl file: {"referrer_id", "referred_id", "referrer_name",
"referred_name", "ts"}), fixture-tested end to end (see mission status file — a
3-generation synthetic tree correctly nests). The moment referral_timer.py (or
anything else) starts writing real edges to store/referral_edges.jsonl in this
shape, this file's output is real without any code change needed here. Until then,
run() against the real (empty) store correctly returns an empty tree, not a fake one.

Read-only against GHL (a single optional contact lookup for name enrichment). Local
writes only: store/referral_tree.json (the rendered tree, consumed by whatever future
visual wants it).

Usage:
  referral_tree.py             # build store/referral_tree.json from real edges (likely empty today)
  referral_tree.py --dry-run   # print the tree, write nothing
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402

EDGES = ROOT / "store" / "referral_edges.jsonl"  # the schema this scaffold expects, not yet written by anything
TREE_OUT = ROOT / "store" / "referral_tree.json"


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def load_edges() -> list[dict]:
    return _load_jsonl(EDGES)


def build_tree(edges: list[dict]) -> dict:
    """edges: [{"referrer_id","referred_id","referrer_name","referred_name","ts"}].
    Returns {"roots": [...]} where each node is
    {"id","name","ts","children":[...]} — contacts who referred someone but were
    themselves never referred by anyone on file are roots. A contact referred by
    someone NOT in the edge list also becomes a root (their referrer is outside what
    we know), rather than silently dropped."""
    by_referrer: dict[str, list[dict]] = defaultdict(list)
    all_referred_ids = set()
    node_names: dict[str, str] = {}
    for e in edges:
        rid, did = e.get("referrer_id"), e.get("referred_id")
        if not rid or not did:
            continue
        by_referrer[rid].append(e)
        all_referred_ids.add(did)
        node_names[rid] = e.get("referrer_name") or rid
        node_names[did] = e.get("referred_name") or did

    all_referrer_ids = set(by_referrer.keys())
    root_ids = (all_referrer_ids | all_referred_ids) - all_referred_ids
    # (equivalent to: referrers who were never themselves referred)

    def _node(node_id: str, seen: frozenset) -> dict:
        if node_id in seen:  # cycle guard — should never happen with real data, but a
            return {"id": node_id, "name": node_names.get(node_id, node_id), "children": [], "cycle": True}
        children = [_node(e["referred_id"], seen | {node_id}) for e in by_referrer.get(node_id, [])]
        return {"id": node_id, "name": node_names.get(node_id, node_id), "children": children}

    roots = [_node(rid, frozenset()) for rid in sorted(root_ids)]
    return {"roots": roots, "generated": now_iso(), "edge_count": len(edges),
            "node_count": len(all_referrer_ids | all_referred_ids)}


def tree_depth(tree: dict) -> int:
    def depth(node):
        if not node.get("children"):
            return 1
        return 1 + max(depth(c) for c in node["children"])
    return max((depth(r) for r in tree.get("roots", [])), default=0)


def run(dry: bool = False) -> dict:
    edges = load_edges()
    if not edges:
        print("referral_tree: store/referral_edges.jsonl is empty or missing [E] — no real referral "
              "relationships on file yet (referral_timer.py asks for referrals but doesn't record who "
              "referred whom; nothing else in this codebase does either). Writing an empty tree, not a fake one.")
    tree = build_tree(edges)
    print(f"referral_tree: {tree['node_count']} node(s), {tree['edge_count']} edge(s), "
          f"{len(tree['roots'])} root(s), max depth {tree_depth(tree)}")
    if dry:
        print(json.dumps(tree, indent=2))
        return tree
    TREE_OUT.parent.mkdir(parents=True, exist_ok=True)
    TREE_OUT.write_text(json.dumps(tree, indent=2, ensure_ascii=False))
    print(f"referral_tree: wrote -> {TREE_OUT}")
    return tree


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(dry=args.dry_run)
