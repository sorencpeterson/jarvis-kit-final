"""The command bar's brain — turns a natural-language command into real actions.

Flow: interpret (cheap Claude CLI → JSON action plan) → execute SAFE actions
immediately, streaming each step → PROPOSE outward actions for one-click confirm.
Policy: safe = auto; outward (sends/enrolls/publishes/calendar writes/browser) = confirm.

Streams Server-Sent Events so the console narrates as it works (the JARVIS feel).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "dashboard", ROOT / "schedule"):
    sys.path.insert(0, str(p))
import owner  # noqa: E402
from store_lib import append_todo, compact, new_id, now_iso  # noqa: E402
import planner  # noqa: E402

GHL = ROOT.parent / "playwright-project/automations/ghl/gohighlevel-cli/api.sh"
DRAFTS = ROOT / "store" / "drafts.jsonl"
VENV_PY = str(ROOT / ".venv" / "bin" / "python")
PENDING: dict[str, dict] = {}  # confirm-token -> {action,args}


def _run(cmd, cwd=ROOT, timeout=200) -> str:
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        return ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:
        return f"error: {e}"


def _short(args: dict) -> str:
    return ", ".join(f"{k}={str(v)[:40]}" for k, v in (args or {}).items())[:90]


# ---------------- SAFE actions (run immediately) ----------------
def a_run_agent(args):
    f = {"organize": "agents/organize.py", "triage": "agents/triage_inbox.py",
         "brief": "agents/daily_brief.py"}.get(args.get("agent", ""))
    if not f:
        return f"unknown agent '{args.get('agent')}'"
    out = _run([VENV_PY, f])
    return out.splitlines()[-1] if out else "done"


def a_ghl_search(args):
    if not GHL.exists():
        return "GHL CLI not found"
    out = _run(["bash", str(GHL), "GET", "/contacts/", "--loc",
                "--query", f"query={args.get('query','')}", "--query", "limit=5"],
               cwd=GHL.parent, timeout=45)
    names = re.findall(r'"(?:contactName|firstName)"\s*:\s*"([^"]+)"', out)
    if names:
        return f"found {len(names)}: " + ", ".join(dict.fromkeys(names))
    return "no matches" if "200" in out or out == "" else out[:140]


def a_remember(args):
    """#1: JARVIS keeps his own memory — 'remember: I never take calls before 10am'."""
    fact = (args.get("fact") or "").strip()
    if not fact:
        return "nothing to remember"
    mf = ROOT / "store" / "jarvis_memory.md"
    cur = mf.read_text() if mf.exists() else "# JARVIS standing memory\n"
    mf.write_text(cur.rstrip() + "\n- " + fact + "\n")
    return f"Noted, sir. I'll remember: {fact}"


def a_add_todo(args):
    text = (args.get("text") or "").strip()
    if not text:
        return "no text given"
    append_todo({"id": new_id(text), "text": text, "status": "inbox", "created": now_iso(),
                 "source": "manual", "source_ref": None, "project": None,
                 "priority": args.get("priority") if args.get("priority") in (1, 2, 3) else None,
                 "scheduled_time": None, "duration_min": None, "gcal_event_id": None, "notes": None})
    compact()
    return f"added: {text}"


def a_draft_outreach(args):
    about = args.get("about") or args.get("text") or "follow-up"
    n = args.get("count") or 3
    prompt = (f"Draft {n} short outreach/follow-up messages in [OWNER]'s voice (direct, punchy, "
              f"no fluff, cut filler) about: {about}.\nContext:\n{planner._context()[:1400]}\n"
              f"Separate each with a line of '---'. No preamble.")
    out = planner._cli(prompt, timeout=160) or ""
    if not out.strip():
        return "draft engine unavailable"
    DRAFTS.parent.mkdir(parents=True, exist_ok=True)
    # lock the append (2026-07-13 hunt): server.py's api_drafts_dismiss does a full-file
    # read-modify-write under this same _flock; without it, an append landing inside that
    # window is silently erased when dismiss rewrites the file.
    from store_lib import _flock
    with _flock(DRAFTS), DRAFTS.open("a") as fh:
        fh.write(json.dumps({"id": new_id(about), "about": about, "drafts": out, "ts": now_iso()}) + "\n")
    return f"drafted {out.count('---') + 1} message(s) → saved to Drafts (review before sending)"


def a_ghl_stats(args):
    if not GHL.exists():
        return "GHL CLI not found"
    out = _run(["bash", str(GHL), "GET", "/contacts/", "--loc", "--query", "limit=1"],
               cwd=GHL.parent, timeout=40)
    m = re.search(r'"total"\s*:\s*(\d+)', out)
    if m:
        return f"GHL: {int(m.group(1)):,} contacts"
    # D7: an empty/failed response used to read back as "connected". A dead API
    # and a real number must never look the same. Say unavailable, distinctly.
    if not out.strip():
        return "GHL stats unavailable (empty response, the API call likely failed)"
    return f"GHL stats unavailable: {out[:140]}"


def a_launch(args):
    """Trigger a dashboard launch action (same as the drawer buttons)."""
    import urllib.request
    from store_lib import secret
    which = (args.get("which") or "").strip()
    if which not in ("job_scan", "job_apply", "net_scan", "net_run"):
        return f"unknown launch '{which}'"
    try:
        r = urllib.request.urlopen(
            urllib.request.Request("http://localhost:8765/api/launch/" + which, method="POST",
                                   headers={"X-Brain-Token": secret("brain_token")}), timeout=25)
        d = json.loads(r.read())
    except Exception as e:
        return f"couldn't launch {which}: {e}"
    msg = {"job_scan": "scanning hiring.cafe for fresh roles now",
           "job_apply": "applying to your approved jobs now (watch the browser window)",
           "net_scan": "LinkedIn scan queued — it runs on your real Chrome when the Claude app is open. For it right now, ask me in chat and I'll source it live.",
           "net_run": "LinkedIn run queued — runs on your real Chrome when the Claude app is open."}
    if d.get("error"):
        return d["error"]
    return msg.get(which, "done")


def a_win(args):
    """Log closed revenue to the ledger (kind=won) so the plan bar moves."""
    import math
    try:
        amt = float(args.get("amount") or 0)
    except (ValueError, TypeError):
        amt = 0
    # a win is a positive, finite dollar amount: a typo'd minus silently shrank the
    # plan bar, and one NaN/inf row poisoned the ledger total forever (D8 test sweep)
    if not math.isfinite(amt) or amt <= 0:
        return "how much closed? give me a positive number"
    import urllib.request
    from store_lib import secret
    body = json.dumps({"kind": "won", "amount": amt, "note": str(args.get("note") or "")[:200]}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(
            "http://localhost:8765/api/ledger", data=body, method="POST",
            headers={"X-Brain-Token": secret("brain_token"), "Content-Type": "application/json"}), timeout=10)
        return f"${amt:,.0f} on the board. The plan bar just moved, sir."
    except Exception as e:  # noqa: BLE001
        return f"ledger write failed: {e}"


def a_proposal(args):
    """Fire the proposal factory for a contact (staged, never sent)."""
    who = str(args.get("who") or "").strip()
    if not who:
        return "who for? name, email, or contact id"
    cmd = [str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "agents" / "proposal_factory.py"),
           "--niche", str(args.get("niche") or "local service")]
    cmd += ["--email", who] if "@" in who else ["--name", who]
    if args.get("url"):
        cmd += ["--url", str(args["url"])]
    subprocess.Popen(cmd, cwd=str(ROOT))
    return f"factory's building for {who}. It lands in the proposals queue in 2-3 minutes; you send it."


def a_audit(args):
    """Run the QA teardown on any site, live (his sales-call weapon)."""
    u = str(args.get("url") or "").strip()
    if not u:
        return "which site?"
    try:
        p = subprocess.run([str(ROOT / ".venv" / "bin" / "python"),
                            str(Path.home() / "Claude" / "elementor-recoder" / "qa.py"), u,
                            "--max-pages", "6"], capture_output=True, text=True, timeout=300)
        lines = [ln for ln in p.stdout.splitlines() if ln.startswith(("- **", "pages "))][:9]
        return "teardown of " + u + ":\n" + "\n".join(lines[:9]) if lines else "site unreachable or empty"
    except subprocess.TimeoutExpired:
        return "site too slow to crawl in 5 minutes, that is itself a finding"


def a_prep_tomorrow(args):
    """Evening brief: tomorrow's block + waiting queue + open proposals in one read."""
    out = []
    try:
        bj = json.loads((ROOT / "store" / "warm_block.json").read_text())
        picks = bj.get("picks") or []
        out.append("tomorrow's 10-block: " + (", ".join(p["name"].split()[0] for p in picks[:10]) or "empty (hitlist exhausted)"))
    except (OSError, ValueError, json.JSONDecodeError):
        out.append("no block built yet (morning builds it)")
    try:
        import proposal_factory
        props = [p for p in proposal_factory.load_queue() if p.get("status") == "staged"]
        sent = [p for p in proposal_factory.load_queue() if p.get("status") == "sent"]
        opened = [p for p in sent if p.get("opens")]
        out.append(f"proposals: {len(props)} staged to send, {len(sent)} out, {len(opened)} opened")
        for p in opened[-3:]:
            out.append(f"  {p.get('name')}: read {p.get('read_secs', 0)}s, scrolled {p.get('scroll_pct', 0)}%")
    except Exception:  # noqa: BLE001
        pass
    try:
        import reply_watch
        pend = [r for r in reply_watch._load() if r.get("status") == "pending"]
        out.append(f"waiting on you: {len(pend)} draft(s) in the approve queue")
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(out)


def a_deal_copilot(args):
    """One contact, full local history, ONE next action."""
    who = str(args.get("who") or "").strip()
    if not who:
        return "which deal?"
    import proposal_factory
    c = proposal_factory.find_contact(email=who if "@" in who else "", name="" if "@" in who else who)
    if not c.get("id"):
        return f"couldn't find {who} in GHL"
    props = [p for p in proposal_factory.load_queue() if p.get("contact_id") == c["id"]]
    import reply_watch
    reps = [r for r in reply_watch._load() if r.get("contact_id") == c["id"]]
    facts = [f"{c.get('name')} ({c.get('email') or c.get('phone') or '?'})"]
    for p in props[-3:]:
        facts.append(f"proposal {p.get('tier')} ${p.get('price')}: {p.get('status')}, opens={p.get('opens', 0)}, read={p.get('read_secs', 0)}s")
    for r in reps[-3:]:
        facts.append(f"reply [{r.get('intent')}]: {r.get('status')}")
    from planner import _cli
    verdict = _cli("Deal history for a web-design prospect:\n" + "\n".join(facts) +
                   "\nGive [OWNER] ONE next action (one sentence, blunt) and one draft line to send if messaging is the move. No em-dashes.",
                   timeout=60, feature="default") or "no verdict"
    return "\n".join(facts) + "\n\nNEXT: " + verdict.strip()[:400]


def a_pipeline_review(args):
    """Walk the open deals aloud, oldest first, with the one-line verdict each (P237)."""
    import urllib.request
    from store_lib import secret
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            "http://localhost:8765/api/deals",
            headers={"X-Brain-Token": secret("brain_token")}), timeout=20)
        d = json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        return f"deals unavailable: {e}"
    items = (d.get("items") or d.get("deals") or [])
    if not items:
        return "no open deals on the board"
    items.sort(key=lambda x: -(x.get("age") or x.get("days") or 0))
    lines = []
    for x in items[:12]:
        age = x.get("age") or x.get("days") or 0
        v = x.get("value") or x.get("monetaryValue") or 0
        verdict = "DEAD WEIGHT, dispo it" if age > 60 else ("stale, one loop-close then decide" if age > 30 else "alive, keep the cadence")
        lines.append(f"{x.get('name') or x.get('title') or '?'} · ${v:,.0f} · {age}d · {verdict}")
    more = f"\n(+{len(items)-12} more)" if len(items) > 12 else ""
    return "\n".join(lines) + more


def a_capacity(args):
    """Honest capacity math: can he take N more builds this month? (P249)."""
    import csv as _csv
    from datetime import date
    hours_hist = []
    try:
        with open(str(Path.home() / "Claude" / "elementor-recoder" / "clients" / "build-log.csv")) as f:
            for row in _csv.DictReader(f):
                try:
                    hours_hist.append(float(row.get("total") or 0))
                except (ValueError, TypeError):
                    pass
    except OSError:
        pass
    per_build = (sum(hours_hist) / len(hours_hist)) if hours_hist else 2.5  # runbook estimate until data
    src = f"measured over {len(hours_hist)} builds" if hours_hist else "runbook estimate (no builds logged yet)"
    days_left = 30 - date.today().day
    weekdays = max(1, round(days_left * 5 / 7))
    build_hours_available = weekdays * 3  # 3 build-hours/day per the operating rhythm
    cap = int(build_hours_available // per_build)
    return (f"~{per_build:.1f}h per standard build ({src}). {weekdays} working days left this month "
            f"x 3 build hours = {build_hours_available:.0f}h. Honest capacity: {cap} more builds. "
            f"Sales calls and care time not included; rush jobs eat two slots.")


# ---------------- name->id actions (2026-07-12 agency audit: these routes existed as
# dashboard buttons but chat couldn't trigger them — "skip the Client A proposal" just
# talked). Every mutation goes through the SAME localhost route the button uses, so the
# server-side gates (lint, suppression, links-live, double-send claim) stay authoritative.
def _brain_api(path: str, method: str = "POST", payload=None, timeout: int = 25):
    import urllib.request
    from store_lib import secret
    req = urllib.request.Request(
        "http://localhost:8765" + path, method=method,
        data=(json.dumps(payload).encode() if payload is not None else None),
        headers={"X-Brain-Token": secret("brain_token"), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _fuzzy_one(q: str, items: list, *keys: str):
    """Unique case-insensitive substring match across keys. Returns (item, err)."""
    q = (q or "").strip().lower()
    if not q:
        return None, "no name given"
    def blob(x):
        return " ".join(str(x.get(k) or "") for k in keys).lower()
    hits = [x for x in items if q in blob(x)]
    if not hits:
        return None, f"no match for '{q}'"
    if len(hits) > 1:
        opts = "; ".join((next((str(h.get(k)) for k in keys if h.get(k)), "?"))[:26] for h in hits[:4])
        return None, f"'{q}' is ambiguous: {opts}. Say more of the name."
    return hits[0], ""


def a_proposal_skip(args):
    import proposal_factory as pf
    staged = [x for x in pf.load_queue() if x.get("status") == "staged"]
    hit, err = _fuzzy_one(args.get("who", ""), staged, "company", "name")
    if err:
        return f"couldn't skip: {err} ({len(staged)} staged)"
    try:
        _brain_api(f"/api/proposal/{hit['id']}/skip")
        return f"skipped the {hit.get('company') or hit.get('name')} proposal (${hit.get('price') or 0:,})"
    except Exception as e:  # noqa: BLE001
        return f"couldn't skip {hit.get('company')}: {e}"


def a_proposal_send(args):
    """OUTWARD: emails the proposal. Rides /api/proposal/{pid}/send = every gate intact."""
    import proposal_factory as pf
    staged = [x for x in pf.load_queue() if x.get("status") == "staged"]
    hit, err = _fuzzy_one(args.get("who", ""), staged, "company", "name")
    if err:
        return f"couldn't send: {err}"
    try:
        d = _brain_api(f"/api/proposal/{hit['id']}/send", timeout=60)
        return (f"sent the {hit.get('company') or hit.get('name')} proposal" if d.get("ok")
                else f"NOT sent: {d.get('error') or d}")
    except Exception as e:  # noqa: BLE001
        return f"send failed: {e}"


def a_reply_send(args):
    """OUTWARD: approves+sends a drafted warm reply via /api/replies/{rid}/approve."""
    import reply_watch as rw
    pend = [x for x in rw._load() if x.get("status") == "pending"]
    hit, err = _fuzzy_one(args.get("who", ""), pend, "name", "email", "company")
    if err:
        return f"couldn't send reply: {err} ({len(pend)} pending)"
    try:
        d = _brain_api(f"/api/replies/{hit['id']}/approve", timeout=60)
        return (f"reply to {hit.get('name') or hit.get('email')} sent" if d.get("ok")
                else f"NOT sent: {d.get('error') or d}")
    except Exception as e:  # noqa: BLE001
        return f"reply send failed: {e}"


def a_reply_skip(args):
    import reply_watch as rw
    pend = [x for x in rw._load() if x.get("status") == "pending"]
    hit, err = _fuzzy_one(args.get("who", ""), pend, "name", "email", "company")
    if err:
        return f"couldn't skip reply: {err}"
    try:
        _brain_api(f"/api/replies/{hit['id']}/skip")
        return f"skipped the reply draft to {hit.get('name') or hit.get('email')}"
    except Exception as e:  # noqa: BLE001
        return f"couldn't skip: {e}"


def a_warm_dispo(args):
    dispo = (args.get("dispo") or "").strip().lower()
    if dispo not in ("booked", "noans", "txt", "dead"):
        return "dispo must be one of booked, noans, txt, dead"
    try:
        warm = (_brain_api("/api/warm", method="GET") or {}).get("items") or []
    except Exception as e:  # noqa: BLE001
        return f"couldn't load the warm list: {e}"
    hit, err = _fuzzy_one(args.get("who", ""), warm, "name", "company", "email")
    if err:
        return f"couldn't log dispo: {err}"
    try:
        _brain_api(f"/api/warm/{hit['id']}/dispo", payload={"dispo": dispo})
        nice = {"booked": "BOOKED 🎉", "noans": "no answer", "txt": "texted", "dead": "dead"}[dispo]
        return f"logged {hit.get('name') or '?'} as {nice}"
    except Exception as e:  # noqa: BLE001
        return f"dispo failed: {e}"


def a_todo_complete(args):
    from store_lib import load_todos
    open_t = [t for t in load_todos() if t.get("status") in ("inbox", "scheduled", "doing")]
    hit, err = _fuzzy_one(args.get("text", "") or args.get("who", ""), open_t, "text")
    if err:
        return f"couldn't complete: {err} ({len(open_t)} open)"
    try:
        _brain_api(f"/api/todo/{hit['id']}/complete")
        return f"done: {hit.get('text', '')[:60]}"
    except Exception as e:  # noqa: BLE001
        return f"couldn't complete: {e}"


def a_content_approve(args):
    import content_gen
    drafts = [p for p in content_gen.load_posts() if p.get("status") == "draft"]
    who = (args.get("who") or "").strip()
    n = args.get("n")
    picks = []
    if who:
        hit, err = _fuzzy_one(who, drafts, "hook", "topic", "angle")
        if err:
            return f"couldn't approve: {err} ({len(drafts)} drafts)"
        picks = [hit]
    else:
        try:
            k = int(n) if n else len(drafts)
        except (TypeError, ValueError):
            k = len(drafts)
        picks = drafts[:k]
    if not picks:
        return "no drafts to approve"
    ok = 0
    for p in picks:
        try:
            _brain_api(f"/api/content/{p['id']}/approve")
            ok += 1
        except Exception:  # noqa: BLE001
            pass
    return f"approved {ok} post(s) (scheduling/publishing stays yours)"


SAFE = {"run_agent": a_run_agent, "ghl_search": a_ghl_search, "ghl_stats": a_ghl_stats,
        "add_todo": a_add_todo, "draft_outreach": a_draft_outreach, "launch": a_launch,
        "remember": a_remember, "win": a_win, "proposal": a_proposal, "audit": a_audit,
        "prep_tomorrow": a_prep_tomorrow, "deal_copilot": a_deal_copilot,
        "pipeline_review": a_pipeline_review, "capacity": a_capacity,
        # name->id actions (2026-07-12): mutations ride the same gated localhost routes
        "proposal_skip": a_proposal_skip, "reply_skip": a_reply_skip,
        "warm_dispo": a_warm_dispo, "todo_complete": a_todo_complete,
        "content_approve": a_content_approve}


# ---------------- OUTWARD actions (only after confirm) ----------------
def a_gcal_create(args):
    try:
        import gcal_write
        from datetime import datetime, timedelta
        at = args.get("at")
        start = datetime.fromisoformat(at) if at else None
        if not start:
            return "no time given"
        dur = args.get("dur") or 60
        svc = gcal_write._service()
        body = {"summary": args.get("title", "Block"),
                "start": {"dateTime": start.isoformat()},
                "end": {"dateTime": (start + timedelta(minutes=dur)).isoformat()}}
        ev = svc.events().insert(calendarId="primary", body=body).execute()
        return f"calendar event created: {ev.get('summary')}"
    except Exception as e:
        return f"calendar error: {e}"


def a_ghl_tag(args):
    name, tag = args.get("contact", ""), args.get("tag", "")
    if not GHL.exists():
        return "GHL CLI not found"
    out = _run(["bash", str(GHL), "GET", "/contacts/", "--loc",
                "--query", f"query={name}", "--query", "limit=1"], cwd=GHL.parent, timeout=40)
    m = re.search(r'"id"\s*:\s*"([^"]+)"', out)
    if not m:
        return f"no GHL contact found for '{name}'"
    cid = m.group(1)
    res = _run(["bash", str(GHL), "POST", f"/contacts/{cid}/tags",
                "--json", json.dumps({"tags": [tag]})], cwd=GHL.parent, timeout=40)
    return f"tagged {name} → '{tag}'" if ('"tags"' in res or res == "") else f"tag result: {res[:140]}"


def a_ghl_build_workflow(args):
    return ("Workflow build is a guided flow — tell me the sequence and I'll build it paused "
            "via the GHL builder. (Not auto-run from the bar for safety.)")


def a_browser_task(args):
    cli = planner._find_claude_cli()
    if not cli:
        return "no Claude CLI"
    out = _run([cli, "-p", "Use the connected Chrome to: " + (args.get("instruction", "")),
                "--model", planner.MODEL], timeout=300)
    return (out[-300:] or "browser task ran") + "  (needs Chrome extension connected)"


OUTWARD = {"gcal_create": a_gcal_create, "ghl_tag": a_ghl_tag,
           "ghl_build_workflow": a_ghl_build_workflow,
           "launch_send": a_launch,
           # real sends (2026-07-12): always a confirm bubble; the routes they call keep
           # every server-side gate (lint, suppression, links-live, double-send claim)
           "proposal_send": a_proposal_send, "reply_send": a_reply_send}


def _gate_action(act, args):
    """job_apply/net_run submit applications / post on LinkedIn: never auto-run from
    chat, always a confirm bubble, regardless of what interpret emitted."""
    if act == "launch" and (args or {}).get("which") in ("job_apply", "net_run"):
        return "launch_send"
    return act


# ---------------- interpreter ----------------
INTERPRET = """You are [OWNER]'s command interpreter for his command-center app. Turn his command into a minimal list of actions from this CATALOG. Output ONLY JSON:
{"reply":"one short line as JARVIS (composed, precise, dry wit; 'sir' sparingly; never obsequious; no em-dashes). When asked what-if/projection questions, model it from the real numbers in CURRENT STATE and say so. If you are genuinely unsure of a fact, end with (unverified)","steps":[{"action":"name","args":{...}}]}

CATALOG (safe = auto-run; outward = needs his confirm):
- run_agent {agent:"organize"|"triage"|"brief"}  safe — organize=reclassify the board, triage=sort inbox, brief=regenerate the daily brief
- ghl_search {query}  safe — search GoHighLevel contacts by name
- ghl_stats {}  safe — GoHighLevel headline numbers (total contacts)
- add_todo {text, priority?}  safe — capture a task
- remember {fact}  safe — store a durable preference/fact [OWNER] teaches you ("remember ...")
- draft_outreach {about, count?}  safe — write outreach/follow-up drafts in his voice (saved, not sent)
- gcal_create {title, at:"YYYY-MM-DDTHH:MM", dur?}  outward — create a Google Calendar event (dur in MINUTES, e.g. 2 hours = 120)
- ghl_tag {contact, tag}  outward — tag/enroll a GHL contact
- ghl_build_workflow {name}  outward — build a GHL workflow (paused)
- win {amount, note?}  safe — log closed revenue ("log a win", "closed X for $1200")
- proposal {who, niche?, url?}  safe — build a proposal+mockup for a contact (staged for his send)
- audit {url}  safe — live QA teardown of any website (use when he asks "what's wrong with X's site")
- prep_tomorrow {}  safe — the evening brief: tomorrow's calls + queue + proposal opens
- deal_copilot {who}  safe — full history of one deal + the ONE next action
- pipeline_review {}  safe — walk every open deal with age + verdict ("review my pipeline")
- capacity {}  safe — honest can-I-take-more-builds math from the build log
- launch {which:"job_scan"|"net_scan"}  safe — run a read-only scan. job_scan=find fresh jobs on hiring.cafe; net_scan=source fresh LinkedIn targets.
- launch_send {which:"job_apply"|"net_run"}  outward — these SEND for real (submit job applications / post on LinkedIn), so they always need his confirm. job_apply=apply to approved jobs; net_run=run approved LinkedIn networking.
- proposal_skip {who:"company or contact name"}  safe — skip/retire a STAGED proposal ("skip the Client A proposal"). Uses fuzzy name match; use the name as [OWNER] said it.
- proposal_send {who:"company or contact name"}  outward — EMAIL a staged proposal to the prospect (his confirm first; the send route re-checks lint/suppression/links).
- reply_send {who:"contact name or email"}  outward — approve+send a drafted warm reply (confirm first).
- reply_skip {who:"contact name or email"}  safe — discard a pending reply draft.
- warm_dispo {who:"contact name", dispo:"booked"|"noans"|"txt"|"dead"}  safe — log the outcome of a warm call ("mark Client A booked").
- todo_complete {text:"words from the todo"}  safe — check off an open todo by its text.
- content_approve {n:int optional, who:"words from the hook" optional}  safe — approve LinkedIn draft post(s); no args = approve all drafts. Publishing stays manual.

ROUTING (use launch/launch_send for these, do NOT try to browse yourself): scan/find jobs → launch job_scan. apply to jobs → launch_send job_apply (confirm). scan/find LinkedIn people or posts → launch net_scan. run/post approved LinkedIn networking → launch_send net_run (confirm). NEVER ask [OWNER] for screenshots and NEVER say you can't see a website — you trigger real actions, you do not browse. job_scan runs immediately; job_apply runs after his confirm; net_scan/net_run run on his real Chrome when the Claude app is open.

If it's just a question, answer in "reply" with steps:[]. Use the fewest steps. Today is %s."""



def _jarvis_memory() -> str:
    """Standing facts/preferences [OWNER] teaches JARVIS — edit store/jarvis_memory.md
    (or tell the commander 'remember: ...' and paste it in). Injected into every interpret."""
    try:
        m = (ROOT / "store" / "jarvis_memory.md").read_text().strip()
        return ("\nSTANDING MEMORY ([OWNER] taught you these):\n" + m[:1500] + "\n") if m else ""
    except OSError:
        return ""

def world_state() -> str:
    """One-glance real state, injected into the interpreter so it answers 'what's queued',
    'what happened today', 'replies waiting' from actual numbers instead of guessing."""
    import json as _json
    from collections import Counter
    from pathlib import Path as _P
    root = _P(__file__).resolve().parent.parent
    lines = []
    try:
        import jobs
        sc = Counter(j.get("status") for j in jobs.load_jobs())
        submitted = sum(sc.get(s, 0) for s in ("applied", "confirmed", "replied", "interview"))
        lines.append(f"Jobs: {sc.get('approved', 0)} queued, {jobs.applied_today()} applied today, "
                     f"{submitted} submitted total ({sc.get('confirmed', 0)} confirmed by ATS), "
                     f"{sc.get('interview', 0)} interviews, {sc.get('replied', 0)} awaiting your reply, "
                     f"{len(jobs.needs_manual())} need manual finish.")
    except Exception:  # noqa: BLE001
        pass
    try:
        import networking
        ns = Counter(x.get("status") for x in networking.load_queue())
        lines.append(f"LinkedIn queue: {ns.get('pending', 0)} pending, {ns.get('approved', 0)} approved, "
                     f"{ns.get('done', 0)} done.")
    except Exception:  # noqa: BLE001
        pass
    try:
        import reply_watch
        rp = sum(1 for r in reply_watch._load() if r.get("status") == "pending")
        lines.append(f"Warm replies: {rp} drafted, waiting for approval.")
    except Exception:  # noqa: BLE001
        pass
    try:
        seen, worked, booked = set(), 0, 0
        for ln in (root / "store" / "warm_dispo.jsonl").read_text().splitlines():
            try:
                r = _json.loads(ln)
            except _json.JSONDecodeError:
                continue
            if r.get("id") and r["id"] not in seen:
                seen.add(r["id"]); worked += 1
                if r.get("dispo") == "booked":
                    booked += 1
        lines.append(f"Warm calls: {worked}/58 worked, {booked} booked.")
    except OSError:
        pass
    try:
        feed = [_json.loads(x) for x in (root / "store" / "feed.jsonl").read_text().splitlines() if x.strip()]
        recent = [e.get("title", "") for e in feed[-8:] if e.get("title")]
        if recent:
            lines.append("Recent activity: " + "; ".join(recent))
    except OSError:
        pass
    return "\n".join(lines) or "(no state available)"


def _mail_digest_reply() -> str | None:
    """Format store/mail_digest.json (built every morning by the mail fleet via the OAuth
    gmail_api helper) into a tight inbox triage for the chat panel. This is the rung-1 answer
    to "triage my inbox": the brain already read Gmail this morning, so JARVIS never spawns a
    live Gmail-MCP crawl (which hangs on a permission prompt with no interactive approver).
    Returns None if there's no digest yet."""
    import json as _json
    from datetime import datetime as _dt
    p = ROOT / "store" / "mail_digest.json"
    try:
        d = _json.loads(p.read_text())
    except (OSError, _json.JSONDecodeError):
        return None
    secs = d.get("sections") or {}
    vip = secs.get("vip") or []
    resp = secs.get("response_needed") or []
    biz = secs.get("business") or []
    built = (d.get("generated") or "")[:16].replace("T", " ")
    stale = bool(d.get("date")) and d["date"] != _dt.now().astimezone().strftime("%Y-%m-%d")
    out = [f"Inbox triage ({'this morning, ' if stale else ''}{built}):"]
    if d.get("top_line"):
        out.append("- " + d["top_line"])

    def _fmt(items, label):
        if not items:
            return
        out.append(f"\n{label} ({len(items)}):")
        for m in items[:8]:
            frm = (m.get("from") or "").split("<")[0].strip().strip('"')
            tag = " [draft ready]" if m.get("draft_ready") else ""
            why = (" - " + m["why"]) if m.get("why") else ""
            out.append(f"  - {frm}: {m.get('subject', '')}{tag}{why}")
    _fmt(vip, "VIP")
    _fmt(resp, "Needs a reply")
    if biz:
        out.append(f"\nPlus {len(biz)} business/other (applications, notifications).")
    if not (vip or resp or biz):
        out.append("Nothing needs a reply. Inbox is clear.")
    out.append('\nSay "refresh inbox" to re-scan, or "draft a reply to <name>" to handle one.')
    return "\n".join(out)


# mail-intent detection: a READ/triage of the inbox as a whole, NOT a compose/reply/send
_MAIL_READ = re.compile(r"\b(inbox|e-?mails?|gmail|my mail)\b", re.I)
_MAIL_VERB = re.compile(r"\b(triage|check|read|go through|summar|scan|show|any|what|catch me up|review|go over)\b", re.I)
_MAIL_COMPOSE = re.compile(r"\b(draft|write|compose|send|reply|respond|forward)\b", re.I)
_MAIL_REFRESH = re.compile(r"\b(refresh|re-?scan|re-?check|update|pull|sync)\b.{0,15}\b(inbox|e-?mail|gmail|mail)\b", re.I)


def _mail_refresh_bg() -> str:
    """Kick a background rescan of Gmail (rung-1: mail_brain classify + mail_digest rebuild,
    both via the OAuth gmail_api helper, no MCP, no permission wall). Returns a status line."""
    py = str(ROOT / ".venv" / "bin" / "python")
    try:
        subprocess.Popen(
            ["bash", "-c",
             f"cd {ROOT} && {py} agents/mail_brain.py && {py} agents/mail_digest.py"],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "Re-scanning your inbox now. Ask again in a moment for the fresh triage."
    except Exception as e:  # noqa: BLE001
        return f"Couldn't start the rescan: {e}"


def interpret(message: str, history=None, defer_converse: bool = False) -> dict:
    # (defer_converse: the SSE stream renders the deep reply token-by-token)
    convo = ""
    if history:
        lines = []
        for h in history[-14:]:   # 6 -> 14 (2026-07-12: 3 exchanges lost context too fast)
            role = owner.get("name", "ME") if h.get("role") == "u" else "You"
            t = (h.get("text") or "").strip()
            if t:
                lines.append(f"{role}: {t}")
        if lines:
            convo = ("RECENT CONVERSATION (resolve follow-ups like 'yes' / 'do it' / 'that one' / "
                     "'the second one' against this — do NOT ask for context you can infer here):\n"
                     + "\n".join(lines) + "\n\n")
    state = (_jarvis_memory() + "CURRENT STATE (answer any 'what's queued / what happened today / replies waiting / "
             "how many' question directly from these real numbers, never guess):\n" + world_state() + "\n\n")
    low = message.lower()
    if re.search(r"\bmadd(y|ie|alena)\b|\bmy partner\b|\bgirlfriend\b", low):
        try:
            state += ("ABOUT MADDY (his partner, the real file, speak of her warmly):\n"
                      + (ROOT / "store" / "maddy.md").read_text()[:2600] + "\n\n")
        except OSError:
            pass
    # INBOX TRIAGE = rung-1 read, answered straight from the morning mail digest, NOT a live
    # agentic Gmail crawl. Before 2026-07-08 "triage my inbox" fell through to the chat brain,
    # which reached for the Gmail MCP and looped forever asking a permission it could never get
    # approved non-interactively. Compose/reply/send asks are excluded and keep the action path.
    if _MAIL_REFRESH.search(low):
        return {"reply": _mail_refresh_bg(), "steps": []}
    if _MAIL_READ.search(low) and _MAIL_VERB.search(low) and not _MAIL_COMPOSE.search(low):
        dg = _mail_digest_reply()
        if dg:
            return {"reply": dg, "steps": []}
    # v14 latency: casual conversation skips the interpret spawn entirely (one CLI call
    # instead of two) and rides the fast chat model. Anything action-shaped keeps the
    # full interpret path; anything deep-shaped keeps the big model.
    # 2026-07-12 agency fix: a message that WANTS an action but lacked a verb from this list
    # used to fall to the pure-chat lane and silently do nothing ([OWNER]: "can't execute under
    # its own volition"). A missed execution is far worse than an extra CLI call on chit-chat
    # (interpret with empty steps still falls through to conversation), so this now casts wide:
    # the original imperatives + the ones the audit found missing + drawer nouns + any URL/domain.
    actiony = (re.search(r"\b(add|done|approve|send|draft|schedule|remind|run|scan|open|book|log|mark|"
                         r"move|delete|snooze|clear|archive|post|publish|pay|buy|order|cancel|start|"
                         r"stop|toggle|enroll|apply|call|email|push|set|skip|regen(erate)?|reject|audit|"
                         r"build|make|review|prep|dispo|finish|complete|summari[sz]e|generate|refresh|"
                         r"fix|update|check|find|show|list|pull|reply|kill|pause|resume|retry|redo)\b", low)
               or re.search(r"\b(proposal|brief|pipeline|todo|to-do|invoice|the queue|warm lead|"
                            r"the (client_a|client_b|client_c)|that one|the (first|second|third|last) one)\b", low)
               or re.search(r"https?://|\b[a-z0-9][a-z0-9-]*\.(com|io|net|org|co|dev)\b", low))
    deepy = len(message) > 180 or re.search(r"\b(think|plan|strateg|analy|write|draft|review|debug|"
                                            r"architect|design|forecast|projection|proposal|pitch|"
                                            r"negotiat|deep|premortem|pre-mortem)\b", low)
    if defer_converse and not actiony:
        data = {"reply": "", "steps": [], "_defer": True,
                "_lane": ("deep" if deepy else "fast")}
    else:
        out = planner._cli(INTERPRET % now_iso()[:10] + "\n\n" + state + convo + "Command: " + message,
                           timeout=120, feature="interpret")
        data = planner._extract_json(out or "")
        if not isinstance(data, dict):
            data = {"reply": "", "steps": []}
        data.setdefault("reply", "")
        data.setdefault("steps", [])
        data["_defer"] = defer_converse
        data["_lane"] = "deep"
    # THE OPUS BRAIN: when there's nothing to execute, this is a conversation —
    # hand it to the full model (config models.jarvis) with everything he knows.
    if not data["steps"]:
        prompt = ("You are JARVIS, [OWNER]'s personal AI: composed, precise, dry wit, 'sir' sparingly, "
                  "never obsequious, NO em-dashes. You know his whole operation (state below is REAL, use the "
                  "numbers). Think like a chief of staff who can also reason deeply: strategy, drafting, math, "
                  "hard questions. Answer fully but tight; format for a small chat panel. "
                  "HARD RAIL: you draft AS [OWNER], never as a client or prospect, never fabricate their words. "
                  "When he asks you to argue AGAINST a deal or plan, do it honestly and hard (pre-mortem mode).\n\n"
                  + state + convo + "[OWNER]: " + message)
        lane = data.get("_lane") or "deep"
        if data.pop("_defer", False):
            # the stream will run this token-by-token (v13: perceived speed)
            data["_converse"] = prompt
            data["_ctx"] = [x for x, on in (("quick" if lane == "fast" else "deep thought", True),
                                            ("live state", bool(state)),
                                            ("memory", "STANDING MEMORY" in state),
                                            ("history", bool(convo))) if on]
        else:
            deep = planner._cli(prompt, timeout=170,
                                feature=("chat_fast" if lane == "fast" else "jarvis"))
            if deep and deep.strip():
                data["reply"] = deep.strip()
    return data


def _sse(obj) -> str:
    return "data: " + json.dumps(obj) + "\n\n"


def _stream_converse(prompt: str, lane: str = "deep"):
    """claude -p --output-format stream-json -> yield text deltas as they arrive.
    lane 'fast' rides models.chat_fast (snappy TTFT for conversation); 'deep' keeps jarvis."""
    from planner import _find_claude_cli, _models
    cli = _find_claude_cli()
    m = _models()
    model = ((m.get("chat_fast") if lane == "fast" else None)
             or m.get("jarvis") or m.get("default"))
    if not cli:
        yield None, "brain offline (no CLI)"
        return
    # perl alarm = hard ceiling on the child itself (same wrapper every other CLI spawn
    # uses). Without it a hung provider stalls `for line in proc.stdout` FOREVER and the
    # finally-terminate never runs (2026-07-07 audit H1).
    # --strict-mcp-config + empty mcp config: the streaming chat brain gets NO MCP servers, so
    # it can never reach for the Gmail tool and stall on a permission prompt (2026-07-08). It
    # reasons over the injected REAL state; inbox triage is served from the mail digest instead.
    proc = subprocess.Popen(["perl", "-e", "alarm 300; exec @ARGV",
                             cli, "-p", prompt, "--model", model,
                             "--output-format", "stream-json", "--verbose",
                             "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, cwd="/tmp")
    full = []
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except (ValueError, json.JSONDecodeError):
                continue
            # stream-json shapes: assistant message deltas carry content blocks
            if ev.get("type") == "assistant":
                for blk in ((ev.get("message") or {}).get("content") or []):
                    t = blk.get("text") or ""
                    if t:
                        full.append(t)
                        yield t, None
            elif ev.get("type") == "result" and not full:
                t = ev.get("result") or ""
                if t:
                    full.append(t)
                    yield t, None
    finally:
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)  # reap: an un-waited child zombies until the parent exits
        except Exception:  # noqa: BLE001
            pass
    yield None, "".join(full)


def run_command_stream(message: str, history=None):
    yield _sse({"type": "step", "text": "interpreting your command…"})
    plan = interpret(message, history, defer_converse=True)
    if plan.get("_converse"):
        if plan.get("_ctx"):
            yield _sse({"type": "ctx", "items": plan["_ctx"]})
        chunks = 0
        for delta, final in _stream_converse(plan["_converse"], plan.get("_lane") or "deep"):
            if delta is not None:
                chunks += 1
                yield _sse({"type": "reply_chunk", "text": delta})
            elif final is not None:
                yield _sse({"type": "reply_final", "text": final.strip()})
        if not chunks:
            yield _sse({"type": "reply", "text": "The brain hiccuped. Ask me again, sir."})
    # 2026-07-12 (chat audit #2/#8): when there ARE steps, the reply comes AFTER execution
    # and can reference actual results ("regenerate my brief and tell me what changed" used
    # to answer first, then run). A steps-free plan keeps the immediate reply.
    steps = plan.get("steps", [])
    if plan.get("reply") and not steps:
        yield _sse({"type": "reply", "text": plan["reply"]})
    did, results = [], []
    # results that start like these are failures, not wins — render them honestly instead
    # of the old unconditional green "✓" (chat audit #5)
    _ERRISH = re.compile(r"^\s*(couldn't|can't|cannot|no |not |unknown|error|failed|blocked|held|"
                         r"ambiguous|expired|dispo must|send failed|NOT sent)", re.I)
    for st in steps:
        act, args = st.get("action"), st.get("args", {}) or {}
        act = _gate_action(act, args)
        if act in SAFE:
            yield _sse({"type": "step", "text": f"▶ {act} · {_short(args)}"})
            res = SAFE[act](args)
            ok = not _ERRISH.search(str(res or ""))
            yield _sse({"type": "done" if ok else "step",
                        "text": (f"✓ {res}" if ok else f"⚠ {res}")})
            did.append(act)
            results.append(f"{act}: {res}")
        elif act in OUTWARD:
            pid = new_id(act + message)
            # sweep confirms [OWNER] never answered (>1h old): every ignored bubble used to
            # leak a PENDING entry forever, and a week-old confirm could still fire
            import time as _t
            cut = _t.time() - 3600
            for k in [k for k, v in list(PENDING.items()) if v.get("ts", 0) < cut]:
                PENDING.pop(k, None)
            PENDING[pid] = {"action": act, "args": args, "ts": _t.time()}
            yield _sse({"type": "confirm", "id": pid,
                        "text": f"{act} · {_short(args)}", "action": act})
            results.append(f"{act}: waiting on [OWNER]'s confirm tap")
        else:
            # chat audit #4: a hallucinated/unwired action used to vanish as a dim note
            yield _sse({"type": "step", "text": f"⚠ no action '{act}' wired — tell me and I'll add it"})
            results.append(f"{act}: not wired")
    if steps:
        # post-action reply that can actually reference what happened (fast lane, short)
        synth = ""
        if results:
            synth = planner._cli(
                "You are JARVIS (composed, precise, dry wit, no em-dashes). One or two short "
                "lines to [OWNER] reacting to what just ran, referencing the concrete results. "
                "If something failed, say so plainly and what to do next.\n"
                "HIS REQUEST: " + message + "\nWHAT RAN:\n" + "\n".join(results),
                timeout=60, feature="chat_fast") or ""
        yield _sse({"type": "reply", "text": (synth.strip() or plan.get("reply") or "Done.")})
    if did:
        planner.feed_add("command", "Ran: " + ", ".join(did))
    yield _sse({"type": "end"})


def confirm(pid: str) -> dict:
    p = PENDING.pop(pid, None)
    if not p:
        return {"ok": False, "error": "expired — run the command again"}
    fn = OUTWARD.get(p["action"])
    if not fn:
        return {"ok": False, "error": "unknown action"}
    res = fn(p["args"])
    planner.feed_add("command", f"Confirmed: {p['action']}")
    return {"ok": True, "result": res}
