#!/usr/bin/env python3
"""The TAM sheet (Q261) — NOT a scraper. A calculator: an honest count of
target businesses per niche per metro, seeded from placeholder estimates
CLEARLY marked ESTIMATE (not scraped/verified), plus the math showing the
path to $1M (what % of TAM does that actually require).

The seed config below (NICHE_METRO_ESTIMATES) is a starting point [OWNER]
should replace with real counts (Census County Business Patterns, state
licensing boards, or a paid data source) whenever he wants the sheet to be
more than a sanity check. Every number in it is tagged "ESTIMATE" in the
output on purpose -- this agent's job is the FRAMEWORK and the MATH, not
claiming it scraped anything.

The $1M path math, per business-library/operating-model.md's stated target
($1M/yr = $83.3k/mo): what fraction of the combined TAM would [OWNER]
Digital need to convert (at avg deal value) to hit that number. This is
compared against the operating model's actual stacked-engine plan (builds +
care + installs + retainers + white-label), since the item explicitly says
"the $1M path (needs 0.4% of TAM etc)" -- meaning: is a pure single-SKU
TAM-conversion story even plausible, or does it confirm the model needs the
compounding engines. Both numbers are shown so the sheet argues honestly.

Writes store/tam.json (structured data) + store/tam-analysis.md (the
narrative doc with formula and assumptions spelled out). Run standalone:
.venv/bin/python agents/tam_sheet.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from store_lib import now_iso  # noqa: E402

OUT_JSON = ROOT / "store" / "tam.json"
OUT_MD = ROOT / "store" / "tam-analysis.md"

ANNUAL_TARGET = 1_000_000
AVG_DEAL_VALUE = 1_400  # matches operating-model.md's "8-12/mo × $1,400 avg (mix incl. booking tier)"

# ESTIMATE ONLY -- seed placeholders, not scraped/verified counts. [OWNER]:
# replace with Census CBP / licensing-board / paid-data numbers when it's
# worth the accuracy. Metros chosen as illustrative examples, not a
# commitment to those specific markets.
NICHE_METRO_ESTIMATES = {
    "hvac": {
        "Phoenix-AZ": {"business_count_estimate": 850, "source": "ESTIMATE: rough per-capita guess, not verified"},
        "Dallas-TX": {"business_count_estimate": 1100, "source": "ESTIMATE: rough per-capita guess, not verified"},
        "Salt Lake City-UT": {"business_count_estimate": 220, "source": "ESTIMATE: rough per-capita guess, not verified"},
    },
    "plumbing": {
        "Phoenix-AZ": {"business_count_estimate": 700, "source": "ESTIMATE: rough per-capita guess, not verified"},
        "Dallas-TX": {"business_count_estimate": 950, "source": "ESTIMATE: rough per-capita guess, not verified"},
        "Salt Lake City-UT": {"business_count_estimate": 190, "source": "ESTIMATE: rough per-capita guess, not verified"},
    },
    "salon": {
        "Phoenix-AZ": {"business_count_estimate": 1400, "source": "ESTIMATE: rough per-capita guess, not verified"},
        "Dallas-TX": {"business_count_estimate": 1900, "source": "ESTIMATE: rough per-capita guess, not verified"},
        "Salt Lake City-UT": {"business_count_estimate": 340, "source": "ESTIMATE: rough per-capita guess, not verified"},
    },
    "local_service_general": {
        "Phoenix-AZ": {"business_count_estimate": 3000, "source": "ESTIMATE: rough per-capita guess, not verified"},
        "Dallas-TX": {"business_count_estimate": 4200, "source": "ESTIMATE: rough per-capita guess, not verified"},
        "Salt Lake City-UT": {"business_count_estimate": 780, "source": "ESTIMATE: rough per-capita guess, not verified"},
    },
}


def compute_tam(estimates: dict) -> dict:
    niches = {}
    grand_total = 0
    for niche, metros in estimates.items():
        total = sum(m["business_count_estimate"] for m in metros.values())
        grand_total += total
        niches[niche] = {"metros": metros, "niche_total": total}
    return {"niches": niches, "grand_total_tam": grand_total}


def _1m_path_math(tam: dict, annual_target: int, avg_deal: float) -> dict:
    grand_total = tam["grand_total_tam"]
    deals_needed = annual_target / avg_deal if avg_deal else 0
    pct_of_tam_needed = (deals_needed / grand_total) if grand_total else None
    return {
        "annual_target": annual_target,
        "avg_deal_value": avg_deal,
        "deals_needed_per_year": round(deals_needed, 1),
        "tam_total_businesses": grand_total,
        "pct_of_tam_needed": round(pct_of_tam_needed, 5) if pct_of_tam_needed is not None else None,
        "pct_of_tam_needed_display": f"{pct_of_tam_needed:.2%}" if pct_of_tam_needed is not None else "n/a (TAM is 0)",
        "formula": "deals_needed_per_year = annual_target / avg_deal_value; "
                  "pct_of_tam_needed = deals_needed_per_year / tam_total_businesses",
        "read": ("a single-SKU pure-conversion story against this TAM alone "
                "would need to convert this % every year forever -- confirms "
                "operating-model.md's stacked-engine plan (care MRR + installs "
                "+ retainers + white-label) is the real path, not raw TAM "
                "conversion at one deal size."),
    }


def _write_markdown(tam: dict, path_math: dict) -> str:
    lines = [
        f"# TAM analysis (generated {now_iso()})", "",
        "**All business_count_estimate values below are ESTIMATE, not scraped or "
        "verified.** Seed placeholders for the framework and math; replace with "
        "Census County Business Patterns, state licensing-board counts, or a paid "
        "data source when the accuracy is worth the effort.", "",
        "## Per-niche, per-metro TAM", "",
    ]
    for niche, data in tam["niches"].items():
        lines.append(f"### {niche} (niche total: {data['niche_total']:,} businesses, ESTIMATE)")
        for metro, m in data["metros"].items():
            lines.append(f"- {metro}: {m['business_count_estimate']:,} businesses ({m['source']})")
        lines.append("")
    lines.append(f"**Combined TAM across all seeded niches/metros: {tam['grand_total_tam']:,} businesses (ESTIMATE)**")
    lines.append("")
    lines.append("## The $1M path — the math")
    lines.append("")
    lines.append(f"Formula: `{path_math['formula']}`")
    lines.append("")
    lines.append(f"- Annual target: ${path_math['annual_target']:,}")
    lines.append(f"- Avg deal value: ${path_math['avg_deal_value']:,} (sourced from "
                 "business-library/operating-model.md's stated builds average)")
    lines.append(f"- Deals needed per year at that avg: {path_math['deals_needed_per_year']:,}")
    lines.append(f"- TAM total (seeded estimate): {path_math['tam_total_businesses']:,}")
    lines.append(f"- **% of TAM needed per year: {path_math['pct_of_tam_needed_display']}**")
    lines.append("")
    lines.append(path_math["read"])
    lines.append("")
    lines.append("## Honest caveats")
    lines.append("- TAM counts are seed placeholders, not verified data. Treat the % "
                 "above as directionally useful (single digits vs double digits), not precise.")
    lines.append("- Real TAM work means pulling actual counts (Census CBP by NAICS code + "
                 "metro, or state contractor-license rolls for HVAC/plumbing) -- that's a "
                 "manual or paid-API task, deliberately NOT automated here per the item's "
                 "own instruction ('NOT scrape').")
    lines.append("- This sheet argues FOR the operating model's stacked-engine plan (care "
                 "MRR, installs, retainers, white-label), not against it -- a pure single-SKU "
                 "TAM-conversion story alone is a much harder path than the blended one.")
    return "\n".join(lines)


def run() -> dict:
    tam = compute_tam(NICHE_METRO_ESTIMATES)
    path_math = _1m_path_math(tam, ANNUAL_TARGET, AVG_DEAL_VALUE)
    result = {"generated": now_iso(), "tam": tam, "path_to_1m": path_math,
             "status": "ESTIMATE -- seed placeholders, see tam-analysis.md for full caveats"}
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, indent=2))
    md = _write_markdown(tam, path_math)
    OUT_MD.write_text(md)
    print(f"tam_sheet: {tam['grand_total_tam']:,} businesses across "
          f"{len(tam['niches'])} niches (ESTIMATE), needs "
          f"{path_math['pct_of_tam_needed_display']} of TAM/yr for $1M at ${AVG_DEAL_VALUE} avg "
          f"-> {OUT_JSON}, {OUT_MD}")
    return result


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
