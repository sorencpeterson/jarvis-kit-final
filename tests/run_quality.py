#!/usr/bin/env python3
"""Quality grading: real recent outputs vs the Fable-authored rubrics (tests/quality/rubrics.md).

Weekly (Mondays, morning.sh after golden). Grades LAST-5 real outputs per category,
writes store/quality_scores.json, flags any dimension under 3.5 or any honesty flag
into the feed + a needs-adjacent alert. Baseline cohort = Fable-era outputs.

Usage: run_quality.py [--force] [--category R1|R2|...]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import planner  # noqa: E402
from store_lib import now_iso  # noqa: E402

RUBRICS = (ROOT / "tests" / "quality" / "rubrics.md").read_text()
OUT = ROOT / "store" / "quality_scores.json"


def _jload(path: Path, n: int = 5, key: str = "id") -> list[dict]:
    if not path.exists():
        return []
    by, order = {}, []
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            continue
        k = r.get(key)
        if k:
            if k not in by:
                order.append(k)
            by[k] = r
    return [by[k] for k in order][-n:]


def collect() -> dict[str, list[str]]:
    """Real recent outputs per rubric category. Fixtures never graded."""
    s = {}
    props = [r for r in _jload(ROOT / "store" / "proposals.jsonl", 40)
             if r.get("status") in ("staged", "sent") and "test" not in (r.get("name") or "").lower()
             and "check" not in (r.get("name") or "").lower()][-5:]
    samples = []
    for r in props:
        try:
            h = (ROOT / "store" / "proposals" / f"{r['id']}.html").read_text()
            lede = re.search(r'class="lede">(.*?)</p>', h, re.S)
            faults = re.findall(r'<div class="fault">.*?<b>(.*?)</b>.*?<p>(.*?)</p>', h, re.S)[:3]
            samples.append(f"[{r.get('name')}] LEDE: {(lede.group(1) if lede else '')[:200]} "
                           f"FAULTS: " + " | ".join(f"{t}: {p[:110]}" for t, p in faults)
                           + f" EMAIL(full): {str(r.get('email_draft'))[:600]}")
        except OSError:
            continue
    s["R1_proposals"] = samples
    s["R2_replies"] = [f"[to {r.get('name')}, re: {str(r.get('their_msg'))[:80]}] {r.get('draft')}"
                       for r in _jload(ROOT / "store" / "replies.jsonl", 30)
                       if r.get("draft") and "test" not in (r.get("name") or "").lower()][-5:]
    s["R2_mail"] = [f"[to {d.get('to')}, subj {d.get('subject')}] {str(d.get('draft'))[:400]}"
                    for d in _jload(ROOT / "store" / "mail_drafts.jsonl", 20)][-5:]
    s["R3_linkedin"] = [f"[{x.get('kind', 'dm')}] {x.get('draft') or x.get('note') or ''}"
                        for x in _jload(ROOT / "store" / "network.jsonl", 30)
                        if (x.get("draft") or x.get("note"))][-5:]
    briefs = []
    for name in ("owner_report.md", "day_plan.md"):
        p = ROOT / "store" / name
        if p.exists():
            briefs.append(f"[{name}] " + p.read_text()[:900])
    s["R5_briefs"] = briefs
    return {k: v for k, v in s.items() if len(v) >= 2}


GRADE = """You are the quality auditor for Alex's second brain. Grade these REAL outputs
against the rubric category {cat}. THE RUBRICS (grade ONLY the named category's dimensions):

{rubrics}

OUTPUTS TO GRADE ({n} samples):
{samples}

Return ONLY JSON: {{"dims": {{"<dimension name>": <avg score 1-5, one decimal>}},
"flags": ["honesty/rule violations, verbatim quote each"], "worst_quote": "...",
"best_quote": "...", "one_fix": "the single change that would raise scores most"}}"""


def run(force: bool = False, only: str = "") -> dict:
    from datetime import date
    if not force and date.today().isoweekday() != 1:
        print("quality grading runs Mondays (--force to override)")
        return {}
    cats = collect()
    if only:
        cats = {k: v for k, v in cats.items() if k.startswith(only)}
    results = {}
    for cat, samples in cats.items():
        rub_section = cat.split("_")[0]
        prompt = GRADE.format(cat=rub_section, rubrics=RUBRICS[:7000],
                              n=len(samples), samples="\n---\n".join(x[:700] for x in samples))
        j = planner._cli_json(prompt, timeout=180, feature="quality_grade") or {}
        if not isinstance(j, dict) or not j.get("dims"):
            print(f"  {cat}: grading failed")
            continue
        results[cat] = {**j, "n": len(samples)}
        low = [d for d, v in j["dims"].items() if isinstance(v, (int, float)) and v < 3.5]
        print(f"  {cat}: " + ", ".join(f"{d}={v}" for d, v in j["dims"].items())
              + (f"  LOW: {low}" if low else "") + (f"  FLAGS: {len(j.get('flags') or [])}" if j.get("flags") else ""))
        if low or j.get("flags"):
            try:
                planner.feed_add("system", f"quality alarm [{cat}]: "
                                 + (f"low dims {low} " if low else "")
                                 + (f"{len(j['flags'])} honesty flag(s)" if j.get("flags") else ""))
            except Exception:  # noqa: BLE001
                pass
    if results:
        OUT.write_text(json.dumps({"graded_at": now_iso(), "categories": results}, indent=1))
        print(f"quality scores -> {OUT}")
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--category", default="")
    a = ap.parse_args()
    run(a.force, a.category)
