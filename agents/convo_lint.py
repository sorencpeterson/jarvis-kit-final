#!/usr/bin/env python3
"""Draft quality gates for reply_watch.py (C167-169, C178-179, C202-204, C217, C219).

Every function here is pure: text in, a verdict/annotation out, no file I/O, no LLM
calls, no store access except the one place price-consistency needs the pricing
source of truth (proposal_factory.PRICING, imported lazily so a missing/broken
proposal_factory never breaks the lint import itself).

reply_watch.py runs these as a gate AFTER the LLM drafts a reply and BEFORE it's
queued: a draft that fails a hard gate either gets a mechanical fix applied (where
one is safe and unambiguous, e.g. stripping a duplicate CTA link) or the draft is
held back with the failure reason attached to the record for [OWNER] to see, never
silently sent through with the underlying problem hidden.

C167 objection-sequence detection lives in convo_context.py (it needs history across
turns, not just the current draft text) — this file only has the drafting-time gates
that operate on one draft in isolation.
"""
from __future__ import annotations

import re
import owner

BOOK_URL = f"{owner.get('site', 'example.com')}/book"
# owner site is escaped and injected at import: a raw [TOKEN] here silently became a
# regex CHARACTER CLASS, matching any single letter of the token name.
_URL_RE = re.compile(r'https?://\S+|(?<!\w)' + re.escape(owner.get("site", "example.com")) + r'/\S+',
                     re.IGNORECASE)
_QUESTION_RE = re.compile(r'[^.!?]*\?')

# C217: profanity/harassment aimed AT [OWNER] in the inbound message. Deliberately narrow
# (slurs + explicit threats + sexual harassment terms) — the goal is "flag for his eyes
# only, never auto-draft a reply to it," not a general profanity filter that would
# false-positive on someone just saying "this is bullshit expensive."
_HARASSMENT_MARKERS = (
    "fuck you", "fuck off", "go to hell", "kill yourself", "kys",
    "i'll find you", "i will find you", "come to your", "i know where you live",
    "you people are", "your kind", "scam artist", "i'm reporting you",
    "i'm calling the police", "lawsuit", "sue you", "sue your",
)
_SEXUAL_HARASSMENT_MARKERS = ("send nudes", "what are you wearing", "sexy", "hot mama", "hey baby")


def check_no_double_question(draft: str) -> tuple[bool, str]:
    """C202: a draft should ask ONE question at most. Two+ questions in one text
    message reads like an interrogation and studies on reply rate agree: one ask
    per message gets answered, two makes people pick (or answer neither).
    Returns (ok, detail)."""
    n = draft.count("?")
    if n <= 1:
        return True, ""
    return False, f"{n} question marks in one draft (max 1)"


def check_price_consistency(draft: str, expected_tier: str | None = None) -> tuple[bool, str]:
    """C203: if a draft states a dollar figure, it must match a real SKU price from
    proposal_factory.PRICING (the single pricing source of truth per pricing-tree.md).
    A draft inventing a number the pricing tree doesn't have is a real risk (quoting
    the wrong price to a prospect), so this is a hard gate, not a suggestion.

    expected_tier: if the caller knows which SKU this contact is quoted at (e.g. from
    a live proposal record), any $ figure in the draft must match THAT tier's price
    specifically, not just any valid SKU price — catches "quoted standard $1,200 but
    drafted the webfix $450 figure" mismatches that check-against-any-SKU would miss.
    Returns (ok, detail)."""
    figures = re.findall(r'\$\s*([\d,]+(?:\.\d{2})?)\s*k?\b', draft, re.IGNORECASE)
    if not figures:
        return True, ""
    try:
        import proposal_factory
        valid_prices = {int(t["price"]) for t in proposal_factory.PRICING.values()}
        valid_deposits = {p // 2 for p in valid_prices}  # 50% deposit is quoted routinely too
        valid_monthly = {75, 150}  # Care Basic / Care Growth (not in PRICING's per-build dict)
        allowed = valid_prices | valid_deposits | valid_monthly
        tier_price = None
        if expected_tier and expected_tier in proposal_factory.PRICING:
            tier_price = int(proposal_factory.PRICING[expected_tier]["price"])
    except Exception:  # noqa: BLE001 — pricing module unavailable; don't block drafting on it,
        # but don't silently pass either: caller sees this came back "ok" with no real check done.
        return True, "(price check skipped: proposal_factory unavailable)"
    bad = []
    for raw in figures:
        is_k = raw.strip().lower().endswith("k") or "k" in draft[max(0, draft.find(raw) - 1):draft.find(raw) + len(raw) + 2].lower()
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue
        if is_k and val < 100:  # "$1k" style shorthand
            val *= 1000
        val = int(round(val))
        if tier_price is not None and val not in (tier_price, tier_price // 2):
            bad.append(val)
        elif tier_price is None and val not in allowed and val >= 50:
            # ignore tiny figures (a $10/mo aside, a "$5 coffee" aside) — only real
            # quote-shaped numbers matter here
            bad.append(val)
    if bad:
        against = f"tier '{expected_tier}' (${tier_price:,})" if tier_price else "any known SKU/deposit/care price"
        return False, f"draft states ${bad[0]:,} which doesn't match {against}"
    return True, ""


def check_name_guard(draft: str, exact_name: str, parsed_guess: str = "") -> tuple[bool, str]:
    """C179: the name used in a draft must be the exact GHL record name, never a
    guess parsed off an email/phone. If a parsed_guess is supplied and it differs
    from exact_name AND the draft contains the guess instead of the real name,
    that's the failure this catches. If exact_name itself is empty/generic
    ("there", "friend"), nothing to guard against — that's an intentional fallback
    elsewhere, not a guessing bug. Returns (ok, detail)."""
    if not exact_name or exact_name.strip().lower() in ("there", "friend", "warm contact"):
        return True, ""
    exact_first = exact_name.strip().split()[0]
    if not parsed_guess or parsed_guess.strip().lower() == exact_first.lower():
        return True, ""
    guess_first = parsed_guess.strip().split()[0] if parsed_guess.strip() else ""
    if guess_first and re.search(r'\b' + re.escape(guess_first) + r'\b', draft, re.IGNORECASE) \
            and not re.search(r'\b' + re.escape(exact_first) + r'\b', draft, re.IGNORECASE):
        return False, f"draft uses parsed guess '{guess_first}' instead of GHL record name '{exact_first}'"
    return True, ""


def check_link_hygiene(draft: str) -> tuple[bool, str]:
    """C204: a first-touch/reply draft should carry at most one link (book link OR
    proposal link, never both — two CTAs split attention and neither gets clicked).
    Returns (ok, detail)."""
    links = _URL_RE.findall(draft)
    if len(links) <= 1:
        return True, ""
    return False, f"{len(links)} links in one draft (max 1): {links}"


_FINANCIAL_RE = re.compile(
    r"\b(wire[ -]?transfer|wiring|bank account|routing number|account number|"
    r"credit card|debit card|card number|cvv|social security|ssn|"
    r"gift[ -]?card|bitcoin|crypto(currency)?|wallet address|western union|"
    r"venmo|zelle|paypal|cash[ -]?app|money order|"
    r"password|login credentials|one-time code|verification code)\b", re.I)


def check_no_financial_ask(draft: str) -> tuple[bool, str]:
    """HARD gate (2026-07-07 audit S3): a reply drafted from an INBOUND message could be
    steered by prompt injection into asking for money or credentials. [OWNER] can send a
    reply with ONE phone tap without seeing the text, so any draft mentioning payment
    rails or credentials is hard-held for eyes-on review. Never a false convenience."""
    m = _FINANCIAL_RE.search(draft or "")
    if m:
        return False, f"draft references '{m.group(0)}' (financial/credential) — hold for review"
    return True, ""


def strip_extra_links(draft: str, keep: str = "") -> str:
    """Mechanical fix companion to check_link_hygiene: when a draft has 2+ links,
    keep the one matching `keep` (if given, e.g. the proposal link that was just
    built) or the LAST link (proposal links are appended after the LLM draft, so
    the last one is usually the intended CTA), drop the rest. Only called when the
    caller has decided a mechanical fix is safe; never invoked silently as part of
    the check itself."""
    links = _URL_RE.findall(draft)
    if len(links) <= 1:
        return draft
    target = keep if keep and keep in links else links[-1]
    out = draft
    removed = 0
    for link in links:
        if link == target and removed < len(links) - 1:
            continue
        if link != target:
            out = out.replace(link, "", 1)
            removed += 1
    return re.sub(r'[ \t]{2,}', ' ', re.sub(r'\n{3,}', '\n\n', out)).strip()


def detect_formality(their_msg: str) -> str:
    """C169: classify their register as 'formal' or 'casual' from surface features,
    so the draft can mirror it. Casual signals: contractions, lowercase-only,
    exclamation points, no punctuation at all, short slangy words. Formal signals:
    full capitalization/punctuation discipline, no contractions, longer sentences,
    a greeting/sign-off. Defaults to 'casual' ([OWNER]'s own house style is casual per
    VOICE-SPEC) when the signal is thin/ambiguous — mirroring should never make a
    draft MORE stiff than his baseline voice."""
    t = (their_msg or "").strip()
    if not t:
        return "casual"
    formal_markers = 0
    casual_markers = 0
    if re.search(r'\bDear\b|\bSincerely\b|\bRegards\b|\bTo whom it may concern\b', t, re.IGNORECASE):
        formal_markers += 2
    if t[0].isupper() and t.rstrip().endswith((".", "!", "?")) and len(t.split()) > 6:
        formal_markers += 1
    if re.search(r"\b(don't|can't|it's|i'm|that's|you're|won't|didn't)\b", t, re.IGNORECASE):
        casual_markers += 1
    if re.search(r'!{1,}|\.\.\.|lol|haha|yeah|ok |nah|gonna|wanna', t, re.IGNORECASE):
        casual_markers += 2
    if t == t.lower() and len(t) > 3:
        casual_markers += 1
    return "formal" if formal_markers > casual_markers else "casual"


def check_length_match(draft: str, their_msg: str, channel: str = "SMS") -> tuple[bool, str]:
    """C178: draft length should track their message length, within reason. A
    one-line "sounds good" shouldn't get a 4-paragraph reply back, and a genuinely
    long detailed question deserves more than 2 words. Soft check: returns ok=True
    (never blocks a send) but a nonempty detail flags a mismatch worth a human
    glance, since 'shorten this' is exactly the kind of edit template_learn.py's
    draft/sent-diff mining already watches for."""
    their_words = len(re.findall(r'\S+', their_msg or ""))
    draft_words = len(re.findall(r'\S+', draft or ""))
    if their_words == 0 or draft_words == 0:
        return True, ""
    cap = 3 if channel == "SMS" else 5  # email tolerates a longer reply-to-short-msg ratio
    if their_words <= 8 and draft_words > their_words * cap and draft_words > 25:
        return True, f"their msg {their_words}w, draft {draft_words}w (long reply to a short message)"
    return True, ""


def detect_harassment(their_msg: str) -> bool:
    """C217: flag inbound as harassment/abuse so reply_watch routes it to
    his-eyes-only (no auto-draft, no auto-suppress-goodbye either — a harassing
    message doesn't get a polite reply, it gets surfaced). Deliberately high
    precision over recall: false negatives (missed harassment) fall through to
    normal classification, which is a much smaller cost than false positives
    (flagging a merely annoyed prospect and having the system go silent on a
    legitimately recoverable conversation)."""
    t = (their_msg or "").lower()
    if any(m in t for m in _HARASSMENT_MARKERS):
        return True
    if any(m in t for m in _SEXUAL_HARASSMENT_MARKERS):
        return True
    return False


# C209: "refusal templates (things we don't do) auto-suggested" -- grounded in
# [OWNER]'s own stated positions from the playbooks, not invented. Each entry is
# (trigger regex, refusal text, source citation) so the source is always traceable
# back to a real playbook line, never a guessed policy.
_REFUSAL_TEMPLATES = [
    (re.compile(r'\b(guarantee|promise).{0,20}(rank|ranking|seo|google|traffic|leads)\b|'
               r'\b(seo|google).{0,20}(guarantee|promise)\b', re.IGNORECASE),
     "I guarantee the build, the timeline, and that you approve before live. Nobody "
     "honest guarantees Google. What I'll show you is the before and after numbers "
     "of people who came before you.",
     "objections.md #50"),
    (re.compile(r'\b(pay when|pay you when|rev.?share|revenue.?share|equity|percentage of '
               r'(?:the )?(?:sales|revenue)|commission.?only)\b', re.IGNORECASE),
     "I don't do equity or rev-share on builds, the price is the price and it's fixed "
     "upfront. If the budget's not there yet, the $450 webfix or the $800 landing "
     "page are the smaller doors in.",
     "pricing-tree.md walk-away triggers"),
    (re.compile(r'\bsame.?day support\b|\b24.?7 support\b|\bsupport (?:on )?weekends?\b',
               re.IGNORECASE),
     "Same-day support runs through Care Growth at $150 a month, that's what keeps "
     "response times fast. Without a care plan it's best-effort, not guaranteed.",
     "pricing-tree.md walk-away triggers"),
    (re.compile(r'\b(use my own|provide my own|already have) hosting\b', re.IGNORECASE),
     "Happy to build on hosting you provide, just know Care Growth (the fast-response "
     "plan) needs me managing the hosting to actually guarantee anything. Your call.",
     "pricing-tree.md walk-away triggers"),
]


def suggest_refusal(their_msg: str) -> dict:
    """C209: if their message is asking for something [OWNER]'s playbooks explicitly
    say he doesn't do, return {"matched": True, "refusal": str, "source": str}. The
    CLASSIFY prompt's own playbook digest already covers price objections; this
    catches the DIFFERENT category of "will you do X" requests for things outside
    scope entirely (SEO guarantees, equity deals, same-day support without a plan),
    which read as scope/policy questions, not price objections, and need a distinct
    refusal, not a counter. Returns {"matched": False} when nothing matches -- this
    is a SUGGESTION surfaced on the record for reply_watch to optionally use, never
    a silent auto-substitution for whatever the LLM actually drafted."""
    t = their_msg or ""
    for pattern, refusal, source in _REFUSAL_TEMPLATES:
        if pattern.search(t):
            return {"matched": True, "refusal": refusal, "source": source}
    return {"matched": False, "refusal": "", "source": ""}


# C219: extremely lightweight language detection — enough to route "this needs a
# Spanish draft + a translation for [OWNER]," not a real language ID model. Common
# Spanish function words/accented characters that essentially never appear in
# English business texts.
_SPANISH_MARKERS = re.compile(
    r'\b(hola|gracias|precio|cu[aá]nto|cuesta|necesito|quiero|d[oó]nde|cu[aá]ndo|'
    r'buenos d[ií]as|buenas tardes|s[ií]|por favor|disculp[ae]|espa[ñn]ol)\b',
    re.IGNORECASE)
_ACCENTED_RE = re.compile(r'[áéíóúñ¿¡]', re.IGNORECASE)


def detect_language(their_msg: str) -> str:
    """C219: 'es' if the inbound reads as Spanish, else 'en'. Deliberately
    conservative (needs a real marker word or 2+ accented characters, not just one
    stray character that could be a typo/emoji-adjacent glyph) since a false
    positive here means drafting in the wrong language entirely."""
    t = their_msg or ""
    if _SPANISH_MARKERS.search(t):
        return "es"
    if len(_ACCENTED_RE.findall(t)) >= 2:
        return "es"
    return "en"


def run_all_gates(draft: str, their_msg: str, exact_name: str, channel: str = "SMS",
                  parsed_guess: str = "", expected_tier: str | None = None) -> dict:
    """Convenience wrapper: run every hard gate + the soft length check, return one
    dict {"ok": bool, "failures": [...], "warnings": [...]}. "ok" reflects only the
    HARD gates (double-question, price, name, link hygiene) — the soft length check
    always lands in warnings, never failures."""
    hard = [
        ("double_question", check_no_double_question(draft)),
        ("price_consistency", check_price_consistency(draft, expected_tier)),
        ("name_guard", check_name_guard(draft, exact_name, parsed_guess)),
        ("link_hygiene", check_link_hygiene(draft)),
        ("financial_ask", check_no_financial_ask(draft)),
    ]
    failures = [{"gate": name, "detail": detail} for name, (ok, detail) in hard if not ok]
    warnings = []
    len_ok, len_detail = check_length_match(draft, their_msg, channel)
    if len_detail:
        warnings.append({"gate": "length_match", "detail": len_detail})
    return {"ok": not failures, "failures": failures, "warnings": warnings}
