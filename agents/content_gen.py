#!/usr/bin/env python3
"""LinkedIn content engine — keeps an always-full queue of on-voice posts.

Reads content/voice.md (scanned from [OWNER]'s real posts) + business context,
generates a batch of distinct LinkedIn posts in his voice via the Claude CLI
(Max plan), self-scores each, keeps only score>=7, and tops the draft buffer
up to TARGET. Posts land in content/posts.jsonl as status="draft" for review.

Run:  uv run python agents/content_gen.py            # top up the buffer
      uv run python agents/content_gen.py --n 5       # force-generate 5
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "dashboard", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import new_id, now_iso, humanize  # noqa: E402
import planner  # noqa: E402
from store_lib import voice_spec  # noqa: E402
import gen_image  # noqa: E402

POSTS = ROOT / "content" / "posts.jsonl"
VOICE = ROOT / "content" / "voice.md"
BIZ = Path.home() / "Claude" / "business-library"
MAX_PER_RUN = 8          # per-invocation ceiling (one CLI call carries at most this many)
DAILY_NEW_DEFAULT = 6    # fresh posts per day (config content_daily_new)
MAX_FRESH_DEFAULT = 30   # ceiling on live drafts+approved so the review pane stays sane
STALE_DAYS_DEFAULT = 14  # a draft nobody approved in 2 weeks is dead weight -> status "stale"

# 2026-07-11 ([OWNER]: "lots of high value, different and fresh content"): the old logic
# topped a buffer up to 12 and counted UNREVIEWED drafts as "ready", so 59 stale drafts
# from Jun 24 starved generation for 2.5 weeks ("Buffer full. Nothing to generate." daily).
# Now: N fresh posts EVERY day, angle-rotated (least-recently-used first), grounded in
# real material (the 50-objection bank, niche books, pricing tree, this week's real feed
# events), and drafts older than STALE_DAYS auto-archive. Publishing stays his click.
ANGLES = [
    ("objection-column", "Take the EXACT objection + counter given in the MATERIAL block and make it a "
                         "post: the objection as prospects say it, the answer the way [OWNER] says it to "
                         "their face, why it works. Land on the principle."),
    ("teardown-lesson", "One concrete fault [OWNER] keeps finding on local-business/agency websites, why it "
                        "quietly costs money, and what he does instead. Specific and visual, no client names."),
    ("contrarian-take", "Pick one piece of agency/marketing advice everyone repeats, and disagree with it "
                        "from operating experience. Sharp but earned, not edgy for its own sake."),
    ("playbook", "How [OWNER] actually does ONE operational thing (delivery, scoping, revisions, pricing a "
                 "build, onboarding). Steps a reader could copy tomorrow. Trench-level specifics."),
    ("local-biz-truth", "Write to LOCAL BUSINESS owners generally (trades, salons, clinics, shops): one "
                        "hard truth about how their website/marketing actually loses them customers, told "
                        "plainly, and the first move that fixes it. No niche jargon."),
    ("pricing-truth", "One honest principle about pricing web/marketing work from the MATERIAL pricing "
                      "notes (deposits, flat price, never discounting for silence). Opinionated, calm."),
    ("myth-bust", "One belief local businesses or agency owners hold about websites/marketing that's wrong, "
                  "what the reality is, one plain proof point."),
    ("operator-lesson", "Something real from THIS WEEK in the MATERIAL events: what happened (anonymized), "
                        "what it taught, the takeaway. If nothing in MATERIAL fits, a hard-won lesson from "
                        "his agency years."),
    ("before-after", "A rebuild story arc: what the old site/process looked like, what changed, what "
                     "happened after. Anonymized, concrete, no invented numbers."),
    ("question-post", "One sharp question to agency owners about their fulfillment/delivery bottleneck, "
                      "framed with 2-3 sentences of context. Short. The question IS the post."),
    ("white-label-pov", "Behind the scenes of white-label building: what agencies get wrong about "
                        "outsourcing builds, what a good handoff looks like. Their brand, invisible partner."),
    ("niche-rotate", "Write for the niche named in MATERIAL (hvac, salon, mens-health...): one "
                     "industry-specific problem and the fix, in their language."),
]

# Per-angle ART DIRECTION (2026-07-11, [OWNER]: "make sure the images are fresh, edited and
# relevant"): one split-screen template for every post made the feed visually same-y. Each
# angle now has its own visual formula; shared rules live in the prompt (1:1, crisp exact
# text, no logos). Keys must match ANGLES.
ART = {
    "objection-column": "A bold editorial quote-card: the objection in large quotation marks top-left on a "
                        "dark textured background, [OWNER]'s one-line counter below in amber, a thin divider.",
    "teardown-lesson": "A photorealistic over-the-shoulder shot of a laptop showing a flawed local-business "
                       "website, with 3 short red annotation tags pinned to the flaws, one green tag on the fix.",
    "contrarian-take": "A stark typographic poster: the common advice crossed out with a single red stroke, "
                       "the contrarian line beneath it in bold white, generous negative space.",
    "playbook": "A clean flat-lay of a desk with a one-page checklist titled from the post, numbered steps "
                "legible, a pen resting on it, soft daylight, editorial style.",
    "local-biz-truth": "A photorealistic storefront or service-van scene at golden hour with a short bold "
                       "headline overlaid and one small caption strip at the bottom.",
    "pricing-truth": "A minimalist receipt/invoice motif on a dark background: line items legible, one line "
                     "highlighted in amber, the principle as a stamped phrase across the corner.",
    "myth-bust": "A split 'MYTH / REALITY' card, myth side dim with a red label, reality side bright with a "
                 "green label, both statements short and crisply rendered.",
    "operator-lesson": "A candid photorealistic workspace scene (late light, notebook, coffee) with the "
                       "lesson as a short handwritten-style overlay line.",
    "before-after": "A clean side-by-side of the same website before and after: left dated and cluttered "
                    "with a red tag, right modern and clear with a green tag, one headline across the top.",
    "question-post": "A huge single question mark built from small UI elements with the question itself "
                     "rendered as one short bold line beneath, plain background.",
    "white-label-pov": "A photorealistic behind-the-curtain scene: two desks, front one branded 'THE AGENCY' "
                       "presenting a polished site, back one in shadow doing the build, subtle spotlight.",
    "niche-rotate": "A photorealistic scene from the niche's actual workplace with a short bold headline "
                    "overlaid and three tiny problem/fix labels along the bottom.",
}


def load_posts() -> list[dict]:
    if not POSTS.exists():
        return []
    by_id, order = {}, []
    for line in POSTS.read_text().splitlines():
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


# R3#5 (2026-07-14): R2-35's flock only serializes writes so two appends can't
# corrupt each other -- it does NOT stop a writer holding a STALE in-memory copy
# (e.g. regen_image() re-rolling an image, which can take several seconds of real
# network time, or server.py's _update_post() re-approving from a stale dashboard
# read) from appending an OLD status AFTER a push has already moved the SAME post
# to "scheduled"/"posted". Last-write-wins by id means that stale write becomes
# the new truth, silently reverting a scheduled/posted post back to "approved" --
# the next push sweep then sees it as approved again and reposts it to LinkedIn a
# second time. A lock alone can't catch this (the stale copy was read before the
# lock was ever taken); this needs a real CAS, comparing against what's ACTUALLY
# on disk right now, not just serializing the write.
_STATUS_RANK = {"draft": 0, "stale": 0, "approved": 1, "scheduled": 2, "posted": 3}


def save_post(rec):
    from store_lib import _flock
    POSTS.parent.mkdir(parents=True, exist_ok=True)
    with _flock(POSTS):
        new_rank = _STATUS_RANK.get(rec.get("status"))
        if new_rank is not None and rec.get("id"):
            current = next((x for x in load_posts() if x.get("id") == rec["id"]), None)
            cur_rank = _STATUS_RANK.get(current.get("status")) if current else None
            if cur_rank is not None and new_rank < cur_rank:
                print(f"  save_post: refusing to revert {rec['id']} from "
                     f"{current.get('status')!r} back to {rec.get('status')!r} (stale write)")
                return
        with POSTS.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _context() -> str:
    bits = []
    try:
        bits.append(VOICE.read_text())
    except OSError:
        pass
    for n in ("business-profile.md", "offers.md", "icp-and-personas.md"):
        p = BIZ / n
        if p.is_file():
            bits.append(f"[{n}]\n" + p.read_text()[:700])
    return "\n\n".join(bits)


# ---- fresh-material miners (what makes each day's batch DIFFERENT) ----
import re as _re
from datetime import datetime as _dt, timedelta as _td


def _objection_bank() -> list[tuple[int, str]]:
    """Parse the 50-objection playbook into (number, block) pairs for rotation."""
    p = BIZ / "playbooks" / "objections.md"
    try:
        txt = p.read_text()
    except OSError:
        return []
    out = []
    # entries look like: **12. "It's too expensive."**\nSay: ...\nWhy: ...
    for m in _re.finditer(r'\*\*(\d+)\.\s+(".*?")\*\*\n(.*?)(?=\n\*\*\d+\.|\n## |\Z)', txt, _re.S):
        out.append((int(m.group(1)), (m.group(2) + "\n" + m.group(3).strip())[:600]))
    return out


def _pick_objection(posts: list[dict]) -> tuple[int, str] | None:
    """Least-recently-used objection from the bank (tracked via objection_n on past posts)."""
    bank = _objection_bank()
    if not bank:
        return None
    used = [p.get("objection_n") for p in posts if p.get("objection_n")]
    order = {n: used[::-1].index(n) if n in used else 10**6 for n, _ in bank}
    bank.sort(key=lambda b: -order[b[0]])   # never-used (inf) first, then oldest-used
    return bank[0]


def _fresh_events(hours: int = 96, cap: int = 25) -> str:
    """This week's REAL business events from feed.jsonl (titles only). The model is told
    to anonymize; this is what keeps operator-lesson / win posts grounded, never invented."""
    cut = (_dt.now().astimezone() - _td(hours=hours)).isoformat()
    lines = []
    try:
        for ln in (ROOT / "store" / "feed.jsonl").read_text().splitlines():
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if (r.get("ts") or "") >= cut and r.get("title"):
                lines.append(f"- [{r.get('kind', '?')}] {r['title'][:110]}")
    except OSError:
        return ""
    return "\n".join(lines[-cap:])


def _niche_notes() -> str:
    """One rotating niche-book excerpt. MEDSPA IS EXCLUDED — [OWNER], 2026-07-11: "no
    medspas". Do not re-add it to content, targeting, or examples."""
    nb = BIZ / "sops" / "niche-books"
    others = sorted(x for x in nb.glob("*.md") if x.stem != "medspa")
    if not others:
        return ""
    pick = others[_dt.now().timetuple().tm_yday % len(others)]  # rotate daily by calendar
    try:
        return f"[{pick.stem}]\n" + pick.read_text()[:800]
    except OSError:
        return ""


def _pricing_notes() -> str:
    try:
        return (BIZ / "playbooks" / "pricing-tree.md").read_text()[:800]
    except OSError:
        return ""


def _pick_angles(posts: list[dict], n: int) -> list[tuple[str, str]]:
    """The n least-recently-used angles (angle field on past posts), so consecutive days
    never repeat the same shapes. Unknown/legacy posts (no angle) don't skew the pick."""
    recent = [p.get("angle") for p in posts[-40:] if p.get("angle")]
    def last_used(key):
        return recent[::-1].index(key) if key in recent else 10**6
    ranked = sorted(ANGLES, key=lambda a: -last_used(a[0]))
    return ranked[:n]


PROMPT = """You are [OWNER]'s LinkedIn ghostwriter. Write %d DISTINCT LinkedIn posts in his EXACT voice.

VOICE, RULES, PILLARS, and a real example:
%s

ASSIGNED ANGLES (one per post, in order — follow the assignment, this is what keeps the feed varied):
%s

FRESH MATERIAL (real, from his actual week — ground posts in THIS, never invent events, numbers, or clients):
%s

Requirements for EACH post:
- 80–200 words. Plain text with real line breaks between thoughts (use \\n), generous white space.
- Open with a contrarian/insight hook line.
- One clear idea, built with concrete specifics, land on a calm takeaway.
- NO hashtags, NO emojis, NO exclamation points, NO "thrilled to announce", no AI clichés ("in today's fast-paced world", "let's dive in", "game-changer").
- ABSOLUTELY NO em-dashes (—) or en-dashes (–). Use commas, periods, or just shorter sentences. This is non-negotiable.
- Sound like a real person talking, not polished marketing copy. Contractions, plain words, a little rough is good.
- Use first-person opinion freely and take a clear stance: "In my experience", "I've found", "What I've seen", "My take is". [OWNER] ran an agency, so he speaks from the trenches.
- ANONYMIZE everything from MATERIAL: never a client, prospect, or contact name ("a med spa owner", "an agency in Texas"). No personal-life details. Real events only from MATERIAL; if an angle needs a win and MATERIAL has none, write the lesson without claiming the win.
- Score each 1-10 for how on-voice + human it reads. Be a harsh judge; most should be 6-8.
- Include the assigned angle key as "angle" in each object.
- image_prompt: ONE detailed prompt for an AI image model, following THIS POST'S "IMAGE DIRECTION" from its assignment (each angle has its own visual formula — this is what keeps the feed visually fresh instead of twelve copies of one template). Shared rules for every image: 1:1 square; editorial, high-contrast, premium; any words in the image SPECIFIED EXACTLY (headline max 6 words, labels 1-3 words each) and drawn from THIS post; instruct the model to render all text crisply and spelled exactly as written; no logos, no watermarks, no fake brand names, no real people's names.

Return ONLY a JSON array, nothing else:
[{"topic":"pillar name","angle":"assigned-angle-key","hook":"the first line","text":"full post with \\n line breaks","score":8,"image_prompt":"a 1:1 square photorealistic cinematic split-screen ... HEADLINE: '...' left labels: ... right labels: ..."}]"""


PW = Path.home() / "Claude" / "playwright-project"
IMGDIR = ROOT / "content" / "images"


def make_card(rec):
    """Make the post image. Tier 1 = AI render per the post's angle art direction (needs
    OpenAI key) + a VISION QUALITY CHECK (img_check) with one re-roll; fallback = clean
    branded text card via Playwright (free, text always crisp)."""
    import img_check
    IMGDIR.mkdir(parents=True, exist_ok=True)
    out = str((IMGDIR / (rec["id"] + ".png")).resolve())
    # Tier 1: real AI image, quality-gated (garbled text / artifacts / irrelevant = re-roll
    # once, then fall through to the deterministic card rather than ship a bad image)
    if rec.get("image_prompt") and gen_image.have_key():
        for attempt in (1, 2):
            ok, msg = gen_image.generate_image(rec["image_prompt"], out)
            if not (ok and Path(out).exists()):
                rec["image_err"] = msg
                break
            v = img_check.check_image(out, rec.get("hook", ""), rec.get("text", ""))
            rec["img_check"] = {**v, "attempt": attempt}
            if v.get("ok"):
                rec["image"] = "/content-img/" + rec["id"] + ".png"
                rec["image_kind"] = "ai"
                return
        if rec.get("img_check") and not rec["img_check"].get("ok"):
            rec["image_err"] = "AI image failed quality check twice: " + (rec["img_check"].get("why") or "")
    # Fallback: branded gradient text-card
    try:
        subprocess.run(["node", "make_card.mjs",
                        json.dumps({"hook": rec["hook"], "out": out, "tag": "OPERATOR NOTES"})],
                       cwd=str(PW), capture_output=True, text=True, timeout=60)
        if Path(out).exists():
            rec["image"] = "/content-img/" + rec["id"] + ".png"
            rec["image_kind"] = "card"
    except Exception:
        pass


def _config() -> dict:
    try:
        return json.loads((ROOT / "store" / "config.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _fallback_prompt(text: str) -> str:
    hook = text.split("\n")[0][:90]
    return ("A 1:1 square, photorealistic, cinematic split-screen comparison. LEFT = the painful "
            "old way (a stressed agency owner in a dim, cluttered office, subtle red accent, a red X). "
            "RIGHT = the calm better way (a composed founder in a bright, clean modern office, subtle "
            "green accent, a green check). Small circular VS badge dead center. Bold uppercase headline "
            f'across the top summarizing "{hook}" with ONE key word emphasized in amber. Three short red '
            "problem-labels bottom-left and three short green outcome-labels bottom-right, drawn from the "
            "idea. High contrast, dark-left vs bright-right lighting, editorial tech aesthetic, no logos, "
            "no watermark. Render all text crisply and spelled exactly.")


def regen_image(pid: str) -> dict:
    """Re-roll a post's AI image (a fresh variation). Returns {ok, image|error}."""
    p = next((x for x in load_posts() if x.get("id") == pid), None)
    if not p:
        return {"ok": False, "error": "post not found"}
    if not gen_image.have_key():
        return {"ok": False, "error": "no OpenAI key set in store/config.json"}
    if not p.get("image_prompt"):
        p["image_prompt"] = _fallback_prompt(p.get("text", ""))
    IMGDIR.mkdir(parents=True, exist_ok=True)
    out = str((IMGDIR / (pid + ".png")).resolve())
    ok, msg = gen_image.generate_image(p["image_prompt"], out)
    if ok and Path(out).exists():
        import img_check
        v = img_check.check_image(out, p.get("hook", ""), p.get("text", ""))
        p["img_check"] = v
        p["image"] = "/content-img/" + pid + ".png"
        p["image_kind"] = "ai"
        save_post(p)
        if not v.get("ok"):
            return {"ok": True, "image": p["image"], "quality": "FAILED",
                    "why": v.get("why", ""), "note": "re-roll again or it will be held at push"}
        return {"ok": True, "image": p["image"], "quality": "passed"}
    return {"ok": False, "error": msg}


def generate(n: int) -> list[dict]:
    aa = int(_config().get("auto_approve_min", 0) or 0)
    history = load_posts()
    angles = _pick_angles(history, n)
    objection = _pick_objection(history)
    # per-post assignments: the objection-column angle carries its exact objection block
    lines = []
    for i, (key, brief) in enumerate(angles, 1):
        extra = ""
        if key == "objection-column" and objection:
            extra = f"\n  Use objection #{objection[0]} verbatim from MATERIAL."
        art = ART.get(key, "")
        lines.append(f"POST {i} -> {key}: {brief}"
                     + (f"\n  IMAGE DIRECTION: {art}" if art else "") + extra)
    assignments = "\n".join(lines)
    niche = _niche_notes()
    material = "\n\n".join(x for x in (
        ("THIS WEEK'S REAL EVENTS (anonymize):\n" + _fresh_events()) if _fresh_events() else "",
        (f"OBJECTION #{objection[0]} (for objection-column):\n{objection[1]}") if objection else "",
        ("NICHE NOTES:\n" + niche) if niche else "",
        ("PRICING PRINCIPLES:\n" + _pricing_notes()) if _pricing_notes() else "",
    ) if x)
    out = planner._cli_json("HARD VOICE SPEC (overrides everything below on conflict):\n"
                            + voice_spec(1800) + "\n\n"
                            + PROMPT % (n, _context(), assignments, material),
                            timeout=200, feature="content")
    posts = []
    known = {k for k, _ in ANGLES}
    if isinstance(out, list):
        for i, p in enumerate(out):
            txt = humanize((p.get("text") or "").strip())
            sc = p.get("score") if isinstance(p.get("score"), (int, float)) else 6
            if txt and sc >= 7:
                angle = p.get("angle") if p.get("angle") in known else (
                    angles[i][0] if i < len(angles) else "general")
                rec = {
                    "id": new_id(txt[:40]),
                    "text": txt, "hook": humanize((p.get("hook") or txt.split("\n")[0]))[:120],
                    "topic": p.get("topic", "general"), "score": sc,
                    "angle": angle,
                    "image_prompt": (p.get("image_prompt") or "").strip(),
                    "status": "approved" if (aa and sc >= aa) else "draft",
                    "created": now_iso(),
                }
                if angle == "objection-column" and objection:
                    rec["objection_n"] = objection[0]
                make_card(rec)
                posts.append(rec)
    return posts


def archive_stale(posts: list[dict], stale_days: int) -> int:
    """Drafts nobody approved in stale_days stop counting as inventory (status 'stale',
    appended last-write-wins; nothing is deleted). Approved/scheduled are HIS decisions
    and never auto-archived."""
    cutoff = (_dt.now().astimezone() - _td(days=stale_days)).isoformat()
    n = 0
    for p in posts:
        if p.get("status") == "draft" and (p.get("created") or "") < cutoff:
            save_post({**p, "status": "stale", "staled": now_iso()})
            n += 1
    return n


def main() -> int:
    n_force = None
    if "--n" in sys.argv:
        try:
            n_force = int(sys.argv[sys.argv.index("--n") + 1])
        except (ValueError, IndexError):
            pass
    cfg = _config()
    # R2-31 (2026-07-13): `cfg.get(...) or DEFAULT` treats an explicit 0 the same as
    # "unset" (0 is falsy), so content_daily_new=0 fell through to DAILY_NEW_DEFAULT (6)
    # -- pausing daily generation via config was impossible. Explicit None-checks so 0
    # means 0. Applied to the sibling knobs too (content_max_fresh/content_stale_days
    # share the identical pattern one/two lines below).
    _daily_new = cfg.get("content_daily_new")
    daily_new = int(_daily_new) if _daily_new is not None else DAILY_NEW_DEFAULT
    _max_fresh = cfg.get("content_max_fresh")
    max_fresh = int(_max_fresh) if _max_fresh is not None else MAX_FRESH_DEFAULT
    _stale_days = cfg.get("content_stale_days")
    stale_days = int(_stale_days) if _stale_days is not None else STALE_DAYS_DEFAULT

    posts = load_posts()
    staled = archive_stale(posts, stale_days)
    if staled:
        print(f"Archived {staled} stale draft(s) (untouched for {stale_days}+ days).")
        posts = load_posts()

    today = now_iso()[:10]
    made_today = sum(1 for p in posts if (p.get("created") or "")[:10] == today)
    live = [p for p in posts if p["status"] in ("draft", "approved")]
    if n_force:
        need = n_force
    else:
        # daily-additive: N fresh per day (idempotent across morning self-heal re-runs via
        # made_today), capped by the live-inventory ceiling so the review pane stays sane
        need = min(daily_new - made_today, max_fresh - len(live))
    need = min(need, MAX_PER_RUN)
    if need <= 0:
        why = f"{made_today}/{daily_new} made today" if made_today >= daily_new else f"{len(live)} live ≥ cap {max_fresh}"
        print(f"Nothing to generate ({why}).")
        return 0
    print(f"Generating {need} posts…")
    fresh = generate(need)
    for r in fresh:
        save_post(r)
        print(f"  + [{r['score']}] ({r.get('angle', '?')}) {r['hook'][:60]}")
    planner.feed_add("content", f"Generated {len(fresh)} LinkedIn draft(s) "
                                f"({', '.join(sorted({r.get('angle', '?') for r in fresh}))})")
    print(f"Added {len(fresh)}. Live inventory ~{len(live)+len(fresh)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
