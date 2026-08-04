#!/usr/bin/env python3
"""Call prep cards for the warm booked-call block: the one thing the coach didn't have,
per-contact context. For each contact in today's warm_block who already has a staged
proposal, assemble a one-screen card [OWNER] reads before/during the call:
  - their real teardown (the 5 faults from their proposal)
  - the proposal + mockup links (pull them up live: "I sent you something, open it")
  - 3 niche discovery questions + the price anchor
  - the 3 likeliest objections + the exact playbook counters

Deterministic: reads the staged proposal, the niche book, and objections.md. No LLM,
no GHL write, no send. Output: store/prep/warm/<wid>.md, surfaced at /api/callprep.
Runs in the morning chain after warm_block. Idempotent (rewrites the day's cards).
"""
from __future__ import annotations

import csv
import hashlib
import os
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import proposal_factory as pf  # noqa: E402

HITLIST = Path(os.environ.get("WARM_CSV") or (ROOT / "store" / "warm-hitlist.csv"))
PLAYBOOKS = Path(os.environ.get("BIZLIB") or (ROOT / "business-library")) / "playbooks"
NICHE_BOOKS = Path(os.environ.get("BIZLIB") or (ROOT / "business-library")) / "sops" / "niche-books"
BLOCK = ROOT / "store" / "warm_block.json"
OUT = ROOT / "store" / "prep" / "warm"

# 3 discovery questions per ICP, from playbooks/sales-calls.md (ICP A local service).
DISCOVERY = [
    "What's an average client/treatment worth to you? (anchor everything to this)",
    "Where do bookings come from now, and what happens when someone hits your site?",
    "If the phone rang twice more a week, what would that change?",
]


def _wid(phone: str, name: str) -> str:
    return "w_" + hashlib.sha1((phone or name).encode()).hexdigest()[:10]


def _faults_from_proposal(pid: str) -> list[str]:
    """Pull the 5 fault titles from the rendered proposal HTML (deterministic)."""
    f = ROOT / "store" / "proposals" / f"{pid}.html"
    if not f.exists():
        return []
    html = f.read_text()
    # fault blocks render as <b>Title</b><p>explanation</p> inside .fault
    return [re.sub(r"<[^>]+>", "", m).strip()
            for m in re.findall(r'class="fault".*?<b>(.*?)</b>', html, re.S)][:5]


def _top_objections(niche: str, n: int = 3) -> list[tuple[str, str]]:
    """The 3 likeliest objections + counters from objections.md for this niche."""
    try:
        txt = (PLAYBOOKS / "objections.md").read_text()
    except OSError:
        return []
    pairs = re.findall(r'\*\*\d+\.\s*"?(.*?)"?\*\*\nSay:\s*"(.*?)"', txt, re.S)
    # price + timing + "my nephew" are the near-universal three
    want = ["too expensive", "think about", "nephew", "already have", "budget"]
    picked, seen = [], set()
    for w in want:
        for q, a in pairs:
            if w in q.lower() and q not in seen:
                picked.append((q.strip(), a.strip()))
                seen.add(q)
                break
        if len(picked) >= n:
            break
    return picked[:n]


def _niche_note(niche: str) -> str:
    n = (niche or "").lower()
    book = "medspa" if any(k in n for k in ("spa", "clinic", "aesthetic", "wellness", "iv")) else \
           "salon" if "salon" in n or "boutique" in n else \
           "hvac" if any(k in n for k in ("hvac", "plumb", "roof", "electric")) else ""
    if not book:
        return ""
    f = NICHE_BOOKS / f"{book}.md"
    if not f.exists():
        return ""
    # first paragraph after the header = the one-liner
    for ln in f.read_text().splitlines():
        ln=ln.strip()
        if ln and not ln.startswith(("#","_","*","-")) and len(ln)>30:
            return ln[:200]
    return ""


def build() -> int:
    try:
        block = json.loads(BLOCK.read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        print("no warm block yet")
        return 0
    picks = block.get("picks") or []
    if not picks:
        print("warm block empty")
        return 0
    # map staged proposals by normalized name
    props = {}
    for r in pf.load_queue():
        if r.get("status") in ("staged", "sent", "sending"):
            props[(r.get("name") or "").strip().lower()] = r
    OUT.mkdir(parents=True, exist_ok=True)
    made = 0
    for pk in picks:
        name = (pk.get("name") or "").strip()
        niche = pk.get("niche") or ""
        prop = props.get(name.lower())
        wid = pk.get("id") or _wid("", name)
        faults = _faults_from_proposal(prop["id"]) if prop else []
        objs = _top_objections(niche)
        lines = [f"# CALL PREP: {name}", f"_{niche} · booked a call, went cold. Warmest money there is._", ""]
        if prop:
            tierp = f"{prop.get('tier')} ${prop.get('price'):,}"
            lines += [f"**Recommend:** {tierp}. Anchor: one missed client/month pays for the whole site.",
                      f"**Proposal (pull it up on the call):** {prop.get('link', '(build one)')}", ""]
        note = _niche_note(niche)
        if note:
            lines += [f"**{niche} note:** {note}", ""]
        if faults:
            lines += ["**What's wrong with their site (say one, not all five):**"]
            lines += [f"- {t}" for t in faults]
            lines += [""]
        lines += ["**Open with their reason, then dig (their numbers, said back to them):**"]
        lines += [f"- {q}" for q in DISCOVERY]
        lines += [""]
        if objs:
            lines += ["**Likeliest objections -> say this, then STOP TALKING:**"]
            for q, a in objs:
                lines += [f'- "{q}"', f"  -> {a}"]
            lines += [""]
        lines += ["**Close:** \"Want it handled?\" Yes -> deposit link + logo today. "
                  "Maybe -> name the objection, use the counter, ask once more, then set a 48h deadline."]
        (OUT / f"{wid}.md").write_text("\n".join(lines))
        made += 1
    print(f"call_prep: wrote {made} prep card(s) to {OUT}")
    return made


if __name__ == "__main__":
    build()
