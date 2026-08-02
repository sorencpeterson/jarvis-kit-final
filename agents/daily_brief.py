#!/usr/bin/env python3
"""Daily-brief agent: ensure today's 3 moves exist, then write a punchy morning
brief summarizing where things stand. Output → store/brief.json + the feed.

E324/E335/E373 (brief personalization, token budget, proofread pass): the
brief now leads with HIS single next action pulled straight from
store/attention.json (agents/attention.py's ranked top item) instead of
generic system news, is hard-capped at MAX_WORDS words (truncated honestly
with a marker if the model overshoots, never silently), and runs through
store_lib.humanize() before saving — the same em-dash-stripping voice filter
every other client-facing/[OWNER]-facing text in this repo goes through.

Run:  uv run python agents/daily_brief.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "dashboard"):
    sys.path.insert(0, str(p))
from store_lib import LOCAL_TZ, humanize, load_todos, now_iso  # noqa: E402
import planner  # noqa: E402
from runlog import track  # noqa: E402  (E353: runlog adoption)

BRIEF = ROOT / "store" / "brief.json"
ATTENTION = ROOT / "store" / "attention.json"
MAIL_DIGEST = ROOT / "store" / "mail_digest.json"
MAIL_MAX_AGE_H = 36  # older than this and the digest is yesterday's inbox, not news
MAX_WORDS = 300


def _sent_stamp(day: str) -> Path:
    return ROOT / "store" / f".daily_brief_sent-{day}"


def already_sent_today() -> bool:
    """R2-53: idempotency owned by daily_brief itself, stamped right after a
    CONFIRMED push -- not ~100 steps later via morning.sh's overall
    .morning-done-<date> completion stamp. A crash anywhere later in that
    108-step chain used to leave .morning-done unwritten even though the brief
    already pushed, so a self-heal re-run of the whole chain re-pushed the exact
    same notification."""
    today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    return _sent_stamp(today).exists()


def _state():
    todos = load_todos()
    today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    return {
        "all_open": [t for t in todos if t["status"] in ("inbox", "scheduled", "doing")],
        "today": [t for t in todos if t["status"] in ("scheduled", "doing") and (t.get("scheduled_time") or "")[:10] <= today and t.get("scheduled_time")],
        "inbox": [t for t in todos if t["status"] == "inbox"],
        "done_today": [t for t in todos if t["status"] == "done" and (t.get("scheduled_time", "") or t.get("created", ""))[:10] == today],
        "now": now_iso(),
    }


def _top_attention_line() -> str:
    """His single most important thing right now, from agents/attention.py's
    output. Missing/empty file is a valid state (attention.py hasn't run
    yet, or every queue is genuinely clear) -> honest fallback string, never
    a crash or a made-up urgency."""
    try:
        data = json.loads(ATTENTION.read_text())
        line = (data.get("top_line") or "").strip()
        return line or "nothing flagged, every queue is clear"
    except (OSError, json.JSONDecodeError):
        return "(attention router hasn't run yet — no ranked top item available)"


def _mail_line() -> str:
    """One line from agents/mail_digest.py's output: top_line plus a couple of
    section counts. Missing, unreadable, or stale (>36h) digest -> '' so the
    brief simply says nothing about mail instead of surfacing an old inbox as
    if it were fresh (same honest-fallback stance as _top_attention_line)."""
    try:
        data = json.loads(MAIL_DIGEST.read_text())
        gen = datetime.fromisoformat(data.get("generated", ""))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return ""
    if gen.tzinfo is None:
        gen = gen.replace(tzinfo=LOCAL_TZ)
    if (datetime.now(LOCAL_TZ) - gen).total_seconds() > MAIL_MAX_AGE_H * 3600:
        return ""
    secs = data.get("sections", {})
    reply_n = len(secs.get("response_needed") or [])
    vip_n = len(secs.get("vip") or [])
    news_n = secs.get("newsletter_count") or 0
    counts = f"{reply_n} need a reply, {vip_n} vip, {news_n} newsletters held back"
    top = humanize((data.get("top_line") or "").strip())
    return f"{top} ({counts})" if top else counts


def _cap_words(text: str, max_words: int = MAX_WORDS) -> str:
    """Hard word-cap. Truncates at a sentence boundary when possible so a cut
    brief still reads cleanly, and ALWAYS marks a truncation visibly rather
    than silently dropping content (E335: information-dense, not silently lossy)."""
    words = text.split()
    if len(words) <= max_words:
        return text
    truncated = " ".join(words[:max_words])
    # prefer cutting at the last sentence-ending punctuation within budget
    last_stop = max(truncated.rfind(". "), truncated.rfind("! "), truncated.rfind("? "))
    if last_stop > len(truncated) * 0.6:  # only trust it if it's not way too early
        truncated = truncated[:last_stop + 1]
    return truncated.rstrip() + " [truncated at word cap]"


GENTLE = (Path(__file__).resolve().parent.parent / "store" / ".gentle-morning").exists()

PROMPT = ("""GENTLE MODE: he slept under six hours. Softer tone, fewer demands, lead with the single most important thing only.
""" if GENTLE else "") + """Write [OWNER]'s morning brief as JARVIS: composed, precise, dry wit, a capable AI butler (3 to 4 short sentences, under %s words total). Address him as "sir" at most once. LEAD with his #1 thing right now (given below as HIS TOP MOVE RIGHT NOW) — that's the first sentence, not buried. Then cover anything else slipping and a nudge toward today's planned moves. If STATE includes a mail line, give the inbox exactly one short sentence (the top mail item or the counts), no more. Don't list everything; be a sharp chief-of-staff, never obsequious, no greeting fluff, NO em-dashes. Plain text only.

HIS TOP MOVE RIGHT NOW (from the attention router — lead with this): %s

STATE:
- scheduled today: %s
- inbox (untriaged or unscheduled): %s
- done today: %s
- open total: %s%s
TODAY'S TOP MOVES:
%s"""


def main() -> int:
    with track("daily_brief"):
        st = _state()
        plan = planner.generate_today(st)  # ensure 3 moves exist
        moves = "\n".join(f"- {a['title']} ({a['area']})" for a in plan.get("actions", [])) or "none yet"
        today_txt = "; ".join(t["text"] for t in st["today"][:6]) or "nothing scheduled"
        inbox_txt = "; ".join(t["text"] for t in st["inbox"][:8]) or "clear"
        top_move = _top_attention_line()
        mail = _mail_line()
        mail_block = f"\n- mail (overnight digest): {mail}" if mail else ""

        text = planner._cli(PROMPT % (MAX_WORDS, top_move, today_txt, inbox_txt, len(st["done_today"]),
                                      len(st["all_open"]), mail_block, moves), timeout=120)
        text = (text or "").strip() or "Brief unavailable — Claude CLI offline."
        text = _cap_words(humanize(text))
        BRIEF.write_text(json.dumps({"date": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d"),
                                     "text": text, "generated": now_iso()}, indent=2))
        planner.feed_add("brief", "Daily brief ready", text[:90])
    # Phone push: content-free by default (nothing private leaves the Mac); full text only if opted in.
    n = len(plan.get("actions", []))
    if planner._config().get("push_full"):
        moves = "\n".join(f"• {a['title']}" for a in plan.get("actions", [])[:3])
        body = text + ("\n\nTOP MOVES:\n" + moves if moves else "")
    else:
        body = f"{n} moves ready · open your command bridge"

    print(text)
    if already_sent_today():
        # R2-53: idempotent -- e.g. a self-heal re-run of the morning chain after a
        # LATER step crashed. brief.json above still refreshed; don't re-push.
        print("\n[phone push: skipped, already sent today]")
        return 0

    pushed = planner.notify("☀️ Daily brief ready", body, tags="sunrise")
    if pushed:
        # Stamp immediately after a CONFIRMED push (R2-53: not ~100 steps later,
        # and never on a failed push below, so a genuine retry can still fire).
        stamp = _sent_stamp(datetime.now(LOCAL_TZ).strftime("%Y-%m-%d"))
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(now_iso())
        push_msg = "sent"
    elif not planner._config().get("ntfy_topic"):
        push_msg = "off — set a private ntfy_topic in store/config.json"
    else:
        push_msg = "FAILED (topic set — network down or ntfy unreachable)"
    print(f"\n[phone push: {push_msg}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
