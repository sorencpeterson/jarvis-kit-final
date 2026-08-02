#!/usr/bin/env python3
"""LinkedIn networking engine — an approval-gated daily engagement queue.

Design principle: sourcing + execution run through [OWNER]'s REAL logged-in Chrome
(DOM, human-paced, capped) — never a headless bot — to keep his account clean.
This module owns the queue store + the AI drafting (on-voice comments/replies via
the Max-plan CLI, free). Items land as status="pending" for one-tap approval.

Queue item schema (store/network.jsonl):
  {id, kind: comment|connect|reply|like|dm, author, target, url, draft, status:
   pending|approved|done|skipped, created}

dm items are staged by agents/li_conveyor.py (accepted-connection ladder) and
agents/li_engager_dm.py (content-engager / agency-fit openers, 2026-07-15) and
release through the SAME approved_to_run() gate as everything else.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

# Don't pile multiple comments/likes onto ONE person's feed in a short window,
# that burst is the #1 "this is a bot" tell. Cap per author across PENDING items.
MAX_PER_AUTHOR = 2

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import new_id, now_iso, humanize  # noqa: E402
import planner  # noqa: E402
# the LinkedIn quality layer (li_* modules) — wired in so drafts pass the gate and
# targets get scored/deduped. Graceful: if any import fails, networking still runs.
try:
    import li_quality  # noqa: E402
    import li_scoring  # noqa: E402
    import li_history  # noqa: E402
except Exception:  # noqa: BLE001
    li_quality = li_scoring = li_history = None

QUEUE = ROOT / "store" / "network.jsonl"
VOICE = ROOT / "content" / "voice.md"


def load_queue() -> list[dict]:
    if not QUEUE.exists():
        return []
    by_id, order = {}, []
    for line in QUEUE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("id"):
            if r["id"] not in by_id:
                order.append(r["id"])
            by_id[r["id"]] = r
    return [by_id[i] for i in order]


def save_item(rec: dict):
    # flock: the 6PM engage task, the poller, and dashboard routes all write this file;
    # janitor's compact holds the same lock (2026-07-06 audit).
    from store_lib import _flock
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    with _flock(QUEUE), QUEUE.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _voice() -> str:
    try:
        return VOICE.read_text()[:1500]
    except OSError:
        return ""


COMMENT_PROMPT = """You are [OWNER], commenting on other people's LinkedIn posts in his exact voice.
Voice notes:
%s

STRUCTURE (the 4-part comment formula from [OWNER]'s father, a veteran copywriter, 2026-07-11;
follow it for EVERY comment):
1. OPEN with a sincere thank-you or compliment tied to something SPECIFIC from their post:
   quote a phrase, name the story, point at the exact idea that stood out. Never a bare
   compliment; the specificity in the same breath is what makes it sincere.
2. A quick PERSONAL reaction or observation, one or two sentences, from [OWNER]'s real agency
   reps ("I've seen agencies...", "I've had clients...", "ran into this when...").
3. ONE short line on why the insight matters, for him or for others reading the thread.
4. END with a curious, open-ended question the author will actually want to answer, about
   their experience or how they do it. Exactly ONE question, and it goes LAST.

Tone: warm, conversational, peer-to-peer. Punchy sentences, no fluff. Slightly personal but
concise, like a thoughtful remark dropped in a busy LinkedIn thread.

EXAMPLES of the exact shape (copy the SHAPE, never the content):
- Really liked the line, "The problem isn't the offer. It's the fulfillment." I've seen agencies close great clients and then get buried trying to deliver everything themselves. That one sentence explains a lot. At what point do you think most agency owners realize they need help?
- This hit home. A lot of people spend all their time figuring out how to get more clients, then realize delivery becomes the real challenge. Good systems make growth a lot less stressful. What's the first thing you'd fix inside an agency that's starting to feel overloaded?
- Great post. Breaking it down into scripts, videos, carousels, and editing really shows how much work happens after the sale. Clients usually remember the delivery more than the pitch. Which part of the fulfillment process do you see agencies underestimate the most?

VARY the openers across the set ("Thanks for sharing the story about...", "Really liked the
line...", "I appreciated your point about...", "This hit home.", "I liked the way you
explained...") and vary total length between roughly 35 and 70 words, so a batch never reads
templated. A compliment opener must ALWAYS carry a specific detail in the same sentence or
the one right after it.

TONE (critical, this posts publicly under [OWNER]'s name): always respectful and additive. You
are a friendly peer adding to the conversation, NEVER correcting them, lecturing, talking
down, being passive-aggressive or snarky, calling their point wrong, or undercutting the
poster's own offer or business. If you see it differently, add your own angle without putting
theirs down. Assume the poster is smart and means well.

Hard rules: no emojis, no hashtags, no BARE compliment-filler (a compliment must reference a
specific detail), no pitching, NO em-dashes or en-dashes (commas or periods only). Exactly one
question mark per comment, at the end. If a post is not genuinely worth a real comment from
[OWNER], set comment to "" and skip it, never force one.

Posts:
%s

Return ONLY a JSON array: [{"i":0,"comment":"..."}]"""


REPLY_PROMPT = """You are [OWNER] replying to comments/DMs on LinkedIn in his voice.
Voice notes:
%s

For EACH item below write a SHORT, warm-but-direct reply (1-3 sentences) that keeps the conversation going. Acknowledge the SPECIFIC thing they said (their phrase or point, not a generic nod), add a quick personal take when it fits ("In my experience", "Yeah, I've found"), and when it feels natural, end with a short open question back to them (at most one, never forced on every reply). No emojis, no fluff. ABSOLUTELY NO em-dashes or en-dashes (use commas or periods). Sound like a real person. Always warm and respectful, never snarky, dismissive, or passive-aggressive, these post publicly under [OWNER]'s name.

Items:
%s

Return ONLY a JSON array: [{"i":0,"reply":"..."}]"""


def _draft(prompt_tmpl: str, rows: list[dict], key: str) -> dict:
    payload = json.dumps([{"i": i, "author": r.get("author", ""),
                           "text": (r.get("text", "") or "")[:1500]} for i, r in enumerate(rows)])
    out = planner._cli_json(prompt_tmpl % (_voice(), payload), timeout=180, feature="networking")
    if isinstance(out, list):
        return {r["i"]: humanize((r.get(key, "") or "").strip())
                for r in out if isinstance(r, dict) and "i" in r}
    return {}


def _seen_urls() -> set:
    return {(x.get("kind"), x.get("url")) for x in load_queue() if x.get("url")}


TONE_PROMPT = """These are comments/replies [OWNER] will post PUBLICLY under his name on other people's LinkedIn posts. Screen each for tone. Flag any that could read as rude, condescending, lecturing, passive-aggressive, snarky, dismissive, know-it-all, argumentative, or that undercut the poster's own point, offer, or business. [OWNER] wants to come across as a sharp, friendly peer who adds value, never someone correcting or talking down to people.

For each: if it is respectful and additive, mark ok=true. If not, mark ok=false and give a "fixed" rewrite that keeps the same insight but warmer and not preachy (no em-dashes). If it cannot be saved, set fixed to "".

Items:
%s

Return ONLY a JSON array: [{"i":0,"ok":true,"fixed":""}]"""


def tone_screen(texts: list[str]) -> list[str | None]:
    """Return cleaned texts: kept as-is if respectful, softened if risky, or None to drop."""
    if not texts:
        return []
    payload = json.dumps([{"i": i, "text": t} for i, t in enumerate(texts)])
    out = planner._cli_json(TONE_PROMPT % payload, timeout=120, feature="tone_screen")
    verdicts = {r["i"]: r for r in out if isinstance(r, dict) and "i" in r} if isinstance(out, list) else {}
    cleaned = []
    for i, t in enumerate(texts):
        v = verdicts.get(i)
        if not v or v.get("ok"):
            cleaned.append(t)  # default keep if respectful or no verdict
        else:
            fix = humanize((v.get("fixed") or "").strip())
            cleaned.append(fix or None)  # softened, or None = drop it
    return cleaned


def _akey(s: str) -> str:
    return (s or "").strip().lower()


def _pending_by_author(kind: str) -> Counter:
    """How many still-PENDING items of this kind each author already has queued."""
    return Counter(_akey(x.get("author")) for x in load_queue()
                   if x.get("kind") == kind and x.get("status") == "pending" and x.get("author"))


def queue_comments(posts: list[dict]) -> list[dict]:
    """posts: [{author, text, url}] -> draft on-voice comments + enqueue.
    Caps comments per author (MAX_PER_AUTHOR) so we never burst one person's feed."""
    drafts = _draft(COMMENT_PROMPT, posts, "comment")
    # tone safety screen, these post publicly under [OWNER]'s name
    idxs = [i for i in drafts if drafts.get(i)]
    cleaned = tone_screen([drafts[i] for i in idxs])
    for j, i in enumerate(idxs):
        drafts[i] = cleaned[j] or ""
    seen, added = _seen_urls(), []
    by_author = _pending_by_author("comment")
    for i, p in enumerate(posts):
        c = (drafts.get(i, "") or "").strip()
        if not c or ("comment", p.get("url")) in seen:
            continue
        if li_quality:
            v = li_quality.validate_draft(c, kind="comment", name=p.get("author", ""))
            if not v.get("ok"):
                continue  # failed the voice/safety gate (emoji, banned phrase, too long...)
            ne = li_quality.is_never_engage(p.get("text", ""), p.get("author", ""))
            if ne:
                continue  # MLM/competitor content — do not engage
        a = _akey(p.get("author"))
        if by_author[a] >= MAX_PER_AUTHOR:
            continue  # already enough queued for this person, diversify
        by_author[a] += 1
        rec = {"id": new_id("cm_" + (p.get("url") or p.get("author", "")) + (p.get("text", "") or "")[:30]),
               "kind": "comment",
               "author": p.get("author", ""), "target": (p.get("text", "") or "")[:240],
               "url": p.get("url", ""), "draft": c, "status": "pending", "created": now_iso()}
        save_item(rec); added.append(rec)
    return added


def queue_replies(items: list[dict]) -> list[dict]:
    """items: [{author, text, url}] -> draft replies to comments/DMs + enqueue."""
    drafts = _draft(REPLY_PROMPT, items, "reply")
    idxs = [i for i in drafts if drafts.get(i)]
    cleaned = tone_screen([drafts[i] for i in idxs])
    for j, i in enumerate(idxs):
        drafts[i] = cleaned[j] or ""
    seen, added = _seen_urls(), []
    for i, it in enumerate(items):
        r = (drafts.get(i, "") or "").strip()
        if not r or ("reply", it.get("url")) in seen:
            continue
        if li_quality and not li_quality.validate_draft(r, kind="reply", name=it.get("author", "")).get("ok"):
            continue
        rec = {"id": new_id("rp_" + (it.get("url") or it.get("author", "")) + (it.get("text", "") or "")[:30]),
               "kind": "reply",
               "author": it.get("author", ""), "target": (it.get("text", "") or "")[:240],
               "url": it.get("url", ""), "draft": r, "status": "pending", "created": now_iso()}
        save_item(rec); added.append(rec)
    return added


def queue_connections(people: list[dict]) -> list[dict]:
    """people: [{name, headline, url}] -> connect items (noteless, per [OWNER]).
    Now scored (ICP fit + recency + geo) and filtered against full history + company
    cooldowns via the li_* quality layer, so we source better targets, not just more."""
    if li_history:
        try:
            people = li_history.filter_unattempted(people)
            people = li_history.filter_cooldown_companies(people)
        except Exception:  # noqa: BLE001
            pass
    if li_scoring:
        try:
            for pp in people:
                pp["_score"] = (li_scoring.score_target(pp) or {}).get("score", 0)
            people = sorted(people, key=lambda x: -(x.get("_score") or 0))
        except Exception:  # noqa: BLE001
            pass
    seen, added = _seen_urls(), []
    for pp in people:
        if ("connect", pp.get("url")) in seen:
            continue
        if li_quality and li_quality.is_never_engage("", pp.get("name", ""), pp.get("headline", "")):
            continue  # MLM/competitor headline — skip
        rec = {"id": new_id("cn_" + (pp.get("url") or pp.get("name", ""))), "kind": "connect",
               "author": pp.get("name", ""), "target": pp.get("headline", ""),
               "url": pp.get("url", ""), "draft": "", "status": "pending", "created": now_iso()}
        save_item(rec); added.append(rec)
    return added


def queue_likes(posts: list[dict]) -> list[dict]:
    """posts: [{author, text, url}] -> like items (no draft). Capped per author too."""
    seen, added = _seen_urls(), []
    by_author = _pending_by_author("like")
    for p in posts:
        if ("like", p.get("url")) in seen:
            continue
        a = _akey(p.get("author"))
        if by_author[a] >= MAX_PER_AUTHOR:
            continue
        by_author[a] += 1
        rec = {"id": new_id("lk_" + (p.get("url") or p.get("author", ""))), "kind": "like",
               "author": p.get("author", ""), "target": (p.get("text", "") or "")[:200],
               "url": p.get("url", ""), "draft": "", "status": "pending", "created": now_iso()}
        save_item(rec); added.append(rec)
    return added


def set_status(item_id: str, status: str) -> dict | None:
    # whole read-modify-append under the lock, matching jobs.set_status (D3 audit): two
    # concurrent status writes can't interleave stale reads on the networking queue.
    from store_lib import _flock
    with _flock(QUEUE):
        rec = next((x for x in load_queue() if x.get("id") == item_id), None)
        if not rec:
            return None
        rec["status"] = status
        if status == "done":
            rec["acted_at"] = now_iso()  # stamp when the action actually fired (for daily caps)
        QUEUE.parent.mkdir(parents=True, exist_ok=True)
        with QUEUE.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec


# ---- Daily / weekly safety caps (enforced at execution across all runs) ----
def _net_caps() -> tuple[dict, dict]:
    n = planner._config().get("network", {}) if hasattr(planner, "_config") else {}
    # dm default 8/day (2026-07-15): outbound 1:1 messages are the most account-risk-shaped
    # action here; keep the ceiling low. NOTE the cap==0 convention below means "uncapped"
    # (reply ships 0 on purpose), so the dm default is MERGED UNDER any configured dict:
    # a config.json that predates the dm kind (has network.daily but no "dm" key) must get
    # the capped default, not fall through to 0 = unlimited. Uncapping dm now takes an
    # explicit "dm": 0 in config, never a missing key.
    daily = {"connect": 15, "comment": 10, "like": 25, "reply": 0, "dm": 8}
    daily.update(n.get("daily", {}) or {})
    return daily, n.get("weekly", {"connect": 100})


def _acted_date(x: dict) -> str:
    return (x.get("acted_at") or x.get("created") or "")[:10]


def usage_today() -> Counter:
    """How many of each kind were actually POSTED today (status done, acted today)."""
    today = now_iso()[:10]
    # RUNNING items count against today's caps too (2026-07-11 autopsy: two
    # approved_to_run calls 18s apart each saw full caps because only 'done' counted,
    # claiming 30 connects in one evening, 2x the cap -- a LinkedIn-safety risk).
    return Counter(x["kind"] for x in load_queue()
                   if (x.get("status") == "done" and _acted_date(x) == today)
                   or (x.get("status") == "running" and (x.get("claimed_at") or "")[:10] == today))


def _connects_last_7d() -> int:
    import datetime
    cutoff = (datetime.date.fromisoformat(now_iso()[:10]) - datetime.timedelta(days=6)).isoformat()
    # R2-32: count 'running' claims too (like usage_today() already does for the daily
    # caps), not just 'done'. Otherwise two overlapping approved_to_run() pulls each see
    # room under the weekly cap (only 'done' counts against it) and both claim connects,
    # so the weekly cap can be overshot (105 vs a cap of 100 in the audit's example).
    return sum(1 for x in load_queue()
               if x.get("kind") == "connect" and (
                   (x.get("status") == "done" and _acted_date(x) >= cutoff)
                   or (x.get("status") == "running" and (x.get("claimed_at") or "")[:10] >= cutoff)
               ))


def allowance() -> dict:
    """Remaining actions allowed RIGHT NOW per kind, after today's usage + weekly cap."""
    daily, weekly = _net_caps()
    used = usage_today()
    out = {}
    for kind in ("connect", "comment", "like", "reply", "dm"):
        cap = int(daily.get(kind, 0) or 0)
        out[kind] = 10 ** 6 if cap == 0 else max(0, cap - used.get(kind, 0))
    wk = int(weekly.get("connect", 0) or 0)
    if wk:
        out["connect"] = min(out["connect"], max(0, wk - _connects_last_7d()))
    return out


def approved_to_run() -> list[dict]:
    """Approved items to execute now, trimmed to today's remaining allowance, low-risk first.

    CLAIMS what it returns: items flip to status 'running' under the file lock, so two
    concurrent executors (the 6PM engage task + a net_run click landing together) can
    never both act on the same item — a double LinkedIn comment/connect is visible to
    the outside world (2026-07-06 audit H5). A crashed executor's 'running' items revert
    to 'pending' after 2h (R2-29, 2026-07-13 — see the comment below for why 'pending'
    and not 'approved').

    Also composes li_budget's hours/weekend/daily-budget guard (R2-33, 2026-07-13): that
    guard used to be an opt-in wrapper ("releasable = li_budget.gate(approved_to_run())")
    that nothing actually called — the one real executor (the operator brief) invoked
    this function directly, so weekend_pause/hours_window/daily_action_budget were
    silently bypassed. Baking it in here means every caller gets it, not just ones that
    remember to compose it."""
    from datetime import datetime, timedelta
    from store_lib import _flock
    with _flock(QUEUE):
        q = load_queue()
        # revert stale claims from a crashed/killed executor
        cutoff = (datetime.now().astimezone() - timedelta(hours=2)).isoformat()
        stale = [x for x in q if x.get("status") == "running" and (x.get("claimed_at") or "") < cutoff]
        # R2-29 (2026-07-13): a claim can go stale two ways — the executor crashed BEFORE
        # acting (safe to retry) or it crashed/lost the write AFTER acting but before
        # recording 'done' (retrying would post the same comment/connect twice). This
        # function can't tell which happened, so it no longer feeds a stale claim straight
        # back into the auto-run 'approved' pool; it drops to 'pending' so a human takes a
        # fresh look before it can run again. (It used to write back as APPROVED, which
        # also silently double-inserted the record: appr below was built as
        # `[x for x in q if x.get("status") == "approved"] + stale`, but x here is the
        # SAME dict object as its entry in q — the mutation two lines up already flipped
        # it to "approved" in q too, so the q-filter caught it once and "+ stale" added it
        # again, meaning ONE stale item could be claimed 'running' and returned TWICE from
        # a single call, i.e. executed twice. 'pending' is excluded from the
        # approved-only filter below, so that double-insert path no longer exists either.)
        for x in stale:
            x["status"] = "pending"
            x["reverted_at"] = now_iso()
        if stale:
            with QUEUE.open("a") as _f:
                for x in stale:
                    _f.write(json.dumps(x, ensure_ascii=False) + "\n")
        allow = allowance()
        appr = [x for x in q if x.get("status") == "approved"]
        out = []
        for kind in ("like", "connect", "reply", "dm", "comment"):
            out += [x for x in appr if x.get("kind") == kind][:allow.get(kind, 0)]
        try:
            import li_budget
            out = li_budget.gate(out)  # R2-33: hours window + weekend pause + daily budget
        except Exception:  # noqa: BLE001
            # R3#7: this used to `pass` on any gate error, which left `out` as whatever
            # allowance-only trimming had already produced -- i.e. an unevaluatable
            # safety gate silently released the FULL allowance outside hours/weekends/
            # over budget. Fail CLOSED: an error means "can't confirm it's safe to post
            # right now", so nothing releases this round rather than everything does.
            out = []
        QUEUE.parent.mkdir(parents=True, exist_ok=True)
        with QUEUE.open("a") as f:
            for x in out:
                x["status"] = "running"
                x["claimed_at"] = now_iso()
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
        return out


def edit_draft(item_id: str, text: str) -> dict | None:
    rec = next((x for x in load_queue() if x.get("id") == item_id), None)
    if not rec:
        return None
    rec["draft"] = text
    save_item(rec)
    return rec
