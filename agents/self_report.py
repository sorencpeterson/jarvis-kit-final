#!/usr/bin/env python3
"""E407: JARVIS self-report — a weekly what-I-did/tokens/cost/errors summary,
so [OWNER] can see what the machine's been up to without reconstructing it
from six separate stores himself.

WHAT: reads store/runs.jsonl (agents/runlog.py's ledger — which agents ran,
      how often, how long, error rate) and store/usage.jsonl
      (app/planner.py's per-call token log — which features spent tokens,
      how many, on which models) over the last N days (default 7), and
      assembles a plain markdown report: per-agent run counts + error rates,
      per-feature token totals + a rough $ estimate (see COST NOTE below),
      and the actual error messages from any failed run so a real problem
      is visible, not just a number.
COST NOTE (honest, not a guess dressed as fact): $ estimates use
      MODEL_PRICES_PER_MTOK below, a hand-maintained table (see also E405's
      knowledge-decay checker, which is the RIGHT tool to periodically flag
      if these prices go stale — this file just uses whatever's here). If a
      model in usage.jsonl isn't in the table, its tokens are counted but
      excluded from the $ total (reported separately as "unpriced"), never
      silently assumed free or guessed at a wrong rate.
WHEN: run weekly (or ad hoc). Pure local reads, no LLM call, no network.
RAILS: read-only against store/runs.jsonl and store/usage.jsonl. Only write
      is store/self_report.md (full overwrite each run). No GHL writes, no
      sends.

Run:  .venv/bin/python agents/self_report.py
      .venv/bin/python agents/self_report.py --days 30
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
from runlog import track  # noqa: E402

RUNS = ROOT / "store" / "runs.jsonl"
USAGE = ROOT / "store" / "usage.jsonl"
OUT = ROOT / "store" / "self_report.md"
DEFAULT_DAYS = 7

# $ per million tokens (input, output). Hand-maintained; see the module
# docstring's COST NOTE. Approximate list-price tiers as of this writing.
MODEL_PRICES_PER_MTOK = {
    "claude-haiku-4-5-20251001": {"in": 1.00, "out": 5.00},
    "claude-sonnet-4-6": {"in": 3.00, "out": 15.00},
}


def _read_jsonl(path: Path) -> list[dict]:
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


def _within_days(ts: str, cutoff_iso: str) -> bool:
    return bool(ts) and ts >= cutoff_iso


def summarize_runs(days: int) -> dict:
    cutoff = (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")
    rows = [r for r in _read_jsonl(RUNS) if _within_days(r.get("start", ""), cutoff)]
    by_agent: dict[str, dict] = defaultdict(lambda: {"runs": 0, "ok": 0, "fail": 0, "errors": []})
    for r in rows:
        a = by_agent[r.get("agent", "?")]
        a["runs"] += 1
        if r.get("ok"):
            a["ok"] += 1
        else:
            a["fail"] += 1
            if r.get("err"):
                a["errors"].append(r["err"])
    return {"total_runs": len(rows), "by_agent": dict(by_agent)}


def summarize_usage(days: int) -> dict:
    cutoff = (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")
    rows = [r for r in _read_jsonl(USAGE) if _within_days(r.get("ts", ""), cutoff)]
    by_feature: dict[str, dict] = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0})
    by_model: dict[str, dict] = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0})
    for r in rows:
        f, m = r.get("feature", "?"), r.get("model", "?")
        by_feature[f]["calls"] += 1
        by_feature[f]["in"] += r.get("in", 0)
        by_feature[f]["out"] += r.get("out", 0)
        by_model[m]["calls"] += 1
        by_model[m]["in"] += r.get("in", 0)
        by_model[m]["out"] += r.get("out", 0)

    priced_cost = 0.0
    unpriced_tokens = 0
    for model, stats in by_model.items():
        price = MODEL_PRICES_PER_MTOK.get(model)
        if price:
            priced_cost += stats["in"] / 1_000_000 * price["in"]
            priced_cost += stats["out"] / 1_000_000 * price["out"]
        else:
            unpriced_tokens += stats["in"] + stats["out"]

    return {"total_calls": len(rows), "by_feature": dict(by_feature),
            "by_model": dict(by_model), "estimated_cost_usd": round(priced_cost, 4),
            "unpriced_tokens": unpriced_tokens}


def render_markdown(runs_summary: dict, usage_summary: dict, days: int) -> str:
    lines = [f"# JARVIS self-report — last {days} day(s)", "",
            f"_generated {now_iso()}_", ""]

    lines.append("## Runs")
    lines.append(f"{runs_summary['total_runs']} total run(s) across "
                f"{len(runs_summary['by_agent'])} runlog-adopted agent(s).")
    lines.append("")
    if runs_summary["by_agent"]:
        lines.append("| agent | runs | ok | fail | error rate |")
        lines.append("|---|---|---|---|---|")
        for agent in sorted(runs_summary["by_agent"].keys()):
            a = runs_summary["by_agent"][agent]
            rate = round(100 * a["fail"] / a["runs"]) if a["runs"] else 0
            lines.append(f"| {agent} | {a['runs']} | {a['ok']} | {a['fail']} | {rate}% |")
        lines.append("")
        any_errors = [(agent, a["errors"]) for agent, a in runs_summary["by_agent"].items() if a["errors"]]
        if any_errors:
            lines.append("### Errors seen")
            for agent, errs in any_errors:
                for e in errs[:3]:
                    lines.append(f"- **{agent}**: {e[:200]}")
            lines.append("")
    else:
        lines.append("No runlog-adopted agent activity in this window (note: most agents in "
                    "this repo haven't adopted runlog yet, so this UNDERCOUNTS real activity "
                    "— see agents/runlog.py's own docstring, it's explicitly opt-in).")
        lines.append("")

    lines.append("## Token usage")
    lines.append(f"{usage_summary['total_calls']} CLI call(s) logged.")
    lines.append("")
    if usage_summary["by_feature"]:
        lines.append("| feature | calls | tokens in | tokens out |")
        lines.append("|---|---|---|---|")
        for feat in sorted(usage_summary["by_feature"].keys()):
            f = usage_summary["by_feature"][feat]
            lines.append(f"| {feat} | {f['calls']} | {f['in']:,} | {f['out']:,} |")
        lines.append("")
    lines.append(f"Estimated cost (priced models only): **${usage_summary['estimated_cost_usd']:.4f}**")
    if usage_summary["unpriced_tokens"]:
        lines.append(f"({usage_summary['unpriced_tokens']:,} token(s) from an unpriced model, "
                    f"excluded from the total above — see MODEL_PRICES_PER_MTOK in this file)")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    args = ap.parse_args()

    with track("self_report"):
        runs_summary = summarize_runs(args.days)
        usage_summary = summarize_usage(args.days)
        md = render_markdown(runs_summary, usage_summary, args.days)
        OUT.write_text(md)

    print(f"self_report: {runs_summary['total_runs']} run(s), {usage_summary['total_calls']} "
          f"usage call(s), ~${usage_summary['estimated_cost_usd']:.4f} over {args.days}d -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
