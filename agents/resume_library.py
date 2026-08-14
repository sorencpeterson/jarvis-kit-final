#!/usr/bin/env python3
"""Pre-built resume variants, picked deterministically instead of written per job.

Mechanism contributed by Austin Apon, who built it on a copy of this kit and measured
the saving. Ported here with the role families generalised and the variant text
generated from whatever resume the current owner actually has.

WHY THIS EXISTS. agents/resume_tailor.py writes a genuinely better resume per job, at
one Sonnet call per application (~27,000 tokens measured). Against a 400k daily budget
that is roughly half the day's tokens spent before a single form is filled. This module
trades the per-job call for a library built ONCE: the same two blocks resume_tailor is
allowed to rewrite (tagline + summary), pre-authored per role family and seniority,
then picked by keyword score at apply time. Per-application cost drops to zero and
stays there.

WHAT IT IS NOT. It is not several different careers. Every variant states the SAME
facts from store/resume-draft.html and only changes which of them leads. Bullets,
employers, dates, education and skills are never touched, exactly as in resume_tailor.
Every generated variant is checked by resume_tailor.validate() against
allowed_numbers() from the real resume, so the anti-fabrication gates that guard the
LLM path guard this one too: no invented numbers, no tools the owner lacks, no
em-dashes, no cliches. A variant that fails is discarded, not repaired.

THE MATCHER IS DELIBERATELY NOT AN LLM. Asking a model to choose among N options costs
about 20k tokens per application, which spends most of what this module saves. Mapping
a job title to a role family is keyword scoring: free, deterministic, inspectable. On
a tie or no match it falls back to the generalist rather than guessing.

WHY THE VARIANTS ARE GENERATED, NOT SHIPPED. Hardcoding one person's taglines would
hand every other owner a stranger's career. The families below are keyword data only.
The words come from your own resume, once:

    .venv/bin/python agents/resume_library.py --build      # one-time, ~1 call/family
    .venv/bin/python agents/resume_library.py              # inspect the library
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

SOURCE = ROOT / "store" / "resume-draft.html"
# Deliberately a SUBdirectory of resume_tailor.OUT_DIR: render_pdf() refuses any path
# resolving outside it, and that guard is worth keeping intact rather than widening for
# a second caller. Variant filenames are family keys, never job ids, so they cannot
# collide with the per-job tailored files living alongside them.
OUTDIR = ROOT / "store" / "resume_tailored" / "variants"
LIBRARY = ROOT / "store" / "resume_variants.json"

# Seniority tiers, cheapest signal first. A variant's `level` is what the matcher tries
# to line up against the job title's own tier language.
_LEVEL_WORDS = {
    "associate": ("associate", "assistant", "junior", "jr", "entry", "coordinator",
                  "specialist", "executive"),
    "manager": ("manager", "lead", "supervisor"),
    "senior": ("senior", "sr", "principal", "staff"),
    "director": ("director", "head", "vp", "vice president", "chief"),
}

# Role families as KEYWORD DATA ONLY. No career facts here: those come from the owner's
# own resume at build time. Add a family and rebuild if you hunt somewhere not listed.
FAMILIES = {
    "generalist": ("marketing", "growth"),
    "seo": ("seo", "search engine", "organic", "content marketing"),
    "paid_media": ("paid media", "ppc", "sem", "google ads", "paid search",
                   "performance marketing", "media buyer"),
    "lifecycle": ("lifecycle", "email marketing", "crm marketing", "retention",
                  "marketing automation"),
    "demand_gen": ("demand generation", "demand gen", "growth marketing",
                   "acquisition", "b2b marketing"),
    "marketing_ops": ("marketing operations", "marketing ops", "revops",
                      "revenue operations", "martech"),
    "web": ("web", "website", "wordpress", "webmaster", "front end", "cro",
            "conversion rate"),
    "content": ("content", "copywriter", "editorial", "brand", "social media"),
    "analytics": ("analytics", "data", "reporting", "insights", "measurement"),
    "product_marketing": ("product marketing", "pmm", "go to market", "positioning"),
    "ecommerce": ("ecommerce", "e-commerce", "shopify", "dtc", "retail media"),
}

LEVELS = ("associate", "manager", "senior", "director")
FALLBACK = "generalist_manager"


def _key(family: str, level: str) -> str:
    return f"{family}_{level}"


def load() -> list[dict]:
    """The built library, or [] when --build has not been run."""
    try:
        data = json.loads(LIBRARY.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for v in data.get("variants", []):
        if v.get("key") and v.get("tagline") and v.get("summary"):
            v["keywords"] = tuple(v.get("keywords") or ())
            out.append(v)
    return out


def _level_of(title: str) -> str | None:
    """Seniority tier a job title advertises, or None when it says nothing."""
    t = f" {(title or '').lower()} "
    for level in ("director", "senior", "manager", "associate"):
        for w in _LEVEL_WORDS[level]:
            if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", t):
                return level
    return None


def score(variant: dict, title: str, query: str = "") -> int:
    """How well one variant fits one job. Pure keyword overlap, no model call.

    Family keywords carry the weight because they decide WHICH resume is sent. The
    seniority match is a smaller tiebreak: a manager-tier summary on a senior posting
    is a much cheaper mistake than an SEO resume on a paid media role.
    """
    hay = f" {(title or '').lower()} {(query or '').lower()} "
    s = 0
    for kw in variant.get("keywords", ()):
        if kw in hay:
            # longer keyword = more specific ("paid media manager" beats "marketing")
            s += 10 + len(kw.split())
    lvl = _level_of(title)
    if lvl and lvl == variant.get("level"):
        s += 6
    elif lvl and lvl != variant.get("level"):
        s -= 2
    return s


def match(job: dict, variants: list[dict] | None = None) -> dict | None:
    """Best variant for a job. Falls back to the generalist rather than guessing.

    The generalist family is deliberately EXCLUDED from the scoring contest and used
    only when nothing specific matches. Its keywords ("marketing", "growth") are
    common enough to appear in almost any posting, so letting it compete meant two
    generic word hits outscored one precise phrase: "Paid Media Manager, Growth
    Marketing" scored generalist 22 against paid_media 12 and sent the wrong resume.
    Specific-or-fallback is what the docstring always claimed; now it is what the
    code does.
    """
    vs = variants if variants is not None else load()
    if not vs:
        return None
    title, query = job.get("title") or "", job.get("query") or ""
    best, best_s = None, 0
    for v in vs:
        if v.get("family") == "generalist":
            continue
        s = score(v, title, query)
        if s > best_s:
            best, best_s = v, s
    if best:
        return best
    # nothing specific: the generalist at the tier the title advertises, if we have it
    by_key = {v["key"]: v for v in vs}
    lvl = _level_of(title)
    if lvl and _key("generalist", lvl) in by_key:
        return by_key[_key("generalist", lvl)]
    return by_key.get(FALLBACK) or next(
        (v for v in vs if v.get("family") == "generalist"), vs[0])


def pdf_path(key: str) -> Path:
    return OUTDIR / f"{key}.pdf"


def resume_for(job: dict) -> tuple[str, Path]:
    """(variant_key, resume PDF) for this job.

    Falls back to the static store/resume.pdf whenever the matched variant has not been
    rendered, so a half-built library degrades to today's behaviour instead of
    uploading nothing. Same never-break contract resume_tailor commits to.
    """
    static = ROOT / "store" / "resume.pdf"
    v = match(job)
    if not v:
        return "static", static
    p = pdf_path(v["key"])
    try:
        if p.exists() and p.stat().st_size > 20000:
            return v["key"], p
    except OSError:
        pass
    return v["key"], static


def resume_for_mode(job: dict) -> tuple[str, str]:
    """(what_was_chosen, absolute resume path), honouring config resume_mode.

    ONE place decides which file goes out, so the per-job tailoring, the variant
    library, and what the apply path actually uploads can never disagree.
      library (default when built)  the matched variant
      llm                           resume_tailor's per-job PDF
      off                           always the static resume
    """
    static = ROOT / "store" / "resume.pdf"
    try:
        mode = json.loads((ROOT / "store" / "config.json").read_text()).get("resume_mode")
    except (OSError, json.JSONDecodeError):
        mode = None
    if mode is None:
        mode = "library" if load() else "llm"
    if mode == "off":
        return "static", str(static)
    if mode == "llm":
        import resume_tailor
        p = resume_tailor.OUT_DIR / f"{resume_tailor.safe_name(job.get('id') or '')}.pdf"
        try:
            if p.exists() and p.stat().st_size > 20000:
                return "tailored", str(p)
        except OSError:
            pass
        return "static", str(static)
    key, p = resume_for(job)
    return (f"variant:{key}" if p.parent == OUTDIR else "static"), str(p)


def build(only_family: str = "", force: bool = False) -> int:
    """Write the variant library from the owner's OWN resume. One LLM call per family.

    This is the only LLM spend in this module and it happens once, not per application.
    Every generated block goes through resume_tailor's existing anti-fabrication gates;
    anything that fails them is dropped rather than patched, because a resume that
    invents a number is worse than a generic one.
    """
    import resume_tailor
    try:
        template = SOURCE.read_text()
    except OSError as e:
        print(f"resume_library: no resume source ({e}). Put your real resume in "
              f"{SOURCE} first.")
        return 1
    m_tag, m_sum = resume_tailor._TAG_RE.search(template), resume_tailor._SUM_RE.search(template)
    if not (m_tag and m_sum):
        print("resume_library: resume source is missing the tagline/summary anchors")
        return 1
    facts = resume_tailor.resume_text(template)
    cur_tag = re.sub(r"\s+", " ", resume_tailor._strip_tags(m_tag.group(2))).strip()
    cur_sum = re.sub(r"\s+", " ", resume_tailor._strip_tags(m_sum.group(2))).strip()

    have = {v["key"]: v for v in load()}
    out, made, kept, failed = [], 0, 0, 0
    for family, keywords in FAMILIES.items():
        if only_family and family != only_family:
            out.extend(v for k, v in have.items() if k.startswith(family + "_"))
            continue
        for level in LEVELS:
            key = _key(family, level)
            if key in have and not force:
                out.append(have[key])
                kept += 1
                continue
            pseudo = {"title": f"{family.replace('_', ' ')} {level}",
                      "query": " ".join(keywords)}
            blocks, why = resume_tailor.tailor_blocks(pseudo, facts, cur_tag, cur_sum)
            if not blocks:
                print(f"  {key}: rejected ({why})")
                failed += 1
                continue
            out.append({"key": key, "family": family, "level": level,
                        "keywords": list(keywords),
                        "tagline": blocks[0], "summary": blocks[1]})
            made += 1
            print(f"  {key}: built")
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    tmp = LIBRARY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"variants": out}, indent=1))
    tmp.replace(LIBRARY)
    print(f"resume_library: {made} built, {kept} kept, {failed} rejected -> {LIBRARY}")
    if made:
        print("  Next: render them to PDFs with agents/resume_library.py --render")
    return 0


def render(force: bool = False) -> int:
    """Render each variant to a one-page PDF. No LLM calls at all."""
    import resume_tailor
    if not resume_tailor._renderer_available():
        print("resume_library: no PDF renderer (need node + playwright-project, or "
              "any Chrome/Chromium). Nothing rendered.")
        return 1
    try:
        template = SOURCE.read_text()
    except OSError:
        print("resume_library: no resume source")
        return 1
    OUTDIR.mkdir(parents=True, exist_ok=True)
    done = skipped = failed = 0
    for v in load():
        p = pdf_path(v["key"])
        if p.exists() and not force:
            skipped += 1
            continue
        doc = resume_tailor.substitute(template, v["tagline"], v["summary"])
        if doc and resume_tailor.render_pdf(doc, p):
            done += 1
        else:
            failed += 1
    print(f"resume_library: {done} rendered, {skipped} already present, {failed} failed")
    return 0


def validate_all() -> list[str]:
    """Re-check every stored variant against the live resume's allowed facts.

    Worth running after editing the resume: a variant written against the OLD facts can
    quietly become a fabrication when a number changes.
    """
    import resume_tailor
    try:
        facts = resume_tailor.resume_text(SOURCE.read_text())
    except OSError:
        return ["no resume source"]
    allowed = resume_tailor.allowed_numbers(facts)
    bad = []
    for v in load():
        why = resume_tailor.validate(v["tagline"], v["summary"], allowed)
        if why:
            bad.append(f"{v['key']}: {why}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description="Pre-built resume variants")
    ap.add_argument("--build", action="store_true", help="write the library (uses the LLM once per family)")
    ap.add_argument("--render", action="store_true", help="render variants to PDFs (no LLM)")
    ap.add_argument("--family", default="", help="only this family")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.build:
        return build(only_family=a.family, force=a.force)
    if a.render:
        return render(force=a.force)

    vs = load()
    if not vs:
        print("\n  No variant library yet. Build it once:\n"
              "    .venv/bin/python agents/resume_library.py --build\n"
              "    .venv/bin/python agents/resume_library.py --render\n")
        return 0
    rendered = sum(1 for v in vs if pdf_path(v["key"]).exists())
    print(f"\n  {len(vs)} variant(s), {rendered} rendered, "
          f"{len(FAMILIES)} families x {len(LEVELS)} levels\n")
    bad = validate_all()
    if bad:
        print("  FAILING the anti-fabrication gates (rebuild these):")
        for b in bad:
            print(f"    {b}")
    else:
        print("  All variants pass the anti-fabrication gates.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
