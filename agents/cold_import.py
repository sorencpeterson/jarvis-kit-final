#!/usr/bin/env python3
"""Stage cold agency contacts in GHL. SAFE by design:

The wl-webdev list already lives in the CRM (imported 2026-06, tags wl-webdev-list /
wl-webdev-email-sent — one prior touch). So this does NOT create duplicates:

- Existing contact -> update its Greeting / Personalization / Breakup Detail custom
  fields (new fields, nothing clobbered) and record it as staged. The enrollment tag
  comes later, in small daily batches, from cold_feeder.py — only after [OWNER] publishes
  the workflow and flips config `cold_daily_enroll` > 0. Nothing sends from here.
- Contact marked DND or carrying an unsub/do-not-contact/client tag -> left alone.
- Genuinely new email -> created with the inert `wl-cold-staged` tag.
- Only enrichment rows with status == send (verified hook + email) are used, and hook
  text passes through humanize() (voice rules: no em-dashes in anything published).
- Every action lands in store/cold_pipeline.jsonl (email-keyed), so the dashboard and
  feeder work from our own record, not GHL queries.

Usage:
  cold_import.py --limit 5           # test batch
  cold_import.py                     # the full send-ready list
  cold_import.py --dry-run           # show what would happen, no writes
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app"):
    sys.path.insert(0, str(p))
from store_lib import humanize, now_iso  # noqa: E402
import ghl_social  # noqa: E402

# tag substrings that mean "never cold-touch this contact"
NO_GO = ("unsub", "dnd", "do-not", "do not", "client", "customer", "booked")
SUPPRESS_FILE = ROOT / "store" / "suppress.jsonl"


def _is_locally_suppressed(email: str, cid: str = "") -> bool:
    """Defense-in-depth (2026-07-05 rails audit): a 'remove me' reply is recorded to
    store/suppress.jsonl by reply_watch. Honor it here too, so a suppressed person is
    never re-enriched/re-staged even if the suppression was never mirrored to a GHL tag."""
    if not SUPPRESS_FILE.exists():
        return False
    em = (email or "").strip().lower()
    cd = (cid or "").strip()
    for line in SUPPRESS_FILE.read_text().splitlines():
        try:
            x = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            continue
        if em and (x.get("email") or "").strip().lower() == em:
            return True
        if cd and (x.get("contact_id") or "").strip() == cd:
            return True
    return False

HOOKS_CSV = Path.home() / "Claude/playwright-project/automations/agency-enrichment/out/wl-hooks.csv"
WEBFIX_CSV = Path.home() / "Claude/playwright-project/automations/agency-enrichment/out/segments/C_webfix.csv"
MASTER_CSV = Path.home() / "Claude/wl-webdev-import-master.csv"
PIPELINE = ROOT / "store" / "cold_pipeline.jsonl"
FIELDS = {"greeting": "Greeting", "personalization": "Personalization",
          "breakup_detail": "Breakup Detail", "site_note": "Site Note"}


def _api_json(args: list[str]) -> dict:
    out = ghl_social._api(args)
    try:
        return json.loads(out[out.find("{"):])
    except (ValueError, json.JSONDecodeError):
        return {"_raw": out}


def _loc() -> str:
    for line in (ghl_social.GHL / ".env").read_text().splitlines():
        if line.startswith("GHL_LOCATION_ID="):
            return line.split("=", 1)[1].strip()
    return ""


def load_pipeline() -> dict:
    recs = {}
    if PIPELINE.exists():
        for line in PIPELINE.read_text().splitlines():
            try:
                r = json.loads(line)
                recs[r["email"]] = r
            except (json.JSONDecodeError, KeyError):
                continue
    return recs


def record(rec: dict):
    from store_lib import _flock  # same lock cold_feeder takes; torn interleaved lines otherwise
    with _flock(PIPELINE), PIPELINE.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def ensure_fields(dry: bool) -> dict:
    """Return {logical_name: field_id}, creating missing TEXT custom fields."""
    j = _api_json(["GET", "/locations/{loc}/customFields"])
    have = {f.get("fieldKey", "").replace("contact.", ""): f["id"]
            for f in j.get("customFields", [])}
    ids = {}
    for key, label in FIELDS.items():
        if key in have:
            ids[key] = have[key]
            continue
        if dry:
            print(f"  [dry] would create custom field {label} (contact.{key})")
            ids[key] = "DRY"
            continue
        r = _api_json(["POST", "/locations/{loc}/customFields",
                       "--json", json.dumps({"name": label, "dataType": "TEXT"})])
        fid = (r.get("customField") or r).get("id")
        if not fid:
            sys.exit(f"could not create custom field {label}: {r}")
        got_key = (r.get("customField") or r).get("fieldKey", "")
        print(f"  created field {label} -> {fid} ({got_key})")
        ids[key] = fid
    return ids


def find_contact(loc: str, email: str) -> dict | None:
    j = _api_json(["GET", f"/contacts/?locationId={loc}&query={email}&limit=5"])
    for c in j.get("contacts", []):
        if (c.get("email") or "").strip().lower() == email:
            return c
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--mode", choices=("wl", "webfix"), default="wl",
                    help="wl = hooked cold list; webfix = broken-site lane (site_note field)")
    args = ap.parse_args()

    loc = _loc()
    if not loc:
        sys.exit("no GHL_LOCATION_ID")
    if args.mode == "webfix":
        rows = []
        for r in csv.DictReader(open(WEBFIX_CSV)):
            if not (r.get("email") or "").strip():
                continue
            note = (f"Went to pull up {r['agency']}'s site and it's offline. {r.get('host','the domain')} isn't resolving."
                    if (r.get("site_state") or "").lower() == "dead"
                    else f"Couldn't find a current website for {r['agency']} anywhere.")
            rows.append({"company": r["agency"], "email": r["email"], "first_name": "",
                         "phone": r.get("phone", ""), "personalization": note, "breakup_detail": ""})
    else:
        rows = [r for r in csv.DictReader(open(HOOKS_CSV)) if r["status"] == "send"
                and r["email"] and r["personalization"]]
    last_names = {}
    try:
        for r in csv.DictReader(open(MASTER_CSV)):
            if r.get("Email") and r.get("Last Name"):
                last_names[r["Email"].strip().lower()] = r["Last Name"].strip()
    except OSError:
        pass

    done = load_pipeline()
    todo = [r for r in rows if r["email"].strip().lower() not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(rows)} send-ready, {len(done)} already processed, importing {len(todo)}")

    fids = ensure_fields(args.dry_run)
    created = skipped = 0
    for r in todo:
        email = r["email"].strip().lower()
        first = (r["first_name"] or "").strip()
        hook = humanize(r["personalization"])
        breakup = humanize(r.get("breakup_detail") or "your latest work")
        base = {"email": email, "company": r["company"], "ts": now_iso(), "campaign": args.mode}
        if args.mode == "webfix":
            cf = [{"id": fids["greeting"], "value": first or "there"},
                  {"id": fids["site_note"], "value": hook}]
        else:
            cf = [{"id": fids["greeting"], "value": first or "there"},
                  {"id": fids["personalization"], "value": hook},
                  {"id": fids["breakup_detail"], "value": breakup}]
        if args.dry_run:
            print(f"  [dry] {r['company']} <{email}> hook: {hook[:60]}")
            continue
        # look up the contact BEFORE the suppress check so a suppression recorded
        # only by contact_id (not email) can actually match -- _is_locally_suppressed
        # accepts a cid, but nothing used to pass one in, so that branch never fired.
        c = find_contact(loc, email)
        if _is_locally_suppressed(email, cid=(c or {}).get("id", "")):
            record({**base, "status": "skipped_suppressed", "detail": "local suppress.jsonl"})
            skipped += 1
            continue
        if c:
            tags = [t.lower() for t in (c.get("tags") or [])]
            if c.get("dnd") or any(s in t for t in tags for s in NO_GO):
                record({**base, "status": "skipped_no_go",
                        "detail": "dnd" if c.get("dnd") else ",".join(tags)[:120]})
                skipped += 1
            else:
                j = _api_json(["PUT", f"/contacts/{c['id']}",
                               "--json", json.dumps({"customFields": cf})])
                if (j.get("contact") or {}).get("id") or j.get("succeded") or j.get("succeeded"):
                    record({**base, "status": "staged", "contact_id": c["id"],
                            "kind": "existing_updated"})
                    created += 1
                else:
                    record({**base, "status": "error", "detail": str(j)[:200]})
                    print(f"  ERROR update {email}: {str(j)[:150]}")
        else:
            payload = {
                "locationId": loc, "email": email,
                "firstName": first, "lastName": last_names.get(email, ""),
                "phone": (r.get("phone") or "").strip(),
                "companyName": r["company"], "source": "wl-cold-import",
                "tags": [("wl-webfix-staged" if args.mode == "webfix" else "wl-cold-staged")], "customFields": cf,
            }
            j = _api_json(["POST", "/contacts/", "--json", json.dumps(payload)])
            cid = (j.get("contact") or {}).get("id")
            if cid:
                record({**base, "status": "staged", "contact_id": cid, "kind": "created"})
                created += 1
            else:
                record({**base, "status": "error", "detail": str(j)[:200]})
                print(f"  ERROR create {email}: {str(j)[:150]}")
        time.sleep(0.35)  # stay far under the public-API burst limit
        if (created + skipped) and (created + skipped) % 25 == 0:
            print(f"  ...{created} staged, {skipped} skipped so far", flush=True)

    print(f"done: {created} staged (fields set, nothing sends), "
          f"{skipped} skipped (DND / no-go tags)")


if __name__ == "__main__":
    main()
