"""Answering a form's own screener questions from answers the owner already gave.

This is what stands between a deterministic run and an application that lands. The
spec fills name, email, phone and the resume; a real Greenhouse or Lever form marks
work authorization, sponsorship, location and a screener or two as required too, and
pressing submit with those blank produces a validation error rather than an
application.

The hard rule under test throughout: NEVER GUESS. An unfilled required field fails
validation and reaches a human. A guessed one is submitted and believed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import ats_forms as af  # noqa: E402

PROFILE = {
    "full_name": "Alex Rivera", "first_name": "Alex", "last_name": "Rivera",
    "email": "alex@example.com", "phone": "555-0100",
    "city_state": "Austin, TX", "state_abbrev": "TX", "zip5": "78701",
    "linkedin": "linkedin.com/in/alexrivera", "portfolio": "example.com",
    "work_authorization": "US citizen", "requires_sponsorship": "No",
    "availability": "2 weeks", "years_experience": "6",
    "salary_expectation": "Open",
    "eeo": {"gender": "Prefer not to say"},
}

BANK = [
    {"q": "Why are you interested in this role?", "a": "The work is close to what I already do."},
    {"q": "Have you worked in a remote team before?", "a": "Yes, five years fully remote."},
]


class TestProfileBackedAnswers:
    def test_work_authorization(self):
        for label in ("Are you legally authorized to work in the United States?",
                      "Work Authorization", "Are you authorized to work in the US?"):
            assert af.answer_for(label, PROFILE) == "US citizen"

    def test_sponsorship(self):
        for label in ("Will you now or in the future require sponsorship?",
                      "Do you require visa sponsorship?"):
            assert af.answer_for(label, PROFILE) == "No"

    def test_location_fields_resolve_separately(self):
        assert af.answer_for("City", PROFILE) == "Austin, TX"
        assert af.answer_for("State", PROFILE) == "TX"
        assert af.answer_for("Zip Code", PROFILE) == "78701"

    def test_state_does_not_match_the_word_statement(self):
        # 'Personal statement' must not be answered with a two-letter state code
        assert af.answer_for("Personal statement", PROFILE) != "TX"

    def test_eeo_comes_from_its_own_block(self):
        assert af.answer_for("Gender", PROFILE) == "Prefer not to say"
        # nothing recorded for race: must not fall through to some other value
        assert af.answer_for("Race / Ethnicity", PROFILE) is None

    def test_identity_fields(self):
        assert af.answer_for("First Name", PROFILE) == "Alex"
        assert af.answer_for("Email Address", PROFILE) == "alex@example.com"
        assert af.answer_for("Phone", PROFILE) == "555-0100"


class TestNeverGuesses:
    def test_unknown_question_returns_none(self):
        assert af.answer_for("Describe a time you disagreed with your manager",
                             PROFILE, BANK) is None

    def test_missing_profile_value_is_not_invented(self):
        thin = {"first_name": "Alex"}
        # no availability recorded: must not produce a plausible-looking date
        assert af.answer_for("What is your notice period?", thin) is None
        assert af.answer_for("Desired salary", thin) is None

    def test_a_loose_bank_overlap_is_rejected(self):
        # shares 'remote' and little else; too weak to answer a real application with
        assert af.answer_for("Are you comfortable with remote onboarding paperwork?",
                             PROFILE, BANK) is None

    def test_empty_and_tiny_labels_are_ignored(self):
        for label in ("", "  ", "?", "ab"):
            assert af.answer_for(label, PROFILE, BANK) is None

    def test_fallback_only_applies_where_the_answer_is_structural(self):
        # authorization/sponsorship have safe defaults; open questions do not
        assert af.answer_for("Authorized to work?", {}) == "Yes"
        assert af.answer_for("Require sponsorship?", {}) == "No"
        assert af.answer_for("Why do you want this job?", {}) is None


class TestAnswerBank:
    def test_exact_question_match_uses_the_owners_own_words(self):
        assert af.answer_for("Why are you interested in this role?", PROFILE, BANK) \
            == "The work is close to what I already do."

    def test_near_match_on_wording_still_resolves(self):
        assert af.answer_for("Why are you interested in this role", PROFILE, BANK) \
            == "The work is close to what I already do."

    def test_profile_beats_the_bank(self):
        bank = [{"q": "Phone", "a": "000-0000"}]
        assert af.answer_for("Phone", PROFILE, bank) == "555-0100"

    def test_an_empty_bank_is_harmless(self):
        assert af.answer_for("Why are you interested in this role?", PROFILE, []) is None


class TestCustomisationIsUsed:
    """The customisation already exists and is already paid for: job_cover.py writes a
    per-job cover during the morning batch and resume_library matches a variant to the
    role. The zero-token path was submitting generic applications anyway."""

    def test_cover_letter_fields_are_recognised(self):
        for label in ("Cover Letter", "Why are you interested in this role?",
                      "Tell us why you want to work here", "Additional information",
                      "What excites you about this opportunity?"):
            assert af.is_cover_field(label), label

    def test_ordinary_screeners_are_not_treated_as_cover_fields(self):
        for label in ("First Name", "Are you authorized to work in the US?",
                      "Zip Code", "Years of experience"):
            assert not af.is_cover_field(label), label

    def test_this_jobs_cover_beats_the_generic_one(self):
        import apply_direct as ad
        job = {"cover_override": "Written for THIS role."}
        prof = {"default_cover": "Generic."}
        assert ad._cover_for(job, prof) == "Written for THIS role."
        assert ad._cover_for({}, prof) == "Generic."
        assert ad._cover_for({}, {}) == ""

    def test_a_blank_override_falls_back_rather_than_emptying_the_field(self):
        import apply_direct as ad
        assert ad._cover_for({"cover_override": "   "}, {"default_cover": "G"}) == "G"

    def test_cover_is_filled_before_screener_matching(self):
        # otherwise "Why are you interested in this role?" gets answered from the
        # answer bank with a one-liner instead of the letter written for this job
        src = (ROOT / "agents" / "apply_direct.py").read_text()
        seg = src.split("def _answer_screeners", 1)[1].split("\ndef ", 1)[0]
        # anchored on the CALL SITES: the docstring names answer_for too
        assert seg.index("ats_forms.is_cover_field(label)") \
            < seg.index("ats_forms.answer_for(label")

    def test_resume_choice_goes_through_the_one_decider(self):
        src = (ROOT / "agents" / "apply_direct.py").read_text()
        seg = src.split("def _resume_path", 1)[1].split("\ndef ", 1)[0]
        assert "resume_library.resume_for_mode" in seg
        # and still cannot upload anything outside store/
        assert "is_relative_to" in seg


class TestJobDoctor:
    """One command that says why applications are not landing. Every failure mode in
    this pipeline is silent: a thin profile, an empty answer bank, a missing resume and
    an unrendered variant library all look identical from the outside."""

    def _load(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import job_doctor
        job_doctor._rows.clear()
        return job_doctor

    def test_it_reports_rather_than_changes_anything(self):
        src = (ROOT / "tools" / "job_doctor.py").read_text()
        body = src.split('"""', 2)[2]
        for banned in ("write_text(", "unlink(", "set_status", "rmtree", ".fill("):
            assert banned not in body, f"the doctor must not act: {banned}"

    def test_a_missing_resume_is_blocking_not_advisory(self, monkeypatch, tmp_path):
        jd = self._load()
        monkeypatch.setattr(jd, "STORE", tmp_path)
        jd.check_resume()
        assert jd._rows[0][0] == "FAIL"

    def test_an_empty_answer_bank_is_blocking(self, monkeypatch, tmp_path):
        jd = self._load()
        monkeypatch.setattr(jd, "STORE", tmp_path)
        jd.check_answer_bank()
        assert jd._rows[0][0] == "FAIL"
        assert "--seed" in jd._rows[0][3]

    def test_a_thin_profile_warns_with_the_specific_fields(self, monkeypatch, tmp_path):
        jd = self._load()
        (tmp_path / "application_profile.json").write_text(json.dumps(
            {"first_name": "A", "last_name": "B", "email": "a@b.co"}))
        monkeypatch.setattr(jd, "STORE", tmp_path)
        jd.check_profile()
        state, _name, detail, _fix = jd._rows[0]
        assert state == "WARN"
        assert "work_authorization" in detail or "phone" in detail

    def test_missing_identity_fields_are_blocking(self, monkeypatch, tmp_path):
        jd = self._load()
        (tmp_path / "application_profile.json").write_text(json.dumps({"phone": "x"}))
        monkeypatch.setattr(jd, "STORE", tmp_path)
        jd.check_profile()
        assert jd._rows[0][0] == "FAIL"

    def test_every_check_carries_a_fix_when_it_fails(self, monkeypatch, tmp_path):
        jd = self._load()
        monkeypatch.setattr(jd, "STORE", tmp_path)
        for fn in (jd.check_profile, jd.check_answer_bank, jd.check_resume):
            fn()
        for state, name, _d, fix in jd._rows:
            if state == "FAIL":
                assert fix, f"{name} fails without telling anyone how to fix it"

    def test_one_broken_check_does_not_hide_the_others(self, monkeypatch):
        jd = self._load()
        monkeypatch.setattr(jd, "check_profile",
                            lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        monkeypatch.setattr(sys, "argv", ["job_doctor"])
        jd.main()
        assert len(jd._rows) > 3, "a raising check aborted the whole run"


class TestBrowserLayerRails:
    def test_radios_and_checkboxes_are_never_auto_clicked(self):
        src = (ROOT / "agents" / "apply_direct.py").read_text()
        seg = src.split("def _answer_screeners", 1)[1].split("\ndef ", 1)[0]
        assert 'if kind in ("checkbox", "radio")' in seg
        assert "never auto-click" in seg

    def test_only_empty_controls_are_touched(self):
        src = (ROOT / "agents" / "apply_direct.py").read_text()
        seg = src.split("def _answer_screeners", 1)[1].split("\ndef ", 1)[0]
        assert "el.input_value()" in seg and "continue" in seg

    def test_a_dropdown_never_settles_for_the_first_option(self):
        src = (ROOT / "agents" / "apply_direct.py").read_text()
        seg = src.split("def _answer_screeners", 1)[1].split("\ndef ", 1)[0]
        assert "never the first one" in seg
        assert "return null" in seg, "no match must select nothing"

    def test_the_control_sweep_is_bounded(self):
        src = (ROOT / "agents" / "apply_direct.py").read_text()
        seg = src.split("def _answer_screeners", 1)[1].split("\ndef ", 1)[0]
        assert "[:60]" in seg
