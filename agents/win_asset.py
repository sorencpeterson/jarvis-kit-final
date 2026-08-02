#!/usr/bin/env python3
"""B4: win/case-study asset generator. The ledger holds one real win (the $1,200
Acme Co Soft white-label build). That proof compounds every future proposal, but
only if it exists as a sendable artifact. This turns the verified record into two
DRAFTS: a proof one-pager and a LinkedIn post. Nothing posts, nothing sends.

WHAT: reads the real win record wherever it actually lives: store/ledger.jsonl
      (kind won/payment/closed with amount > 0, the source of truth per
      business-library/sops/log-a-win.md), enriched from store/won_patterns.jsonl
      (won_mining.py output, if any), store/close_prob.json (the GHL deal
      snapshot), store/contact_graph.json (source tags), and
      ~/Claude/wl-webdev-import-master.csv (the client's own site/location).
      Builds a VERIFIED-FACTS block, then drafts via planner._cli
      (feature="content", the public-facing Sonnet lane) with
      business-library/VOICE-SPEC.md injected verbatim. Both drafts pass through
      store_lib.humanize() before writing.
WHEN: after a win is logged (log-a-win.md flow), or ad hoc to refresh the assets.
      One-shot; re-running overwrites the drafts with a fresh generation.
RAILS: read-only against every store except its two draft files
      (store/drafts/win_onepager.md, store/drafts/win_linkedin.md) and one feed
      line. DRAFTS ONLY: no posting, no sending, no GHL writes. Facts are decided
      BEFORE the model sees them; anything not in the record is flagged
      "[GAP: ...]" in the doc, never invented. No client quotes are fabricated:
      the one-pager's quotable line is explicitly labeled as proposed and pending
      the client's sign-off. If the claude CLI is unavailable the module writes a
      mechanical verified-facts skeleton instead of failing (fresh-install safe).

Run:  .venv/bin/python agents/win_asset.py [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso, humanize, voice_spec  # noqa: E402
import planner  # noqa: E402

LEDGER = ROOT / "store" / "ledger.jsonl"
WON_PATTERNS = ROOT / "store" / "won_patterns.jsonl"
CLOSE_PROB = ROOT / "store" / "close_prob.json"
CONTACT_GRAPH = ROOT / "store" / "contact_graph.json"
WL_IMPORT = Path.home() / "Claude" / "wl-webdev-import-master.csv"
DRAFTS = ROOT / "store" / "drafts"
ONEPAGER = DRAFTS / "win_onepager.md"
LINKEDIN = DRAFTS / "win_linkedin.md"

WON_KINDS = ("won", "payment", "closed")  # log-a-win.md's exact ledger filter
LLM_TIMEOUT = 180
LLM_FEATURE = "content"  # public-facing writing lane (Sonnet per store/config.json)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:  # per-line guard: one bad line must never blank the source
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def load_win() -> dict | None:
    """The biggest (then latest) confirmed win in the ledger, or None. The ledger
    is the single source of truth for closed money (log-a-win.md)."""
    wins = [r for r in _read_jsonl(LEDGER)
            if r.get("kind") in WON_KINDS and (r.get("amount") or 0) > 0]
    if not wins:
        return None
    wins.sort(key=lambda r: (-(r.get("amount") or 0), r.get("ts") or ""))
    return wins[0]


def _match(needle: str, hay: str) -> bool:
    return bool(needle) and needle.lower() in (hay or "").lower()


def enrich(win: dict) -> dict:
    """Join every store that mentions this client. Read-only, all guarded; a
    missing store just means fewer verified facts, never a crash."""
    note = win.get("note") or ""
    client = note.split(" - ")[0].strip() or note.strip() or "unknown"
    offer = note.split(" - ")[1].strip() if " - " in note else ""
    out = {"ledger": win, "client": client, "offer": offer, "deal": None,
           "pattern": None, "graph": None, "import_row": None}

    for p in _read_jsonl(WON_PATTERNS):
        if _match(client, p.get("name") or ""):
            out["pattern"] = p
            break
    try:
        for d in json.loads(CLOSE_PROB.read_text()).get("deals", []):
            if _match(client, d.get("name") or ""):
                out["deal"] = d
                out["deal_snapshot"] = json.loads(CLOSE_PROB.read_text()).get("generated", "")
                break
    except (OSError, json.JSONDecodeError):
        pass
    try:
        for person in json.loads(CONTACT_GRAPH.read_text()).get("people", []):
            if _match(client, person.get("name") or ""):
                out["graph"] = person
                break
    except (OSError, json.JSONDecodeError):
        pass
    if WL_IMPORT.exists():
        try:
            for r in csv.DictReader(open(WL_IMPORT, newline="")):
                if _match(client, r.get("Company Name") or ""):
                    out["import_row"] = r
                    break
        except (OSError, csv.Error):
            pass
    return out


def facts_block(w: dict) -> tuple[str, list[str]]:
    """The verified-facts block the model is confined to, plus the honest gap
    list. Facts are decided here, in code, before any model sees them."""
    led = w["ledger"]
    amount = led.get("amount") or 0
    facts = [
        f"- Client: {w['client']}" + (f" (deal note: {led.get('note')})" if led.get("note") else ""),
        f"- Money confirmed: ${amount:,.0f}, logged {str(led.get('ts') or '')[:10]} "
        f"in the ledger (kind={led.get('kind')}). This is real closed cash, not pipeline.",
    ]
    if w.get("offer"):
        facts.append(f"- Offer per the deal note: {w['offer']} "
                     "(WL Webdev = white-label web development, built under the agency's brand).")
    deal = w.get("deal")
    if deal:
        facts.append(f"- GHL deal record: \"{deal.get('name')}\", value ${deal.get('value') or 0:,.0f}, "
                     f"{deal.get('age_days')}d old at the {str(w.get('deal_snapshot') or '')[:10]} snapshot.")
        try:  # arithmetic on the record only: deal-open to cash-logged span
            snap = datetime.fromisoformat(w.get("deal_snapshot") or "")
            won = datetime.fromisoformat(led.get("ts") or "")
            open_to_close = float(deal.get("age_days") or 0) - max(
                0.0, (snap - won).total_seconds() / 86400.0)
            if 0 < open_to_close < 60:
                facts.append(f"- Deal opened to cash logged: about {open_to_close:.0f} days "
                             "(derived from the two record timestamps above).")
        except (ValueError, TypeError):
            pass
        if deal.get("factors", {}).get("had_proposal_open") is False:
            facts.append("- The proposal page was never opened; this closed in the "
                         "conversation itself, not off the proposal link.")
    graph = w.get("graph")
    if graph:
        tags = ", ".join(graph.get("tags") or [])
        if tags:
            facts.append(f"- Source (contact-graph tags): {tags}. "
                         "wl-webdev-* tags mean this came through the cold white-label "
                         "webdev email list and its sequence.")
        if graph.get("emails"):
            facts.append(f"- Contact email on record: {graph['emails'][0]}")
    row = w.get("import_row")
    if row:
        loc = ", ".join(x for x in ((row.get("City") or "").strip(),
                                    (row.get("State") or "").strip()) if x)
        facts.append(f"- Client's own site: {row.get('Website')}"
                     + (f" ({loc})" if loc else ""))

    gaps = []
    if not w.get("pattern"):
        gaps.append("no won_patterns.jsonl arc record exists yet (won_mining.py has not "
                    "mined this contact): objections raised, touch count, and the winning "
                    "conversation tail are not on record")
    gaps.append("no client quote or testimonial is on record "
                "(testimonial-ask is the play, EXECUTION-PACK/testimonial-ask.md)")
    gaps.append("no build timeline (handoff date, delivery date, revision rounds) is on record")
    gaps.append("what specifically was broken before the build is not on record "
                "(no teardown/audit artifact references this client)")
    return "\n".join(facts), gaps


ONEPAGER_PROMPT = """You write as [OWNER], [OWNER_COMPANY]. First person. Opinionated.

VOICE SPEC (law, obey verbatim):
%s

TASK: a one-page proof one-pager in markdown for a real closed win. Audience: agency
owners deciding whether to hand [OWNER] their overflow web builds. Sections, in order:
a short punchy title, What was broken, What got built, Time to live, Result, and
"Proposed pull quote (pending client sign-off)".

HARD RULES:
- Use ONLY the verified facts below. Every number must come from the facts block.
- NEVER invent metrics, dates, praise, or client words. Where the record is silent,
  write the marker [GAP: what is missing] on its own line instead of filling it in.
- The pull quote is a line [OWNER] will ASK the client to approve. Label it exactly as
  proposed and pending sign-off. Do not present it as something the client said.
- No em-dashes, no en-dashes, no emojis, no hashtags. Short sentences. Contractions.
- Under 300 words of body. End the Result section on the real dollar figure.

VERIFIED FACTS (the complete record, nothing else exists):
%s

KNOWN GAPS (flag these honestly in the doc where they belong):
%s

Output ONLY the markdown document, nothing else."""

LINKEDIN_PROMPT = """You write as [OWNER], [OWNER_COMPANY]. First person. Opinionated.

VOICE SPEC (law, obey verbatim):
%s

TASK: one LinkedIn post drafting this real win as proof. Audience: agency owners with
a website fulfillment bottleneck. Open with the diagnosis flip or the receipt (see the
voice spec's "actual moves"). Under 120 words. End on a short imperative.

HARD RULES:
- Use ONLY the verified facts below. Real numbers only; the dollar figure is real,
  use it. NEVER invent metrics, timelines, or client praise.
- If a detail is not in the facts, write around it. Do not fabricate. Do not use
  [GAP] markers in this one; a post just omits what it does not know.
- Client name: the record names the client, so you may name them as the record does.
- No em-dashes, no en-dashes, no emojis, no hashtags, no links.

VERIFIED FACTS (the complete record, nothing else exists):
%s

Output ONLY the post text, nothing else."""


def _fallback_onepager(facts: str, gaps: list[str]) -> str:
    gap_lines = "\n".join(f"- [GAP: {g}]" for g in gaps)
    return (f"# One real win, on the record\n\n"
            f"_Mechanical draft (claude CLI unavailable). Verified facts only._\n\n"
            f"## The record\n{facts}\n\n## Known gaps\n{gap_lines}\n")


def _fallback_linkedin(facts: str) -> str:
    return ("Draft placeholder (claude CLI unavailable). Verified facts to write from:\n\n"
            + facts + "\n\nDM me.\n")


def generate(w: dict) -> tuple[str, str, list[str]]:
    """Build both drafts. Facts first, model second, humanize() always."""
    facts, gaps = facts_block(w)
    spec = voice_spec()
    gap_txt = "\n".join(f"- {g}" for g in gaps)
    one = planner._cli(ONEPAGER_PROMPT % (spec, facts, gap_txt),
                       timeout=LLM_TIMEOUT, feature=LLM_FEATURE)
    li = planner._cli(LINKEDIN_PROMPT % (spec, facts),
                      timeout=LLM_TIMEOUT, feature=LLM_FEATURE)
    one = (one or "").strip() or _fallback_onepager(facts, gaps)
    li = (li or "").strip() or _fallback_linkedin(facts)
    return humanize(one), humanize(li), gaps


def run(dry: bool = False) -> dict:
    win = load_win()
    if not win:
        print("win_asset: no confirmed win in store/ledger.jsonl yet. "
              "Honest empty state, nothing to draft from.")
        return {"ok": False, "reason": "no won ledger entry"}

    w = enrich(win)
    one, li, gaps = generate(w)

    if dry:
        print("win_asset (dry run): nothing written\n")
        print("=== win_onepager.md ===\n" + one + "\n")
        print("=== win_linkedin.md ===\n" + li)
        return {"ok": True, "dry": True, "gaps": gaps}

    DRAFTS.mkdir(parents=True, exist_ok=True)
    header = (f"<!-- DRAFT, generated {now_iso()} by agents/win_asset.py from the "
              f"verified ledger record. Nothing posts without [OWNER]. -->\n\n")
    ONEPAGER.write_text(header + one + "\n")
    LINKEDIN.write_text(header + li + "\n")
    print(f"win_asset: 2 draft(s) written -> {ONEPAGER} and {LINKEDIN} "
          f"({len(gaps)} gap(s) flagged, see the one-pager)")
    try:
        planner.feed_add("agent", f"Win assets drafted from the ${win.get('amount'):,.0f} "
                                  f"{w['client']} record: one-pager + LinkedIn post in "
                                  "store/drafts/. Drafts only, your click to use.")
    except Exception:  # noqa: BLE001 - feed logging is best-effort, never blocks the run
        pass
    return {"ok": True, "onepager": str(ONEPAGER), "linkedin": str(LINKEDIN), "gaps": gaps}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.dry_run:
        run(dry=True)
        return 0
    from runlog import track
    with track("win_asset"):
        run(dry=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
