#!/usr/bin/env python3
"""E323 (star pick): Contact graph v2 — one unified, entity-resolved person
record across GHL contacts, warm-hitlist calls (+ their dispositions), job-
posting companies, inbound replies, and (when present) the LinkedIn fleet's
graph export, joined by normalized name/email so "have I touched this
person/company before, and how" has one place to check instead of six.

WHAT: pulls GHL contacts via the existing api.sh CLI (same subprocess pattern
      app/ghl_social.py already uses, no new auth surface), warm-hitlist rows
      from ~/Claude/WARM-HITLIST.csv joined against store/warm_dispo.jsonl by
      the SAME w_<hash> id scheme agents/warm_block.py and app/server.py's
      _warm_rows()/_warm_dispos() already use, companies from jobs.jsonl,
      names from store/replies.jsonl, and — CONSUME IF PRESENT, TOLERATE
      ABSENCE — nodes from store/li_graph_nodes.jsonl, a contract the
      LinkedIn fleet may or may not have shipped yet (see LI_GRAPH_CONTRACT
      below for the expected shape; this file works identically with zero
      LinkedIn nodes today). Entity resolution joins by email first (case-
      insensitive), falling back to brainlib.normalize_name() when no email
      is present, so "Braydon", "braydon bergeson", and "BRAYDON BERGESON JR."
      all resolve to the same person (E378).
WHEN: run standalone any time; a good morning-chain candidate alongside
      warm_block.py/attention.py since attention.py doesn't currently need
      this, but future features (meeting_prep already does) benefit from a
      fresh graph.
RAILS: read-only against GHL (a GET, never a write), the local stores, and
      WARM-HITLIST.csv. Only write is store/contact_graph.json (full overwrite
      each run). BACKWARD COMPATIBLE: the top-level "people" list keeps the
      exact v1 shape (name/emails/phones/sources/tags) that app/server.py's
      /api/cgraph and agents/meeting_prep.py already read — v2 only ADDS a new
      "edges" list and new source types; it never removes or renames an
      existing people[] field.

LI_GRAPH_CONTRACT (documented, not enforced — this file degrades gracefully
if the file is absent or shaped differently than expected):
  store/li_graph_nodes.jsonl, one JSON object per line:
    {"name": str, "company": str|null, "email": str|null, "li_url": str|null,
     "tags": [str], "last_touch": iso8601|null}
  Any record missing 'name' is skipped. Extra/unknown fields are ignored
  rather than causing a parse error, so this file need not track the
  LinkedIn fleet's schema evolution to keep working.

EDGE TYPES (people[].sources tells you WHERE a person appears; edges[] tells
you HOW two people/entities relate, when that's derivable from the data):
  "dispo"        person <-> outcome of a worked warm call (booked/dead/noans/...)
  "applied_to"   person (as a company) <-> a job [OWNER] applied to there

Run standalone: .venv/bin/python agents/contact_graph.py
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import brainlib  # noqa: E402

GHL_DIR = Path(os.environ.get("PLAYWRIGHT_DIR") or (ROOT / "playwright-project")) / "automations" / "ghl" / "gohighlevel-cli"
API_SH = GHL_DIR / "api.sh"
JOBS = ROOT / "store" / "jobs.jsonl"
REPLIES = ROOT / "store" / "replies.jsonl"
WARM_CSV = Path(os.environ.get("WARM_CSV") or (ROOT / "store" / "warm-hitlist.csv"))
WARM_DISPO = ROOT / "store" / "warm_dispo.jsonl"
LI_GRAPH_NODES = ROOT / "store" / "li_graph_nodes.jsonl"
OUT = ROOT / "store" / "contact_graph.json"


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


def _ghl_location_id() -> str:
    try:
        for line in (GHL_DIR / ".env").read_text().splitlines():
            if line.strip().startswith("GHL_LOCATION_ID="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _ghl_contacts(limit: int = 100) -> list[dict]:
    loc = _ghl_location_id()
    if not loc or not API_SH.exists():
        return []
    try:
        r = subprocess.run(
            ["bash", str(API_SH), "GET", f"/contacts/?locationId={loc}&limit={limit}"],
            cwd=str(GHL_DIR), capture_output=True, text=True, timeout=40,
        )
        out = (r.stdout or "") + (r.stderr or "")
    except Exception:  # noqa: BLE001
        return []
    # api.sh can print non-JSON preamble (source .env echoes, etc); parse loosely
    # from the first '{' the way ghl_social.py / cold_preflight.py already do.
    start = out.find("{")
    if start < 0:
        return []
    try:
        data = json.loads(out[start:])
    except json.JSONDecodeError:
        return []
    return data.get("contacts", []) or []


def _warm_rid(phone: str, name: str) -> str:
    """Must match agents/warm_block.py._rid and app/server.py._warm_rows'
    inline id scheme EXACTLY (same hash of phone-or-name) — this is how a
    warm_dispo.jsonl record (which only carries the id) joins back to a name."""
    return "w_" + hashlib.sha1((phone or name).encode()).hexdigest()[:10]


def _warm_hitlist_rows() -> list[dict]:
    if not WARM_CSV.exists():
        return []
    try:
        rows = list(csv.DictReader(open(WARM_CSV, newline="")))
    except OSError:
        return []
    out = []
    for r in rows:
        phone = (r.get("phone") or "").strip()
        name = (r.get("name") or "").strip() or (r.get("company") or "").strip()
        if not (phone or name):
            continue
        out.append({
            "id": _warm_rid(phone, name), "name": name, "company": (r.get("company") or "").strip(),
            "phone": phone, "email": (r.get("email") or "").strip(),
            "niche": (r.get("niche") or "").strip(), "tier": (r.get("tier") or "").strip(),
        })
    return out


def _warm_dispos() -> dict[str, dict]:
    out = {}
    for r in _read_jsonl(WARM_DISPO):
        if r.get("id"):
            out[r["id"]] = r  # last-write-wins, matches app/server.py._warm_dispos()
    return out


def _li_graph_nodes() -> list[dict]:
    """Tolerant reader for the LinkedIn fleet's export (see LI_GRAPH_CONTRACT
    in the module docstring). Missing file -> [] (not an error, a valid
    not-shipped-yet state). Records without a usable 'name' are skipped."""
    out = []
    for r in _read_jsonl(LI_GRAPH_NODES):
        name = (r.get("name") or "").strip()
        if not name:
            continue
        out.append(r)
    return out


class Person:
    __slots__ = ("name", "emails", "phones", "sources", "tags", "dispo", "niche")

    def __init__(self, name: str):
        self.name = name
        self.emails: set[str] = set()
        self.phones: set[str] = set()
        self.sources: set[str] = set()
        self.tags: set[str] = set()
        self.dispo: str | None = None
        self.niche: str | None = None

    def to_dict(self) -> dict:
        d = {"name": self.name, "emails": sorted(self.emails), "phones": sorted(self.phones),
             "sources": sorted(self.sources), "tags": sorted(self.tags)}
        # v2 additions are appended, never replacing a v1 key — server.py's
        # /api/cgraph and meeting_prep.py only ever read the v1 keys above.
        if self.dispo:
            d["dispo"] = self.dispo
        if self.niche:
            d["niche"] = self.niche
        return d


def build_graph() -> dict:
    by_email: dict[str, Person] = {}
    by_name: dict[str, Person] = {}  # keyed by brainlib.normalize_name(), not raw lowercase (E378)
    edges: list[dict] = []

    def _get_or_make(name: str, email: str) -> Person:
        email_key = (email or "").strip().lower()
        name_key = brainlib.normalize_name(name)
        if email_key and email_key in by_email:
            p = by_email[email_key]
        elif not email_key and name_key and name_key in by_name:
            p = by_name[name_key]
        else:
            p = Person(name or email or "(unknown)")
            if email_key:
                by_email[email_key] = p
            if name_key:
                by_name[name_key] = p
        if name and not p.name.strip():
            p.name = name
        # Backfill the OTHER index too, so a later record with just an email
        # for a person we already know by name (or vice versa) still merges
        # into the same Person instead of creating a duplicate.
        if email_key and email_key not in by_email:
            by_email[email_key] = p
        if name_key and name_key not in by_name:
            by_name[name_key] = p
        return p

    # 1. GHL contacts (source of truth for email/phone/tags)
    for c in _ghl_contacts():
        name = (c.get("contactName") or c.get("companyName")
                or f"{c.get('firstName', '')} {c.get('lastName', '')}".strip() or "")
        email = c.get("email") or ""
        p = _get_or_make(name, email)
        if email:
            p.emails.add(email.strip().lower())
        if c.get("phone"):
            p.phones.add(c["phone"])
        p.sources.add("ghl")
        for t in (c.get("tags") or []):
            p.tags.add(t)

    # 2. jobs.jsonl companies (no email/phone, name-only join) + applied_to edges
    for j in _read_jsonl(JOBS):
        company = (j.get("company") or "").strip()
        if not company:
            continue
        p = _get_or_make(company, "")
        p.sources.add("jobs")
        p.tags.add("job-company")
        if j.get("title"):
            edges.append({"type": "applied_to", "from": company, "to": j.get("title", ""),
                          "status": j.get("status", "")})

    # 3. store/replies.jsonl names (reply_watch.py writes contact_id/name)
    for r in _read_jsonl(REPLIES):
        name = (r.get("name") or "").strip()
        email = (r.get("email") or "").strip()
        if not name and not email:
            continue
        p = _get_or_make(name, email)
        if email:
            p.emails.add(email.strip().lower())
        p.sources.add("replies")

    # 4. Warm hitlist + warm_dispo.jsonl join (E323's headline new source):
    #    the CSV has name/phone/company/niche; warm_dispo.jsonl (id-keyed only)
    #    carries the worked OUTCOME. Join by the shared w_<hash> id scheme.
    dispos = _warm_dispos()
    for w in _warm_hitlist_rows():
        p = _get_or_make(w["name"], w["email"])
        if w["phone"]:
            p.phones.add(w["phone"])
        if w["email"]:
            p.emails.add(w["email"].strip().lower())
        p.sources.add("warm")
        if w["niche"]:
            p.niche = w["niche"]
        if w["tier"]:
            p.tags.add(f"warm-tier-{w['tier']}")
        d = dispos.get(w["id"])
        if d and d.get("dispo"):
            p.dispo = d["dispo"]
            p.tags.add(f"dispo-{d['dispo']}")
            edges.append({"type": "dispo", "from": w["name"] or w["id"], "to": d["dispo"],
                          "ts": d.get("ts", ""), "note": d.get("note", "")})

    # 5. LinkedIn fleet's graph export — consume if present, tolerate absence.
    for n in _li_graph_nodes():
        name = (n.get("name") or "").strip()
        email = (n.get("email") or "").strip()
        p = _get_or_make(name, email)
        if email:
            p.emails.add(email.strip().lower())
        p.sources.add("linkedin")
        for t in (n.get("tags") or []):
            if isinstance(t, str):
                p.tags.add(t)
        if n.get("company"):
            p.tags.add(f"li-company:{n['company']}")

    # de-dupe: by_email and by_name can point at the SAME Person object (when a
    # record had both), so collect distinct objects by identity, not by dict values.
    seen_ids = set()
    people = []
    for p in list(by_email.values()) + list(by_name.values()):
        if id(p) not in seen_ids:
            seen_ids.add(id(p))
            people.append(p)
    people.sort(key=lambda p: p.name.lower())

    return {"generated": now_iso(), "version": 2, "people": [p.to_dict() for p in people],
            "edges": edges}


def main() -> int:
    from runlog import track
    with track("contact_graph"):
        graph = build_graph()
        OUT.parent.mkdir(parents=True, exist_ok=True)
        # G: atomic write (tmp + os.replace) -- a crash mid-write used to be able to
        # blank the 461KB contact graph, and nothing rebuilds it until the next
        # scheduled run (up to a week).
        tmp = OUT.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(graph, indent=2))
        os.replace(tmp, OUT)

    n = len(graph["people"])
    multi = sum(1 for p in graph["people"] if len(p["sources"]) > 1)
    li_note = "" if LI_GRAPH_NODES.exists() else " (li_graph_nodes.jsonl not present yet — 0 LinkedIn nodes, tolerated)"
    print(f"contact_graph: {n} people ({multi} multi-source), {len(graph['edges'])} edge(s) -> {OUT}{li_note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
