#!/usr/bin/env python3
"""LinkedIn draft quality gate — A6, A21-A40 from 500-IDEAS-AGGREGATORS.md section A.

Every draft networking.py stages (comment/reply/connect-note/DM) should pass through
validate_draft() before it's saved to the queue. This is a pure-function lint battery,
no LLM call, no network — cheap enough to run on every draft, every time, and unit-
testable deterministically (see tests/test_li_quality.py).

Covers directly:
  (21) no-emoji enforcement
  (22) 300-char cap
  (23) first-name correctness from profile not email (checked by caller — see
       first_name_from_profile() here; validate_draft() checks the NAME is present
       and non-generic when name-personalized content is passed in)
  (25) banned-phrase lint (love your energy, huge fan, ...)
  (26) one-question rule per DM (at most one '?')
  (27) no links in first touch (first_touch=True forbids URLs in the draft body)
  (39) profanity/negativity guard — applied to the TARGET's content before engaging,
       see screen_target_content() (a separate check: the thing we're commenting ON,
       not the draft itself)
  (40) never-engage list — see is_never_engage()

Covers indirectly / are caller responsibilities documented here (not re-implemented,
because they need context validate_draft() doesn't have):
  (24) their-content reference required for warm DMs -> caller passes has_content_ref
  (28) book-link only after reply -> same first_touch/link-hygiene check as (27)
  (29) job-title mirroring -> a drafting-prompt concern (see PROMPT additions in
       networking.py / li_conveyor.py), not a lint
  (30) draft variant count 2 -> a queueing concern (see li_variants.py)
  (31) reply-draft context (last 3 messages) -> a context-assembly concern
       (see li_thread.py thread_context())
  (32) congrats-trigger drafts -> li_congrats.py (fixture-mode, [E])
  (33) mutual-connection name-drop only when true -> caller must pass real mutuals
  (34) niche value-nugget bank -> li_nuggets.py
  (35)/(36) follow-up ladder / dead-thread closer -> li_conveyor.py
  (37) voice drift vs real sent DMs -> reuses agents/voice_drift.py pattern,
       [E] until enough sent-DM history exists (see li_conveyor.py note)
  (38) length variance -> a batch-level check, see length_variance_ok()

validate_draft() never raises; it returns a verdict dict so callers can decide to
drop, log, or (for tone) request a rewrite. Nothing here sends anything, ever.
"""
from __future__ import annotations

import re
import os
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

MAX_CHARS = 420   # raised 300->420 (2026-07-11): the 4-part comment formula (specific
                  # thank-you -> reaction -> why-it-matters -> open question) runs 250-400
MAX_QUESTIONS = 1

# (25) banned phrases — generic LinkedIn-bot compliment filler that reads as fake.
# Superset of brand-voice.md's banned list (VOICE-SPEC.md's banned WORDS are a
# separate, business-copy-wide list checked by store_lib elsewhere for outbound
# copy; these are specifically the networking/comment-thread tells).
BANNED_PHRASES = [
    "love your energy", "huge fan", "great post", "love this", "amazing post",
    "so inspiring", "this resonates", "totally agree", "well said", "spot on",
    "couldn't agree more", "thanks for sharing", "great insight", "great content",
    "love this post", "such a great point", "this is gold", "game changer",
    "game-changer", "crushing it", "killing it", "let's connect", "would love to connect",
    "excited to connect", "looking forward to connecting", "circle back", "touch base",
    "synergy", "leverage", "unlock", "seamless", "cutting-edge", "elevate",
    "game-changing", "revolutionary", "delve", "in today's world", "i'm excited to",
]

# (40) never-engage: known MLM / pyramid-scheme language patterns + explicit
# competitor names. Competitor names come from business-library if present;
# otherwise this stays keyword-only. Nothing here means "block the person" —
# it means "flag, don't auto-queue," the human still sees it via status/reason.
_MLM_PATTERNS = [
    r"\bbe your own boss\b", r"\bfinancial freedom\b.{0,40}\bteam\b",
    r"\bjoin my team\b", r"\bDM me['’]?\s*(the word|to learn|for details)\b",
    r"\bpassive income opportunity\b", r"\bwork from home\b.{0,30}\bunlimited\b",
    r"\bearn \$?\d+k?\s*(a|per)\s*(week|month)\s*from home\b",
    r"\bno experience (needed|required)\b.{0,40}\b(income|earn|money)\b",
]
_MLM_RE = re.compile("|".join(_MLM_PATTERNS), re.IGNORECASE)

_COMPETITOR_NAMES_CACHE: list[str] | None = None


def _competitor_names() -> list[str]:
    """Optional file business-library/competitors.md with one name per line
    (## headers / blank lines skipped). Missing file = empty list, never an error."""
    global _COMPETITOR_NAMES_CACHE
    if _COMPETITOR_NAMES_CACHE is not None:
        return _COMPETITOR_NAMES_CACHE
    names = []
    p = Path(os.environ.get("BIZLIB") or (ROOT / "business-library")) / "competitors.md"
    try:
        for line in p.read_text().splitlines():
            line = line.strip().lstrip("-").strip()
            if line and not line.startswith("#"):
                names.append(line.lower())
    except OSError:
        pass
    _COMPETITOR_NAMES_CACHE = names
    return names


def is_never_engage(text: str, author: str = "", headline: str = "") -> str | None:
    """(40) Returns a reason string if this looks like MLM/pyramid content or a
    named competitor, else None. Checked against the TARGET's post/profile text,
    not [OWNER]'s draft."""
    blob = f"{text} {headline}".strip()
    if blob and _MLM_RE.search(blob):
        return "mlm-pattern"
    a = (author or "").lower()
    for name in _competitor_names():
        if name and name in a:
            return f"competitor:{name}"
    return None


# Stems, not exact tokens, so "fucking"/"shitty"/"assholes" etc. are also caught
# (matched as substrings against the lowercased text, see screen_target_content).
_PROFANITY_STEMS = (
    "fuck", "shit", "bitch", "asshole", "bastard", "cunt", "dick", "piss",
    "goddamn", "cock", "twat", "wanker", "slut", "whore",
)
_NEGATIVITY_MARKERS = [
    r"\bkill (yourself|myself)\b", r"\bi hate\b", r"\bscam(mer|my)?\b",
    r"\bfuck(ing)? (this|that|you|off)\b", r"\btrash\b.{0,15}\bindustry\b",
]
_NEG_RE = re.compile("|".join(_NEGATIVITY_MARKERS), re.IGNORECASE)


def screen_target_content(text: str) -> dict:
    """(39) Profanity/negativity guard on the TARGET's content, run BEFORE [OWNER]
    engages with it (comment/like/reply). Not a check on [OWNER]'s own draft."""
    t = (text or "")
    low = t.lower()
    hit_profanity = next((s for s in _PROFANITY_STEMS if s in low), None)
    hit_negativity = bool(_NEG_RE.search(t))
    ok = not hit_profanity and not hit_negativity
    reason = ""
    if hit_profanity:
        reason = f"profanity:{hit_profanity}"
    elif hit_negativity:
        reason = "negativity"
    return {"ok": ok, "reason": reason}


_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # symbols/pictographs/emoticons/transport/supplemental
    "\U00002600-\U000027BF"  # misc symbols + dingbats
    "\U0001F1E6-\U0001F1FF"  # regional indicators (flags)
    "\U00002700-\U000027BF"
    "\U0001F900-\U0001F9FF"
    "\U00002B00-\U00002BFF"  # arrows/stars used as decoration
    "\U0000FE0F"             # variation selector-16 (emoji presentation)
    "]",
    flags=re.UNICODE,
)


def has_emoji(text: str) -> bool:
    if not text:
        return False
    if _EMOJI_RE.search(text):
        return True
    # catch anything the range list missed via unicodedata category "So" (symbol, other)
    return any(unicodedata.category(ch) == "So" for ch in text)


_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)


def has_link(text: str) -> bool:
    return bool(_URL_RE.search(text or ""))


# (2026-07-11, [OWNER]'s dad's comment formula) These openers are only bot-filler when they
# stand BARE. "Thanks for sharing." = banned; "Thanks for sharing the story about the
# prospect asking X" = the formula's sincere-specific opener and exactly what we want.
# Soft = allowed when the same sentence continues with >=25 chars of specifics.
SOFT_OPENERS = {"thanks for sharing", "great post"}


def find_banned_phrase(text: str) -> str | None:
    t = (text or "").lower()
    for phrase in BANNED_PHRASES:
        if phrase not in t:
            continue
        if phrase in SOFT_OPENERS:
            # specifics test: substantial content must follow within the same sentence OR
            # the very next one ("Great post. Breaking it down into scripts..." is the
            # formula's sincere form; a bare "Great post." with nothing after is filler)
            after = t.split(phrase, 1)[1]
            window = " ".join(re.split(r"[.!?\n]", after, 2)[:2])
            if len(window.strip()) >= 25:
                continue  # sincere-specific opener, allowed
        return phrase
    return None


def question_count(text: str) -> int:
    return (text or "").count("?")


GENERIC_NAME_TOKENS = {"", "there", "friend", "connection", "sir", "madam", "team"}


def name_looks_generic(name: str) -> bool:
    """(23) Flags placeholder-y names. This is a shape check, not a lookup — the
    caller is responsible for actually sourcing the name from the LinkedIn profile
    (not an email local-part like 'jsmith2019') at sourcing time; see A5/A_dedupe
    in networking.py where `author` is captured directly from the profile scrape."""
    n = (name or "").strip().lower()
    if n in GENERIC_NAME_TOKENS:
        return True
    if re.fullmatch(r"[a-z]+\d{2,}", n.replace(" ", "")):  # e.g. "jsmith2019"
        return True
    return False


def length_variance_ok(drafts: list[str], min_stdev_chars: float = 15.0) -> bool:
    """(38) Batch-level check: a set of drafts that are all nearly the same length
    reads as templated. Only meaningful for len(drafts) >= 4; smaller batches pass
    by default (not enough signal to judge variance)."""
    lens = [len(d or "") for d in drafts if (d or "").strip()]
    if len(lens) < 4:
        return True
    mean = sum(lens) / len(lens)
    var = sum((x - mean) ** 2 for x in lens) / len(lens)
    return (var ** 0.5) >= min_stdev_chars


def validate_draft(
    text: str,
    *,
    kind: str = "comment",
    first_touch: bool = False,
    name: str = "",
    max_chars: int = MAX_CHARS,
    max_questions: int = MAX_QUESTIONS,
) -> dict:
    """The single gate every draft passes through before it's queued.

    kind: comment|reply|connect_note|dm — informational, doesn't change the rules
          (the rules are the same hard voice/safety floor across all LinkedIn touch
          kinds; per-kind context requirements like (24)/(31)/(33) are the CALLER's
          job to satisfy before calling this, since they need data this function
          doesn't have).
    first_touch: True for the FIRST message in a new thread (connect note, first
          DM to a stranger, first reply to someone who's never replied before).
          Forces the no-link rule (27)/(28).

    Returns {"ok": bool, "reasons": [str, ...], "text": str}. "text" echoes the
    input unchanged — this function never rewrites, only judges (rewriting is
    networking.tone_screen()'s job, which already exists and stays as-is).
    Never raises.
    """
    t = (text or "").strip()
    reasons: list[str] = []

    if not t:
        reasons.append("empty")
        return {"ok": False, "reasons": reasons, "text": t}

    if has_emoji(t):
        reasons.append("emoji")

    if len(t) > max_chars:
        reasons.append(f"too_long:{len(t)}>{max_chars}")

    phrase = find_banned_phrase(t)
    if phrase:
        reasons.append(f"banned_phrase:{phrase}")

    qn = question_count(t)
    if qn > max_questions:
        reasons.append(f"too_many_questions:{qn}")

    if first_touch and has_link(t):
        reasons.append("link_in_first_touch")

    if name and name_looks_generic(name):
        reasons.append("generic_name")

    return {"ok": len(reasons) == 0, "reasons": reasons, "text": t}


def validate_batch(items: list[dict], *, first_touch: bool = False) -> list[dict]:
    """Convenience: run validate_draft() over a list of {"draft"/"text": ..., "author": ...}
    and also apply the batch-level length-variance check (38), tagging every item's
    verdict with a shared 'batch_variance_ok' flag so callers can decide whether to
    ask for a rewrite pass on the whole set."""
    texts = [it.get("draft", it.get("text", "")) for it in items]
    variance_ok = length_variance_ok(texts)
    out = []
    for it in items:
        text = it.get("draft", it.get("text", ""))
        v = validate_draft(text, first_touch=first_touch, name=it.get("author", ""))
        v["batch_variance_ok"] = variance_ok
        out.append(v)
    return out


if __name__ == "__main__":
    # Tiny manual smoke check (real coverage lives in tests/test_li_quality.py).
    samples = [
        ("Great post! Love your energy 🔥", True),
        ("Ran into this exact issue last month, curious what stack you're on and if it scaled?", True),
        ("A" * 400, True),
    ]
    for text, _ in samples:
        v = validate_draft(text)
        print(f"{'FAIL' if not v['ok'] else 'ok  '} {v['reasons']} :: {text[:60]}")
