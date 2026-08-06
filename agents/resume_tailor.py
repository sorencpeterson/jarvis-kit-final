#!/usr/bin/env python3
"""Per-job tailored resume PDFs (2026-07-12, interview-rate push).

Every application currently uploads the SAME store/resume.pdf. Recruiters and
ATS keyword screens reward a resume whose headline and summary mirror the role;
this agent produces that, per approved job, without ever touching the facts:

  For each approved/pending job missing store/resume_tailored/<id>.pdf:
    1. LLM (feature "tailor" -> Sonnet) rewrites ONLY two blocks of the v2
       resume source (store/resume-draft.html): the tagline under the name and
       the summary paragraph, re-emphasizing whichever of [OWNER]'s REAL
       strengths this role's own title/query text calls for.
    2. Hard code-side gates reject anything the LLM invented:
       - every numeric token in the output must already exist in the resume
         source (no invented percentages, years, or dollar figures, ever)
       - martech he does NOT have (Salesforce, Marketo, Klaviyo...) is banned
       - em-dashes/en-dashes and AI cover-letter cliches are banned
       - length bounds on both blocks
    3. Render to a one-page PDF via the playwright-project's chromium
       (node subprocess), verify page count == 1 and sane size.
  Any failure at any step -> NO file is written and the apply operator falls
  back to the static store/resume.pdf. This agent can only ever add a better
  option, never break an apply.

Bullets, employers, dates, skills lines, education: NEVER touched. The two
rewritten blocks may only rephrase/reorder what the resume already says.

Attribution: app/server.py's applied callback claims each applied job onto
resume_ab variant "v2-tailored" (tailored file existed) or "v2" (static), so
resume_ab.py's table turns this into a measured interview-rate A/B.

RAILS: writes only under store/resume_tailored/ (plus reading jobs.jsonl and
config). No sends. LLM output is gated, never trusted. Honors config
job_tailor_resume=0 as a kill switch.

Run:  .venv/bin/python agents/resume_tailor.py [--limit N] [--dry-run] [--jobs id1,id2]
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import humanize  # noqa: E402
import jobs  # noqa: E402

TEMPLATE = ROOT / "store" / "resume-draft.html"
OUT_DIR = ROOT / "store" / "resume_tailored"
PW_DIR = Path(os.environ.get("PLAYWRIGHT_DIR") or (ROOT / "playwright-project"))

# the ONLY two blocks the LLM may rewrite; both regexes match exactly once in
# the committed template (test_resume_tailor pins this)
_TAG_RE = re.compile(r'(<div class="tag">)(.*?)(</div>)', re.S)
_SUM_RE = re.compile(r'(<p class="sum">)(.*?)(</p>)', re.S)

# martech/platforms NOT on his resume that LLMs love to slip in. A tailored
# resume claiming Marketo experience is a fabrication; kill it at the gate.
_BANNED_TOOLS = (
    "salesforce", "marketo", "klaviyo", "braze", "pardot", "mailchimp",
    "shopify", "amplitude", "mixpanel", "segment", "iterable", "eloqua",
    "customer.io", "tableau", "power bi", "jira", "asana", "monday.com",
)

# the same AI-cliche net _build_prompt bans for cover letters
_BANNED_PHRASES = (
    "excited", "thrilled", "passionate", "leverage", "leveraging",
    "results-driven", "proven track record", "dynamic", "spearhead",
    "deep dive", "wheelhouse", "synerg",
)


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


def resume_text(template_html: str) -> str:
    """The template's VISIBLE text only: the <style> block is dropped BEFORE
    tag-stripping (bug found 2026-07-12 hunt: CSS numbers like font-size 23pt
    were leaking into the fact whitelist, so an LLM's invented '23% growth'
    would have passed the gate; #111 hex colors, 1.25 line-heights, all of it).
    Entities are unescaped so facts read clean in the prompt."""
    no_style = re.sub(r"<style>.*?</style>", " ", template_html, flags=re.S)
    txt = _strip_tags(no_style)
    txt = txt.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", txt).strip()


def allowed_numbers(template_text: str) -> set[str]:
    """Every numeric token that already appears in the resume's VISIBLE text
    (callers must pass resume_text(), not raw html). LLM output may not
    contain a number outside this set."""
    return set(re.findall(r"\d+(?:\.\d+)?", template_text))


def safe_name(jid: str) -> str:
    """Job ids come from attacker-postable public boards (real store charset
    includes ':', ',', '%', '&'). This is the ONLY spelling allowed to touch
    the filesystem: [A-Za-z0-9._-], length-capped, traversal-proof, PLUS an
    8-hex sha1 suffix of the RAW id (2026-07-13 fix, CX-G2/R2-45, 3rd model to
    confirm: the bare sanitizer was many-to-one -- 'role:a' and 'role?a' both
    collapsed to 'role_a' -- so the 2nd job silently reused the 1st job's PDF
    and got a resume tailored for the WRONG employer). The suffix is
    deterministic (sha1, not random) so re-running the agent still finds the
    file it already rendered for a given id.

    FLAG for the server agent: app/server.py has TWO touch points and only one
    still tracks this function.
      - app/server.py:878-882 (api_jobs_applied-area) calls
        `resume_tailor.safe_name(jid)` directly -- it picks up this hash
        automatically, no server.py edit needed.
      - app/server.py:3676-3683 (`_resume_line`) INLINES the old pre-hash
        regex (`sub(r"[^A-Za-z0-9._-]", "_", ...)[:180].lstrip(".") or "job"`)
        as a byte-identical copy for import-weight reasons. That copy does
        NOT know about the hash suffix added here, so as of this commit it
        computes a DIFFERENT (stale) filename than this function -- its
        tailored-resume existence check will miss every file this agent
        writes from now on. It needs the identical suffix appended:
        `hashlib.sha1((j.get("id") or "").encode()).hexdigest()[:8]`.
      tests/test_resume_tailor.py::test_server_sanitizer_parity still pins
      the BASE spelling (unchanged), not full-string parity."""
    s = re.sub(r"[^A-Za-z0-9._-]", "_", jid or "")[:180]
    s = s.lstrip(".") or "job"
    h = hashlib.sha1((jid or "").encode("utf-8")).hexdigest()[:8]
    return f"{s}_{h}"


def numbers_ok(text: str, allowed: set[str]) -> bool:
    return all(n in allowed for n in re.findall(r"\d+(?:\.\d+)?", text))


def validate(tagline: str, summary: str, allowed: set[str]) -> str | None:
    """Return a rejection reason, or None when the rewrite is safe to ship."""
    if not tagline or not summary:
        return "empty block"
    if not (10 <= len(tagline) <= 90):
        return f"tagline length {len(tagline)}"
    if not (280 <= len(summary) <= 700):
        return f"summary length {len(summary)}"
    both = f"{tagline}\n{summary}"
    if "<" in both or ">" in both:
        # 2026-07-13 fix, R2-20: a job title/query is board-controlled text that reaches the
        # prompt, so a prompt-injected LLM output could contain a live <script>/<img onerror>
        # tag; the resume HTML is rendered by network-enabled Chromium (PDF export), so a tag
        # that survives to substitute() could execute and exfiltrate the DOM (with PII). No
        # legitimate tagline/summary ever needs an angle bracket -- reject outright rather than
        # ship escaped-garbage text.
        return "html markup in output"
    if "—" in both or "–" in both:
        return "em/en dash"
    low = both.lower()
    for t in _BANNED_TOOLS:
        if re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", low):
            return f"banned tool: {t}"
    for ph in _BANNED_PHRASES:
        if ph in low:
            return f"banned phrase: {ph}"
    if not numbers_ok(both, allowed):
        bad = [n for n in re.findall(r"\d+(?:\.\d+)?", both) if n not in allowed]
        return f"invented number(s): {bad}"
    return None


def substitute(template: str, tagline: str, summary: str) -> str | None:
    """Swap the two blocks; None if either anchor isn't found exactly once. Both blocks are
    HTML-escaped here (2026-07-13 fix, R2-20), defense-in-depth alongside validate()'s markup
    rejection: this is the actual insertion point into the HTML Chromium renders, so any
    caller -- today's or a future one -- gets a safe insert even if a `<`/`>` slipped past
    the gate some other way."""
    if len(_TAG_RE.findall(template)) != 1 or len(_SUM_RE.findall(template)) != 1:
        return None
    tagline, summary = html.escape(tagline), html.escape(summary)
    out = _TAG_RE.sub(lambda m: m.group(1) + tagline + m.group(3), template, count=1)
    out = _SUM_RE.sub(lambda m: m.group(1) + summary + m.group(3), out, count=1)
    return out


_PROMPT = """Rewrite ONLY the headline tagline and the summary paragraph of this resume so they emphasize the overlap with ONE specific job. You may reorder, reweight, and rephrase what the resume already says. You may NOT add any fact, number, tool, employer, metric, or claim that is not already in the resume text below. This is a hard rule; an invented claim gets the application rejected and is dishonest.

THE JOB:
title: {title}
company: {company}
matched search: {query}
seniority: {seniority}

THE RESUME (verified facts, the only allowed source material):
{facts}

CURRENT TAGLINE: {cur_tag}
CURRENT SUMMARY: {cur_sum}

Rules:
- tagline: 3 short segments separated by " · " (middle dot), max 80 chars, lead with what THIS job is about (e.g. for a lifecycle role, lead with lifecycle/email; for a web role, lead with web delivery). Only name disciplines the resume already supports.
- summary: 60 to 90 words, first person implied, plain confident sentences. Put the most job-relevant strengths and the [PRIOR_RESULT] scaling fact early. Keep "Google-certified in Ads, Analytics (GA4), and Data Analytics" if it fits naturally.
- NO em-dashes or en-dashes. NO buzzwords (excited, passionate, leverage, results-driven, proven track record, dynamic, spearheaded). No markdown.
- If the job is outside his lanes, stay close to the current summary rather than stretching.

Return ONLY this JSON, nothing else:
{{"tagline": "...", "summary": "..."}}"""


def tailor_blocks(job: dict, template_text: str, cur_tag: str, cur_sum: str):
    """LLM rewrite -> (tagline, summary) or None. Gated by validate()."""
    from planner import _cli_json
    prompt = _PROMPT.format(
        title=job.get("title") or "?", company=job.get("company") or "?",
        query=job.get("query") or "?", seniority=job.get("seniority") or "?",
        facts=template_text, cur_tag=cur_tag, cur_sum=cur_sum)
    d = _cli_json(prompt, timeout=110, feature="tailor")
    if not isinstance(d, dict):
        return None, "no/invalid LLM json"
    tagline = humanize(str(d.get("tagline") or "").strip())
    summary = humanize(str(d.get("summary") or "").strip())
    why = validate(tagline, summary, allowed_numbers(template_text))
    if why:
        return None, why
    return (tagline, summary), None


# paths reach node ONLY via env vars (2026-07-12 hunt: they used to be f-string
# interpolated into the -e script; a job id carrying a quote/backslash would have
# broken out of the JS string = code exec in node. Ids are board-controlled.)
_RENDER_JS = (
    "const {chromium}=require('playwright');(async()=>{"
    "const b=await chromium.launch();const p=await b.newPage();"
    "await p.emulateMedia({media:'print'});"
    "await p.goto('file://'+process.env.RT_HTML,{waitUntil:'networkidle'});"
    "await p.pdf({path:process.env.RT_PDF,format:'Letter',printBackground:true,preferCSSPageSize:true});"
    "await b.close();})().catch(e=>{console.error(e.message);process.exit(1)});")


def render_pdf(doc_html: str, out_pdf: Path) -> bool:
    """Print html to a one-page Letter PDF with playwright-project's chromium.
    True only when the render is >20KB and is exactly 1 page. Callers must
    build out_pdf from safe_name() -- this function additionally refuses any
    path that resolves outside OUT_DIR (defense in depth).

    2026-07-13 fix (R2-23): render to a PID-scoped temp PDF and os.replace()
    it onto the canonical path only after every check passes, so a failed
    validation (wrong size, wrong page count) can never leave a bad file
    sitting at out_pdf -- the OLD code rendered straight to out_pdf and only
    deleted it when size was ALSO <20KB, so a 30KB 2-page render (fails the
    page-count check) used to survive, get skipped as "already exists" on the
    next run, and get uploaded as the tailored resume. The finally block now
    unlinks the temp render unconditionally, whatever its size."""
    import os
    out_pdf = out_pdf.resolve()
    if OUT_DIR.resolve() not in out_pdf.parents:
        return False
    pid = os.getpid()
    tmp_html = out_pdf.with_name(f"{out_pdf.stem}.{pid}.html")  # pid: no cross-run collision
    tmp_pdf = out_pdf.with_name(f"{out_pdf.stem}.{pid}.tmp.pdf")
    try:
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        tmp_html.write_text(doc_html)
        env = {**os.environ, "RT_HTML": str(tmp_html), "RT_PDF": str(tmp_pdf)}
        r = subprocess.run(["node", "-e", _RENDER_JS], cwd=PW_DIR, env=env,
                           capture_output=True, text=True, timeout=90)
        if r.returncode != 0 or not tmp_pdf.exists() or tmp_pdf.stat().st_size < 20000:
            return False
        raw = tmp_pdf.read_bytes().decode("latin1")
        if len(re.findall(r"/Type\s*/Page[^s]", raw)) != 1:
            return False
        os.replace(tmp_pdf, out_pdf)  # atomic: out_pdf only ever becomes a fully-validated render
        return True
    except Exception:  # noqa: BLE001 -- any render failure means "no tailored file", never a crash
        return False
    finally:
        tmp_html.unlink(missing_ok=True)
        tmp_pdf.unlink(missing_ok=True)  # any invalid/partial render, regardless of its size


def _enabled() -> bool:
    try:
        cfg = json.loads((ROOT / "store" / "config.json").read_text())
        return bool(cfg.get("job_tailor_resume", 1))
    except (OSError, json.JSONDecodeError):
        return True


# statuses whose job still benefits from keeping its tailored PDF: active pipeline stages plus
# submitted-successfully (applied/confirmed/interview keep the file as the record of what was
# actually sent). Everything else (skipped/expired/rejected + gone-from-queue) is prunable.
_KEEP_STATUSES = ("pending", "approved", "applying", "applied", "confirmed", "interview")


def prune() -> int:
    """Delete tailored PDFs whose job is gone or in a terminal non-active status, bounding
    OUT_DIR growth (each file is ~95KB and the skipped bucket dwarfs everything else).
    2026-07-13 hunt: nothing pruned this dir, so it grew one file per tailored job forever."""
    if not OUT_DIR.exists():
        return 0
    keep = {safe_name(j["id"]) + ".pdf" for j in jobs.load_jobs()
            if j.get("id") and j.get("status") in _KEEP_STATUSES}
    removed = 0
    for p in OUT_DIR.glob("*.pdf"):
        if p.name not in keep:
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def _tailor_limit(default: int = 60) -> int:
    """How many resumes to tailor per run.

    Each one is a separate Sonnet call, so this is the largest LLM cost in the
    job pipeline. Tailoring 60 when the daily apply cap is 5 spends ~55 calls on
    jobs that will not be applied to today, and the PDFs are cached anyway, so
    tomorrow's run would have picked them up for free.

    config resume_tailor_limit wins; otherwise track the apply cap with a little
    headroom (a couple of jobs get skipped by the preflight/dup guards).
    """
    try:
        cfg = json.loads((ROOT / "store" / "config.json").read_text())
    except (OSError, json.JSONDecodeError):
        return default
    explicit = cfg.get("resume_tailor_limit")
    if explicit is not None:
        try:
            return max(0, int(explicit))
        except (TypeError, ValueError):
            pass
    try:
        return max(3, int(cfg.get("job_daily_apply_cap", default)) + 2)
    except (TypeError, ValueError):
        return default


def _tailor_order(all_jobs: list) -> list:
    """Approved before pending, then best fit first.

    The old order was whatever order the store happened to be in, so a capped
    run could spend its whole budget tailoring low-fit jobs it will never submit
    while the high-fit ones went untailored.
    """
    def key(j):
        approved = 0 if j.get("status") == "approved" else 1
        return (approved, -(j.get("fit") or 0))
    return sorted(all_jobs, key=key)


def run(limit: int | None = None, dry_run: bool = False, only_ids: set[str] | None = None) -> int:
    if not _enabled():
        print("resume_tailor: disabled (job_tailor_resume=0)")
        return 0
    try:
        template = TEMPLATE.read_text()
    except OSError as e:
        print(f"resume_tailor: no template ({e}); nothing to do")
        return 0
    m_tag, m_sum = _TAG_RE.search(template), _SUM_RE.search(template)
    if not (m_tag and m_sum):
        print("resume_tailor: template anchors missing; refusing to run")
        return 0
    cur_tag = re.sub(r"\s+", " ", _strip_tags(m_tag.group(2))).strip()
    cur_sum = re.sub(r"\s+", " ", _strip_tags(m_sum.group(2))).strip()
    facts = resume_text(template)   # visible text only; also the number whitelist source

    if limit is None:
        limit = _tailor_limit()
    done = skipped = failed = 0
    for j in _tailor_order(jobs.load_jobs()):
        if done >= limit:
            break
        jid = j.get("id")
        if not jid or j.get("status") not in ("pending", "approved"):
            continue
        if only_ids and jid not in only_ids:
            continue
        out_pdf = OUT_DIR / f"{safe_name(jid)}.pdf"
        if out_pdf.exists():
            skipped += 1
            continue
        if dry_run:
            print(f"[dry-run] would tailor {jid}: {j.get('title')} @ {j.get('company')}")
            done += 1
            continue
        blocks, why = tailor_blocks(j, facts, cur_tag, cur_sum)
        if not blocks:
            print(f"resume_tailor: {jid} rejected ({why}); static resume will be used")
            failed += 1
            continue
        doc_html = substitute(template, *blocks)
        if not doc_html or not render_pdf(doc_html, out_pdf):
            print(f"resume_tailor: {jid} render failed; static resume will be used")
            failed += 1
            continue
        print(f"resume_tailor: {jid} tailored -> {blocks[0][:60]}")
        done += 1
    if not dry_run:
        pruned = prune()
        if pruned:
            print(f"resume_tailor: pruned {pruned} tailored PDF(s) for done/gone jobs")
    print(f"resume_tailor: {done} tailored, {skipped} already had files, {failed} fell back to static")
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-job tailored resume PDFs")
    ap.add_argument("--limit", type=int, default=None,
                    help="override the config-derived limit")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--jobs", help="comma-separated job ids to tailor (default: all approved/pending)")
    a = ap.parse_args()
    only = set(x.strip() for x in a.jobs.split(",") if x.strip()) if a.jobs else None
    run(limit=a.limit, dry_run=a.dry_run, only_ids=only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
