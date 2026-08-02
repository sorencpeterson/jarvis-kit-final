#!/usr/bin/env python3
"""Pytest suite for the E333 pure classification/matching helpers in
agents/meeting_prep.py. No LLM calls (classify_event, _match_words,
_niche_for_contact, _niche_stats, _open_objections, _matching_contact's
exclude_job_only flag are all pure/read-only). Uses the REAL store/*.json
files where useful (small, safe, read-only) and synthetic data for edge
cases the real store doesn't happen to contain right now.

Run: .venv/bin/python -m pytest tests/test_meeting_prep.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import meeting_prep  # noqa: E402


class TestMatchWords:
    def test_filters_short_words(self):
        assert "at" not in meeting_prep._match_words("meet at noon")

    def test_filters_stopwords(self):
        words = meeting_prep._match_words("Call with the team about site updates")
        assert "about" not in words
        assert "with" not in words
        assert "site" not in words
        assert "call" not in words

    def test_keeps_real_proper_nouns(self):
        words = meeting_prep._match_words("Sync with Acme Corporation")
        assert "acme" in words
        assert "corporation" in words

    def test_lowercases(self):
        words = meeting_prep._match_words("ACME Corp")
        assert "acme" in words

    def test_empty_title(self):
        assert meeting_prep._match_words("") == []


class TestClassifyEvent:
    def test_interview_keyword_phone_screen(self):
        assert meeting_prep.classify_event("Phone screen with Acme") == "interview"

    def test_interview_keyword_onsite(self):
        assert meeting_prep.classify_event("Onsite interview") == "interview"

    def test_generic_no_match(self):
        # a title with no interview keyword and no plausible contact/job match
        assert meeting_prep.classify_event("Totally unmatched xyz999qqq title") == "generic"

    def test_stopword_titles_do_not_false_match_sales(self):
        # regression guard for the exact false positives found while
        # verifying E333 against the real store: generic phrasing must
        # never accidentally classify as "sales" via a stopword collision
        for title in ("Weekly check-in meeting", "Doctor appointment",
                      "Call with the team about site updates"):
            assert meeting_prep.classify_event(title) != "sales"


def _has_829():
    """These 3 pin against a REAL live contact_graph entry; on a fresh-install restore
    (empty store/, DR drill) there's no graph, so skip rather than fail (survivability)."""
    try:
        import json
        people = json.loads(meeting_prep.CONTACT_GRAPH.read_text()).get("people", [])
        return any(p.get("name") == "829 Studios" for p in people)
    except (OSError, ValueError):
        return False


@pytest.mark.skipif(not _has_829(), reason="no live contact_graph (fresh install)")
class TestMatchingContactExcludeJobOnly:
    def test_job_only_source_excluded_when_flag_set(self):
        # Regression guard: "829 Studios" is a REAL contact_graph.json entry
        # sourced ONLY from jobs.jsonl (Alex applied there for a job) — it
        # must never register as a sales prospect.
        without = meeting_prep._matching_contact("Meeting about 829 Studios", exclude_job_only=False)
        with_excl = meeting_prep._matching_contact("Meeting about 829 Studios", exclude_job_only=True)
        assert without is not None
        assert without["sources"] == ["jobs"]
        assert with_excl is None or with_excl["sources"] != ["jobs"]

    def test_job_only_source_included_by_default(self):
        contact = meeting_prep._matching_contact("Meeting about 829 Studios")
        assert contact is not None
        assert contact["name"] == "829 Studios"

    def test_multi_source_contact_never_excluded(self):
        # a contact with sources beyond just ["jobs"] must still match
        # regardless of the flag (the exclusion is specifically for
        # sources == ["jobs"] exactly, not "jobs" present among others)
        import json
        people = json.loads(meeting_prep.CONTACT_GRAPH.read_text()).get("people", [])
        multi = next((p for p in people if len(p.get("sources", [])) > 1), None)
        if multi is None:
            return  # no multi-source contact in the current real graph; nothing to assert
        title = f"Call about {multi['name']}"
        with_excl = meeting_prep._matching_contact(title, exclude_job_only=True)
        assert with_excl is not None


class TestNicheHelpers:
    def test_niche_for_contact_present(self):
        assert meeting_prep._niche_for_contact({"niche": "hvac"}) == "hvac"

    def test_niche_for_contact_absent(self):
        assert meeting_prep._niche_for_contact({}) is None

    def test_niche_for_contact_none(self):
        assert meeting_prep._niche_for_contact(None) is None

    def test_niche_stats_real_data(self):
        # store/niche_db.json is real and has a "consulting" niche as of
        # this writing; if that ever changes this just returns None, which
        # is still a valid, non-crashing result.
        result = meeting_prep._niche_stats("consulting")
        assert result is None or isinstance(result, dict)

    def test_niche_stats_missing_niche(self):
        assert meeting_prep._niche_stats("definitely-not-a-real-niche-xyz") is None

    def test_niche_stats_none_input(self):
        assert meeting_prep._niche_stats(None) is None


class TestOpenObjections:
    def test_empty_file_returns_empty_list(self, tmp_path, monkeypatch):
        # state-independent: point at a genuinely empty/absent file rather than assuming the
        # real store/objections.jsonl is empty (an agent can log a real objection any day, and
        # that used to flip this test red -- 2026-07-07)
        monkeypatch.setattr(meeting_prep, "OBJECTIONS", tmp_path / "objections.jsonl")
        assert meeting_prep._open_objections("hvac") == []

    def test_niche_filter_and_fallback(self, tmp_path, monkeypatch):
        f = tmp_path / "objections.jsonl"
        f.write_text('{"niche":"hvac","text":"too expensive"}\n'
                     '{"niche":"dental","text":"already have a guy"}\n')
        monkeypatch.setattr(meeting_prep, "OBJECTIONS", f)
        # exact-niche match filters
        assert meeting_prep._open_objections("hvac") == ["too expensive"]
        # unknown niche falls back to all (better a few generic than none)
        assert set(meeting_prep._open_objections("roofing")) == {"too expensive", "already have a guy"}

    def test_none_niche_still_returns_list(self):
        result = meeting_prep._open_objections(None)
        assert isinstance(result, list)


class TestMatchingJob:
    def test_no_jobs_file_or_no_match(self):
        result = meeting_prep._matching_job("Totally unmatched xyz999qqq nothing")
        assert result is None or isinstance(result, dict)

    def test_stopword_title_does_not_false_match(self):
        # regression guard for the exact false positive found while
        # verifying: "site" alone must not match "...onsite..." in an
        # unrelated job title via bare substring containment
        result = meeting_prep._matching_job("Call with the team about their site")
        # words extracted are now stopword-filtered ("site"/"call"/"about"/
        # "their" all excluded), so this should not match purely on those words
        if result is not None:
            blob = f"{result.get('company', '')} {result.get('title', '')}".lower()
            real_words = meeting_prep._match_words("Call with the team about their site")
            assert any(w in blob for w in real_words), "matched without a real word overlap"
