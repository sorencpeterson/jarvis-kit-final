"""Proactive planner — the chief-of-staff brain.

generate_today(): a Haiku sub-agent reads goals + current state + business context
and proposes the 3 highest-leverage actions toward [OWNER]'s goals. Cached per day.

accept(action_id): a Haiku sub-agent BUILDS the accepted action — drafts the thing,
breaks it into todos, produces concrete output — and logs to the feed. Outward/
irreversible steps are never executed here; they're surfaced for a second confirm.

Runs on the Claude CLI (Max plan, no per-use cost). Falls back gracefully.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "store" / "plan.json"
FEED = ROOT / "store" / "feed.jsonl"
GOALS = ROOT / "store" / "goals.json"
CONFIG = ROOT / "store" / "config.json"
BIZLIB = Path(os.environ.get("BIZLIB") or (ROOT / "business-library"))


def _config() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def notify(title: str, body: str, tags: str = "brain", actions: list | None = None) -> bool:
    """Push to [OWNER]'s phone via ntfy.sh (free, no account).

    actions: optional ntfy action buttons, e.g.
      [{"action": "view", "label": "Approve", "url": "https://.../api/act/retro_apply?sig=..."}]
    One-tap buttons only reach the Mac when config public_base_url points at the
    Tailscale URL, so callers skip actions when it's unset.
    """
    topic = _config().get("ntfy_topic")
    if not topic:
        return False
    try:
        import urllib.request
        # HTTP headers are latin-1; strip emoji/non-ASCII from the Title (put emoji in Tags instead)
        title_safe = (title or "").encode("ascii", "ignore").decode().strip() or "Brain"
        if actions:
            payload = json.dumps({"topic": topic, "title": title_safe, "message": body,
                                  "tags": [t.strip() for t in tags.split(",") if t.strip()],
                                  "actions": actions[:3]}).encode("utf-8")
            req = urllib.request.Request("https://ntfy.sh", data=payload,
                                         headers={"Content-Type": "application/json"})
        else:
            req = urllib.request.Request(
                "https://ntfy.sh/" + topic, data=body.encode("utf-8"),
                headers={"Title": title_safe, "Tags": tags},
            )
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception:
        return False

import sys
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from store_lib import LOCAL_TZ, new_id, now_iso, humanize, _flock  # noqa: E402
from brain import _find_claude_cli  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"


def _today() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


def feed_add(kind: str, title: str, detail: str = ""):
    FEED.parent.mkdir(parents=True, exist_ok=True)
    with FEED.open("a") as f:
        f.write(json.dumps({"ts": now_iso(), "kind": kind, "title": title, "detail": detail}) + "\n")


def feed_recent(n: int = 30) -> list[dict]:
    if not FEED.exists():
        return []
    lines = [l for l in FEED.read_text().splitlines() if l.strip()]
    out = []
    for l in lines[-n:]:
        try:
            out.append(json.loads(l))
        except json.JSONDecodeError:
            pass
    return list(reversed(out))


def _context() -> str:
    bits = []
    # WHO comes from the owner config, not a baked description. A copy of this
    # system must not tell its planner it runs somebody else's business.
    try:
        import owner
        o = owner.load()
    except Exception:  # noqa: BLE001
        o = {}
    who = f"WHO: {o.get('name') or '[OWNER]'}"
    if o.get("site"):
        who += f" ({o['site']})"
    if o.get("company"):
        who += f". Runs {o['company']}"
    if o.get("what_you_do"):
        who += f". {o['what_you_do']}"
    if o.get("icp"):
        who += f". Works with: {o['icp']}"
    if o.get("offer"):
        who += f". Offer: {o['offer']}"
    bits.append(who + ".")
    try:
        bits.append("GOALS (across life areas): " + GOALS.read_text())
    except OSError:
        pass
    try:
        bits.append("PROJECTS (across life areas): " + (ROOT / "store" / "projects.json").read_text())
    except OSError:
        pass
    try:
        bits.append("CURRENT WORK BOARD (his live status/tasks/info by domain): "
                    + (ROOT / "store" / "board.json").read_text()[:1800])
    except OSError:
        pass
    for name in ("business-profile.md", "offers.md", "icp-and-personas.md", "brand-voice.md"):
        p = BIZLIB / name
        if p.is_file():
            bits.append(f"{name}:\n" + p.read_text()[:1000])
    return "\n\n".join(bits)


# Run sub-agents from a neutral dir so they DON'T inherit the ~/Claude workspace
# CLAUDE.md (which makes them chatty / ask questions). Cheaper + deterministic.
NEUTRAL = "/tmp"


def _models() -> dict:
    try:
        return json.loads((ROOT / "store" / "config.json").read_text()).get("models", {})
    except (OSError, json.JSONDecodeError):
        return {}


def _log_usage(feature: str, model: str, usage: dict):
    try:
        rec = {"ts": now_iso(), "feature": feature, "model": model,
               "in": usage.get("input_tokens", 0), "out": usage.get("output_tokens", 0),
               "cache_read": usage.get("cache_read_input_tokens", 0),
               "cache_write": usage.get("cache_creation_input_tokens", 0)}
        with (ROOT / "store" / "usage.jsonl").open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _cli(prompt: str, timeout: int = 130, feature: str = "default") -> str | None:
    # Owner identity: prompts across this codebase carry [OWNER]-style tokens so the
    # system is not hardcoded to one person. Swapping them HERE means every agent
    # speaks as the configured owner without knowing owner.py exists. Failure is
    # swallowed: an unconfigured copy still runs, it just says "the owner".
    try:
        import owner
        prompt = owner.personalize(prompt)
    except Exception:  # noqa: BLE001
        pass
    # budget-aware routing (#40): past the daily token budget, internal features
    # degrade to Haiku automatically; public-facing writing keeps its model.
    try:
        budget = int(_config().get("daily_token_budget") or 0)
        if budget and feature not in ("content", "networking", "reply"):
            spent = 0
            for line in (ROOT / "store" / "usage.jsonl").read_text().splitlines():
                try:  # per-line: one bad line must not fail the whole budget check open
                    r = json.loads(line)
                except (ValueError, json.JSONDecodeError):
                    continue
                if (r.get("ts") or "")[:10] == datetime.now().strftime("%Y-%m-%d"):
                    # CX26: cache_read/cache_write are real token throughput too (they were
                    # being logged by _log_usage but never counted here) — a heavy-cache day
                    # could run ~5x actual usage while this sum stayed under budget and never
                    # triggered the Haiku downgrade.
                    spent += ((r.get("in") or 0) + (r.get("out") or 0)
                             + (r.get("cache_read") or 0) + (r.get("cache_write") or 0))
            if spent > budget:
                feature = "over_budget"  # routes to the default (Haiku) tier
    except Exception:
        pass
    """Route the model by feature (public-facing writing -> Sonnet, internal -> Haiku) and
    meter every call to store/usage.jsonl. Falls back to raw output on any non-JSON result."""
    m = _models()
    model = m.get(feature) or m.get("default") or MODEL

    # Cheap-provider routing. Only fires when this feature's model is an explicit
    # "provider:<name>" entry the owner configured, so the default path below is
    # untouched. Any failure (missing key, outage, bad response) falls THROUGH to the
    # claude CLI rather than taking the agent down: a third party being unreachable
    # should cost money, not a morning run. The fallback is printed, never silent,
    # because a provider that quietly never works looks identical to one that is
    # simply cheap.
    try:
        import providers
        prov = providers.resolve(model)
    except Exception:  # noqa: BLE001
        prov = None
    if prov:
        text, usage = providers.call(prov, prompt, timeout=timeout)
        if text is not None:
            _log_usage(feature, f"provider:{prov['name']}/{prov['model']}", usage)
            return text
        model = m.get("default") or MODEL     # fell back: use a real claude model id

    cli = _find_claude_cli()
    if not cli:
        return None
    try:
        out = subprocess.run(
            # --strict-mcp-config + an EMPTY mcp config = this text-gen child inherits NONE of
            # the user's MCP servers (gmail, playwright, ...). Without it the chat/interpret
            # child could decide to "use the Gmail tool", hit a permission prompt with no
            # interactive approver, and loop forever emitting "may I access Gmail?" (2026-07-08).
            # Every _cli caller is pure text over injected state; the agents that truly need a
            # tool (interview_prep, meeting_prep, ...) spawn their own claude with --allowedTools.
            ["perl", "-e", f"alarm {timeout-10}; exec @ARGV", cli, "-p", prompt,
             "--model", model, "--output-format", "json",
             "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
            capture_output=True, text=True, timeout=timeout, cwd=NEUTRAL,
        ).stdout
    except Exception:  # noqa: BLE001
        return None
    try:
        j = json.loads(out)
        if isinstance(j, dict) and "result" in j:
            _log_usage(feature, model, j.get("usage") or {})
            return j.get("result") or ""
    except (ValueError, json.JSONDecodeError):
        pass
    return out


def _extract_json(s: str):
    """Parse the outermost JSON value — whichever of { or [ appears FIRST in the
    text (so a top-level object isn't mistaken for its inner array)."""
    if not s:
        return None
    cands = []
    for opener, closer in (("{", "}"), ("[", "]")):
        i = s.find(opener)
        if i >= 0:
            cands.append((i, opener, closer))
    cands.sort()
    for i, opener, closer in cands:
        j = s.rfind(closer)
        if j > i:
            try:
                return json.loads(s[i:j + 1])
            except json.JSONDecodeError:
                continue
    return None


def _cli_json(prompt: str, timeout: int = 130, feature: str = "default"):
    return _extract_json(_cli(prompt, timeout, feature) or "")


PLAN_PROMPT = """You are [OWNER]'s chief-of-staff. Propose the THREE highest-leverage actions he could take TODAY to move toward his goals. Span life areas where it helps (business, content, finance, health, relationships, mental health, personal) but bias to real leverage, not busywork. Warm channels beat cold outreach.

Do NOT ask clarifying questions. Use the context below; make reasonable assumptions if thin. Output ONLY the JSON, nothing else.

Return ONLY a JSON array of exactly 3 objects:
[{"title":"short imperative","area":"business|content|finance|health|relationships|mind|personal","why":"one sharp line on the payoff","effort":"15m|1h|halfday","plan":["concrete step","concrete step"]}]

CONTEXT:
%s

OPEN TODOS: %s
RECENT ACTIVITY: %s
Be direct and specific. No fluff."""


def generate_today(state: dict, force: bool = False) -> dict:
    if not force and PLAN.exists():
        try:
            cached = json.loads(PLAN.read_text())
            if cached.get("date") == _today():
                return cached
        except (OSError, json.JSONDecodeError):
            pass
    open_txt = "; ".join(t["text"] for t in state.get("all_open", [])[:15]) or "none"
    recent = "; ".join(f"{e['kind']}:{e['title']}" for e in feed_recent(8)) or "none"
    data = _cli_json(PLAN_PROMPT % (_context(), open_txt, recent))
    actions = []
    if isinstance(data, list):
        for a in data[:3]:
            actions.append({
                "id": new_id(a.get("title", "") + _today()),
                "title": a.get("title", "Untitled"),
                "area": a.get("area", "business"),
                "why": a.get("why", ""),
                "effort": a.get("effort", "1h"),
                "plan": a.get("plan", []),
                "status": "proposed",
            })
    plan = {"date": _today(), "actions": actions,
            "generated": now_iso(), "ok": bool(actions)}
    # CX25: lock the write — accept() below also writes plan.json (it can hold the file for
    # up to 180s mid-build), and an unlocked overwrite here could silently discard a build it
    # just persisted, or vice versa.
    with _flock(PLAN):
        PLAN.write_text(json.dumps(plan, indent=2))
    if actions:
        feed_add("plan", f"{len(actions)} actions proposed for today")
    return plan


BUILD_PROMPT = """You are [OWNER]'s chief-of-staff executor. He ACCEPTED this action. BUILD it now, fully, as far as you safely can without sending anything externally.

HARD RULE: Do NOT ask [OWNER] any questions. Never reply with "I need more intel", "do you have", or any request for information. You already have everything you need in CONTEXT below. Make reasonable, specific assumptions from his business and just produce the finished deliverable. If one detail is genuinely unknown, pick a sensible default and flag it inline like [assumption: ...], never stop to ask.

ACTION: %s
WHY: %s
PLAN: %s

CONTEXT:
%s

LEAD WITH THE DELIVERABLE ITSELF, not caveats. Do NOT open with apologies about lacking credentials, access, or details. Just build everything you can; if the final step is outward/irreversible (sending, activating, publishing), build right up to it and list that single step as a todo at the end.

Produce the actual finished deliverable inline (draft copy, checklist, outline, analysis, whatever the action needs), in [OWNER]'s voice: direct, punchy, no fluff, NO em-dashes, human-sounding, first-person opinion where it fits. Then, if useful, end with a fenced block labelled actions containing JSON Lines of follow-up todos to create:
{"op":"add","text":"..","project":"ghl-dbr|agency-cold-outreach|web-automation|null","priority":1|2|3|null}
Do NOT send emails, publish, or take irreversible steps. Note those as todos for [OWNER] to confirm."""


def accept(action_id: str, state: dict, run_action):
    plan = json.loads(PLAN.read_text()) if PLAN.exists() else {"actions": []}
    act = next((a for a in plan.get("actions", []) if a["id"] == action_id), None)
    if not act:
        return {"ok": False, "error": "action not found"}
    if not _find_claude_cli():
        return {"ok": False, "error": "no Claude CLI (login required)"}
    prompt = BUILD_PROMPT % (act["title"], act.get("why", ""),
                             " | ".join(act.get("plan", [])), _context())
    out = _cli(prompt, timeout=180)
    # S: a perl-alarm timeout can return raw/empty stdout without _cli itself returning
    # None (e.g. the subprocess was killed mid-write and captured nothing) — guard on
    # blank/whitespace-only too, or an empty string gets persisted as a "built" deliverable.
    if not out or not out.strip():
        return {"ok": False, "error": "build failed"}

    deliverable, did = out, []
    if "```actions" in out:
        deliverable, _, rest = out.partition("```actions")
        for line in rest.split("```", 1)[0].splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    r = run_action(json.loads(line))
                    if r:
                        did.append(r)
                except json.JSONDecodeError:
                    pass
    clean = humanize(deliverable.strip())
    act["status"] = "built"
    act["deliverable"] = clean
    # CX25: the _cli() build above can run for up to 180s; lock + re-read the LATEST
    # plan.json right before writing so a concurrent generate_today(force=True) regen (or a
    # second accept() call) that wrote during that window doesn't get silently discarded by
    # a stale-snapshot overwrite (and isn't itself discarded by this write).
    with _flock(PLAN):
        latest = json.loads(PLAN.read_text()) if PLAN.exists() else plan
        for i, a in enumerate(latest.get("actions", [])):
            if a["id"] == action_id:
                latest["actions"][i] = act
                break
        else:
            latest.setdefault("actions", []).append(act)
        PLAN.write_text(json.dumps(latest, indent=2))
    feed_add("built", f"Built: {act['title']}", f"created {len(did)} todo(s)")
    return {"ok": True, "deliverable": clean, "did": did, "action": act}
