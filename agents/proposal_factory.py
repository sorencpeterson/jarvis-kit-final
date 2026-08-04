#!/usr/bin/env python3
"""Proposal Factory: reply -> same-hour close kit.

Input: a contact (GHL id/email/name) or raw --name/--url, plus a niche hint.
Output, staged for [OWNER]'s one-click send (NOTHING here sends anything):
  - store/proposals/<pid>.html  (rendered proposal, served at /prop/<pid>?sig=HMAC)
  - a queue record in store/proposals.jsonl (status=staged) carrying the drafted
    cover email; the dashboard PROPOSALS drawer + needs-queue pick it up.

Pricing logic mirrors business-library/playbooks/pricing-tree.md - that file is the
documentation, PRICING below is the implementation. Change one, change both.

Usage:
  proposal_factory.py --email braydon.lj@yahoo.com --niche "local service"
  proposal_factory.py --name "Legacy Plumbing" --url https://legacyplumbing.com
  proposal_factory.py --contact-id abc123 --tier standard --dry
  --dry: skip GHL lookups (use provided args only). --no-llm: template-fill smoke test.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import html as _html
import os
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import owner  # noqa: E402
from store_lib import now_iso, new_id, humanize, secret, voice_spec, sign_secret  # noqa: E402
import planner  # noqa: E402
import ghl_social  # noqa: E402

STORE = ROOT / "store"
OUT = STORE / "proposals"
QUEUE = STORE / "proposals.jsonl"
TEMPLATE = ROOT / "agents" / "templates" / "proposal.html"


def _fill_owner(html: str) -> str:
    """Fill the owner slots in the proposal template at render time.

    The template ships with {{OWNER_NAME}}/{{OWNER_INITIALS}} rather than a baked-in
    name so a copy of this system presents as whoever owns it."""
    name = owner.get("name", "") or "Your Name"
    initials = "".join(w[0] for w in name.split()[:2]).upper() or "YN"
    return html.replace("{{OWNER_NAME}}", name).replace("{{OWNER_INITIALS}}", initials)
PLAYBOOK = Path(os.environ.get("BIZLIB") or (ROOT / "business-library")) / "playbooks" / "pricing-tree.md"
PAST_CLIENTS = STORE / "past-clients.csv"  # A15: optional, cols name,quote,company (hidden until it exists)
BOOK_URL = f"{owner.get('site', 'example.com')}/book"
TRADE_NICHES = ("hvac", "plumbing", "roofing", "electrical", "landscap")
STUDIO_NICHES = ("salon", "boutique", "agency", "studio", "spa")

# ---- pricing tree implementation (doc: playbooks/pricing-tree.md) ----
PRICING = {
    "landing":    {"name": "Landing page",        "price": 800,  "days": 5,
                   "desc": "Single page, one goal: calls and quote requests."},
    "standard":   {"name": "Standard site",       "price": 1200, "days": 7,
                   "desc": "Up to 6 pages, mobile-first, built to convert visitors into calls."},
    "booking":    {"name": "E-com / Booking site", "price": 2500, "days": 10,
                   "desc": "Everything in Standard plus online booking or checkout, wired end to end."},
    "whiteglove": {"name": "White-Glove build",   "price": 3500, "days": 14,
                   "desc": "Copy, brand direction, and the site. You approve, we handle the rest."},
    "webfix":     {"name": "Site fix bundle",     "price": 450,  "days": 3,
                   "desc": "Speed, mobile, and SEO faults on your current site, fixed and verified."},
    # CX5: 2 days, not 7 -- the cold campaign (business-library/campaigns/
    # wl-cold-email-7.md) promises "48-hour turnaround" for this exact SKU; the
    # generated agreement/proposal timeline must match what the prospect was already
    # told, not a different, slower number.
    "agencyfirst": {"name": "Agency first build",  "price": 1000, "days": 2,
                    "desc": "Your first white-label order, flat, to prove the work."},
}
CARE = {"basic": {"name": "Care Basic", "price": 75}, "growth": {"name": "Care Growth", "price": 150, "onboard": 250}}
BOOKING_NICHES = ("restaurant", "salon", "gym", "spa", "dentist", "clinic", "barber",
                  "medspa", "med spa", "aesthetic", "iv ", "wellness", "inject")


def _has_site(site_url: str, site: dict) -> bool:
    """CX10: has_site feeds route()'s $800 landing-vs-$1200 standard fork. A fetch
    FAILURE (403, timeout, a JS-only page that strips down to empty text) is NOT
    the same as a CONFIRMED absence of a site -- fetch_site() returns an "error"
    key on any of those, and treating that as "no site" silently underpriced by
    $400. Only "no url was ever given" is a confirmed absence; a failed/unknown
    fetch defaults to True (assume a site exists) rather than guess wrong.

    R2#8: that same "can't confirm no-site, don't underprice" logic also covers a
    fetch that SUCCEEDED (no "error" key) but still returned EMPTY extracted text
    -- a JS-only/SPA page whose real content never lands in the server-rendered
    HTML fetch_site() reads. That used to fall through to bool(site.get("text"))
    == False and get treated exactly like a confirmed-empty domain, underpricing
    via a different path than the error case right above it."""
    if not site_url:
        return False
    # error, empty-but-fetched, and real text are all "not a CONFIRMED no-site" --
    # only a missing url ever is.
    return True


def route(niche: str, tier_override: str = "", faults_n: int = 0, has_site: bool = True) -> str:
    """pricing-tree.md routing rules, in code."""
    if tier_override in PRICING:
        return tier_override
    n = (niche or "").lower()
    if "agency" in n:
        return "agencyfirst"
    if any(b in n for b in BOOKING_NICHES) or "e-com" in n or "ecom" in n or "booking" in n:
        return "booking"
    if not has_site:
        return "landing"
    # pricing-tree.md webfix lane: salvageable site -> $450 bundle, BUT "if the
    # teardown finds 4+ structural faults, recommend Standard rebuild instead".
    # The old order checked `"webfix" in n` before the fault count (and "webfix"
    # contains "fix"), so the 4+-fault override to standard could never fire.
    if faults_n < 4 and ("webfix" in n or (0 < faults_n and "fix" in n)):
        return "webfix"
    return "standard"


# ---- GHL + site helpers ----
def _loc() -> str:
    try:
        for line in (ghl_social.GHL / ".env").read_text().splitlines():
            if line.startswith("GHL_LOCATION_ID="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def find_contact(email: str = "", name: str = "", cid: str = "") -> dict:
    """Resolve a GHL contact to {id,name,company,email,phone,website,tags}. Read-only.

    L: a fuzzy GHL search can return candidates that don't actually match what we
    queried for. Falling back to cands[0] on no exact match sent a proposal built
    for (and possibly emailed to) a total stranger. No exact email/name match ->
    {}, same as cold_import.find_contact / warm_refresh.find_contact."""
    try:
        if cid:
            raw = ghl_social._api(["GET", f"/contacts/{cid}"])
            c = (json.loads(raw, strict=False) or {}).get("contact") or {}
        else:
            q = email or name
            raw = ghl_social._api(["GET", f"/contacts/?locationId={_loc()}&query={urllib.request.quote(q)}&limit=5"])
            cands = (json.loads(raw, strict=False) or {}).get("contacts") or []
            c = None
            if email:
                for x in cands:
                    if (x.get("email") or "").strip().lower() == email.strip().lower():
                        c = x
                        break
            elif name:
                want = name.strip().lower()
                for x in cands:
                    full = (x.get("contactName") or
                            f"{x.get('firstName','')} {x.get('lastName','')}").strip().lower()
                    # R2#5: --name is documented for a BUSINESS name too (module
                    # docstring: `--name "Legacy Plumbing"`), but contactName/first+last
                    # is the PERSON's name -- a fuzzy search can legitimately return the
                    # right contact under a totally different person name. Also match
                    # companyName, or a legit business-name lookup silently returns {}.
                    company = (x.get("companyName") or "").strip().lower()
                    if full == want or (company and company == want):
                        c = x
                        break
            c = c or {}  # no exact match -> unknown, never guess a stranger
        if not c:
            return {}
        return {"id": c.get("id", ""),
                "name": (c.get("contactName") or f"{c.get('firstName','')} {c.get('lastName','')}").strip(),
                "company": c.get("companyName") or "",
                "email": c.get("email") or "", "phone": c.get("phone") or "",
                "website": c.get("website") or "",
                "tags": c.get("tags") or []}
    except Exception as e:  # noqa: BLE001
        print(f"  contact lookup failed: {e}")
        return {}


def fetch_site(u: str) -> dict:
    if not u:
        return {}
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    # SSRF gate: u can come from a GHL contact's website field (attacker-settable) and this
    # runs UNATTENDED from reply_watch. safe_urlopen refuses localhost/internal/metadata on
    # the initial URL AND every redirect hop (2026-07-07 audit S4 CRITICAL + re-audit).
    try:
        import net_guard
        resp = net_guard.safe_urlopen(u, timeout=12,
                                      headers={"User-Agent": "Mozilla/5.0 ([OWNER]Digital audit)"})
    except ValueError as e:
        return {"url": u, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"url": u, "error": str(e)[:100]}
    try:
        headers = {k.lower(): v for k, v in dict(resp.headers).items()}
        raw = resp.read(400_000).decode("utf-8", "replace")
        title = (re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I) or [None, ""])[1]
        has_viewport = 'name="viewport"' in raw
        img_count = len(re.findall(r"<img", raw, re.I))
        body = re.sub(r"<(script|style|nav|footer)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
        body = re.sub(r"<[^>]+>", " ", body)
        body = re.sub(r"\s+", " ", body).strip()
        # B21: can this site legally be iframed? Checked server-side (read-only header
        # inspection, same request we already made) rather than guessed client-side --
        # X-Frame-Options/CSP frame-ancestors blocks don't fire a JS error event, they
        # just render blank, so a client-side heuristic would be unreliable.
        xfo = (headers.get("x-frame-options") or "").upper()
        csp = (headers.get("content-security-policy") or "").lower()
        frameable = "DENY" not in xfo and "SAMEORIGIN" not in xfo and "frame-ancestors" not in csp
        return {"url": u, "title": (title or "").strip()[:120], "text": body[:2600],
                "viewport": has_viewport, "imgs": img_count, "bytes": len(raw),
                "raw_html": raw[:120_000], "frameable": frameable}
    except Exception as e:  # noqa: BLE001
        print(f"  site fetch failed ({u}): {e}")
        return {"url": u, "error": str(e)[:100]}


# ---- LLM slot fill ----
GEN = """You write website proposals for [OWNER] ([OWNER_COMPANY]).
VOICE SPEC (follow it to the letter):
{voice}
Second person always ("your site", "your customers").

PROSPECT
name: {name} | company: {company} | niche: {niche}
site: {site_url}
site title: {site_title}
site evidence (truncated text of their current site; empty means NO site found):
\"\"\"{site_text}\"\"\"
technical facts: viewport meta present: {viewport} | <img> tags: {imgs} | page weight: {kb} KB

TASK: return ONLY a JSON object with these keys:
- "headline": 6-10 word proposal title. Concrete outcome, their business named or implied.
- "diagnosis_title": 2-4 words. "What's costing you" style if site exists, else "Starting position".
- "personal_line": 1-2 sentences to {name} showing we actually looked at THEIR situation.
  Reference something real from the site evidence (a phrase, a missing thing). Never generic.
- "faults": array of exactly 5 {{"t": "short bold fault", "p": "1-2 sentence plain explanation
  tied to lost customers/calls"}}. Ground each in the evidence when it exists (quote a
  fragment if useful). Each fault's explanation must include a WALKING-AWAY SCENE: a
  moment in time where a specific customer takes a competing action ("Tuesday 9pm,
  comparing on her phone, she books the one that shows prices").
  IF NO SITE / EMPTY EVIDENCE: every fault must be about the ABSENCE itself and what we
  VERIFIED ("we pulled your domain and got zero content", "your only presence is a
  booking page on someone else's domain") — NEVER invent details about a site you
  could not see, never write generic niche claims dressed as findings.
- "scope": array of 6 short deliverable lines fixing exactly those faults. Concrete.
- "faq": array of 3 {{"q": "the objection in their words", "a": "2-sentence disarm, [OWNER]'s
  voice"}}. Pick the 3 most likely for this niche (price anchor to one lost job, timeline,
  "my nephew does sites" or "I already have a guy" or "is this a template").
- "email_subject": subject for the cover email. Under 8 words, lowercase ok, no colons-hype.
- "email_body": the cover email. Under 90 words. Points to the proposal link (write LINK
  where it goes), one CTA: book at {book}. Sign "[OWNER]". NEVER state a dollar price or a
  delivery timeline (number of days) in the email: the proposal page carries the price and
  timeline, and the email must not contradict it. The email sells the specific problem you
  spotted and the link, nothing else.
- "tier_bump": 0, 1 or -1 if the evidence says the routed tier is wrong (e.g. they clearly
  need booking -> 1). Include "bump_reason" (short) when nonzero.
- "avg_job": your best single-number estimate of their average job/ticket value in USD
  (integer, e.g. plumber 350, HVAC install 5500 -> use a NORMAL service call ~300-500,
  restaurant 40, salon 65, agency retainer 1500). Conservative and defensible.
- "mockup": an object that fills their REBUILT homepage concept (shown to them, so make
  it feel like THEIR business, not a template). Keys:
  "hero_h" (8-12 words, benefit-first, may address their city/area),
  "hero_sub" (one sentence), "cta_main" (2-4 words w/ urgency), "cta_alt" (2-3 words),
  "trust" (3 short chips like "Licensed and insured", niche-true),
  "form_head", "form_sub", "form_field3" (the niche-specific ask), "form_btn",
  "services_h", "services_sub",
  "services": 3-6 of {{"icon": one emoji, "t": "3-4 word service", "p": "one plain sentence"}}
    (use their REAL services from the site evidence when present),
  "stats": 3 of {{"n": "number like 24/7 or 15+", "l": "short label"}} (only claims that are
    safe generics or evidenced, e.g. "same-week" not "4.9 stars" unless their site says so),
  "cta_band_h", "cta_band_sub", "area_line" (city/service area from evidence or their location).
JSON only. No prose around it."""


def generate(contact: dict, niche: str, site: dict) -> dict:
    prompt = GEN.format(
        voice=voice_spec(),
        name=contact.get("name") or "there", company=contact.get("company") or contact.get("name") or "your business",
        niche=niche or "local service", site_url=site.get("url") or "(none found)",
        site_title=site.get("title") or "", site_text=(site.get("text") or "")[:2400],
        viewport=site.get("viewport", "n/a"), imgs=site.get("imgs", "n/a"),
        kb=round((site.get("bytes") or 0) / 1024), book=BOOK_URL)
    out = planner._cli_json(prompt, timeout=180, feature="proposal")
    if not isinstance(out, dict) or not out.get("faults"):
        print("  generate: empty first pass, retrying once")
        out = planner._cli_json(prompt, timeout=240, feature="proposal")
    return out if isinstance(out, dict) else {}


FALLBACK_FAULTS = [
    {"t": "Invisible on mobile", "p": "Most of your customers search on a phone. If the site fights them, they call the next result."},
    {"t": "No clear next step", "p": "Every page needs one obvious action: call, book, or get a quote. Right now there isn't one."},
    {"t": "Slow to load", "p": "Every extra second of load time costs real visitors. Speed is a ranking factor and a patience factor."},
    {"t": "Nothing building trust", "p": "No reviews, no photos of real work, no faces. People buy from businesses that look alive."},
    {"t": "Google can't read it", "p": "Missing titles and descriptions mean you lose the local searches you should own."},
]


# ---- mockup palettes: niche -> (accent, accent2) ----
PALETTES = {
    "hvac": ("#1d5fd6", "#f2842a"), "plumbing": ("#1467c9", "#12b3a4"),
    "roofing": ("#39424e", "#e8a020"), "electrical": ("#14337a", "#ffc21f"),
    "landscap": ("#1f7a34", "#8ac926"), "restaurant": ("#c0392b", "#e8a020"),
    "salon": ("#b8506e", "#c9a227"), "gym": ("#16181d", "#e23b3b"),
    "medspa": ("#9d6b8f", "#c9a227"), "aesthetic": ("#9d6b8f", "#c9a227"),
    "iv": ("#0f8b8d", "#7fc8c9"), "wellness": ("#5b8a72", "#c9a227"),
    "dental": ("#0f8b8d", "#48c0b8"), "clinic": ("#0f8b8d", "#48c0b8"),
    "medical": ("#0f8b8d", "#48c0b8"), "legal": ("#26355c", "#a68a4d"),
    "agency": ("#4638d6", "#8d7bff"), "default": ("#1d5fd6", "#12b3a4"),
}


def _palette(niche: str) -> tuple:
    n = (niche or "").lower()
    for k, v in PALETTES.items():
        if k != "default" and k in n:
            return v
    return PALETTES["default"]


def _pretty_phone(p: str) -> str:
    d = re.sub(r"\D", "", p or "")
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if len(d) == 10 else (p or "(555) 000-0000")


# ---- B19/20: which mockup pack for which niche ----
def mockup_template_name(niche: str, override: str = "") -> str:
    """trades -> bold, salon/boutique/agency/studio/spa -> studio, else the classic pack."""
    if override in ("mockup.html", "mockup-bold.html", "mockup-studio.html"):
        return override
    n = (niche or "").lower()
    if any(t in n for t in TRADE_NICHES):
        return "mockup-bold.html"
    if any(s in n for s in STUDIO_NICHES):
        return "mockup-studio.html"
    return "mockup.html"


# ---- B24: inline SVG favicon from initials + accent, no external assets ----
def _favicon_svg(biz: str, accent: str) -> str:
    import base64
    words = [w for w in re.split(r"\s+", (biz or "PD").strip()) if w]
    initials = ("".join(w[0] for w in words[:2]) or "PD").upper()
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'<rect width="64" height="64" rx="14" fill="{accent}"/>'
        f'<text x="32" y="42" font-family="-apple-system,Helvetica,Arial,sans-serif" '
        f'font-size="26" font-weight="800" fill="#fff" text-anchor="middle">{_esc(initials)}</text></svg>'
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


# ---- B26: static shaded-radius service-area map, no API keys ----
def _map_svg(area_line: str, accent: str) -> str:
    label = _esc((area_line or "Serving the local area")[:60])
    return (
        '<svg viewBox="0 0 640 260" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Service area map">'
        '<rect width="640" height="260" fill="#eef1f6"/>'
        '<path d="M0 200 Q160 150 320 190 T640 170 V260 H0 Z" fill="#e2e7ee"/>'
        f'<circle cx="320" cy="130" r="90" fill="{accent}" opacity="0.16"/>'
        f'<circle cx="320" cy="130" r="55" fill="{accent}" opacity="0.24"/>'
        f'<circle cx="320" cy="130" r="8" fill="{accent}"/>'
        f'<circle cx="320" cy="130" r="8" fill="{accent}"><animate attributeName="r" values="8;16;8" dur="2.4s" '
        'repeatCount="indefinite"/><animate attributeName="opacity" values="0.9;0;0.9" dur="2.4s" repeatCount="indefinite"/></circle>'
        f'<text x="320" y="230" font-family="-apple-system,Helvetica,Arial,sans-serif" font-size="15" '
        f'fill="#5b6472" text-anchor="middle" font-weight="600">{label}</text></svg>'
    )


# ---- B27: seasonal, date-aware hero line for HVAC-style niches ----
def _seasonal_line(niche: str, dt: datetime | None = None) -> str:
    n = (niche or "").lower()
    if "hvac" not in n and "heat" not in n and "air" not in n:
        return ""
    month = (dt or datetime.now()).month
    if month in (11, 12, 1, 2):
        return "Furnace acting up? We get you warm again, fast."
    if month in (6, 7, 8, 9):
        return "AC down in the heat? Same-day calls, done right."
    return "Heating and cooling, handled before the season turns."


# ---- B29: best-effort logo pull from their fetched site HTML ----
def _logo_from_site(site: dict) -> str:
    raw = site.get("raw_html") or ""
    if not raw:
        return ""
    m = re.search(r'<img[^>]+(?:class|id)="[^"]*logo[^"]*"[^>]*src="([^"]+)"', raw, re.I)
    if not m:
        m = re.search(r'<img[^>]+src="([^"]+)"[^>]*(?:class|id)="[^"]*logo[^"]*"', raw, re.I)
    if not m:
        return ""
    src = m.group(1)
    base = site.get("url") or ""
    if src.startswith("//"):
        src = "https:" + src
    elif src.startswith("/") and base:
        m2 = re.match(r"(https?://[^/]+)", base)
        if m2:
            src = m2.group(1) + src
    elif not src.startswith(("http://", "https://")):
        return ""  # relative path we can't safely resolve; skip rather than guess wrong
    if not src.startswith(("http://", "https://")):
        return ""
    return f'<img class="logo-img" src="{_esc(src)}" alt="" loading="lazy" onerror="this.remove()">'


# ---- B32: speed badge, real render size vs a generic "their current site" number ----
def _speed_badge_html(site: dict) -> str:
    kb = round((site.get("bytes") or 0) / 1024)
    if not kb:
        return ""
    ours = "0.4s" if kb < 200 else ("0.6s" if kb < 500 else "0.9s")
    theirs = "1.2s" if kb < 800 else ("2.4s" if kb < 2000 else "4.8s+")
    return f'<span class="speedbadge">⚡ This loads in <b>{ours}</b> · yours: <b>{theirs}</b></span>'


# ---- B25: GBP rating, [L] stub. No live fetch (no reliable server-side path); lights
# up automatically once a caller populates contact["gbp_rating"]/["gbp_count"]. ----
def _gbp_row_html(contact: dict) -> str:
    try:
        rating = float(contact.get("gbp_rating") or 0)
        count = int(contact.get("gbp_count") or 0)
    except (TypeError, ValueError):
        return ""
    if not (0 < rating <= 5) or count <= 0:
        return ""
    stars = "★" * round(rating) + "☆" * (5 - round(rating))
    return (f'<div class="gbprow"><span class="stars">{stars}</span>'
            f'<b>{rating:.1f}</b> from {count:,} Google reviews</div>')


# ---- B36: which niches get the fake-but-interactive booking calendar ----
def _booking_section_html(niche: str, tier_key: str) -> str:
    if tier_key != "booking" and not any(b in (niche or "").lower() for b in BOOKING_NICHES):
        return ""
    return (
        '<section id="book" style="padding-top:12px"><div class="wrap">'
        '<div class="kick">Book online</div><h2>Your customers pick a time. Done.</h2>'
        '<p class="lead">No more phone tag. This calendar becomes real bookings the day you launch.</p>'
        '<div class="calwrap"><div style="font-weight:700;font-size:14px">Pick a day</div>'
        '<div class="calgrid" id="calgrid"></div>'
        '<div style="font-weight:700;font-size:14px;margin-top:16px">Pick a time</div>'
        '<div class="calslots"><button type="button">9:00 AM</button><button type="button">11:30 AM</button>'
        '<button type="button">2:00 PM</button><button type="button">4:30 PM</button></div>'
        '<div class="calconfirm" id="calconfirm">Selected: <span id="calpicked"></span></div>'
        '</div></div></section>'
    )


# ---- B37: e-com add-to-cart affordance on service cards, booking/ecom tier only ----
def _cart_button(name: str, price_hint: str, tier_key: str) -> str:
    if tier_key != "booking":
        return ""
    p = price_hint or "$--"
    return f'<button type="button" class="addcart" data-name="{_esc(name)}" data-price="{_esc(p)}">+ Add to cart</button>'


def render_mockup(slots: dict, contact: dict, niche: str, site: dict | None = None,
                   tier_key: str = "", template: str = "") -> str:
    """template: optional override, one of mockup.html / mockup-bold.html / mockup-studio.html.
    Auto-picked by niche (B19/B20) when not given; see mockup_template_name()."""
    m = slots.get("mockup") or {}
    site = site or {}
    biz = contact.get("company") or contact.get("name") or "Your Business"
    words = biz.split()
    brand_a, brand_b = (" ".join(words[:-1]) + " ", words[-1]) if len(words) > 1 else (biz[: max(1, len(biz) // 2)], biz[max(1, len(biz) // 2):])
    ac, ac2 = _palette(niche)
    if m.get("accent") and re.fullmatch(r"#[0-9a-fA-F]{6}", m["accent"]):
        ac = m["accent"]
    phone = contact.get("phone") or "+15550000000"
    area_line = m.get("area_line") or contact.get("location") or "Serving the local area"
    tier_key = tier_key or route(niche)

    cards, cards_full = [], []
    for c in (m.get("services") or [])[:6]:
        cart_btn = _cart_button(c.get("t") or "", c.get("price") or "", tier_key)
        cards.append(f'<div class="card"><div class="ic">{_esc(c.get("icon") or "⚙️")}</div>'
                      f'<b>{_esc(c.get("t"))}</b><p>{_esc(c.get("p"))}</p>{cart_btn}</div>')
        cards_full.append(f'<div class="card"><div class="ic">{_esc(c.get("icon") or "⚙️")}</div>'
                           f'<b>{_esc(c.get("t"))}</b><p>{_esc(c.get("p"))}</p>{cart_btn}</div>')
    stats = "".join(
        f'<div><b>{_esc(x.get("n"))}</b><span>{_esc(x.get("l"))}</span></div>'
        for x in (m.get("stats") or [])[:4])
    chips = "".join(f'<span><b>✓</b> {_esc(t)}</span>' for t in (m.get("trust") or [])[:3])

    # B27: seasonal hero sub-line, only for heating/cooling niches, only if the LLM
    # didn't already write something (never override real generated copy)
    hero_sub = humanize(m.get("hero_sub") or _seasonal_line(niche) or "Fast, honest, and done right the first time.")

    pack = mockup_template_name(niche, template)
    tpl = (ROOT / "agents" / "templates" / pack).read_text()
    fill = {
        "biz": _esc(biz), "brand_a": _esc(brand_a), "brand_b": _esc(brand_b),
        "accent": ac, "accent2": ac2,
        "phone": _esc(phone), "phone_pretty": _esc(_pretty_phone(phone)),
        "hero_h": _esc(humanize(m.get("hero_h") or f"The {niche or 'local'} pros your neighbors already trust")),
        "hero_sub": _esc(hero_sub),
        "cta_main": _esc(m.get("cta_main") or "Call now"),
        "cta_alt": _esc(m.get("cta_alt") or "See services"),
        "trust_chips": chips or '<span><b>✓</b> Licensed and insured</span><span><b>✓</b> Same-week service</span>',
        "form_head": _esc(m.get("form_head") or "Get a fast quote"),
        "form_sub": _esc(m.get("form_sub") or "Tell us what you need. We reply fast."),
        "form_field3": _esc(m.get("form_field3") or "What do you need done?"),
        "form_btn": _esc(m.get("form_btn") or "Get my quote"),
        "services_h": _esc(m.get("services_h") or "Everything you need, one call"),
        "services_sub": _esc(humanize(m.get("services_sub") or "")),
        "service_cards": "".join(cards),
        "service_cards_full": "".join(cards_full) or "".join(cards),
        "stat_blocks": stats or '<div><b>15<i>+</i></b><span>years serving the area</span></div>',
        "cta_band_h": _esc(humanize(m.get("cta_band_h") or "Ready when you are")),
        "cta_band_sub": _esc(humanize(m.get("cta_band_sub") or "Call now or send the form. We answer.")),
        "area_line": _esc(area_line),
        "favicon_uri": _favicon_svg(biz, ac),                       # B24
        "map_svg": _map_svg(area_line, ac),                          # B26
        "gbp_row": _gbp_row_html(contact),                           # B25 [L]
        "logo_img": _logo_from_site(site),                           # B29
        "speed_badge": _speed_badge_html(site),                      # B32
        "booking_section": _booking_section_html(niche, tier_key),   # B36
    }
    for k, v in fill.items():
        tpl = tpl.replace("{{%s}}" % k, v)
    return tpl


# ---- render ----
def _esc(s: str) -> str:
    return _html.escape(str(s or ""), quote=False)


# ---- A4: severity meters. LLM faults aren't scored today, so severity decays by
# position (fault 1 = most severe), matching "weight = importance" from the brief.
# Supports an optional "sev" 1-5 key on a fault dict if one is ever added upstream. ----
def _severity_bar(fault: dict, idx: int, total: int) -> str:
    try:
        sev = float(fault.get("sev")) if fault.get("sev") is not None else None
    except (TypeError, ValueError):
        sev = None
    if sev is None:
        sev = 100 - int((idx / max(1, total)) * 55)  # first fault ~100%, last ~45%
    pct = max(15, min(100, round(sev if sev > 5 else sev * 20)))
    return f'<div class="sevmeter" aria-hidden="true"><i style="width:{pct}%"></i></div>'


# ---- A13: A/B headline test harness. Config flag ab_headline: "" (off) | "a" | "b" |
# "auto" (alternate deterministically by pid hash so the split holds steady per link). ----
def _headline_variant(slots: dict, niche: str, pid: str) -> tuple[str, str]:
    """Returns (headline_text, variant_label) where variant_label is "a"/"b"/"" (off)."""
    base = humanize(slots.get("headline") or f"A site that turns searches into {niche or 'local'} jobs")
    flag = str((planner._config().get("ab_headline") or "")).lower()
    if flag not in ("a", "b", "auto"):
        return base, ""
    variant_b = humanize(slots.get("headline_b") or f"Stop losing {niche or 'local'} jobs to a bad website")
    if flag == "a":
        return base, "a"
    if flag == "b":
        return variant_b, "b"
    # auto: stable per-pid split so the SAME proposal never flips between opens
    is_b = bool(int(hashlib.sha1((pid or "x").encode()).hexdigest(), 16) % 2)
    return (variant_b, "b") if is_b else (base, "a")


# ---- A15: testimonial slot, hidden until store/past-clients.csv has rows.
# Expected columns: name,company,quote (extra columns ignored). ----
def _testimonial_html() -> str:
    if not PAST_CLIENTS.exists():
        return ""
    try:
        rows = list(csv.DictReader(PAST_CLIENTS.read_text().splitlines()))
    except (OSError, csv.Error):
        return ""
    rows = [r for r in rows if (r.get("quote") or "").strip()]
    if not rows:
        return ""
    import random
    r = random.choice(rows)
    name = _esc(r.get("name") or "A client")
    company = _esc(r.get("company") or "")
    quote = _esc(r.get("quote") or "")
    who = f"{name}, {company}" if company else name
    return (f'<div class="testi"><div class="stars">★★★★★</div><p>&ldquo;{quote}&rdquo;</p>'
            f'<span>— {who}</span></div>')


# ---- A16: proposal versions. A prior staged/sent proposal for the same contact_id
# gets a diff banner on the new one ("updated after our call"). ----
def _prior_version(contact_id: str) -> dict:
    if not contact_id:
        return {}
    prior = [r for r in load_queue() if r.get("contact_id") == contact_id and r.get("status") != "skipped"]
    return prior[-1] if prior else {}


def _version_banner_html(prior: dict, version: int) -> str:
    if version <= 1 or not prior:
        return ""
    when = (prior.get("created") or "")[:10]
    return (f'<div class="verbanner">🔄 <b>Updated proposal (v{version}).</b> '
            f'{("Refreshed after our call. " if when else "")}The version from {when or "before"} is still on file if you need it.</div>')


# ---- A17: utm passthrough, built once per proposal, appended to every CTA href ----
def _utm_qs(slots: dict) -> str:
    pairs = []
    for k in ("utm_source", "utm_medium", "utm_campaign"):
        v = slots.get(k)
        if v:
            pairs.append(f"{k}={urllib.request.quote(str(v))}")
    if not pairs:
        pairs = ["utm_source=proposal", "utm_medium=web"]
    # "?" not "&": every {{utm_qs}} append site is a QUERY-LESS url (book_url, /case/slug).
    # The old "&" join produced literal paths like /book&utm_source=... — a broken URL that
    # soft-404s to the homepage instead of the booking page ([OWNER] caught it live 2026-07-11).
    return "?" + "&".join(pairs)


# ---- B21: before/after slider. Only rendered when the prospect's real site was
# fetched. Uses the server-checked "frameable" flag from fetch_site() (see there for
# why this can't be a client-side heuristic); falls back to a link card when blocked. ----
def _before_after_html(site: dict, mock_link: str) -> str:
    real_url = site.get("url") or ""
    if not real_url or not mock_link:
        return ""
    if site.get("frameable"):
        return f"""
  <section id="beforeafter"><h2>Drag the line. See the difference.</h2>
    <p style="color:var(--sub);margin-bottom:18px">Your site today, on the left. Your rebuilt homepage, on the right. One gesture, the whole pitch.</p>
    <div class="baslider" id="baslider" style="position:relative;border:1px solid var(--line);border-radius:16px;overflow:hidden;
      background:#fff;box-shadow:0 10px 34px rgba(22,24,29,.07);height:380px;user-select:none">
      <!-- layer geometry, learned the hard way ([OWNER] caught it live, 2026-07-11): the
           FULL underlying layer is what shows RIGHT of the handle (badge "Rebuilt");
           the 50%-clipped TOP layer shows LEFT of it (badge "Today"). So their REAL
           site goes in the clipped/top layer and the MOCK goes underneath — the old
           order had them swapped, presenting their own site as our rebuild. -->
      <div class="ba-before" style="position:absolute;inset:0;overflow:hidden">
        <iframe src="{_esc(mock_link)}" loading="lazy" style="width:200%;height:760px;transform:scale(.5);
          transform-origin:0 0;border:0;pointer-events:none" title="Your site, rebuilt"></iframe>
      </div>
      <div class="ba-after" id="baAfter" style="position:absolute;inset:0;overflow:hidden;width:50%;border-right:3px solid var(--gold)">
        <iframe src="{_esc(real_url)}" loading="lazy" style="width:400%;height:760px;transform:scale(.5);
          transform-origin:0 0;border:0;pointer-events:none" title="Your site today"></iframe>
      </div>
      <div id="baHandle" style="position:absolute;top:0;bottom:0;left:50%;width:40px;margin-left:-20px;
        display:flex;align-items:center;justify-content:center;cursor:ew-resize;touch-action:none">
        <div style="width:38px;height:38px;border-radius:50%;background:var(--ink);color:#fff;display:flex;
          align-items:center;justify-content:center;font-size:13px;box-shadow:0 4px 14px rgba(0,0,0,.3)">↔</div>
      </div>
      <div style="position:absolute;top:10px;left:14px;background:rgba(22,24,29,.75);color:#fff;font-size:11px;
        font-weight:700;padding:4px 10px;border-radius:6px;letter-spacing:.04em;text-transform:uppercase">Today</div>
      <div style="position:absolute;top:10px;right:14px;background:rgba(184,134,11,.9);color:#fff;font-size:11px;
        font-weight:700;padding:4px 10px;border-radius:6px;letter-spacing:.04em;text-transform:uppercase">Rebuilt</div>
    </div>
    <script>(function(){{
      var wrap=document.getElementById('baslider'),after=document.getElementById('baAfter'),h=document.getElementById('baHandle');
      if(!wrap||!after||!h)return;
      var dragging=false;
      function setPct(clientX){{
        var r=wrap.getBoundingClientRect();
        var pct=Math.max(4,Math.min(96,((clientX-r.left)/r.width)*100));
        after.style.width=pct+'%'; h.style.left=pct+'%';
      }}
      function down(e){{dragging=true;e.preventDefault();}}
      function move(e){{if(!dragging)return;var x=e.touches?e.touches[0].clientX:e.clientX;setPct(x);}}
      function up(){{dragging=false;}}
      h.addEventListener('mousedown',down); h.addEventListener('touchstart',down,{{passive:false}});
      window.addEventListener('mousemove',move); window.addEventListener('touchmove',move,{{passive:false}});
      window.addEventListener('mouseup',up); window.addEventListener('touchend',up);
      wrap.addEventListener('click',function(e){{if(e.target===h||h.contains(e.target))return;setPct(e.clientX);}});
    }})();</script>
  </section>"""
    # frame-blocked fallback: link card, tested path (see status-templates.md)
    return f"""
  <section id="beforeafter"><h2>Your site today vs your site rebuilt</h2>
    <p style="color:var(--sub);margin-bottom:18px">Your current site blocks embedding (a lot of sites do), so here's a direct link
    to open it side by side with your new homepage concept.</p>
    <div style="display:flex;gap:14px;flex-wrap:wrap">
      <a href="{_esc(real_url)}" target="_blank" rel="noopener" style="flex:1;min-width:220px;border:1px solid var(--line);
        border-radius:14px;padding:20px;text-decoration:none;color:var(--ink);background:var(--card)">
        <div style="font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--sub);font-weight:700;margin-bottom:6px">Today</div>
        <div style="font-weight:700">Your current site →</div></a>
      <a href="{_esc(mock_link)}" style="flex:1;min-width:220px;border:1px solid var(--gold);border-radius:14px;padding:20px;
        text-decoration:none;color:var(--ink);background:var(--card)">
        <div style="font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--gold2);font-weight:700;margin-bottom:6px">Rebuilt</div>
        <div style="font-weight:700">Your homepage concept →</div></a>
    </div>
  </section>"""


# ---- B22: og:image tags, rendered UNCONDITIONALLY (see og_shots.py). The image path is
# predictable from pid alone (token = sig_for(pid)[:10], same capability-link pattern as
# /prop /mock /agree), so we can point at it before the shot exists -- og_shots.py fills
# store/og/<pid>-<token>.png asynchronously and the preview 404s gracefully until then,
# which self-heals the next time a link preview is fetched (email/iMessage crawlers
# re-fetch og:image per-open, they don't cache a miss forever). No server.py change
# needed to emit these tags; only the GET /og/{name}.png route itself is main-session work
# (see status-ogshots.md contract). ----
def _og_tags_html(pid: str, headline: str, personal_line: str) -> str:
    base = (planner._config().get("public_base_url") or "http://127.0.0.1:8765").rstrip("/")
    img_url = f"{base}/og/{pid}-{sig_for(pid)[:10]}.png"
    prop_url = link_for(pid) if pid else base
    title = _esc(headline or "Your website proposal")
    desc = _esc((personal_line or "A plan built on your evidence, not a template.")[:150])
    return (
        f'<meta property="og:type" content="website">\n'
        f'<meta property="og:title" content="{title}">\n'
        f'<meta property="og:description" content="{desc}">\n'
        f'<meta property="og:image" content="{_esc(img_url)}">\n'
        f'<meta property="og:image:width" content="1200">\n'
        f'<meta property="og:image:height" content="630">\n'
        f'<meta property="og:url" content="{_esc(prop_url)}">\n'
        f'<meta name="twitter:card" content="summary_large_image">\n'
        f'<meta name="twitter:title" content="{title}">\n'
        f'<meta name="twitter:description" content="{desc}">\n'
        f'<meta name="twitter:image" content="{_esc(img_url)}">'
    )


def render(slots: dict, contact: dict, tier_key: str, niche: str, mock_link: str = "",
           site: dict | None = None, pid: str = "", version: int = 1, prior: dict | None = None,
           utm_qs: str = "") -> str:
    t = PRICING[tier_key]
    tpl = _fill_owner(TEMPLATE.read_text())
    site = site or {}
    faults = slots.get("faults") or FALLBACK_FAULTS
    faults = faults[:5]
    faults_html = "".join(
        f'<div class="fault"><div class="fno">{i+1}</div><div><b>{_esc(humanize(str(f.get("t") or "")))}</b>'
        f'<p>{_esc(humanize(str(f.get("p") or "")))}</p>{_severity_bar(f, i, len(faults))}</div></div>'
        for i, f in enumerate(faults))
    scope_html = "".join(f"<li>{_esc(humanize(str(s)))}</li>" for s in (slots.get("scope") or [t["desc"]])[:7])
    faq_items = (slots.get("faq") or [])[:3]
    faq_html = "".join(
        f'<div class="qa"><button type="button" class="q"><span>{_esc(humanize(str(f.get("q") or "")))}</span>'
        f'<span class="car">▾</span></button><div class="a"><p>{_esc(humanize(str(f.get("a") or "")))}</p></div></div>'
        for f in faq_items)
    if not faq_html:
        faq_html = ('<div class="qa"><button type="button" class="q"><span>Is this a template?</span>'
                    '<span class="car">▾</span></button><div class="a"><p>No. Built for you, on evidence '
                    'from your market. You saw the diagnosis above; templates can\'t do that.</p></div></div>')
    care = CARE["growth"]
    care_row = ("" if tier_key in ("webfix",) else
                f'<div class="row"><div class="what"><b>{care["name"]} (optional)</b>'
                f'<span>Updates, edits, backups, monthly report. Cancel anytime.</span></div>'
                f'<div class="price">${care["price"]}/mo</div></div>')
    price = t["price"]
    deposit_num = price // 2
    expires_dt = datetime.now() + timedelta(days=14)

    headline, ab_variant = _headline_variant(slots, niche, pid)
    utm_qs = utm_qs or _utm_qs(slots)
    personal_line = humanize(slots.get("personal_line") or "")

    mock_block = ("" if not mock_link else
        '<section id="mock"><h2>Stop imagining it</h2>'
        '<p style="color:var(--sub);margin-bottom:18px">We built the concept. This is your homepage, rebuilt: your services, your phone number, your area. Two minutes, on any device.</p>'
        f'<div style="border:1px solid var(--line);border-radius:16px;overflow:hidden;background:#fff;box-shadow:0 10px 34px rgba(22,24,29,.07)">'
        '<div style="display:flex;gap:6px;padding:10px 14px;border-bottom:1px solid var(--line);background:#fafaf8">'
        '<span style="width:10px;height:10px;border-radius:50%;background:#ff5f57"></span>'
        '<span style="width:10px;height:10px;border-radius:50%;background:#febc2e"></span>'
        '<span style="width:10px;height:10px;border-radius:50%;background:#28c840"></span></div>'
        f'<div style="height:340px;overflow:hidden;position:relative"><iframe src="{mock_link}" loading="lazy" '
        'style="width:200%;height:680px;transform:scale(.5);transform-origin:0 0;border:0;pointer-events:none"></iframe>'
        f'<a href="{mock_link}" style="position:absolute;inset:0"></a></div></div>'
        f'<div style="text-align:center;margin-top:18px"><a class="btn" href="{mock_link}" style="font-size:15px;padding:13px 26px">Open your homepage concept →</a></div>'
        '</section>')
    # B21: before/after slider is additive, only when we actually fetched their real site
    mock_block += _before_after_html(site, mock_link) if (site.get("url") and mock_link) else ""

    fill = {
        "date": datetime.now().strftime("%B %d, %Y"),
        "name": _esc(contact.get("name") or "there"),
        "company": _esc((contact.get("company") if contact.get("company") and contact.get("company") != contact.get("name") else "") or "your business"),
        "headline": _esc(headline),
        "personal_line": _esc(personal_line),
        "diagnosis_title": _esc(slots.get("diagnosis_title") or "What's costing you"),
        "og_tags": _og_tags_html(pid, headline, personal_line),  # B22, see og_shots.py + contract in status-ogshots.md
        "faults": faults_html,
        "scope": scope_html,
        "faq": faq_html,
        "tier_name": t["name"], "tier_desc": t["desc"],
        "price": f"${price:,}", "deposit": f"${deposit_num:,}", "deposit_num": str(deposit_num),
        "care_row": care_row, "days": str(t["days"]),
        "cta_head": _esc(humanize(slots.get("cta_head") or "Want it handled?")),
        "book_url": BOOK_URL,
        "expires": expires_dt.strftime("%B %d, %Y"),
        "expires_iso": expires_dt.strftime("%Y-%m-%d"),
        "roi_section": _roi_section(slots, tier_key, niche),
        "mock_section": mock_block,
        "version_banner": _version_banner_html(prior or {}, version),
        "testimonial": _testimonial_html(),
        "utm_qs": utm_qs,
        "public_prop_url": link_for(pid) if pid else "",
        # cta_buttons intentionally NOT in this dict: build() fills it after links exist
    }
    for k, v in fill.items():
        tpl = tpl.replace("{{%s}}" % k, v)
    if ab_variant:
        print(f"  A/B header: variant {ab_variant}")
    return tpl


def _roi_section(slots: dict, tier_key: str, niche: str) -> str:
    """Interactive payback widget: their job value slider vs the site price."""
    price = PRICING[tier_key]["price"]
    try:
        avg = int(slots.get("avg_job") or 0)
    except (ValueError, TypeError):
        avg = 0
    if not 20 <= avg <= 20000:
        nl = (niche or "").lower()
        if any(k in nl for k in ("medspa", "med spa", "aesthetic", "spa", "clinic", "inject")):
            avg = 450   # niche book: tox visit $300-600; conservative midpoint
        elif "hvac" in nl or "plumb" in nl:
            avg = 400
        else:
            avg = 250
    return f"""
  <section id="roi"><h2>Do the math yourself</h2>
    <div style="background:var(--card);border:1px solid var(--line);border-radius:16px;padding:26px">
      <div style="font-size:14.5px;color:var(--sub);margin-bottom:6px">What's an average job worth to you?</div>
      <div style="display:flex;align-items:center;gap:16px">
        <input id="jv" type="range" min="50" max="5000" step="50" value="{avg}"
          style="flex:1;accent-color:var(--gold)" oninput="roi()">
        <div id="jvn" style="font-weight:800;font-size:22px;min-width:90px;text-align:right">${avg}</div>
      </div>
      <div style="display:flex;gap:14px;margin-top:20px;flex-wrap:wrap">
        <div style="flex:1;min-width:150px;background:var(--bg);border-radius:12px;padding:16px;text-align:center">
          <div id="payback" style="font-size:26px;font-weight:800;color:var(--gold2)">-</div>
          <div style="font-size:12.5px;color:var(--sub)">jobs to pay for the whole site</div></div>
        <div style="flex:1;min-width:150px;background:var(--bg);border-radius:12px;padding:16px;text-align:center">
          <div id="yearly" style="font-size:26px;font-weight:800;color:var(--gold2)">-</div>
          <div style="font-size:12.5px;color:var(--sub)">a year, if it brings ONE extra job a month</div></div>
      </div>
      <script>function roi(){{var v=+document.getElementById('jv').value;
        document.getElementById('jvn').textContent='$'+v.toLocaleString();
        document.getElementById('payback').textContent=Math.max(1,Math.ceil({price}/v));
        document.getElementById('yearly').textContent='$'+(v*12).toLocaleString()}}roi();</script>
    </div>
  </section>"""


def _pay_link(tier_key: str) -> str:
    return str((planner._config().get("payment_links") or {}).get(tier_key) or "")


# ---- C44: delivery checklist items, one per scope line ----
def _checklist_items_html(scope_lines: list) -> str:
    items = []
    for i, s in enumerate(scope_lines[:7]):
        cid = f"chk{i}"
        items.append(f'<li><input type="checkbox" id="{cid}" value="{_esc(s)[:80]}">'
                     f'<label for="{cid}">{_esc(s)}</label></li>')
    return "".join(items)


# ---- C45: milestone strip, real computed dates. state: done (deposit, since the
# agreement implies a deposit is about to happen) / now (upcoming next step) / plain ----
def _milestone_strip_html(days: int) -> str:
    today = datetime.now()
    preview = today + timedelta(days=3)
    live = today + timedelta(days=days)
    steps = [
        ("done", "DEPOSIT", "Books your build slot", today),
        ("now", "DAY 3 PREVIEW", "You see it working", preview),
        ("", "LIVE", "On your domain", live),
    ]
    out = []
    for cls, label, desc, dt in steps:
        out.append(f'<div class="{cls}"><div class="dot"></div><b>{label}</b><span>{desc}</span>'
                    f'<div class="when">{dt.strftime("%b %d")}</div></div>')
    return "".join(out)


# ---- C48: multi-currency formatting guard. Everything is still priced/charged in
# USD per the brief; this only adds a clarifying "(USD)" note when the contact looks
# non-US, so a CAD/GBP prospect never misreads $3,500 as their home currency. ----
def _currency_note(contact: dict) -> str:
    phone = re.sub(r"\D", "", contact.get("phone") or "")
    country = str(contact.get("country") or "").upper()
    non_us = country not in ("", "US", "USA") or (phone.startswith("1") and len(phone) == 11 and phone[1] in "0")
    # crude but honest signal: full E.164 with a non-NANP-looking leading digit group
    looks_intl = bool(re.match(r"^(44|61|64|33|49|34|39|31)", phone)) if phone else False
    return " (USD)" if (non_us or looks_intl) else ""


def render_agreement(slots: dict, contact: dict, tier_key: str, pid: str) -> str:
    t = PRICING[tier_key]
    tpl = (ROOT / "agents" / "templates" / "agreement.html").read_text()
    pay = _pay_link(tier_key)
    scope_lines = (slots.get("scope") or [t["desc"]])[:7]
    scope = "".join(f"<li>{_esc(x)}</li>" for x in scope_lines)
    fill = {
        "date": datetime.now().strftime("%B %d, %Y"),
        "name": _esc(contact.get("name") or "Client"),
        "company": _esc(contact.get("company") or contact.get("name") or "your business"),
        "tier_name": t["name"], "tier_desc": t["desc"], "scope": scope,
        "price": f"${t['price']:,}", "deposit": f"${t['price'] // 2:,}",
        "days": str(t["days"]), "pid": pid,
        "pay_button": (f'<a class="paybtn" href="{_esc(pay)}">Pay the {"$%s" % format(t["price"] // 2, ",")} deposit now</a>'
                       if pay else '<span style="color:var(--sub);font-size:13px">Deposit link lands in your inbox within the hour.</span>'),
        "checklist_items": _checklist_items_html(scope_lines),               # C44
        "milestone_strip": _milestone_strip_html(t["days"]),                  # C45
        "currency_note": _currency_note(contact),                             # C48
        # C46: late-payment nudge needs a delivery+7d-unpaid signal that doesn't exist
        # yet (a scheduled job, not template rendering); always empty today. See
        # status-templates.md contract notes.
        "nudge_banner": "",
        # C50: W9/insurance attach links; empty until a real upload mechanism exists
        # to populate contact/config doc URLs. See status-templates.md.
        "attachments_row": "",
    }
    for k, v in fill.items():
        tpl = tpl.replace("{{%s}}" % k, v)
    return tpl


# ---- queue ----
def sig_for(pid: str) -> str:
    return hmac.new(sign_secret().encode(), f"prop:{pid}".encode(), hashlib.sha256).hexdigest()[:24]


def link_for(pid: str) -> str:
    base = (planner._config().get("public_base_url") or "http://127.0.0.1:8765").rstrip("/")
    return f"{base}/prop/{pid}?sig={sig_for(pid)}"


# ---- D6: bot/prefetch open filtering. Email scanners and link-preview fetchers hit
# /prop links the moment a send lands; counting those as opens fires
# proposal_open_pulse's "call now, do not email" push on a machine, not a human.
# The opens increment itself lives in server.py's prop_view (GET /prop/{pid}), which
# should gate on this helper; defined here so it's testable and server.py's change
# stays a two-line wiring diff. ----
BOT_UA_MARKERS = ("headlesschrome", "phantomjs", "bot", "crawler", "spider", "preview",
                  "curl", "wget", "python-requests", "python-urllib", "go-http-client",
                  "facebookexternalhit")


def is_bot_open(user_agent: str, sent_at: str = "", now: str = "") -> bool:
    """True when a /prop open should NOT count as a human open.

    Order matters: GoogleImageProxy is exempted FIRST because Gmail proxies every
    image through it, so that UA is exactly what a REAL Gmail open looks like.
    Then known scanner/headless/preview UAs ("bot" also catches Slackbot/Googlebot/
    Discordbot etc), then any open within 60 seconds of the send, which is the
    scanner-prefetch window, not a human reading their email that fast."""
    ua = (user_agent or "").lower()
    if "googleimageproxy" in ua:
        return False  # Gmail's image proxy = a real human opened the email
    if any(m in ua for m in BOT_UA_MARKERS):
        return True
    if sent_at:
        from datetime import datetime as _dt
        try:
            sent = _dt.fromisoformat(sent_at)
            cur = _dt.fromisoformat(now) if now else _dt.now(sent.tzinfo)
            if 0 <= (cur - sent).total_seconds() < 60:
                return True
        except (ValueError, TypeError):
            pass
    return False


def _queue(rec: dict):
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    # lock the append: the beacon (server process) and proposal_timers/og_shots (agent
    # subprocess) both write this file; without the lock a torn line is possible once
    # records grow past PIPE_BUF, and load_queue's last-write-wins can drop an update.
    from store_lib import _flock
    with _flock(QUEUE), QUEUE.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def patch(pid: str, delta: dict) -> dict | None:
    """Atomic field update: read the latest record + merge delta + append, ALL under the
    queue lock, so two processes racing on the same pid can't clobber each other's fields
    (2026-07-05 seams audit: an unlocked read-modify-write let a beacon write drop a
    *_drafted flag, re-firing a follow-up draft). Use this instead of load_queue()+save()
    whenever a background writer might touch the same pid concurrently."""
    from store_lib import _flock
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    with _flock(QUEUE):
        cur = None
        if QUEUE.exists():
            for line in QUEUE.read_text().splitlines():
                try:
                    r = json.loads(line)
                except (ValueError, json.JSONDecodeError):
                    continue
                if r.get("id") == pid:
                    cur = r
        if cur is None:
            return None
        merged = {**cur, **delta}
        with QUEUE.open("a") as f:
            f.write(json.dumps(merged, ensure_ascii=False) + "\n")
        return merged


def claim(pid: str, from_status: str = "staged", to_status: str = "sending") -> dict | None:
    """Locked compare-and-swap so a proposal can't be double-sent: under the queue lock,
    read the latest record, and ONLY if it is still `from_status` append it flipped to
    `to_status` and return it. A concurrent send (double-tap or threadpool race) sees the
    already-flipped status and gets None -> it must not send. (2026-07-05 audit finding #1.)"""
    from store_lib import _flock
    with _flock(QUEUE):
        cur = None
        if QUEUE.exists():
            for line in QUEUE.read_text().splitlines():
                try:
                    r = json.loads(line)
                except (ValueError, json.JSONDecodeError):
                    continue
                if r.get("id") == pid:
                    cur = r
        if cur is None or cur.get("status") != from_status:
            return None
        claimed = {**cur, "status": to_status}
        with QUEUE.open("a") as f:
            f.write(json.dumps(claimed, ensure_ascii=False) + "\n")
        return claimed


def load_queue() -> list[dict]:
    if not QUEUE.exists():
        return []
    by_id, order = {}, []
    for line in QUEUE.read_text().splitlines():
        try:
            r = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if r.get("id"):
            if r["id"] not in by_id:
                order.append(r["id"])
            by_id[r["id"]] = r
    return [by_id[i] for i in order]


def save(rec: dict):
    _queue(rec)


# ---- main build ----

def _strip_price_timeline(body: str) -> str:
    """Excise any dollar price or delivery-timeline claim from the cover email, WITHOUT
    disturbing paragraph breaks or the booking-link line. The proposal PAGE owns price
    and timeline; an email stating a different number reads as bait-and-switch the moment
    the prospect opens the priced proposal. (2026-07-05: caught a live $1K-email vs
    $3,500-proposal mismatch on Client A. Processes line-by-line so newlines/signature
    survive; only substitutes the offending clause, never rejoins the whole body.)"""
    import re as _re

    def fix_line(line: str) -> str:
        low = line.lower()
        if "/book" in low or "[OWNER_SITE]" in low:
            return line  # never touch the booking CTA line (matched by URL, not the
            # word "book" -- "booking path" must NOT be treated as the CTA line)
        # broadened (red-team F1 #3): the guard used to require a literal $, so "1,200 dollars",
        # "USD 2500", "two thousand dollars" all leaked a price the proposal page contradicts.
        _spelled = (r"(\$\s?\d|\b\d[\d,]*\s*(?:dollars?|usd|bucks|k)\b|\b(?:usd|us\$)\s*\d|"
                    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
                    r"twenty|thirty|forty|fifty|hundred|thousand)[\w\s-]*?\b(?:dollars?|bucks|k)\b)")
        if not (_re.search(_spelled, low) or _re.search(r"\b\d+\s*(?:business\s*)?days?\b", low)):
            return line
        s2 = _re.sub(r"\$\s?\d[\d,]*K?\b(?:\s*(?:flat|/mo|/month))?", "", line)
        s2 = _re.sub(r"\b\d[\d,]*\s*(?:dollars?|usd|bucks|k)\b", "", s2, flags=_re.I)  # number-then-unit
        s2 = _re.sub(r"\b(?:usd|us\$)\s*\d[\d,]*\b", "", s2, flags=_re.I)  # USD-then-number
        s2 = _re.sub(r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|"
                     r"thirty|forty|fifty|hundred|thousand)(?:[\s-]+(?:hundred|thousand|and))*"
                     r"\s+(?:dollars?|bucks)\b", "", s2, flags=_re.I)  # spelled-out
        s2 = _re.sub(r",?\s*live in \d+\s*(?:business\s*)?days?", "", s2, flags=_re.I)
        s2 = _re.sub(r"\b(?:in\s+)?\d+\s*(?:business\s*)?days?\b", "", s2, flags=_re.I)
        s2 = _re.sub(r"\s{2,}", " ", s2)
        s2 = _re.sub(r"\s+([.,])", r"\1", s2)
        s2 = _re.sub(r"[.,]\s*([.,])", r"\1", s2)
        s2 = _re.sub(r"\.\s*\.", ".", s2)
        return s2.strip()

    return "\n".join(fix_line(ln) for ln in body.split("\n"))

def build(email: str = "", name: str = "", cid: str = "", url: str = "", niche: str = "",
          tier: str = "", dry: bool = False, no_llm: bool = False, template: str = "",
          utm_source: str = "", utm_medium: str = "", utm_campaign: str = "") -> dict:
    contact = {} if dry else find_contact(email, name, cid)
    if not contact:
        contact = {"id": cid, "name": name or (email.split("@")[0] if email else "there"),
                   "company": name or "", "email": email, "website": url}
    site_url = url or contact.get("website") or ""
    site = fetch_site(site_url) if site_url else {}
    has_site = _has_site(site_url, site)
    print(f"contact: {contact.get('name')} | site: {site.get('url') or 'NONE'} ({len(site.get('text') or '')} chars)")

    # A16: detect a prior proposal for this same contact BEFORE minting the new pid,
    # so the version banner can reference it.
    prior = _prior_version(contact.get("id") or "") if contact.get("id") else {}
    version = int(prior.get("version") or 1) + 1 if prior else 1

    slots = {} if no_llm else generate(contact, niche, site)
    if utm_source:
        slots["utm_source"] = utm_source
    if utm_medium:
        slots["utm_medium"] = utm_medium
    if utm_campaign:
        slots["utm_campaign"] = utm_campaign
    tier_key = route(niche, tier, faults_n=len(slots.get("faults") or []), has_site=has_site)
    bump = int(slots.get("tier_bump") or 0)
    if bump and not tier:
        order = ["webfix", "landing", "standard", "booking", "whiteglove"]
        if tier_key in order:
            i = max(0, min(len(order) - 1, order.index(tier_key) + bump))
            print(f"  tier bump {bump:+d}: {tier_key} -> {order[i]} ({slots.get('bump_reason','')})")
            tier_key = order[i]

    pid = "prop_" + new_id((contact.get("email") or contact.get("name") or "") + now_iso()).split("_", 1)[1]
    OUT.mkdir(parents=True, exist_ok=True)
    mock_link = ""
    try:
        pack = mockup_template_name(niche, template)
        (OUT / f"{pid}.mock.html").write_text(render_mockup(slots, contact, niche, site, tier_key, template))
        # relative: the mock always lives on the same host the proposal is being read from
        mock_link = f"/mock/{pid}?sig={sig_for(pid)}"
        print(f"  mockup pack: {pack}")
    except Exception as e:  # noqa: BLE001
        print(f"  mockup render failed (proposal continues without it): {e}")
    agree_rel = f"/agree/{pid}?sig={sig_for(pid)}"
    (OUT / f"{pid}.agree.html").write_text(render_agreement(slots, contact, tier_key, pid))
    pay = _pay_link(tier_key)
    utm_qs = _utm_qs(slots)
    ctas = (f'<div style="display:flex;gap:12px;justify-content:center;flex-wrap:wrap;margin-top:14px">'
            f'<a href="{agree_rel}" style="color:var(--gold2);font-size:14px;font-weight:600">Read the one-page agreement →</a>'
            + (f'<a href="{_esc(pay)}" style="color:var(--gold2);font-size:14px;font-weight:600">Reserve your slot: ${PRICING[tier_key]["price"] // 2:,} deposit →</a>' if pay else "")
            + '</div>')
    html_out = render(slots, contact, tier_key, niche, mock_link, site, pid, version, prior, utm_qs).replace('{{cta_buttons}}', ctas)
    (OUT / f"{pid}.html").write_text(html_out)

    # A13: same pure computation render() used internally, mirrored here so the
    # variant actually served can be recorded on the queue record (not just printed).
    _, ab_variant = _headline_variant(slots, niche, pid)

    link = link_for(pid)
    body = humanize((slots.get("email_body") or
                     f"Put together a short plan for {contact.get('company') or 'your site'}. "
                     f"Five specific things costing you customers and exactly what I'd do about them: LINK\n"
                     f"If it reads right, grab 15 minutes with me: {BOOK_URL}\n\n[OWNER]").replace("LINK", link))
    body = _strip_price_timeline(body)  # email must never contradict the proposal's price/timeline
    if link not in body:
        body = body.rstrip() + f"\n\nThe plan: {link}"
    rec = {"id": pid, "status": "staged", "created": now_iso(),
           "contact_id": contact.get("id") or "", "name": contact.get("name") or "",
           "company": contact.get("company") or "", "email": contact.get("email") or email,
           "niche": niche, "tier": tier_key, "price": PRICING[tier_key]["price"],
           "site_url": site.get("url") or "", "link": link, "version": version,
           "email_subject": humanize(slots.get("email_subject") or "a plan for your site"),
           "email_draft": body}
    if ab_variant:
        rec["ab_variant"] = ab_variant
    if version > 1:
        rec["email_subject"] = "updated: " + rec["email_subject"]
        print(f"  version {version} (prior: {prior.get('id')})")
    _queue(rec)
    print(f"staged {pid}: {PRICING[tier_key]['name']} ${PRICING[tier_key]['price']:,} -> {link}")
    return rec


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--contact-id", default="")
    ap.add_argument("--url", default="")
    ap.add_argument("--niche", default="")
    ap.add_argument("--tier", default="", choices=[""] + list(PRICING))
    ap.add_argument("--dry", action="store_true", help="skip GHL lookup")
    ap.add_argument("--no-llm", action="store_true", help="template smoke test")
    ap.add_argument("--template", default="", choices=["", "mockup.html", "mockup-bold.html", "mockup-studio.html"],
                     help="B19/B20: override the auto-picked mockup pack")
    ap.add_argument("--utm-source", default="")
    ap.add_argument("--utm-medium", default="")
    ap.add_argument("--utm-campaign", default="")
    a = ap.parse_args()
    if not (a.email or a.name or a.contact_id or a.url):
        ap.error("need --email, --name, --contact-id or --url")
    build(a.email, a.name, a.contact_id, a.url, a.niche, a.tier, a.dry, a.no_llm, a.template,
          a.utm_source, a.utm_medium, a.utm_campaign)
