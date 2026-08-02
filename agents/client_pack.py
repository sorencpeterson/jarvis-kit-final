#!/usr/bin/env python3
"""Past-client activation (#81/#41): the moment [OWNER] fills EXECUTION-PACK/
past-clients-TEMPLATE.csv (renamed past-clients.csv), this turns every row into a
personalized care-plan email + (for happy=y) a testimonial ask, queued as drafts +
todos. Nothing sends itself."""
from __future__ import annotations
import csv
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import append_todo, new_id, now_iso  # noqa: E402
import planner  # noqa: E402

CSV_P = Path.home() / "Claude" / "EXECUTION-PACK" / "past-clients.csv"
PACK = Path.home() / "Claude" / "EXECUTION-PACK"
OUT = ROOT / "store" / "client_sends.jsonl"


def _tpl(name):
    try:
        return (PACK / name).read_text()
    except OSError:
        return ""


def run():
    if not CSV_P.exists():
        print("client pack: fill EXECUTION-PACK/past-clients-TEMPLATE.csv and save as past-clients.csv")
        return 0
    done = set()
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            try:
                done.add(json.loads(line)["email"])
            except (json.JSONDecodeError, KeyError):
                continue
    care, ask = _tpl("care-plan-email.md"), _tpl("testimonial-ask.md")
    n = 0
    with OUT.open("a") as f:
        for r in csv.DictReader(open(CSV_P)):
            em = (r.get("email") or "").strip().lower()
            if not em or em in done or em.endswith("example.com"):
                continue
            fill = lambda t: (t.replace("{{first_name}}", r.get("first_name", "there"))
                              .replace("{{site_domain}}", r.get("site_domain", "your site"))
                              .replace("{{project}}", r.get("project", "your site")))
            rec = {"email": em, "name": r.get("first_name", ""), "company": r.get("company", ""),
                   "care_draft": fill(care), "ts": now_iso()}
            if (r.get("happy(y/n)") or r.get("happy") or "").strip().lower().startswith("y"):
                rec["testimonial_draft"] = fill(ask)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            append_todo({"id": new_id("cp_" + em), "text": f"Send care-plan email to {r.get('first_name','')} ({r.get('company','')}) — draft ready in store/client_sends.jsonl",
                         "status": "inbox", "source": "client_pack", "created": now_iso()})
            n += 1
    if n:
        planner.feed_add("agent", f"Client pack: {n} care-plan sends drafted, queued for your review")
    print(f"client pack: {n} new client send(s) drafted")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
