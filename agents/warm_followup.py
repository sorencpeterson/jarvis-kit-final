#!/usr/bin/env python3
"""Post-dispo conveyor: the moment [OWNER] dispos a warm call, the follow-up drafts
itself into the ONE approve queue (replies.jsonl). Deterministic templates, instant,
his voice. booked additionally fires the Proposal Factory so he walks into the
booked call with the plan already built. NOTHING here sends anything.

Called by the server (Popen) on POST /api/warm/{wid}/dispo. Also runnable by hand:
  warm_followup.py --wid w_abc --dispo booked --name "Braydon" --phone +1435... --niche "Local service"

C187 addition (500-IDEAS-AGGREGATORS.md): the suppress-list check reply_watch.py
already applies to every LLM-drafted reply was NOT previously applied to this
deterministic-template conveyor -- a genuine gap, since a warm-call contact who was
separately suppressed (e.g. replied "remove me" through a DIFFERENT channel, or GHL
flagged them unsub/dnd) could still get a follow-up drafted here with zero suppress
awareness. run() now checks it FIRST, before any template rendering or GHL contact
lookup, same as every other draft path in this build.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import owner  # noqa: E402
from store_lib import now_iso, new_id, humanize  # noqa: E402
import reply_watch  # noqa: E402
import proposal_factory  # noqa: E402

BOOK = f"{owner.get('site', 'example.com')}/book"
SUPPRESS = ROOT / "store" / "suppress.jsonl"

TEMPLATES = {
    "booked": ("Locked in, {first}. I'll bring a written plan for {biz} to the call. "
               "If the time stops working, grab a new slot here: " + BOOK),
    "noans": ("Tried you just now, {first}. It's [OWNER], about the site for {biz}. "
              "I'll try once more this week, or you can grab me faster here: " + BOOK),
    "dead": ("All good {first}, closing your file for now. If the website itch comes "
             "back, my line's open. Good luck out there."),
}


def _is_suppressed(email: str, name: str) -> bool:
    """C187: same union check (email OR name) reply_watch.py's suppress read uses,
    adapted here since warm_followup.py's inputs are email/name, not a contact_id
    (the GHL contact_id isn't resolved yet at the point this check needs to run --
    checking before the lookup, not after, is the whole point of 'first')."""
    if not SUPPRESS.exists():
        return False
    email_l = (email or "").strip().lower()
    name_l = (name or "").strip().lower()
    for line in SUPPRESS.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if email_l and (r.get("email") or "").strip().lower() == email_l:
            return True
        if name_l and (r.get("name") or "").strip().lower() == name_l:
            return True
    return False


def run(wid: str, dispo: str, name: str, phone: str, email: str, niche: str) -> None:
    tpl = TEMPLATES.get(dispo)
    if not tpl:
        return

    # C187 (audited): suppress check FIRST, before any template rendering or GHL
    # contact lookup below.
    if _is_suppressed(email, name):
        print(f"  warm_followup: {name or wid} is suppressed, skipping follow-up draft")
        return

    first = (name or "there").split()[0].title()
    biz = name.title() if name else "your business"
    draft = humanize(tpl.format(first=first, biz=biz))

    # resolve the GHL contact so the approve click can actually send
    contact = proposal_factory.find_contact(email=email, name=name) if (email or name) else {}
    cid = contact.get("id", "")

    rec = {"id": new_id("wf_" + wid + dispo), "convo": None, "contact_id": cid,
           "name": name or "warm contact", "phone": phone,
           "channel": "SMS" if phone else "Email",
           "their_msg": f"[call dispo: {dispo}]" + ("" if cid else "  (no GHL match, send by hand: " + (phone or email or "?") + ")"),
           "intent": "followup", "draft": draft,
           "status": "pending", "created": now_iso(), "src": "warm_conveyor"}
    reply_watch._save(rec)
    print(f"follow-up staged for {name} ({dispo})" + ("" if cid else " [no GHL contact match]"))

    # booked = hottest: prep pack for the call itself (coach-adjacent, human-readable)
    if dispo == "booked":
        try:
            import planner
            prep = planner._cli_json(
                "Prospect: " + (name or "?") + " | niche: " + (niche or "local service") + ". "
                "[OWNER] booked a sales call with them (web design / marketing ops, pricing tree: "
                "landing 800, standard 1200, booking 2500). Return JSON: "
                '{"opener": "one natural first line referencing that THEY booked", '
                '"questions": ["3 discovery questions, his blunt voice"], '
                '"objections": [{"hear": "likeliest objection", "say": "the counter"}] (2 items), '
                '"close": "the exact closing line"}', timeout=90, feature="reply") or {}
            lines = ["# Sales call prep: " + (name or "?"), "",
                     "niche: " + (niche or "?") + " | phone: " + (phone or "?"), "",
                     "OPENER: " + str(prep.get("opener", "")), "", "DIG:"]
            lines += ["- " + q for q in (prep.get("questions") or [])]
            lines.append("")
            for o in (prep.get("objections") or []):
                lines.append('THEY SAY: "' + str(o.get("hear", "")) + '"')
                lines.append('YOU SAY: "' + str(o.get("say", "")) + '"')
            lines += ["", "CLOSE: " + str(prep.get("close", "")),
                      "", "(proposal + mockup land in the PROPOSALS queue; playbooks: business-library/playbooks/)"]
            prep_dir = ROOT / "store" / "prep"
            prep_dir.mkdir(exist_ok=True)
            (prep_dir / f"sales_{wid}.md").write_text("\n".join(lines))
            planner.feed_add("warm", f"prep pack ready for the {name} call (store/prep/sales_{wid}.md)")
            planner.notify("Call booked: prep ready",
                           f"{name}: prep pack + proposal are building. Coach is one tap when you take it.")
        except Exception as e:  # noqa: BLE001
            print(f"  prep pack skipped: {e}")

    # booked also fires the factory so the call starts with a plan on the table.
    if dispo == "booked":
        args = [str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "agents" / "proposal_factory.py"),
                "--niche", niche or "local service"]
        if cid:
            args += ["--contact-id", cid]
        elif email:
            args += ["--email", email]
        else:
            args += ["--name", name, "--dry"]
        subprocess.Popen(args, cwd=str(ROOT))
        print("proposal factory fired for the booked call")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--wid", required=True)
    ap.add_argument("--dispo", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--phone", default="")
    ap.add_argument("--email", default="")
    ap.add_argument("--niche", default="")
    a = ap.parse_args()
    run(a.wid, a.dispo, a.name, a.phone, a.email, a.niche)
