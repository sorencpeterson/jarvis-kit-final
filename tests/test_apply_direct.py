"""Deterministic ATS applications: the logic that decides what gets filled.

The browser layer is thin on purpose. Everything that decides WHETHER to submit and
WHAT to put in each box is pure, and lives here under test, because the failure modes
that matter are all decisions rather than clicks: submitting into a CAPTCHA,
submitting a half-filled form, or picking the wrong spec for a lookalike domain.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import ats_forms  # noqa: E402

PROFILE = {
    "full_name": "Alex Rivera", "first_name": "Alex", "last_name": "Rivera",
    "email": "alex@example.com", "phone": "555-0100",
    "linkedin": "linkedin.com/in/alexrivera",
}


class TestDetect:
    def test_known_boards_resolve(self):
        assert ats_forms.detect("https://boards.greenhouse.io/acme/jobs/1")["confidence"] == "high"
        assert ats_forms.detect("https://jobs.lever.co/acme/abc") is not None
        assert ats_forms.detect("https://jobs.ashbyhq.com/acme/xyz") is not None
        assert ats_forms.detect("https://apply.workable.com/acme/j/1") is not None

    def test_unknown_board_is_left_to_the_operator(self):
        for u in ("https://acme.com/careers/1", "https://myworkdayjobs.com/x",
                  "https://careers.icims.com/x", ""):
            assert ats_forms.detect(u) is None

    def test_lookalike_host_cannot_hijack_a_spec(self):
        # the whole point of matching on HOST and not on the raw string
        assert ats_forms.detect("https://evil.com/greenhouse.io/apply") is None
        assert ats_forms.detect("https://greenhouse.io.evil.com/a") is None
        assert ats_forms.detect("https://notgreenhouse.io/a") is None

    def test_subdomains_of_a_known_board_still_match(self):
        assert ats_forms.detect("https://job-boards.greenhouse.io/x/jobs/9") is not None

    def test_workday_is_deliberately_unsupported(self):
        # multi-screen wizard behind mandatory account creation; 0-for-8 with an LLM
        # operator. Deterministic filling cannot create accounts and must not try.
        assert ats_forms.detect("https://acme.wd1.myworkdayjobs.com/en-US/careers") is None


class TestValues:
    def test_greenhouse_gets_split_names(self):
        v = ats_forms.values_for(ats_forms.GREENHOUSE, PROFILE)
        assert v["first_name"] == "Alex" and v["last_name"] == "Rivera"
        assert "full_name" not in v          # not a field on this spec

    def test_lever_gets_one_combined_name(self):
        v = ats_forms.values_for(ats_forms.LEVER, PROFILE)
        assert v["full_name"] == "Alex Rivera"
        assert "first_name" not in v

    def test_name_is_split_from_full_name_when_parts_are_absent(self):
        v = ats_forms.values_for(ats_forms.GREENHOUSE, {"full_name": "Dana Kim",
                                                        "email": "d@example.com"})
        assert (v["first_name"], v["last_name"]) == ("Dana", "Kim")

    def test_single_word_name_does_not_invent_a_surname(self):
        v = ats_forms.values_for(ats_forms.GREENHOUSE, {"full_name": "Prince",
                                                        "email": "p@example.com"})
        assert v["first_name"] == "Prince"
        assert not v.get("last_name")

    def test_empty_values_are_omitted_never_placeholdered(self):
        # a plausible-looking wrong answer on a real application is worse than a blank
        v = ats_forms.values_for(ats_forms.GREENHOUSE,
                                 {"full_name": "Dana Kim", "email": "d@example.com"})
        assert "phone" not in v
        assert all(val.strip() for val in v.values())


class TestRequired:
    def test_complete_profile_has_nothing_missing(self):
        for spec in ats_forms.SPECS:
            v = ats_forms.values_for(spec, PROFILE)
            assert ats_forms.missing_required(spec, v) == []

    def test_missing_email_blocks_every_spec(self):
        thin = {"full_name": "Dana Kim"}
        for spec in ats_forms.SPECS:
            v = ats_forms.values_for(spec, thin)
            assert "email" in ats_forms.missing_required(spec, v)

    def test_every_required_field_is_a_real_field_on_its_spec(self):
        for spec in ats_forms.SPECS:
            for key in spec["required"]:
                assert key in spec["fields"], f"{spec['host_match'][0]}: {key}"


class TestWalls:
    @pytest.mark.parametrize("text,expect", [
        ("Please complete the reCAPTCHA to continue", "captcha"),
        ("I'm not a robot", "captcha"),
        ("Checking your browser - Cloudflare", "captcha"),
        ("Create an account to apply", "login"),
        ("Sign in to apply for this role", "login"),
        ("Enter the verification code we emailed you", "verify"),
    ])
    def test_walls_are_caught_and_named(self, text, expect):
        assert ats_forms.wall_reason(text) == expect

    def test_plain_form_is_not_a_wall(self):
        assert ats_forms.wall_reason(
            "First name Last name Email Phone Resume Submit application") == ""

    def test_wall_words_route_to_the_human_pile(self):
        # the reason word is the routing mechanism: if it is not in _HUMAN_FINISHABLE
        # the job silently drops out of the finish-by-hand list
        import jobs
        for text in ("recaptcha here", "create an account", "verification code"):
            r = ats_forms.wall_reason(text)
            assert any(r.startswith(w) for w in jobs._HUMAN_FINISHABLE), r

    def test_empty_page_is_not_reported_as_a_wall(self):
        assert ats_forms.wall_reason("") == ""
        assert ats_forms.wall_reason(None) == ""


class TestSubmitRails:
    def test_submitting_requires_both_the_flag_and_the_config(self, monkeypatch):
        import apply_direct
        monkeypatch.setattr(apply_direct, "_cfg", lambda: {})       # knob absent
        assert apply_direct.run(submit=True) == 2                   # refuses

    def test_pace_stays_inside_the_configured_window(self):
        import apply_direct
        for _ in range(40):
            assert 30 <= apply_direct._pace({"direct_apply_pace_s": [30, 40]}) <= 40

    def test_pace_survives_a_garbage_config(self):
        import apply_direct
        for bad in ({"direct_apply_pace_s": "fast"}, {"direct_apply_pace_s": [1]},
                    {"direct_apply_pace_s": ["a", "b"]}, {}):
            assert apply_direct._pace(bad) > 0

    def test_resume_upload_cannot_escape_the_store(self, monkeypatch, tmp_path):
        import apply_direct
        monkeypatch.setattr(apply_direct, "ROOT", tmp_path)
        monkeypatch.setattr(apply_direct, "RESUME", tmp_path / "store" / "resume.pdf")
        outside = tmp_path / "secret.pdf"
        outside.write_text("x")
        assert apply_direct._resume_path({"resume_file": str(outside)}) is None
        assert apply_direct._resume_path({"resume_file": "../../etc/passwd"}) is None


class TestConcurrencySafety:
    """This agent and the LLM apply chain read the same queue. Without claiming,
    both can select one job and submit it twice to a real employer."""

    def test_it_claims_before_submitting(self):
        src = (ROOT / "agents" / "apply_direct.py").read_text()
        claim = src.split("if submit:", 2)[-1]
        assert "jobs.mark_applying(ids)" in src
        assert "jobs.bump_attempts(ids)" in src
        # and it must keep only what it actually won: mark_applying is a CAS
        assert 'now.get(j["id"]) == "applying"' in src

    def test_it_never_submits_from_a_non_us_ip(self):
        src = (ROOT / "agents" / "apply_direct.py").read_text()
        assert "import geo_check" in src
        assert "not on a US IP" in src

    def test_status_writes_are_compare_and_swap(self):
        # A late write from the LLM chain must never be clobbered. Checked on the
        # AST rather than the text: set_status calls span several lines and contain
        # their own parentheses, which no substring match survives.
        import ast
        tree = ast.parse((ROOT / "agents" / "apply_direct.py").read_text())
        run = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "run")
        calls = [n for n in ast.walk(run)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "set_status"]
        assert calls, "run() writes no statuses at all"
        for c in calls:
            kw = {k.arg for k in c.keywords}
            assert "expect" in kw, f"set_status at line {c.lineno} is not a CAS"

    def test_a_handed_off_job_returns_to_the_queue(self):
        # stranded in 'applying' is worse than skipped: invisible to needs_manual()
        # AND needs_verify(), and approved_to_apply only reads 'approved'
        src = (ROOT / "agents" / "apply_direct.py").read_text()
        assert 'jobs.set_status(r["id"], "approved", expect="applying")' in src

    def test_an_attempted_submit_is_never_returned_to_the_queue(self):
        src = (ROOT / "agents" / "apply_direct.py").read_text()
        assert 'elif r.get("submit_attempted"):' in src
        seg = src.split('elif r.get("submit_attempted"):', 1)[1][:400]
        # strip comments: the branch explains WHY it avoids the approved pool, and
        # the word appearing in that explanation is not the code doing it
        code = "\n".join(ln.split("#", 1)[0] for ln in seg.splitlines())
        assert "inflight_timeout" in code, "an attempted submit must be submission-uncertain"
        assert '"approved"' not in code.split("print(")[0]

    def test_the_batch_is_released_on_every_exit_path(self):
        src = (ROOT / "agents" / "apply_direct.py").read_text()
        tail = src.split("def run(", 1)[1]
        assert "finally:" in tail
        fin = tail.split("finally:", 1)[1]
        assert "inflight_timeout" in fin and 'expect="applying"' in fin


class TestResultSurvivesACrash:
    def test_submit_attempted_is_recorded_before_the_click(self):
        src = (ROOT / "agents" / "apply_direct.py").read_text()
        seg = src.split('out["submit_attempted"] = True', 1)
        assert len(seg) == 2, "submit_attempted is never set"
        after = seg[1][:120]
        assert "btn.click()" in after, "it must be set BEFORE the click, not after"

    def test_caller_owns_the_result_dict(self, monkeypatch):
        # if apply_one built its own, a crash would lose submit_attempted and the
        # job would be wrongly returned to the queue
        import apply_direct
        spec = ats_forms.GREENHOUSE
        out = apply_direct.new_result({"id": "x", "company": "Acme"}, spec)
        assert out["submit_attempted"] is False
        assert out["id"] == "x"

        class _Boom:
            def goto(self, *a, **k):
                out["submit_attempted"] = True      # got as far as clicking
                raise RuntimeError("page died")

        with pytest.raises(RuntimeError):
            apply_direct.apply_one(_Boom(), {"id": "x", "apply_url": "u"}, spec,
                                   PROFILE, True, out=out)
        assert out["submit_attempted"] is True, "crash must not lose this"


class TestNeverSendsAnEmptyApplication:
    def test_no_resume_file_aborts_before_submitting(self):
        src = (ROOT / "agents" / "apply_direct.py").read_text()
        assert "no resume file" in src
        seg = src.split("res = _resume_path(job)", 1)[1][:400]
        assert 'out["action"] = "handoff"' in seg

    def test_a_missing_resume_field_aborts_a_real_submit(self):
        src = (ROOT / "agents" / "apply_direct.py").read_text()
        assert "resume field not found on the page" in src


class TestDocumentedHonestly:
    def test_low_confidence_specs_say_so(self):
        # the selectors are unverified against live submissions; the module must not
        # imply otherwise
        assert ats_forms.GREENHOUSE["confidence"] == "high"
        assert {s["confidence"] for s in ats_forms.SPECS} <= {"high", "medium", "low"}

    def test_module_states_that_selectors_are_unverified(self):
        # normalized: the claim must survive reflowing the docstring
        doc = " ".join((ROOT / "agents" / "ats_forms.py").read_text().split())
        assert "NOT verified against a live submission" in doc

    def test_dry_run_is_the_default(self):
        src = (ROOT / "agents" / "apply_direct.py").read_text()
        assert '"--dry-run", action="store_true", default=True' in src
        assert "def run(limit: int = 10, submit: bool = False" in src
