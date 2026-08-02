#!/usr/bin/env python3
"""Content-engager DM lane ([OWNER]'s ask, 2026-07-15): "We need to reach out and DM
people who interacted with our stuff, and people who could be a great fit."

THE CONTRACT (operator side): the LinkedIn Chrome operator, while sourcing or
executing (see agents/prompts/li_operator_brief.md, Step 2.5), appends people who
liked/commented on [OWNER]'s OWN posts to store/li_engagers.jsonl:

    {"url": "<profile url>", "name": "<their name>", "headline": "<their headline>",
     "degree": "1st"|"2nd"|"3rd", "interaction": "commented: <their words, short>"
                                              or "liked post: <post topic>",
     "ts": "<ISO ts>"}

What this module does with those rows (brain side, all drafts land status="pending"
in networking's queue — NOTHING sends without [OWNER]'s approval, same as every kind):
  - degree 1st  -> draft an agency-fit opener DM from [OWNER]'s own 5 approved template
                   shapes (rotated deterministically per person), quality-gated, queued
                   as kind="dm". Releases through networking.approved_to_run() under
                   the daily dm cap like everything else.
  - degree 2nd+ -> you can't DM a non-connection, so stage a CONNECT item instead
                   (existing kind, existing caps); when it's accepted the li_conveyor
                   day-2 ladder takes over, and the interaction context rides along in
                   the headline so the sourcing shows WHY they were picked.

Engagers are the warmest cold audience there is (they already raised a hand on his
content), which is exactly the warm-over-cold weighting [OWNER] wants.

Idempotent: one opener DM per person ever from this lane — a ("dm", url) already in
network.jsonl (any status, either lane) permanently skips the row. Connect fallback
dedupes through queue_connections' own seen/history/cooldown filters.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import new_id, now_iso, humanize  # noqa: E402
import networking  # noqa: E402
import planner  # noqa: E402
import li_quality  # noqa: E402

ENGAGERS = ROOT / "store" / "li_engagers.jsonl"
MAX_DM_DRAFTS_PER_RUN = 10  # bound the LLM fan-out; the daily dm send-cap is enforced
#                             separately at release time by networking.allowance()

# [OWNER]'s 5 approved opener angles (2026-07-15, his exact copy). Drafts copy the SHAPE
# of the assigned angle — greeting, short observation, soft capacity question — with the
# person's real first name and one natural touch of their own context. Never pasted
# verbatim to hundreds of people: identical bulk copy is a LinkedIn spam fingerprint.
TEMPLATES = [
    "Hey Sarah,\n\nI noticed your agency has been putting out a lot of great work "
    "lately.\n\nQuick question. When website projects come in, do you build them "
    "in-house, or do you ever partner with outside teams when capacity gets tight?",

    "Hi James,\n\nI've been following some of your recent client wins. Congrats.\n\n"
    "I'm curious. Have website builds ever become the bottleneck that slows everything "
    "else down for your team?",

    "Hey Chris,\n\nRandom question.\n\nWhen a client asks for a new website, what's "
    "usually harder for your agency: finding the time to build it or keeping the "
    "quality high while juggling everything else?",

    "Hi Megan,\n\nI've spoken with a few agency owners recently who said websites are "
    "great revenue but can completely wreck their production schedule.\n\nHas that "
    "ever been an issue for your team?",

    "Hey Alex,\n\nQuick question for you.\n\nDo you ever turn down website projects "
    "because your team is already booked, or do you usually find a way to squeeze "
    "them in?",
]

DM_PROMPT = """You are [OWNER], sending a FIRST direct message on LinkedIn to someone who
recently engaged with his content (liked or commented on his posts). He runs a
white-label web team that builds sites for agencies. The goal is to open a real
conversation about how they handle website builds, NOT to pitch.

Voice: direct, punchy, first person, contractions always, no fluff, no emojis,
ABSOLUTELY no em-dashes or en-dashes (commas or periods only).

Write ONE message following the SHAPE of this approved template (structure, length,
soft question-first angle), never copied word for word:

TEMPLATE:
%s

Them:
%s

Rules:
- Greet them by their real FIRST NAME (from the context above), matching the
  template's greeting style.
- Where it fits naturally, reference their actual engagement or their agency in one
  short clause. If the context is too thin, stay general rather than faking a
  specific.
- If you compliment them, keep it RESTRAINED and specific, the way a peer who has
  done the work talks: "solid case study, that kind of ROAS is strong for e-com"
  beats "I love your work." Understatement reads as expertise. Gushing reads as
  desperate. Prefer no compliment over an eager one.
- If their context shows a GENUINE commonality (same niche, a view they voiced in
  their comment, a shared background), name it in one honest clause. Never invent one.
- Keep the template's blank-line paragraph breaks and its single question. Exactly
  one question mark, at the end.
- No links, no pricing, no service list, no "hope you're well" filler.
- Under 500 characters total.

Return ONLY a JSON object: {"draft": "..."}"""


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


def load_engagers() -> list[dict]:
    """Last-write-wins by normalized url (an operator may re-capture the same person
    across runs), same convention li_conveyor.load_accepted() uses."""
    import li_history
    by_url: dict[str, dict] = {}
    order: list[str] = []
    for rec in _read_jsonl(ENGAGERS):
        uk = li_history._url_key(rec.get("url", ""))
        if not uk or not rec.get("name"):
            continue
        if uk not in by_url:
            order.append(uk)
        by_url[uk] = rec
    return [by_url[u] for u in order]


def _template_for(url: str) -> str:
    """Stable per-person angle: same person always gets the same template on a re-run
    (idempotent drafting), and the 5 angles spread evenly across a batch."""
    h = int(hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:8], 16)
    return TEMPLATES[h % len(TEMPLATES)]


def draft_fit_dm(rec: dict) -> str:
    bits = [f"Name: {rec.get('name', '')}"]
    if rec.get("headline"):
        bits.append(f"Headline: {rec['headline']}")
    if rec.get("interaction"):
        bits.append(f"How they engaged with [OWNER]'s content: {rec['interaction']}")
    out = planner._cli_json(DM_PROMPT % (_template_for(rec.get("url", "")), "\n".join(bits)),
                            timeout=90, feature="networking")
    if isinstance(out, dict):
        return humanize((out.get("draft") or "").strip())
    return ""


def run(dry: bool = False) -> dict:
    engagers = load_engagers()
    if not engagers:
        return {"engagers": 0, "queued_dms": [], "queued_connects": [],
                "note": "no store/li_engagers.jsonl rows yet (operator hasn't captured "
                        "content engagers — machinery is ready, waiting on operator runs)"}
    seen = networking._seen_urls()
    dm_ready, connect_ready = [], []
    for rec in engagers:
        url = rec.get("url", "")
        if not url:
            continue
        if rec.get("skip"):
            # a human (or the orchestrator) marked this capture do-not-contact, with the
            # reason in the value (first real capture 2026-07-15: [OWNER]'s own dad liked
            # his posts — family and friends engage too, and must never get a pitch DM
            # or a connect). Honored before every other gate.
            continue
        if li_quality.is_never_engage(rec.get("interaction", ""), rec.get("name", ""),
                                      rec.get("headline", "")):
            continue  # MLM/competitor — do not engage
        if (rec.get("degree") or "").strip().lower().startswith("1"):
            # one opener per person EVER from any dm lane (conveyor included)
            if ("dm", url) not in seen:
                dm_ready.append(rec)
        else:
            # can't DM a non-connection: stage a connect; the day-2 conveyor DMs them
            # after acceptance. Interaction context rides in the headline so the NETWORK
            # tab (and li_scoring) can see why they were sourced.
            if ("connect", url) not in seen:
                hl = rec.get("headline", "") or ""
                if rec.get("interaction"):
                    hl = (hl + " · engaged: " + rec["interaction"])[:200]
                connect_ready.append({"name": rec.get("name", ""), "headline": hl, "url": url})
    dm_ready = dm_ready[:MAX_DM_DRAFTS_PER_RUN]
    if dry:
        return {"engagers": len(engagers),
                "queued_dms": [r.get("url") for r in dm_ready],
                "queued_connects": [p.get("url") for p in connect_ready], "dry": True}
    queued_dms = []
    for rec in dm_ready:
        draft = draft_fit_dm(rec)
        if not draft:
            continue
        v = li_quality.validate_draft(draft, kind="dm", first_touch=True,
                                      name=rec.get("name", ""))
        if not v["ok"]:
            continue  # failed the voice/safety gate — never queue it
        item = {"id": new_id("dm_" + rec.get("url", "") + "engager"),
                "kind": "dm", "author": rec.get("name", ""),
                "target": rec.get("headline", "") or rec.get("interaction", ""),
                "url": rec.get("url", ""), "draft": v["text"],
                "status": "pending", "created": now_iso(),
                "source": "engager_fit"}
        networking.save_item(item)
        queued_dms.append(item)
    queued_connects = networking.queue_connections(connect_ready) if connect_ready else []
    return {"engagers": len(engagers), "queued_dms": queued_dms,
            "queued_connects": queued_connects}


if __name__ == "__main__":
    r = run(dry="--dry" in sys.argv)
    print(json.dumps({"engagers": r.get("engagers"),
                      "dms_queued": len(r.get("queued_dms") or []),
                      "connects_queued": len(r.get("queued_connects") or []),
                      "note": r.get("note", "")}, indent=2))
