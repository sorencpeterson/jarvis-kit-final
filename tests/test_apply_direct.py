"""Deterministic ATS applications: the logic that decides what gets filled.

The browser layer is thin on purpose. Everything that decides WHETHER to submit and
WHAT to put in each box is pure, and lives here under test, because the failure modes
that matter are all decisions rather than clicks: submitting into a CAPTCHA,
submitting a half-filled form, or picking the wrong spec for a lookalike domain.
"""
from __future__ import annotations

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
