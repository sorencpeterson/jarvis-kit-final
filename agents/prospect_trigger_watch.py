#!/usr/bin/env python3
"""S1: prospect trigger watch. An open deal that gets a warm-outreach hook the week
something changed at THEIR business closes better than a "just checking in". This
watches every open-deal company for a trigger and stages a 2-sentence hook per fire.

WHAT: builds the open-deal set from proposal_factory.load_queue() (status staged or
      sent, sending counted with sent) plus store/convo_states.json (state engaged or
      negotiating), then checks three LOCAL trigger lanes per company:
        1. site_changed: fetches the company's site_url (net_guard.safe_urlopen, capped
           at FETCH_CAP per run), hashes the content with scripts/styles/comments and
           whitespace stripped, and compares against store/site_hashes.json. The first
           sighting only BASELINES (no trigger); a later hash mismatch fires.
        2. proposal_reopened: the proposal beacon's `opens` count rose since the last
           run (they went back and read it again).
        3. convo_state_change: the contact's convo_state machine label moved since the
           last run (e.g. new -> negotiating).
      Plus one OPTIONAL web lane: other agents in this repo already do research through
      the claude CLI with --allowedTools WebSearch (competitor_watch.py, meeting_prep.py),
      so this runs ONE batched pass in that exact pattern over up to WEB_COMPANIES open
      deals asking for a concrete recent change per company (JSON, null when nothing).
      Fires trigger web_news only on a concrete change. Skipped cleanly when the CLI is
      missing, times out, or returns junk.
      Every fired trigger gets a 2-sentence warm-outreach hook in [OWNER]'s voice (ONE
      batched planner._cli call for all fires, voice_spec injected, humanize() applied)
      appended to store/trigger_hooks.jsonl as {company, trigger, hook, ts}, plus one
      feed line. NOTHING here contacts anyone: hooks are raw material for HIS outreach.
WHEN: weekly (Sunday block in morning.sh). Self-gates: exits 0 when the last completed
      run is younger than WEEKLY_GAP_DAYS, so accidental daily wiring cannot burn
      fetches. --force overrides the gate; --dry-run bypasses it, prints local triggers
      only, and writes nothing (no fetch-baseline writes, no LLM, no web).
RAILS: read-only against proposals/convo_states. Writes only store/site_hashes.json,
      store/trigger_watch_state.json, store/trigger_hooks.jsonl and one feed line.
      Fetches are read-only GETs, net_guard-gated (public hosts only, redirect-safe,
      FETCH_CAP per run). No GHL writes, no sends, no pushes.

WHAT THIS DOES NOT WATCH (honest limits): social media, Google Business Profile,
reviews, job boards, funding databases, LinkedIn, email threads. The site check hashes
the ONE page at site_url as served (server HTML): JS-rendered changes are invisible,
and a rotating banner or inline seasonal text can false-positive. web_news is one
cheap model pass over public search, not monitoring: it misses more than it catches
and it says so per company when it finds nothing.

Tunables (change here, nowhere else):
  WEEKLY_GAP_DAYS = 6    self-gate: a completed run younger than this exits 0
  FETCH_CAP       = 10   max site fetches per run (net_guard-gated GETs)
  FETCH_BYTES     = 300_000  read cap per fetch
  WEB_COMPANIES   = 5    max companies in the one optional WebSearch pass
  HOOK_CAP        = 6    max hooks staged per run (freshest triggers first)
  REFIRE_DAYS     = 14   the same (company, trigger) pair will not re-fire inside this

Run:  .venv/bin/python agents/prospect_trigger_watch.py [--dry-run] [--force]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import LOCAL_TZ, now_iso, humanize, voice_spec  # noqa: E402
import planner  # noqa: E402

PROPOSALS = ROOT / "store" / "proposals.jsonl"
CONVO_STATES = ROOT / "store" / "convo_states.json"
SITE_HASHES = ROOT / "store" / "site_hashes.json"
STATE = ROOT / "store" / "trigger_watch_state.json"
HOOKS = ROOT / "store" / "trigger_hooks.jsonl"

WEEKLY_GAP_DAYS = 6
FETCH_CAP = 10
FETCH_BYTES = 300_000
WEB_COMPANIES = 5
HOOK_CAP = 6
REFIRE_DAYS = 14

OPEN_STATUSES = ("staged", "sending", "sent")
ACTIVE_CONVO = ("engaged", "negotiating")


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


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True))
    tmp.replace(path)


def _proposals_by_id() -> dict[str, dict]:
    """Last-write-wins over the raw store (proposal_factory.load_queue when importable,
    per-line fallback otherwise, same pattern attention.py uses)."""
    try:
        import proposal_factory
        rows = proposal_factory.load_queue()
    except Exception:  # noqa: BLE001
        by_id: dict[str, dict] = {}
        for r in _read_jsonl(PROPOSALS):
            if r.get("id"):
                by_id[r["id"]] = r
        rows = list(by_id.values())
    return {r["id"]: r for r in rows if r.get("id")}


def open_deals() -> list[dict]:
    """One entry per open-deal company: {company, site_url, pids: [..], opens, cid}.
    Companies come from open proposals plus active convo states; merged by a
    normalized company key so one business is one watch target."""
    merged: dict[str, dict] = {}

    def key_of(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (name or "").lower())

    for r in _proposals_by_id().values():
        if r.get("status") not in OPEN_STATUSES:
            continue
        company = (r.get("company") or r.get("name") or "").strip()
        if not company:
            continue
        k = key_of(company)
        d = merged.setdefault(k, {"company": company, "site_url": "", "pids": [],
                                  "opens": 0, "cid": ""})
        d["pids"].append(r.get("id", ""))
        d["site_url"] = d["site_url"] or (r.get("site_url") or "").strip()
        try:
            d["opens"] += int(r.get("opens") or 0)
        except (TypeError, ValueError):
            pass
        d["cid"] = d["cid"] or (r.get("contact_id") or "")

    states = (_read_json(CONVO_STATES, {}) or {}).get("states") or {}
    for cid, st in states.items():
        if (st or {}).get("state") not in ACTIVE_CONVO:
            continue
        company = ((st or {}).get("name") or "").strip()
        if not company:
            continue
        k = key_of(company)
        d = merged.setdefault(k, {"company": company, "site_url": "", "pids": [],
                                  "opens": 0, "cid": cid})
        d["cid"] = d["cid"] or cid
        d["convo_state"] = st.get("state", "")
    return list(merged.values())


# ---- trigger lane 1: site content hash ----

def content_hash(html: str) -> str:
    """Hash the page with the churn-prone parts stripped: script/style bodies, HTML
    comments (build stamps live there), and collapsed whitespace. Still fires on
    rotating inline content; documented as a known false-positive source."""
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html or "", flags=re.S | re.I)
    t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
    t = re.sub(r"\s+", " ", t).strip()
    return hashlib.sha256(t.encode("utf-8", "replace")).hexdigest()


def _fetch(url: str) -> tuple[bool, str]:
    """(ok, html). net_guard-gated read-only GET; tests monkeypatch this."""
    try:
        import net_guard
        resp = net_guard.safe_urlopen(url, timeout=12,
                                      headers={"User-Agent": "Mozilla/5.0 ([OWNER]Digital watch)"})
        return True, resp.read(FETCH_BYTES).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 (ValueError from the guard, URLError, timeout)
        return False, str(e)[:120]


def check_sites(deals: list[dict], *, dry_run: bool = False) -> tuple[list[dict], dict]:
    """Fires {company, trigger: 'site_changed', detail} per changed site. First
    sighting baselines silently. Caps at FETCH_CAP. Does NOT write SITE_HASHES here:
    returns (fired, pending) where pending[url] = {"entry": {...}, "company": str,
    "first": bool}. The caller commits a changed-site baseline ONLY once that
    company's hook stages (so a failed hook draft re-fetches + re-fires next run
    instead of the hash advancing and swallowing the change); first-sighting
    baselines carry first=True and are always safe to commit."""
    hashes = _read_json(SITE_HASHES, {})
    fired, fetched = [], 0
    pending: dict = {}
    for d in deals:
        url = d.get("site_url") or ""
        if not url or fetched >= FETCH_CAP:
            continue
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        fetched += 1
        ok, body = _fetch(url)
        if not ok:
            continue  # unreachable is webfix territory, not a warm trigger
        h = content_hash(body)
        prev = (hashes.get(url) or {}).get("hash")
        entry = {"hash": h, "ts": now_iso()}
        if not prev:
            pending[url] = {"entry": entry, "company": d["company"], "first": True}
        elif prev != h:
            fired.append({"company": d["company"], "trigger": "site_changed",
                          "detail": "their site content changed since last week's check"})
            pending[url] = {"entry": entry, "company": d["company"], "first": False}
        # else: unchanged, no baseline change needed
    return fired, pending


# ---- trigger lanes 2 + 3: beacon opens, convo state moves ----

def check_local_signals(deals: list[dict], state: dict) -> tuple[list[dict], dict, dict]:
    """Beacon re-opens and convo-state moves vs what the last run remembered.
    Does NOT mutate state: returns (fired, pending_opens, pending_convo). Each pending
    dict maps key -> {"val": new_value, "company": company, "first": bool}. The caller
    commits first-sightings (first=True, no trigger to lose) always, and change-baselines
    ONLY once that company's hook actually staged, so a failed hook draft (LLM down)
    re-fires next run instead of silently consuming the trigger."""
    fired = []
    seen_opens: dict = state.get("opens", {})
    seen_convo: dict = state.get("convo", {})
    pending_opens: dict = {}
    pending_convo: dict = {}
    for d in deals:
        k = d["company"]
        opens = int(d.get("opens") or 0)
        prev = seen_opens.get(k)
        if prev is None:
            pending_opens[k] = {"val": opens, "company": k, "first": True}
        elif opens > int(prev):
            fired.append({"company": k, "trigger": "proposal_reopened",
                          "detail": f"proposal opens went {prev} -> {opens}"})
            pending_opens[k] = {"val": opens, "company": k, "first": False}
        # else: unchanged, leave the existing baseline (nothing to commit)
        cur_state = d.get("convo_state") or ""
        if cur_state:
            ck = d.get("cid") or k
            prev_state = seen_convo.get(ck)
            if prev_state is None:
                pending_convo[ck] = {"val": cur_state, "company": k, "first": True}
            elif prev_state != cur_state:
                fired.append({"company": k, "trigger": "convo_state_change",
                              "detail": f"conversation moved {prev_state} -> {cur_state}"})
                pending_convo[ck] = {"val": cur_state, "company": k, "first": False}
    return fired, pending_opens, pending_convo


# ---- optional web lane (competitor_watch.py's exact CLI pattern) ----

def check_web(deals: list[dict]) -> list[dict]:
    """ONE batched WebSearch pass via the claude CLI, same subprocess pattern as
    competitor_watch.py. Only a concrete, dated-ish change fires; 'nothing found'
    per company is the expected common answer. Fail-silent by design."""
    names = [d["company"] for d in deals if d.get("company")][:WEB_COMPANIES]
    if not names:
        return []
    try:
        cli = planner._find_claude_cli()
    except Exception:  # noqa: BLE001
        cli = None
    if not cli:
        return []
    prompt = ("Use WebSearch briefly. These are small local businesses I have an open "
              "website deal with. For EACH, report one concrete recent change if you can "
              "actually find one (new location, hiring, rebrand, new service, press, big "
              "review swing). Return ONLY a JSON array: "
              '[{"company": "<name>", "change": "<one short factual line>" or null}]. '
              "null when you find nothing real. Never invent. Companies: "
              + "; ".join(names))
    try:
        out = subprocess.run(["perl", "-e", "alarm 170; exec @ARGV", cli, "-p", prompt,
                              "--model", "claude-haiku-4-5-20251001",
                              "--allowedTools", "WebSearch"],
                             capture_output=True, text=True, timeout=190, cwd="/tmp").stdout
    except Exception:  # noqa: BLE001
        return []
    data = planner._extract_json(out or "")
    fired = []
    if isinstance(data, list):
        known = {n.lower() for n in names}
        for row in data:
            if not isinstance(row, dict):
                continue
            change = (row.get("change") or "").strip() if row.get("change") else ""
            company = (row.get("company") or "").strip()
            if change and company and company.lower() in known:
                fired.append({"company": company, "trigger": "web_news",
                              "detail": change[:200]})
    return fired


# ---- hook drafting (one batched LLM call for every fire) ----

HOOK_PROMPT = """You write 2-sentence warm outreach hooks for [OWNER] ([OWNER_COMPANY]).
VOICE SPEC (follow it to the letter, no em-dashes ever):
{voice}

Each line below is an open deal where something just happened (the trigger). For EACH,
write a 2-sentence hook [OWNER] can text or email: sentence 1 names the trigger naturally
(what he noticed about THEM), sentence 2 bridges to the open conversation without
begging. No greetings, no sign-off, no links, under 45 words total.

TRIGGERS:
{lines}

Return ONLY a JSON array: [{{"company": "<name>", "hook": "<two sentences>"}}]."""


def draft_hooks(fired: list[dict]) -> list[dict]:
    """{company, trigger, hook, ts} per fire, hooks from ONE planner._cli call.
    A fire whose hook the model missed is dropped (no templated filler in his voice)."""
    if not fired:
        return []
    lines = "\n".join(f"- {f['company']}: {f['trigger']} ({f.get('detail', '')})" for f in fired)
    out = planner._cli(HOOK_PROMPT.format(voice=voice_spec(1600), lines=lines),
                       timeout=120, feature="reply")
    data = planner._extract_json(out or "")
    by_company: dict[str, str] = {}
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict) and row.get("company") and row.get("hook"):
                by_company[str(row["company"]).strip().lower()] = str(row["hook"]).strip()
    hooks = []
    for f in fired:
        hook = by_company.get(f["company"].strip().lower(), "")
        if not hook:
            continue
        hooks.append({"company": f["company"], "trigger": f["trigger"],
                      "hook": humanize(hook)[:400], "ts": now_iso()})
    return hooks


def _fire_key(f: dict) -> str:
    return f"{f['company'].strip().lower()}::{f['trigger']}"


def _filter_recent(fired: list[dict], state: dict, now: datetime) -> list[dict]:
    """Drop (company, trigger) pairs already fired inside REFIRE_DAYS. Does NOT
    record anything: fires are only marked in state once a hook actually staged,
    so a failed hook draft retries next run instead of losing the trigger."""
    seen: dict = state.get("fired") or {}
    out = []
    for f in fired:
        last = seen.get(_fire_key(f))
        if last:
            try:
                if now - datetime.fromisoformat(last) < timedelta(days=REFIRE_DAYS):
                    continue
            except (ValueError, TypeError):
                pass
        out.append(f)
    return out


def run(*, dry_run: bool = False, force: bool = False) -> int:
    now = datetime.now(LOCAL_TZ)
    state = _read_json(STATE, {})
    last_run = state.get("last_run")
    if last_run and not (force or dry_run):
        try:
            if now - datetime.fromisoformat(last_run) < timedelta(days=WEEKLY_GAP_DAYS):
                print(f"trigger watch: last run {last_run} is inside the "
                      f"{WEEKLY_GAP_DAYS}d gate, exiting (use --force)")
                return 0
        except (ValueError, TypeError):
            pass

    deals = open_deals()
    if not deals:
        print("trigger watch: no open deals (no staged/sent proposals, no active "
              "conversations), nothing to watch")
        return 0
    print(f"trigger watch: {len(deals)} open-deal company(ies) on watch")

    fired, pending_opens, pending_convo = check_local_signals(deals, state)
    site_fired, pending_hashes = check_sites(deals, dry_run=dry_run)
    fired += site_fired
    if dry_run:
        print(f"[dry-run] {len(fired)} local trigger(s) would be considered "
              "(web pass + hook drafting + state writes all skipped):")
        for f in fired:
            print(f"  {f['company']}: {f['trigger']} ({f.get('detail', '')})")
        return 0
    fired += check_web(deals)
    fired = _filter_recent(fired, state, now)[:HOOK_CAP]

    hooks = draft_hooks(fired)
    hooked = {h["company"].strip().lower() for h in hooks}
    if hooks:
        HOOKS.parent.mkdir(parents=True, exist_ok=True)
        with HOOKS.open("a") as f:
            for h in hooks:
                f.write(json.dumps(h, ensure_ascii=False) + "\n")
        seen = state.setdefault("fired", {})
        for fr in fired:
            if fr["company"].strip().lower() in hooked:
                seen[_fire_key(fr)] = now.isoformat(timespec="seconds")
        try:
            top = hooks[0]
            planner.feed_add("agent", f"Trigger watch: {len(hooks)} warm hook(s) staged "
                                      f"({top['company']}: {top['trigger']})")
        except Exception:  # noqa: BLE001
            pass

    # Commit baselines. First-sightings always commit (no trigger to lose). A CHANGED
    # signal advances its baseline ONLY when that company's hook actually staged, so a
    # failed hook draft (LLM down) leaves the old baseline and the trigger re-fires
    # next run. This is what makes _filter_recent's "retries next run" comment true.
    def _commit(base: dict, pending: dict) -> None:
        for key, info in pending.items():
            if info["first"] or info["company"].strip().lower() in hooked:
                base[key] = info["val"]

    _commit(state.setdefault("opens", {}), pending_opens)
    _commit(state.setdefault("convo", {}), pending_convo)
    hashes = _read_json(SITE_HASHES, {})
    for url, info in pending_hashes.items():
        if info.get("first") or info["company"].strip().lower() in hooked:
            hashes[url] = info["entry"]
    _write_json(SITE_HASHES, hashes)

    state["last_run"] = now.isoformat(timespec="seconds")
    _write_json(STATE, state)

    print(f"trigger watch: {len(fired)} trigger(s) fired, {len(hooks)} hook(s) staged -> {HOOKS}")
    for h in hooks:
        print(f"  {h['company']} [{h['trigger']}]: {h['hook'][:90]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="weekly open-deal trigger watch")
    ap.add_argument("--dry-run", action="store_true",
                    help="local triggers only, print, write nothing, no LLM/web")
    ap.add_argument("--force", action="store_true", help="ignore the weekly self-gate")
    args = ap.parse_args()
    from runlog import track
    with track("prospect_trigger_watch"):
        return run(dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
