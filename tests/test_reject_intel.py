#!/usr/bin/env python3
"""Unit tests for the D-lane reject-intel builds (2026-07-07 audit).

BUILD 1 -- rejection-reason classifier + learning loop:
  * job_replies.classify_reject_reason() maps a rejection email BODY to a fixed
    taxonomy (state_restriction | overqualified | underqualified |
    position_filled | competitive_no_reason | ghost), regex-only, "" on a miss
    so the caller can fall through to a single capped LLM call.
  * job_fit_signals.rebuild_auto_blocklist() blocklists a company after ONE
    state_restriction rejection (Alex keeps his SD domicile, so a geo-reject
    recurs -- one is enough), in addition to the original rejected-2x rule.

BUILD 2 -- state-eligibility pre-filter:
  * job_fit_signals.state_eligibility_reason(job) skips only when the job's own
    text/structured-states AFFIRMATIVELY prove South Dakota is ineligible;
    fail-open ("") on missing/ambiguous data.
  * gated behind store/config.json:job_state_filter (default OFF): off => the
    filter never feeds the apply-time skip path.

The 3 real state-restriction phrasings (Veracity/Maze/Sift) and the real soft-no
(Constructor "decided not to move forward") are pinned as regression fixtures.

Pure-function + tmp-store; NO live Gmail, NO network, NO real store/ touched.
planner._cli is monkeypatched everywhere it could be reached.
Run: .venv/bin/python -m pytest tests/test_reject_intel.py -v
"""
from __future__ import annotations

import os
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents", Path(os.environ.get("GMAIL_LIB") or (ROOT / "gmail"))):
    sys.path.insert(0, str(p))

import job_fit_signals as jfs  # noqa: E402
import job_replies  # noqa: E402
import jobs  # noqa: E402
import planner  # noqa: E402

# The real rejection snippets pulled from store/jobs.jsonl (2026-07-07). These
# are the exact phrasings the pipeline actually receives, kept as regression
# fixtures so a taxonomy retune can't silently regress the geo family.
REAL_STATE_RESTRICTION = [
    # Veracity Insurance Solutions
    ("we are only able to hire in the states listed in our posting"),
    # Maze
    ("some of our positions require candidates to be located in specific locales"),
    # Sift
    ("you are not located in a region where Sift is registered to employ individuals"),
]
REAL_SOFT_NO = "After reviewing your application, we've decided not to move forward at this time."


class TestClassifyRejectReason:
    """BUILD 1: the regex taxonomy classifier."""

    def test_real_state_restriction_phrasings(self):
        for body in REAL_STATE_RESTRICTION:
            assert job_replies.classify_reject_reason(body) == "state_restriction", body

    def test_decided_not_to_move_forward_is_competitive_no_reason(self):
        assert job_replies.classify_reject_reason(REAL_SOFT_NO) == "competitive_no_reason"

    def test_more_state_restriction_templates(self):
        for body in (
            "Unfortunately, while most of our roles are remote, we can only hire in a few states.",
            "You must reside in one of the states where we are registered to employ.",
            "We are unable to offer employment outside of our approved states.",
        ):
            assert job_replies.classify_reject_reason(body) == "state_restriction", body

    def test_position_filled(self):
        assert job_replies.classify_reject_reason(
            "Thank you for applying. This position has been filled.") == "position_filled"

    def test_competitive_variants(self):
        for body in (
            "We have decided to move forward with other candidates.",
            "After much consideration, we've decided not to proceed.",
            "We will not be moving forward with your application.",
        ):
            assert job_replies.classify_reject_reason(body) == "competitive_no_reason", body

    def test_state_restriction_wins_over_generic_footer(self):
        # A geo-reject that ALSO carries the generic soft-no boilerplate must
        # classify as state_restriction (it's the load-bearing, recurring reason).
        body = ("We are only able to hire in the states listed. "
                "We have decided not to move forward with your application at this time.")
        assert job_replies.classify_reject_reason(body) == "state_restriction"

    def test_miss_returns_empty_for_llm_fallback(self):
        # No confident regex match -> "" so the caller can fall through to the LLM.
        assert job_replies.classify_reject_reason("Thanks for your time chatting today!") == ""
        assert job_replies.classify_reject_reason("") == ""
        assert job_replies.classify_reject_reason(None) == ""


class TestStateEligibilityReason:
    """BUILD 2: the pre-filter. FAIL-OPEN is the contract -- never over-skip."""

    def test_skips_when_sd_absent_from_prose_allow_list(self):
        r = jfs.state_eligibility_reason(
            {"description": "You must reside in one of: California, Texas, New York, Florida."})
        assert r and "state_ineligible" in r

    def test_allows_when_sd_present_in_prose_allow_list(self):
        assert jfs.state_eligibility_reason(
            {"description": "You must reside in one of: California, South Dakota, Texas."}) == ""

    def test_skips_when_sd_named_in_deny_list(self):
        r = jfs.state_eligibility_reason(
            {"description": "This role is not available in South Dakota or Alaska."})
        assert r and "state_ineligible" in r

    def test_allows_when_deny_list_excludes_other_states(self):
        assert jfs.state_eligibility_reason(
            {"description": "This role is not available in California."}) == ""

    def test_allows_when_no_description(self):
        assert jfs.state_eligibility_reason({}) == ""
        assert jfs.state_eligibility_reason({"description": ""}) == ""

    def test_allows_when_no_geo_language(self):
        assert jfs.state_eligibility_reason(
            {"description": "Fully remote marketing role, competitive pay, great team."}) == ""

    def test_structured_states_skip_only_with_restriction_prose(self):
        # states list without SD + restriction language -> skip
        r = jfs.state_eligibility_reason(
            {"description": "You must reside in one of the following states.",
             "workplace_states": ["New York, US", "Texas, US"]})
        assert r and "state_ineligible" in r

    def test_structured_states_allow_when_sd_present(self):
        assert jfs.state_eligibility_reason(
            {"description": "You must reside in one of the following states.",
             "workplace_states": ["South Dakota, US", "Texas, US"]}) == ""

    def test_structured_office_tag_without_restriction_does_not_skip(self):
        # A bare single-state office tag on an otherwise-remote role must NOT skip.
        assert jfs.state_eligibility_reason(
            {"description": "Great remote role, join our team.",
             "workplace_states": ["New York, US"]}) == ""

    def test_allow_leadin_with_list_elsewhere_does_not_overskip(self):
        # Lead-in in prose but the enumeration lives in the structured field WITH
        # SD present -> must ALLOW (regression for the over-skip bug).
        assert jfs.state_eligibility_reason(
            {"description": "Must reside in one of the following states.",
             "workplace_states": ["South Dakota, US"]}) == ""


class TestConfigKnobGatesWiring:
    """BUILD 2: job_state_filter (default OFF) gates whether the filter feeds the
    apply-time skip path (extra_block_reason)."""

    def _wire(self, tmp_path, monkeypatch, knob):
        (tmp_path / "store").mkdir(exist_ok=True)
        cfg = tmp_path / "store" / "config.json"  # _state_filter_on reads ROOT/store/config.json
        cfg.write_text(json.dumps({"job_state_filter": knob} if knob is not None else {}))
        # point the module's ROOT/config read at the tmp config
        monkeypatch.setattr(jfs, "ROOT", tmp_path)
        # neutralize the other additive guards so we isolate the state check:
        monkeypatch.setattr(jfs, "keyword_mismatch_reason", lambda j, *a, **k: "")
        monkeypatch.setattr(jfs, "salary_gate_reason", lambda j: "")
        monkeypatch.setattr(jfs, "blocklist_reason", lambda j: "")

    INELIGIBLE = {"company": "GeoCo", "title": "Marketing Manager",
                  "description": "You must reside in one of: California, Texas, New York."}

    def test_off_by_default_no_config(self, tmp_path, monkeypatch):
        self._wire(tmp_path, monkeypatch, None)  # no key at all
        assert jfs._state_filter_on() is False
        assert jfs.extra_block_reason(dict(self.INELIGIBLE)) == ""  # NOT skipped when off

    def test_off_explicit_false(self, tmp_path, monkeypatch):
        self._wire(tmp_path, monkeypatch, False)
        assert jfs.extra_block_reason(dict(self.INELIGIBLE)) == ""

    def test_on_true_feeds_skip_path(self, tmp_path, monkeypatch):
        self._wire(tmp_path, monkeypatch, True)
        assert jfs._state_filter_on() is True
        r = jfs.extra_block_reason(dict(self.INELIGIBLE))
        assert r and "state_ineligible" in r

    def test_on_but_eligible_job_not_skipped(self, tmp_path, monkeypatch):
        self._wire(tmp_path, monkeypatch, True)
        ok = {"company": "GoodCo", "title": "Marketing Manager",
              "description": "Fully remote, hiring nationwide including South Dakota."}
        assert jfs.extra_block_reason(ok) == ""


class TestBlocklistLearningLoop:
    """BUILD 1: one state_restriction rejection blocklists that employer."""

    def _wire_store(self, tmp_path, monkeypatch, jobs_list):
        monkeypatch.setattr(jobs, "load_jobs", lambda: jobs_list)
        monkeypatch.setattr(jfs, "BLOCKLIST_STORE", tmp_path / "job_blocklist.json")

    def test_single_state_restriction_rejection_blocklists_company(self, tmp_path, monkeypatch):
        jobs_list = [
            {"id": "j1", "company": "Sift", "status": "rejected",
             "reject_reason": "state_restriction"},
            # a plain single competitive rejection at another co must NOT block (needs 2)
            {"id": "j2", "company": "Constructor", "status": "rejected",
             "reject_reason": "competitive_no_reason"},
        ]
        self._wire_store(tmp_path, monkeypatch, jobs_list)
        auto = jfs.rebuild_auto_blocklist()
        assert jfs._conorm("Sift") in auto
        assert jfs._conorm("Constructor") not in auto
        # and the block actually fires for a NEW approved job at that company
        assert jfs.blocklist_reason({"company": "Sift"}).startswith("blocklisted")

    def test_two_competitive_rejections_still_block(self, tmp_path, monkeypatch):
        # the original rejected-2x rule must survive the geo addition
        jobs_list = [
            {"id": "a", "company": "Acme", "status": "rejected", "reject_reason": "competitive_no_reason"},
            {"id": "b", "company": "Acme", "status": "rejected", "reject_reason": "competitive_no_reason"},
        ]
        self._wire_store(tmp_path, monkeypatch, jobs_list)
        auto = jfs.rebuild_auto_blocklist()
        assert jfs._conorm("Acme") in auto

    def test_one_competitive_rejection_does_not_block(self, tmp_path, monkeypatch):
        jobs_list = [{"id": "a", "company": "Solo", "status": "rejected",
                      "reject_reason": "competitive_no_reason"}]
        self._wire_store(tmp_path, monkeypatch, jobs_list)
        assert jfs._conorm("Solo") not in jfs.rebuild_auto_blocklist()


class TestMultiLocationDedupePicksOneWinner:
    """R2-18 (2026-07-13 hunt): two location-variant reposts that are BOTH still 'approved'
    used to mutually block each other (each saw the OTHER as a dup) -> zero candidates
    survived, instead of the intended one. Now exactly one deterministic winner survives."""

    def test_two_approved_variants_exactly_one_survives(self):
        a = {"id": "a", "company": "Sidetrade", "title": "VP Demand Generation - Paris",
             "status": "approved", "created": "2026-07-01T00:00:00+00:00"}
        b = {"id": "b", "company": "Sidetrade", "title": "VP Demand Generation - Calgary",
             "status": "approved", "created": "2026-07-02T00:00:00+00:00"}
        all_jobs = [a, b]
        ra, rb = jfs.multi_location_dupe_reason(a, all_jobs), jfs.multi_location_dupe_reason(b, all_jobs)
        # exactly one of the two is blocked -- never both (the old bug) and never neither
        assert (ra == "") != (rb == "")

    def test_winner_is_the_earliest_created(self):
        a = {"id": "a", "company": "Sidetrade", "title": "VP Demand Generation - Paris",
             "status": "approved", "created": "2026-07-01T00:00:00+00:00"}
        b = {"id": "b", "company": "Sidetrade", "title": "VP Demand Generation - Calgary",
             "status": "approved", "created": "2026-07-02T00:00:00+00:00"}
        all_jobs = [a, b]
        assert jfs.multi_location_dupe_reason(a, all_jobs) == ""              # earliest: survives
        assert "multi_location_dupe" in jfs.multi_location_dupe_reason(b, all_jobs)  # later: blocked

    def test_already_submitted_variant_always_beats_a_merely_approved_one(self):
        # even with a LATER created timestamp, "already applied" must still outrank "still
        # approved" -- resubmitting to the same effective role is the exact harm being
        # prevented, and the submitted one already happened.
        applied = {"id": "a", "company": "Sidetrade", "title": "VP Demand Generation - Paris",
                   "status": "applied", "created": "2026-07-05T00:00:00+00:00"}
        approved = {"id": "b", "company": "Sidetrade", "title": "VP Demand Generation - Calgary",
                    "status": "approved", "created": "2026-07-01T00:00:00+00:00"}
        all_jobs = [applied, approved]
        assert jfs.multi_location_dupe_reason(approved, all_jobs) != ""
        assert jfs.multi_location_dupe_reason(applied, all_jobs) == ""

    def test_unrelated_jobs_never_flagged(self):
        a = {"id": "a", "company": "Sidetrade", "title": "VP Demand Generation - Paris",
             "status": "approved", "created": "2026-07-01T00:00:00+00:00"}
        c = {"id": "c", "company": "OtherCo", "title": "Marketing Manager",
             "status": "approved", "created": "2026-07-01T00:00:00+00:00"}
        assert jfs.multi_location_dupe_reason(a, [a, c]) == ""
        assert jfs.multi_location_dupe_reason(c, [a, c]) == ""


class TestPersistBodyAndLLMFallback:
    """BUILD 1 plumbing: body persistence is capped/flocked, LLM fallback only
    returns a valid taxonomy label and is never called on a regex hit."""

    def test_persist_body_is_capped_and_keyed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(job_replies, "REJECT_BODIES", tmp_path / "rejection_bodies.jsonl")
        big = "x" * 9000
        job_replies._persist_reject_body("job-9", "BigCo", "state_restriction", big)
        lines = (tmp_path / "rejection_bodies.jsonl").read_text().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["job_id"] == "job-9" and rec["reject_reason"] == "state_restriction"
        assert len(rec["body"]) == 4000  # capped at ~4KB

    def test_llm_fallback_only_accepts_taxonomy_labels(self, monkeypatch):
        monkeypatch.setattr(planner, "_cli", lambda *a, **k: "  State_Restriction  ")
        assert job_replies._reason_llm("some novel phrasing") == "state_restriction"
        monkeypatch.setattr(planner, "_cli", lambda *a, **k: "I think it was a rejection maybe")
        assert job_replies._reason_llm("some novel phrasing") == ""  # not a valid label -> ""
        monkeypatch.setattr(planner, "_cli", lambda *a, **k: None)
        assert job_replies._reason_llm("x") == ""  # cli offline -> ""


class TestRunLoopEndToEnd:
    """BUILD 1 full wiring: run() reads the full body, writes reject_reason,
    persists the body, and rebuilds the blocklist -- all against a tmp store
    with Gmail + planner fully monkeypatched (no live calls)."""

    def test_state_restriction_rejection_flows_end_to_end(self, tmp_path, monkeypatch):
        q = tmp_path / "jobs.jsonl"
        q.write_text(json.dumps(
            {"id": "sift-1", "company": "Sift", "title": "SEO", "status": "applied"}) + "\n")
        monkeypatch.setattr(jobs, "QUEUE", q)
        monkeypatch.setattr(job_replies, "REJECT_BODIES", tmp_path / "rejection_bodies.jsonl")
        monkeypatch.setattr(job_replies, "SEEN", tmp_path / "seen.json")
        monkeypatch.setattr(jfs, "BLOCKLIST_STORE", tmp_path / "job_blocklist.json")

        body = ("Hi Alex, Unfortunately, you are not located in a region where Sift is "
                "registered to employ individuals. Thank you.")
        monkeypatch.setattr(job_replies.gmail_api, "search", lambda *a, **k: [{"id": "m1"}])
        monkeypatch.setattr(job_replies.gmail_api, "get_message", lambda i: {
            "id": "m1", "from": "no-reply@ashbyhq.com", "subject": "Update from Sift",
            "snippet": body[:120], "body": body})
        monkeypatch.setattr(planner, "_cli",
                            lambda *a, **k: json.dumps([{"company": "Sift", "type": "rejection"}]))
        monkeypatch.setattr(planner, "notify", lambda *a, **k: True)
        monkeypatch.setattr(planner, "feed_add", lambda *a, **k: None)
        monkeypatch.setattr(job_replies.job_mail_patterns, "classify", lambda *a, **k: "rejection")

        flips = job_replies.run()
        assert flips == ["Sift->rejected"]

        rec = {r["id"]: r for r in
               (json.loads(x) for x in q.read_text().splitlines() if x.strip())}["sift-1"]
        assert rec["status"] == "rejected"
        assert rec["reject_reason"] == "state_restriction"

        bodies = (tmp_path / "rejection_bodies.jsonl").read_text().splitlines()
        assert len(bodies) == 1 and json.loads(bodies[0])["job_id"] == "sift-1"

        bl = json.loads((tmp_path / "job_blocklist.json").read_text())
        assert jfs._conorm("Sift") in bl["auto_rejected_2x"]  # blocklisted on ONE geo-reject


    def test_geo_reject_misread_as_confirmation_is_upgraded(self, tmp_path, monkeypatch):
        # The Airship class: fast-path/LLM score a geo-reject (with a thanks
        # footer) as CONFIRMATION; run() must upgrade it to rejected and tag the
        # reason, so a rescan self-heals the mis-marked record.
        q = tmp_path / "jobs.jsonl"
        q.write_text(json.dumps(
            {"id": "air-1", "company": "Airship", "title": "Growth", "status": "applied"}) + "\n")
        monkeypatch.setattr(jobs, "QUEUE", q)
        monkeypatch.setattr(job_replies, "REJECT_BODIES", tmp_path / "rejection_bodies.jsonl")
        monkeypatch.setattr(job_replies, "SEEN", tmp_path / "seen.json")
        monkeypatch.setattr(jfs, "BLOCKLIST_STORE", tmp_path / "job_blocklist.json")

        body = ("Hi Alex, Thank you for applying to Airship! Although we are an all-remote "
                "company, some of our positions require candidates to be located in specific "
                "locales, and we are only able to hire in the states listed.")
        monkeypatch.setattr(job_replies.gmail_api, "search", lambda *a, **k: [{"id": "m1"}])
        monkeypatch.setattr(job_replies.gmail_api, "get_message", lambda i: {
            "id": "m1", "from": "no-reply@greenhouse.io", "subject": "Airship application",
            "snippet": body[:120], "body": body})
        monkeypatch.setattr(planner, "notify", lambda *a, **k: True)
        monkeypatch.setattr(planner, "feed_add", lambda *a, **k: None)
        # BOTH detectors say "confirmation" -- the upgrade must still fire off the body.
        monkeypatch.setattr(planner, "_cli",
                            lambda *a, **k: json.dumps([{"company": "Airship", "type": "confirmation"}]))
        monkeypatch.setattr(job_replies.job_mail_patterns, "classify", lambda *a, **k: "confirmation")

        flips = job_replies.run()
        assert flips == ["Airship->rejected"]
        rec = {r["id"]: r for r in
               (json.loads(x) for x in q.read_text().splitlines() if x.strip())}["air-1"]
        assert rec["status"] == "rejected"
        assert rec["reject_reason"] == "state_restriction"


class TestPerMessageWriteIsAtomicAgainstConcurrentWriters:
    """R1#5 (regression, post-17bf56c): the reject-reason LLM fallback (_reason_llm) can take
    up to 60s between run() reading a job's current status and this message's eventual write
    -- a real gap a concurrent writer (another job_replies run, job_rescan) can land a newer
    status in. expect=cur now makes the check-then-write atomic (one lock) against what THIS
    message actually observed, so a slow rejection write can't clobber a status that moved on
    meanwhile."""

    def test_slow_reason_llm_cannot_clobber_a_status_that_moved_on_meanwhile(self, tmp_path, monkeypatch):
        q = tmp_path / "jobs.jsonl"
        q.write_text(json.dumps(
            {"id": "race-1", "company": "Raceco", "title": "Growth", "status": "applied"}) + "\n")
        monkeypatch.setattr(jobs, "QUEUE", q)
        monkeypatch.setattr(job_replies, "REJECT_BODIES", tmp_path / "rejection_bodies.jsonl")
        monkeypatch.setattr(job_replies, "SEEN", tmp_path / "seen.json")
        monkeypatch.setattr(jfs, "BLOCKLIST_STORE", tmp_path / "job_blocklist.json")

        # a rejection body the regex taxonomy CANNOT classify -> forces the capped LLM
        # reason-fallback (_reason_llm), which is where the real-world slow gap lives.
        body = "We are sorry to say we will not be able to extend an offer at this time."
        monkeypatch.setattr(job_replies.gmail_api, "search", lambda *a, **k: [{"id": "m1"}])
        monkeypatch.setattr(job_replies.gmail_api, "get_message", lambda i: {
            "id": "m1", "from": "no-reply@ashbyhq.com", "subject": "Update from Raceco",
            "snippet": body[:120], "body": body})
        monkeypatch.setattr(planner, "_cli",
                            lambda *a, **k: json.dumps([{"company": "Raceco", "type": "rejection"}]))
        monkeypatch.setattr(planner, "notify", lambda *a, **k: True)
        monkeypatch.setattr(planner, "feed_add", lambda *a, **k: None)
        monkeypatch.setattr(job_replies.job_mail_patterns, "classify", lambda *a, **k: "rejection")

        def _racy_reason_llm(body_arg):
            # simulate a concurrent writer (e.g. a real human reply landing, or job_rescan)
            # advancing this SAME job to 'interview' WHILE this slow LLM call is in flight.
            q.write_text(json.dumps({"id": "race-1", "company": "Raceco", "title": "Growth",
                                     "status": "interview"}) + "\n")
            return "competitive_no_reason"
        monkeypatch.setattr(job_replies, "_reason_llm", _racy_reason_llm)

        job_replies.run()

        rec = {r["id"]: r for r in
               (json.loads(x) for x in q.read_text().splitlines() if x.strip())}["race-1"]
        # the concurrent writer's 'interview' must survive -- the stale rejection write
        # (decided off the pre-LLM-call snapshot of "applied") must have been skipped, not
        # clobber it.
        assert rec["status"] == "interview"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
