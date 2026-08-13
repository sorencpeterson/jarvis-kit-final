#!/usr/bin/env python3
"""Tests for the per-job tailored-resume lane (2026-07-12 interview-rate push):
agents/resume_tailor.py gates + substitution, resume_ab.claim() attribution,
and resume_ab._sink() post-swap backfill routing.

No LLM, no render, no touch of real store/: planner._cli_json is
monkeypatched, template/OUT_DIR/REG point at tmp_path.

Run: .venv/bin/python -m pytest tests/test_resume_tailor.py -v
"""
from __future__ import annotations

import json
import re

import pytest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import resume_tailor as rt  # noqa: E402
import resume_ab as rab  # noqa: E402
import jobs  # noqa: E402
import planner  # noqa: E402

TPL = """<html><head><style>h1{}</style></head><body>
<div class="name">ALEX RIVERA</div>
<div class="tag">Fractional COO · Marketing Operations · Growth</div>
<p class="sum">Scaled one agency from $400K to over $1M in annual revenue (2.5x).
Built 34+ websites, frequently on 48 to 72 hour turnarounds since 2019.</p>
<ul><li>bullet stays verbatim</li></ul>
</body></html>"""

GOOD_TAG = "Lifecycle Marketing · Marketing Ops · Web Delivery"
GOOD_SUM = ("Growth and operations leader. Scaled one agency from $400K to over $1M "
            "in annual revenue (2.5x) by rebuilding acquisition, delivery, and retention. "
            "Runs lifecycle and email programs end to end and ships production sites, "
            "34+ builds, many on 48 to 72 hour turnarounds. Plain systems that hold up.")


def _allowed():
    return rt.allowed_numbers(rt.resume_text(TPL))


# ---------------------------------------------------------------- 2026-07-12 hunt regressions
class TestHuntRegressions:
    def test_css_numbers_do_not_widen_the_whitelist(self):
        # bug: font-size 23pt etc. leaked into the fact whitelist, so an LLM's
        # invented "23% growth" would have passed. Pin against the SHIPPED template.
        # Asserts the rule, not one person's resume: CSS numbers stay out, numbers
        # in the visible body stay in.
        real = (ROOT / "store-templates" / "resume-draft.html").read_text()
        allowed = rt.allowed_numbers(rt.resume_text(real))
        for css_only in ("23", "1.25", "0.4", "700", "10.5"):
            assert css_only not in allowed, f"CSS number {css_only} leaked into whitelist"
        body_numbers = [n for n in ("2020", "2022", "555") if n in rt.resume_text(real)]
        assert body_numbers, "template has no body numbers to check"
        for fact in body_numbers:
            assert fact in allowed, f"body number {fact} missing from whitelist"

    def test_safe_name_neutralizes_hostile_ids(self):
        for hostile in ("../../../etc/passwd", "../../x", "..", "a/../../b"):
            s = rt.safe_name(hostile)
            assert "/" not in s and "\\" not in s and not s.startswith(".") and s != ".."
        # 2026-07-13 fix (CX-G2/R2-45): base spelling is unchanged, PLUS a deterministic
        # 8-hex sha1 suffix of the RAW id -- so these are no longer exact-match to a bare
        # sanitized string, just "sanitized base" + "_" + 8 hex chars.
        assert re.fullmatch(r"a_b_c_d_e_[0-9a-f]{8}", rt.safe_name("a'b\\c`d$e"))
        assert re.fullmatch(r"board_jid_with_odd_chars_[0-9a-f]{8}",
                            rt.safe_name("board:jid,with%odd&chars"))
        assert len(rt.safe_name("x" * 500)) <= 180 + 9   # base cap + "_" + 8 hex
        assert re.fullmatch(r"job_[0-9a-f]{8}", rt.safe_name(""))
        assert re.fullmatch(r"normal-id_1\.2_[0-9a-f]{8}", rt.safe_name("normal-id_1.2"))
        # deterministic: same raw id -> same name every time (not random per-call)
        assert rt.safe_name("stable-id") == rt.safe_name("stable-id")

    def test_safe_name_is_collision_resistant(self):
        # 2026-07-13 fix (CX-G2/R2-45, 3rd model to confirm): these used to BOTH sanitize to
        # 'role_a' / 'board_a' and share one PDF -- the 2nd job's application silently got a
        # resume tailored for the WRONG employer. The hash suffix must disambiguate them.
        assert rt.safe_name("role:a") != rt.safe_name("role?a")
        assert rt.safe_name("board:a") != rt.safe_name("board/a")
        assert rt.safe_name("board:a/b") != rt.safe_name("board:a:b")

    def test_server_sanitizer_parity(self):
        # app/server.py's _resume_line INLINES the sanitizer (import-weight reasons); it must
        # stay byte-identical to safe_name -- INCLUDING the sha1 suffix -- or its tailored-file
        # existence check misses every file the agent writes.
        import hashlib
        src = (ROOT / "app" / "server.py").read_text()
        # the base regex spelling is still inlined verbatim ...
        assert r'sub(r"[^A-Za-z0-9._-]", "_", _raw)[:180].lstrip(".") or "job"' in src
        # ... AND the same 8-hex sha1(raw id) suffix safe_name appends (CX-G2/R2-45, closed
        # 2026-07-13 by the server agent).
        assert r'sha1(_raw.encode(' in src and "[:8]" in src
        # api_jobs_applied's attribution no longer derives a filename AT ALL: it reads the
        # resume_file stamp _build_prompt wrote at spawn time (jobs.note_fields), so it
        # records what actually went out rather than what exists at callback time (field
        # report 2026-08-12, C3). The intent this line used to guard -- "the callback
        # attributes the correct file" -- is now carried by the stamp round-trip.
        assert 'res.get("resume_file")' in src
        assert "jobs.note_fields" in src

        def _server_inline_full(jid: str) -> str:
            # a faithful reconstruction of app/server.py _resume_line's spelling
            raw = jid or ""
            base = re.sub(r"[^A-Za-z0-9._-]", "_", raw)[:180].lstrip(".") or "job"
            return f"{base}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:8]}"

        # FULL-string parity now (the pre-fix version asserted DIVERGENCE while _resume_line
        # still lacked the suffix; that gap is closed). Cover the collision-prone ids too, so
        # the server lookup lands on exactly the file safe_name/the agent produced.
        for jid in ("board:jid", "role:a", "role?a", "board:a/b", "", "x" * 500,
                    "normal-id_1.2", "../../etc/passwd"):
            assert rt.safe_name(jid) == _server_inline_full(jid), jid

    def test_render_pdf_refuses_paths_outside_out_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rt, "OUT_DIR", tmp_path / "jail")
        (tmp_path / "jail").mkdir()
        assert rt.render_pdf("<html></html>", tmp_path / "escape.pdf") is False
        assert not (tmp_path / "escape.pdf").exists()

    def test_render_js_has_no_interpolated_paths(self):
        # paths must travel via env vars, never into the -e script string
        assert "process.env.RT_HTML" in rt._RENDER_JS
        assert "process.env.RT_PDF" in rt._RENDER_JS
        assert "{tmp_html}" not in rt._RENDER_JS and "f\"" not in rt._RENDER_JS


# ------------------------------------------------------- R2-23: atomic render
class TestRenderPdfAtomicity:
    """2026-07-13 fix: the OLD render_pdf wrote straight to the canonical out_pdf and only
    deleted it when size was ALSO <20KB -- a page-count failure (2-page render, wrong content)
    at >=20KB used to survive, look like "already tailored" on the next run, and get uploaded.
    render_pdf now renders to a PID-scoped temp file and os.replace()s it into place only once
    every check passes; the finally block unlinks the temp file regardless of its size."""

    @pytest.fixture(autouse=True)
    def _force_node_path(self, tmp_path, monkeypatch):
        # render_pdf now gates the node renderer on PW_DIR existing + node on PATH, and
        # otherwise falls back to headless Chrome (field report 2026-08-12, C1). These
        # tests exercise the NODE path's validation mechanics; force that path so the
        # mock's env['RT_PDF'] contract holds on machines with no playwright-project
        # checkout -- without this, the mock crashed inside the Chrome branch and the
        # False-expecting tests here passed for the wrong reason.
        monkeypatch.setattr(rt, "PW_DIR", tmp_path)
        monkeypatch.setattr(rt.shutil, "which", lambda c: "/usr/bin/node")

    @staticmethod
    def _fake_run(page_markers: int, size_pad: int = 20000):
        """subprocess.run replacement: writes a fake PDF with `page_markers` occurrences of
        the '/Type/Page' marker render_pdf's page-count regex looks for, straight to
        env['RT_PDF'] (mirroring what the real node/chromium subprocess would produce)."""
        def _run(cmd, cwd=None, env=None, capture_output=None, text=None, timeout=None):
            body = (b"/Type/Page " * page_markers) + b"x" * size_pad
            Path(env["RT_PDF"]).write_bytes(body)

            class _Result:
                returncode = 0
            return _Result()
        return _run

    def test_two_page_render_never_lands_at_canonical_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rt, "OUT_DIR", tmp_path)
        monkeypatch.setattr(rt.subprocess, "run", self._fake_run(page_markers=2))
        out_pdf = tmp_path / "job.pdf"
        assert rt.render_pdf("<html></html>", out_pdf) is False
        assert not out_pdf.exists()                 # canonical path was NEVER touched
        assert list(tmp_path.glob("*")) == []        # no tmp leftovers either

    def test_undersized_render_never_lands_at_canonical_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rt, "OUT_DIR", tmp_path)
        monkeypatch.setattr(rt.subprocess, "run", self._fake_run(page_markers=1, size_pad=100))
        out_pdf = tmp_path / "job.pdf"
        assert rt.render_pdf("<html></html>", out_pdf) is False
        assert not out_pdf.exists()
        assert list(tmp_path.glob("*")) == []

    def test_valid_render_moves_atomically_into_place(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rt, "OUT_DIR", tmp_path)
        monkeypatch.setattr(rt.subprocess, "run", self._fake_run(page_markers=1))
        out_pdf = tmp_path / "job.pdf"
        assert rt.render_pdf("<html></html>", out_pdf) is True
        assert out_pdf.exists()
        remaining = {p.name for p in tmp_path.glob("*")}
        assert remaining == {"job.pdf"}              # temp .html/.tmp.pdf both cleaned up


# ---------------------------------------------------------------- gates
class TestValidate:
    def test_good_blocks_pass(self):
        assert rt.validate(GOOD_TAG, GOOD_SUM, _allowed()) is None

    def test_invented_number_rejected(self):
        bad = GOOD_SUM.replace("(2.5x)", "(2.5x), lifting conversion 37%")
        why = rt.validate(GOOD_TAG, bad, _allowed())
        assert why and "37" in why

    def test_invented_dollar_figure_rejected(self):
        bad = GOOD_SUM.replace("$1M", "$3M")
        assert rt.validate(GOOD_TAG, bad, _allowed())

    def test_em_and_en_dash_rejected(self):
        assert rt.validate(GOOD_TAG, GOOD_SUM.replace(",", " —", 1), _allowed())
        assert rt.validate(GOOD_TAG.replace("·", "–"), GOOD_SUM, _allowed())

    def test_banned_tool_rejected(self):
        why = rt.validate(GOOD_TAG, GOOD_SUM.replace("email programs", "Marketo programs"),
                          _allowed())
        assert why and "marketo" in why

    def test_banned_tool_is_word_bounded(self):
        # 'segment' the martech is banned; 'segments' as a plain word is not
        ok = GOOD_SUM.replace("Plain systems", "Audience segments and plain systems")
        assert rt.validate(GOOD_TAG, ok, _allowed()) is None

    def test_ai_cliche_rejected(self):
        assert rt.validate(GOOD_TAG, GOOD_SUM + " Excited to leverage this.", _allowed())

    def test_length_bounds(self):
        assert rt.validate("x", GOOD_SUM, _allowed())            # tagline too short
        assert rt.validate(GOOD_TAG, "too short.", _allowed())   # summary too short
        assert rt.validate(GOOD_TAG, GOOD_SUM + " pad" * 120, _allowed())

    def test_html_markup_rejected(self):
        # 2026-07-13 fix, R2-20: a prompt-injected LLM output could carry a live tag; no
        # legitimate tagline/summary ever needs '<' or '>', so reject outright.
        why = rt.validate(GOOD_TAG.replace("Lifecycle", "<script>Lifecycle"), GOOD_SUM, _allowed())
        assert why == "html markup in output"
        why2 = rt.validate(GOOD_TAG, GOOD_SUM.replace("Growth", "Growth<img src=x onerror=1>"),
                           _allowed())
        assert why2 == "html markup in output"


class TestSubstitute:
    def test_swaps_both_blocks_only(self):
        out = rt.substitute(TPL, GOOD_TAG, GOOD_SUM)
        assert GOOD_TAG in out and GOOD_SUM in out
        assert "Fractional COO · Marketing Operations · Growth" not in out
        assert "bullet stays verbatim" in out            # bullets untouched

    def test_missing_or_duplicated_anchor_refuses(self):
        assert rt.substitute("<html>no anchors</html>", GOOD_TAG, GOOD_SUM) is None
        assert rt.substitute(TPL + '<div class="tag">again</div>', GOOD_TAG, GOOD_SUM) is None

    def test_escapes_html_in_blocks(self):
        # 2026-07-13 fix, R2-20: defense-in-depth even if a '<'/'>' reached substitute()
        # some other way (validate() is the primary gate, this is the actual insertion
        # point into HTML Chromium renders to PDF) -- a live tag must never survive.
        hostile_tag = GOOD_TAG + "<script>alert(1)</script>"
        out = rt.substitute(TPL, hostile_tag, GOOD_SUM)
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_committed_template_has_exactly_one_of_each_anchor(self):
        # pins the real store/resume-draft.html the agent runs against
        real = (ROOT / "store-templates" / "resume-draft.html").read_text()
        assert len(rt._TAG_RE.findall(real)) == 1
        assert len(rt._SUM_RE.findall(real)) == 1


class TestTailorBlocks:
    def test_good_llm_json_passes(self, monkeypatch):
        monkeypatch.setattr(planner, "_cli_json",
                            lambda *a, **k: {"tagline": GOOD_TAG, "summary": GOOD_SUM})
        blocks, why = rt.tailor_blocks({"title": "t"}, rt._strip_tags(TPL), "a", "b")
        assert why is None and blocks == (GOOD_TAG, GOOD_SUM)

    def test_invented_number_from_llm_rejected(self, monkeypatch):
        monkeypatch.setattr(planner, "_cli_json",
                            lambda *a, **k: {"tagline": GOOD_TAG,
                                             "summary": GOOD_SUM.replace("(2.5x)", "(4x)")})
        blocks, why = rt.tailor_blocks({"title": "t"}, rt._strip_tags(TPL), "a", "b")
        assert blocks is None and "4" in why

    def test_llm_failure_is_clean_none(self, monkeypatch):
        monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: None)
        blocks, why = rt.tailor_blocks({"title": "t"}, rt._strip_tags(TPL), "a", "b")
        assert blocks is None


class TestRun:
    def _wire(self, tmp_path, monkeypatch, rows):
        tpl = tmp_path / "tpl.html"
        tpl.write_text(TPL)
        monkeypatch.setattr(rt, "TEMPLATE", tpl)
        monkeypatch.setattr(rt, "OUT_DIR", tmp_path / "tailored")
        monkeypatch.setattr(rt, "_enabled", lambda: True)
        monkeypatch.setattr(jobs, "load_jobs", lambda: rows)

    def test_dry_run_targets_only_pending_approved(self, tmp_path, monkeypatch, capsys):
        rows = [{"id": "a1", "status": "approved", "title": "T", "company": "C"},
                {"id": "a2", "status": "applied", "title": "T", "company": "C"},
                {"id": "a3", "status": "skipped", "title": "T", "company": "C"}]
        self._wire(tmp_path, monkeypatch, rows)
        assert rt.run(dry_run=True) == 1
        assert "a1" in capsys.readouterr().out

    def test_existing_file_skipped(self, tmp_path, monkeypatch):
        rows = [{"id": "a1", "status": "approved", "title": "T", "company": "C"}]
        self._wire(tmp_path, monkeypatch, rows)
        (tmp_path / "tailored").mkdir()
        (tmp_path / "tailored" / f"{rt.safe_name('a1')}.pdf").write_bytes(b"x")
        assert rt.run(dry_run=True) == 0

    def test_kill_switch(self, tmp_path, monkeypatch):
        self._wire(tmp_path, monkeypatch, [{"id": "a1", "status": "approved"}])
        monkeypatch.setattr(rt, "_enabled", lambda: False)
        assert rt.run(dry_run=True) == 0

    def test_rejected_blocks_write_no_file(self, tmp_path, monkeypatch):
        rows = [{"id": "a1", "status": "approved", "title": "T", "company": "C"}]
        self._wire(tmp_path, monkeypatch, rows)
        monkeypatch.setattr(planner, "_cli_json",
                            lambda *a, **k: {"tagline": GOOD_TAG,
                                             "summary": GOOD_SUM + " Grew MRR 900%."})
        assert rt.run() == 0
        assert not (tmp_path / "tailored" / "a1.pdf").exists()


# ---------------------------------------------------------------- attribution
class TestClaim:
    def _wire(self, tmp_path, monkeypatch, reg=None):
        monkeypatch.setattr(rab, "REG", tmp_path / "reg.json")
        if reg is not None:
            (tmp_path / "reg.json").write_text(json.dumps(reg))

    def test_claim_auto_registers_and_dedupes(self, tmp_path, monkeypatch):
        self._wire(tmp_path, monkeypatch)
        assert rab.claim("j1", "v2-tailored", file="store/resume_tailored/")
        assert rab.claim("j1", "v2-tailored")
        reg = json.loads((tmp_path / "reg.json").read_text())
        assert reg["v2-tailored"]["applied"] == ["j1"]
        assert reg["v2-tailored"]["file"] == "store/resume_tailored/"

    def test_claim_evicts_from_default(self, tmp_path, monkeypatch):
        self._wire(tmp_path, monkeypatch,
                   {"default": {"file": "store/resume.pdf", "registered": "x",
                                "applied": ["j1"], "outcomes": {}}})
        rab.claim("j1", "v2")
        reg = json.loads((tmp_path / "reg.json").read_text())
        assert reg["default"]["applied"] == []
        assert reg["v2"]["applied"] == ["j1"]

    def test_claim_bad_input_never_raises(self, tmp_path, monkeypatch):
        self._wire(tmp_path, monkeypatch)
        assert rab.claim("", "v2") is False
        assert rab.claim("j1", "") is False


class TestSink:
    def test_default_when_default_holds_live_file(self):
        reg = {"default": {"file": "store/resume.pdf"}}
        assert rab._sink(reg) == "default"

    def test_v2_after_swap(self):
        reg = {"default": {"file": "store/resume-old-2026-06-27.pdf"},
               "v2": {"file": "store/resume.pdf"},
               "v2-tailored": {"file": "store/resume_tailored/"}}
        assert rab._sink(reg) == "v2"

    def test_backfill_lands_on_sink_not_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rab, "REG", tmp_path / "reg.json")
        reg = {"default": {"file": "old.pdf", "registered": "x", "applied": [], "outcomes": {}},
               "v2": {"file": "store/resume.pdf", "registered": "x", "applied": [], "outcomes": {}}}
        added = rab.backfill(reg, [{"id": "jX", "status": "applied"}])
        assert added == 1
        assert reg["v2"]["applied"] == ["jX"]
        assert reg["default"]["applied"] == []


class TestComputeOutcomesMaxStage:
    """2026-07-13 fix, CX15/R2-7: outcomes must reflect the MAX stage a job EVER reached, not
    just its current status -- a job that interviewed and was later rejected must still count
    as an interview, or the A/B experiment's primary metric (interview_rate) is corrupted."""

    def _write_history(self, tmp_path, monkeypatch, lines):
        q = tmp_path / "jobs.jsonl"
        q.write_text("".join(json.dumps(r) + "\n" for r in lines))
        monkeypatch.setattr(jobs, "QUEUE", q)

    def test_interview_survives_later_rejection(self, tmp_path, monkeypatch):
        # j1's real history: applied -> interview -> rejected. Current folded snapshot is
        # 'rejected' (last-write-wins), which is exactly what used to erase the interview credit.
        self._write_history(tmp_path, monkeypatch, [
            {"id": "j1", "status": "applied"},
            {"id": "j1", "status": "interview"},
            {"id": "j1", "status": "rejected"},
        ])
        reg = {"default": {"file": "store/resume.pdf", "applied": ["j1"], "outcomes": {}}}
        out = rab.compute_outcomes(reg, [{"id": "j1", "status": "rejected"}])
        o = out["default"]["outcomes"]
        assert o["interviewed"] == 1
        assert o["replied"] == 1         # interview implies a reply
        assert o["rejected"] == 1        # both true at once: it DID interview AND end rejected

    def test_plain_rejection_without_interview_does_not_count(self, tmp_path, monkeypatch):
        self._write_history(tmp_path, monkeypatch, [
            {"id": "j2", "status": "applied"},
            {"id": "j2", "status": "rejected"},
        ])
        reg = {"default": {"file": "store/resume.pdf", "applied": ["j2"], "outcomes": {}}}
        out = rab.compute_outcomes(reg, [{"id": "j2", "status": "rejected"}])
        o = out["default"]["outcomes"]
        assert o["interviewed"] == 0 and o["replied"] == 0 and o["rejected"] == 1

    def test_falls_back_to_current_status_when_history_missing(self, tmp_path, monkeypatch):
        # id absent from the queue log (e.g. compacted/archived) -- degrade to the current
        # snapshot rather than silently zeroing the outcome out.
        self._write_history(tmp_path, monkeypatch, [])
        reg = {"default": {"file": "store/resume.pdf", "applied": ["j3"], "outcomes": {}}}
        out = rab.compute_outcomes(reg, [{"id": "j3", "status": "interview"}])
        assert out["default"]["outcomes"]["interviewed"] == 1


class TestEvictFromAllVariants:
    def test_reclaim_evicts_from_prior_nondefault_variant(self, tmp_path, monkeypatch):
        # 2026-07-13 hunt: a re-claim used to evict only from 'default', so a jid moving between
        # two non-default variants got double-counted. Now it lives in exactly one.
        monkeypatch.setattr(rab, "REG", tmp_path / "reg.json")
        (tmp_path / "reg.json").write_text(json.dumps({
            "default": {"file": "store/resume.pdf", "registered": "x", "applied": [], "outcomes": {}},
            "v2": {"file": "a", "registered": "x", "applied": ["j1"], "outcomes": {}},
            "v2-tailored": {"file": "b", "registered": "x", "applied": [], "outcomes": {}}}))
        rab.claim("j1", "v2-tailored")
        reg = json.loads((tmp_path / "reg.json").read_text())
        assert reg["v2-tailored"]["applied"] == ["j1"]
        assert reg["v2"]["applied"] == []          # not double-counted


class TestPrune:
    def _wire(self, tmp_path, monkeypatch, rows):
        out = tmp_path / "tailored"
        out.mkdir()
        monkeypatch.setattr(rt, "OUT_DIR", out)
        monkeypatch.setattr(jobs, "load_jobs", lambda: rows)
        return out

    def test_prunes_done_and_gone_keeps_active(self, tmp_path, monkeypatch):
        rows = [{"id": "keep_appr", "status": "approved"},
                {"id": "keep_applied", "status": "applied"},
                {"id": "drop_skipped", "status": "skipped"},
                {"id": "drop_rejected", "status": "rejected"}]
        out = self._wire(tmp_path, monkeypatch, rows)
        # a file for each row above, plus one orphan whose job no longer exists
        for name in ("keep_appr", "keep_applied", "drop_skipped", "drop_rejected", "orphan_gone"):
            (out / f"{rt.safe_name(name)}.pdf").write_bytes(b"x" * 30000)
        removed = rt.prune()
        assert removed == 3                         # skipped + rejected + orphan
        remaining = {p.stem for p in out.glob("*.pdf")}
        assert remaining == {rt.safe_name("keep_appr"), rt.safe_name("keep_applied")}

    def test_prune_empty_dir_is_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rt, "OUT_DIR", tmp_path / "nope")
        monkeypatch.setattr(jobs, "load_jobs", lambda: [])
        assert rt.prune() == 0
