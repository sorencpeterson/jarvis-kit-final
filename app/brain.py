"""The brain behind the chat console.

Two modes:
  1. Command mode (default): a deterministic parser handles the common verbs
     (add / done / schedule / what's my day / list). No LLM, free, offline.
  2. Full AI mode: if ANTHROPIC_API_KEY is set (env or second-brain/.env), natural
     language that the parser can't handle is sent to the Anthropic API, which can
     reply and emit the same action protocol.

Returns (reply: str, actions: list[dict]) where each action is one of:
  {"op":"add","text":..,"project":..,"priority":..,"at":..,"dur":..}
  {"op":"complete","id":..}  {"op":"triage","id":..,...}  {"op":"reschedule","id":..,"at":..}
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
PROJECTS = ("ghl-dbr", "agency-cold-outreach", "web-automation")


def _load_key() -> str | None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            if line.strip().startswith("ANTHROPIC_API_KEY"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


# ---------- light natural-time parsing ----------
def parse_when(text: str, now: datetime):
    """Pull a rough date/time out of free text. Returns (iso_or_none, cleaned_text)."""
    t = text
    day = now.date()
    has_day = False
    if re.search(r'\btomorrow\b', t, re.I):
        day = (now + timedelta(days=1)).date(); has_day = True
        t = re.sub(r'\btomorrow\b', '', t, flags=re.I)
    elif re.search(r'\b(today|tonight)\b', t, re.I):
        has_day = True
        t = re.sub(r'\b(today|tonight)\b', '', t, flags=re.I)
    m = re.search(r'\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b', t, re.I)
    iso = None
    if m:
        hh = int(m.group(1)); mm = int(m.group(2) or 0)
        ap = (m.group(3) or '').lower()
        if ap == 'pm' and hh < 12: hh += 12
        if ap == 'am' and hh == 12: hh = 0
        if not ap and hh <= 7: hh += 12  # "at 3" -> afternoon, business default
        dt = datetime(day.year, day.month, day.day, hh, mm, tzinfo=now.tzinfo)
        iso = dt.strftime("%Y-%m-%dT%H:%M")
        t = t[:m.start()] + t[m.end():]
    elif has_day:
        dt = datetime(day.year, day.month, day.day, 9, 0, tzinfo=now.tzinfo)
        iso = dt.strftime("%Y-%m-%dT%H:%M")
    return iso, re.sub(r'\s{2,}', ' ', t).strip(' ,.')


def parse_priority(text: str):
    m = re.search(r'\b(?:priority|prio|p)\s*([123])\b', text, re.I)
    if m:
        return int(m.group(1)), re.sub(r'\b(?:priority|prio|p)\s*[123]\b', '', text, flags=re.I).strip()
    if re.search(r'\b(urgent|asap|important)\b', text, re.I):
        return 1, text
    return None, text


def parse_project(text: str):
    low = text.lower()
    if any(w in low for w in ("ghl", "campaign", "dbr", "email sequence")):
        return "ghl-dbr"
    if any(w in low for w in ("agency", "cold outreach", "enrichment")):
        return "agency-cold-outreach"
    if any(w in low for w in ("upwork", "automation", "playwright", "scrape", "linkedin")):
        return "web-automation"
    return None


# ---------- command mode ----------
def command_mode(msg: str, state: dict, now: datetime):
    m = msg.strip()
    low = m.lower()

    # add / remind
    am = re.match(r'^(?:add|remind me to|todo|capture|new)\b[:\-\s]+(.+)$', m, re.I)
    if am:
        body = am.group(1)
        prio, body = parse_priority(body)
        at, body = parse_when(body, now)
        proj = parse_project(body)
        if not body:
            return "What should I add?", []
        return (f"Added: {body}" + (f" · {at.replace('T',' ')}" if at else "")
                + (f" · P{prio}" if prio else ""),
                [{"op": "add", "text": body, "project": proj, "priority": prio, "at": at, "dur": 30 if at else None}])

    # done / complete
    dm = re.match(r'^(?:done|complete|finish|completed|did)\b[:\-\s]+(.+)$', m, re.I)
    if dm:
        needle = dm.group(1).strip().lower()
        hit = [t for t in state["all_open"] if needle in t["text"].lower()]
        if len(hit) == 1:
            return f"Marked done: {hit[0]['text']}", [{"op": "complete", "id": hit[0]["id"]}]
        if not hit:
            return f"Nothing open matches “{needle}”.", []
        return "Which one? " + "; ".join(h["text"] for h in hit[:5]), []

    # what's my day / status
    if re.search(r"(what'?s|hows|how is).*(day|today|on)|^today$|^status$|what.*do.*today", low):
        tb = state["todos"]
        lines = [f"Today: {len(tb['today'])} scheduled, {len(tb['inbox'])} in inbox, {len(tb['upcoming'])} upcoming."]
        for t in tb["today"][:6]:
            when = t["scheduled_time"][11:16] if t.get("scheduled_time") else ""
            lines.append(f"  • {when} {t['text']}")
        if tb["inbox"]:
            lines.append("Inbox: " + "; ".join(t["text"] for t in tb["inbox"][:5]))
        return "\n".join(lines), []

    # list / inbox
    if low in ("list", "inbox", "what's in my inbox", "show inbox"):
        inb = state["todos"]["inbox"]
        if not inb:
            return "Inbox is clear.", []
        return "Inbox:\n" + "\n".join(f"  • {t['text']}" for t in inb[:12]), []

    # schedule
    if re.search(r'\bschedule\b', low):
        n = len(state["todos"]["inbox"])
        return (f"You have {n} inbox item(s). I can propose time-blocks per your 9–5 rules — "
                "say 'yes, schedule them' and (once Google Calendar is connected) I'll write them. "
                "For now I can set times: try 'add prep deck tomorrow at 10am'.", [])

    if low in ("help", "?", "commands"):
        return ("I understand: add <thing> [tomorrow at 3pm] [p1], done <thing>, "
                "what's my day, inbox, schedule. Add an ANTHROPIC_API_KEY to second-brain/.env "
                "for full natural-language mode.", [])
    return None  # no command matched


# ---------- full AI mode ----------
SYSTEM = """You are [OWNER]'s second-brain assistant inside his command-center app. You can see his open todos and act on the store. Reply briefly in his voice (direct, no fluff). To act, end with a fenced block labelled actions containing JSON Lines:
{"op":"add","text":"..","project":"ghl-dbr|agency-cold-outreach|web-automation|null","priority":1|2|3|null,"at":"YYYY-MM-DDTHH:MM"|null,"dur":30}
{"op":"complete","id":"tdo_.."}
{"op":"triage","id":"tdo_..","project":"..","priority":2}
{"op":"reschedule","id":"tdo_..","at":"YYYY-MM-DDTHH:MM","dur":60}
Only emit actions you're confident about. Working hours 9-5 Eastern."""


def ai_mode(msg: str, state: dict, key: str):
    try:
        import urllib.request
        payload = {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 700,
            "system": SYSTEM,
            "messages": [{"role": "user",
                          "content": f"OPEN:{json.dumps(state['all_open'], default=str)}\nNOW:{state['now']}\n\n[OWNER]: {msg}"}],
        }
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode(),
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        out = "".join(b.get("text", "") for b in data.get("content", []))
    except Exception as e:
        return f"(AI mode error: {e})", []

    reply, actions = out, []
    if "```actions" in out:
        reply, _, rest = out.partition("```actions")
        for line in rest.split("```", 1)[0].splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    actions.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return reply.strip() or "Done.", actions


def _find_claude_cli() -> str | None:
    """Locate the Claude Code CLI — runs on the Max subscription, no per-use cost."""
    import shutil
    found = shutil.which("claude")
    if found:
        return found
    for p in (Path.home() / ".local/bin/claude",
              Path.home() / ".claude/local/claude",
              Path("/opt/homebrew/bin/claude"),
              Path("/usr/local/bin/claude")):
        if p.exists():
            return str(p)
    return None


def cli_mode(msg: str, state: dict, cli: str):
    """Full AI via the Claude Code CLI (`claude -p`) — billed to the Max plan."""
    import subprocess
    prompt = (SYSTEM + "\n\nOPEN:" + json.dumps(state["all_open"], default=str)
              + "\nNOW:" + str(state["now"]) + "\n\n[OWNER]: " + msg)
    try:
        out = subprocess.run(
            ["perl", "-e", "alarm 120; exec @ARGV", cli, "-p", prompt,
             "--model", "claude-haiku-4-5-20251001"],
            capture_output=True, text=True, timeout=130,
        ).stdout
    except Exception as e:
        return f"(CLI error: {e})", []
    reply, actions = out, []
    if "```actions" in out:
        reply, _, rest = out.partition("```actions")
        for line in rest.split("```", 1)[0].splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    actions.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return reply.strip() or "Done.", actions


def respond(msg: str, state: dict, now: datetime):
    cmd = command_mode(msg, state, now)
    if cmd is not None:
        return cmd
    # Prefer the CLI (covered by Max, no per-use cost) over the metered API.
    cli = _find_claude_cli()
    if cli:
        return cli_mode(msg, state, cli)
    key = _load_key()
    if key:
        return ai_mode(msg, state, key)
    return ("I didn't catch a command. Try: add <thing> [tomorrow at 3pm], done <thing>, "
            "what's my day, inbox. Install the Claude CLI (covered by your Max plan) or add an "
            "ANTHROPIC_API_KEY to second-brain/.env for full natural-language chat.", [])
