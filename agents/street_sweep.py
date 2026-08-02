#!/usr/bin/env python3
"""Market street sweep (biz D64): after a close, audit the client's local competitors
and stage the expansion ammo. READ-ONLY against GHL and the web; sends nothing.

Two input modes:
  street_sweep.py --niche hvac --city "St George"     # same-niche GHL contacts in that city
  street_sweep.py --urls competitors.txt --niche hvac  # a hand-fed list of competitor sites

Output: store/sweeps/<niche>-<city>-<date>.md — ranked by how broken their site is,
each with the 2-3 concrete faults and a ready first-line for outreach (VOICE-SPEC).
The pitch this powers: "3 of your 5 competitors have broken sites. Want the town?"
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import ghl_social  # noqa: E402

QA = Path.home() / "Claude" / "elementor-recoder" / "qa.py"
VENV = ROOT / ".venv" / "bin" / "python"
OUT = ROOT / "store" / "sweeps"


def _loc() -> str:
    for line in (ghl_social.GHL / ".env").read_text().splitlines():
        if line.startswith("GHL_LOCATION_ID="):
            return line.split("=", 1)[1].strip()
    return ""


def ghl_candidates(niche: str, city: str) -> list[dict]:
    """Same-niche contacts in the city from GHL (read-only)."""
    try:
        raw = ghl_social._api(["GET", f"/contacts/?locationId={_loc()}&query={city}&limit=50"])
        d = json.loads(raw[raw.find("{"):], strict=False)
    except Exception as e:  # noqa: BLE001
        print(f"GHL read failed: {e}")
        return []
    out = []
    for c in (d.get("contacts") or []):
        blob = json.dumps(c).lower()
        site = (c.get("website") or "").strip()
        # a leading '-' would be parsed by qa.py's argparse as a flag (argv injection);
        # normalize scheme so it's always a real positional URL (2026-07-07 audit S4)
        if site.startswith("-"):
            continue
        if site and not site.startswith(("http://", "https://")):
            site = "https://" + site
        if niche.lower() in blob and site:
            out.append({"name": c.get("contactName") or c.get("companyName") or "?",
                        "site": site, "contact_id": c.get("id")})
    return out


def audit(url: str) -> dict:
    try:
        p = subprocess.run([str(VENV), str(QA), url, "--max-pages", "6"],
                           capture_output=True, text=True, timeout=300)
        head = next((ln for ln in p.stdout.splitlines() if "FAIL" in ln and "WARN" in ln), "")
        m = re.search(r"\*\*(\d+) FAIL / (\d+) WARN\*\*", head)
        fails, warns = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        findings = [ln[2:].replace("**", "") for ln in p.stdout.splitlines()
                    if ln.startswith("- **")][:3]
        return {"ok": True, "fails": fails, "warns": warns, "findings": findings}
    except subprocess.TimeoutExpired:
        return {"ok": False, "fails": 0, "warns": 0,
                "findings": ["site took over 5 minutes to crawl, which is a finding in itself"]}
    except OSError as e:
        return {"ok": False, "fails": 0, "warns": 0, "findings": [str(e)[:80]]}


def run(niche: str, city: str = "", urls_file: str = "") -> Path | None:
    targets = []
    if urls_file:
        for ln in Path(urls_file).read_text().splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                targets.append({"name": ln.split("//")[-1].split("/")[0], "site": ln})
    elif city:
        targets = ghl_candidates(niche, city)
    if not targets:
        print("no targets found (give --urls or a city with same-niche GHL contacts)")
        return None
    print(f"sweeping {len(targets)} {niche} sites" + (f" in {city}" if city else ""))
    rows = []
    for t in targets[:12]:
        r = audit(t["site"])
        score = r["fails"] * 3 + r["warns"]
        rows.append({**t, **r, "score": score})
        print(f"  {t['name'][:30]:32} fails={r['fails']} warns={r['warns']}")
    rows.sort(key=lambda r: -r["score"])
    broken = sum(1 for r in rows if r["score"] >= 3)
    OUT.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", f"{niche}-{city or 'list'}".lower()).strip("-")
    fp = OUT / f"{slug}-{date.today().isoformat()}.md"
    lines = [f"# Street sweep: {niche}" + (f" in {city}" if city else ""),
             f"_{now_iso()[:10]} · {len(rows)} audited · {broken} materially broken_", "",
             f"**The pitch line:** \"{broken} of the {len(rows)} {niche} sites we checked in "
             f"{city or 'your market'} have real problems. Want the town?\"", ""]
    for r in rows:
        lines.append(f"## {r['name']} · score {r['score']}")
        lines.append(f"{r['site']}")
        for f in r["findings"]:
            lines.append(f"- {f}")
        if r["score"] >= 3:
            lines.append(f"Opener: your site has {r['findings'][0].split('`')[-1].strip() if r['findings'] else 'problems'}. "
                         "We just rebuilt one of your competitors. Worth 15 minutes?")
        lines.append("")
    fp.write_text("\n".join(lines))
    print(f"sweep written: {fp}")
    return fp


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--niche", required=True)
    ap.add_argument("--city", default="")
    ap.add_argument("--urls", default="")
    a = ap.parse_args()
    if not (a.city or a.urls):
        ap.error("need --city or --urls")
    run(a.niche, a.city, a.urls)
