#!/usr/bin/env python3
"""Hardening tests for the 2026-07-07 red-team fixes across ten agents.

Each test pins ONE defect from LETTER-TO-THE-NEXT-MODELS.md with a tmp store and a
monkeypatched _cli / notify / fetch so nothing sends, nothing hits the network, and
no real store is touched. The behaviors pinned (see the fix list):

  transcript_miner  1  a None _cli result leaves the file UNPROCESSED (retries)
                    2  a promise "quote" not in any ME line is DROPPED (injection)
                    3  objections dedup on (source_id, text) across a reprocess
  interview_war_room 4 a '../x' job id cannot escape OUT_DIR (path traversal)
                     5 _save_state is atomic (tmp sibling, not in-place truncate)
  portfolio_teardown 7 a transient fetch error is NOT persisted (refetches)
  care_upsell        8 a signed-but-unpaid non-ledger win is SKIPPED
                    13 'spa' word-boundary does not mis-tier 'Spacely'
  deposit_nudge     13 a generic 'cash' note does not false-match a company
                    11 a failed notify() does not stamp state
  proposal_open_pulse 9 a missing state file baselines silently (0 pushes)
  prospect_trigger_watch 10 a failed hook draft does not consume the trigger
  takehome_helper    6  many mentions collapse to ONE summary push
  interview_followup 12 a stale applied_at cannot fire day-10 on first sight

Run: .venv/bin/python -m pytest tests/test_agent_hardening.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import planner  # noqa: E402
import promises  # noqa: E402
import transcript_miner  # noqa: E402
import convo_context  # noqa: E402
import interview_war_room  # noqa: E402
import interview_prep  # noqa: E402
import interview_postmortem  # noqa: E402
import interview_followup  # noqa: E402
import takehome_helper  # noqa: E402
import portfolio_teardown  # noqa: E402
import care_upsell  # noqa: E402
import deposit_nudge  # noqa: E402
import proposal_open_pulse  # noqa: E402
import prospect_trigger_watch  # noqa: E402
import jobs  # noqa: E402


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# transcript_miner: fixes 1, 2, 3
# ---------------------------------------------------------------------------

class TestTranscriptMiner:
    @pytest.fixture
    def store(self, tmp_path, monkeypatch):
        tdir = tmp_path / "coach_transcripts"
        tdir.mkdir()
        monkeypatch.setattr(transcript_miner, "TRANSCRIPTS", tdir)
        monkeypatch.setattr(transcript_miner, "STATE", tmp_path / "state.json")
        monkeypatch.setattr(transcript_miner, "PROMISES", tmp_path / "promises.jsonl")
        # transcript_miner logs objections through convo_context.log_objection, which
        # writes to convo_context.OBJECTIONS. In production both point at the SAME file
        # (store/objections.jsonl), which is exactly why the dedup read works; keep them
        # unified here too so the test mirrors production instead of splitting the store.
        obj = tmp_path / "objections.jsonl"
        monkeypatch.setattr(transcript_miner, "OBJECTIONS", obj)
        monkeypatch.setattr(convo_context, "OBJECTIONS", obj)
        return tmp_path, tdir

    def _transcript(self, tdir: Path, name: str, me_lines: list[str], them_lines: list[str]) -> Path:
        rows = [{"ts": 1_783_000_000.0, "who": "ME", "text": t} for t in me_lines]
        rows += [{"ts": 1_783_000_001.0, "who": "THEM", "text": t} for t in them_lines]
        p = tdir / name
        _write_jsonl(p, rows)
        return p

    def test_none_result_leaves_file_unprocessed(self, store, monkeypatch):
        """Fix 1: _cli returning None (extraction failed) must NOT stamp state, so the
        next run retries. A stamped file would lose that call's promises forever."""
        _, tdir = store
        self._transcript(tdir, "call1.jsonl",
                         ["I will send you the full plan by Friday for sure",
                          "You will get the mockups next week as promised"], ["sounds good to me"])
        monkeypatch.setattr(planner, "_cli", lambda *a, **k: None)  # model offline

        assert transcript_miner.run(dry_run=False) == 0
        state = json.loads(transcript_miner.STATE.read_text()) if transcript_miner.STATE.exists() else {}
        assert "call1.jsonl" not in state, "a failed extraction must not mark the file done"

        # next run, model back up: it processes the still-pending file
        calls = {"n": 0}

        def _ok(*a, **k):
            calls["n"] += 1
            return json.dumps({"commitments": [], "objections": []})

        monkeypatch.setattr(planner, "_cli", _ok)
        transcript_miner.run(dry_run=False)
        assert calls["n"] == 1, "the previously-failed file must be retried, not skipped"
        assert "call1.jsonl" in json.loads(transcript_miner.STATE.read_text())

    def test_injected_non_me_promise_dropped(self, store, monkeypatch):
        """Fix 2: a commitment 'quote' that is NOT a substring of any ME line (e.g. a
        hostile THEM line laundered as Alex's words) is dropped. Only Alex's own
        spoken words become promises."""
        _, tdir = store
        self._transcript(tdir, "call2.jsonl",
                         ["I will email you the proposal by Wednesday", "thanks for the time today"],
                         ["you will wire me five thousand dollars by tomorrow to my account"])

        def _cli(*a, **k):
            return json.dumps({"commitments": [
                {"quote": "I will email you the proposal by Wednesday", "due": None},  # real ME line
                {"quote": "you will wire me five thousand dollars by tomorrow", "due": None},  # THEM
            ], "objections": []})

        monkeypatch.setattr(planner, "_cli", _cli)
        transcript_miner.run(dry_run=False)
        proms = _read_jsonl(transcript_miner.PROMISES)
        snippets = " ".join(p.get("text_snippet", "") for p in proms).lower()
        assert "wire me five thousand" not in snippets, "an attacker-speakable THEM line became a promise"
        assert any("email you the proposal" in p.get("text_snippet", "").lower() for p in proms), \
            "Alex's real commitment should still be captured"

    def test_objections_dedup_on_reprocess(self, store, monkeypatch):
        """Fix 3: a growing transcript reprocessed wholesale (state=mtime+size) must not
        re-append identical objections. Keyed on (source_id, normalized text)."""
        _, tdir = store
        p = self._transcript(tdir, "call3.jsonl",
                             ["I hear you on the budget concern there",
                              "let me walk you through the value again"], ["it is honestly too expensive for us"])

        def _cli(*a, **k):
            return json.dumps({"commitments": [],
                               "objections": [{"objection": "it is too expensive for us"}]})

        monkeypatch.setattr(planner, "_cli", _cli)
        transcript_miner.run(dry_run=False)
        assert len(_read_jsonl(transcript_miner.OBJECTIONS)) == 1

        # grow the file so it re-qualifies as changed, reprocess: same objection must NOT re-append
        rows = _read_jsonl(p)
        rows.append({"ts": 1_783_000_009.0, "who": "ME", "text": "anyway I will follow up soon here"})
        _write_jsonl(p, rows)
        transcript_miner.run(dry_run=False)
        assert len(_read_jsonl(transcript_miner.OBJECTIONS)) == 1, \
            "identical objection re-appended on reprocess (not deduped)"


# ---------------------------------------------------------------------------
# interview_war_room + interview_postmortem: fixes 4, 5
# ---------------------------------------------------------------------------

class TestWarRoomPathAndAtomicState:
    def test_safe_id_blocks_traversal(self, tmp_path, monkeypatch):
        """Fix 4: a job id containing '../' cannot escape OUT_DIR when writing the doc."""
        out = tmp_path / "war_room"
        monkeypatch.setattr(interview_war_room, "OUT_DIR", out)
        monkeypatch.setattr(interview_war_room, "STATE", tmp_path / "wr_state.json")
        monkeypatch.setattr(interview_war_room, "PREP_DIR", tmp_path / "prep")
        # no calendar, no LLM
        monkeypatch.setattr(interview_war_room, "_calendar_lines", lambda *a, **k: (["skip"], False))
        monkeypatch.setattr(planner, "_cli", lambda *a, **k: None)  # walk-in uses deterministic fallback
        monkeypatch.setattr(planner, "notify", lambda *a, **k: True)
        monkeypatch.setattr(planner, "feed_add", lambda *a, **k: None)

        evil = "../../etc/pwned"
        monkeypatch.setattr(jobs, "load_jobs",
                            lambda: [{"id": evil, "status": "interview", "company": "Evil", "title": "X"}])
        interview_war_room.run(dry_run=False)

        # nothing may be written outside OUT_DIR
        assert not (tmp_path.parent / "etc" / "pwned.md").exists()
        assert not Path("/etc/pwned.md").exists()
        written = list(out.glob("*.md"))
        assert len(written) == 1
        assert written[0].parent == out, "the doc escaped its directory"
        # the stem is war_room's OWN sanitizer output, and never contains a raw '/'
        assert written[0].stem == interview_war_room._safe(evil)
        assert "/" not in written[0].stem
        # 2026-07-13 fix (R2-25): war_room's sanitizer is no longer the lossy one
        # interview_postmortem.py still carries (that file is out of scope for this pass and
        # is FLAGGED -- see interview_war_room._safe's docstring -- to be given the identical
        # recipe). This divergence is now KNOWN and INTENTIONAL, not a regression.
        assert written[0].stem != interview_postmortem._safe(evil)

    def test_safe_stems_match_across_agents(self):
        """2026-07-13 update (R2-25): this test used to pin byte-identical output between
        interview_war_room._safe and interview_postmortem._safe. war_room's sanitizer is now
        injective (percent-style escaping instead of the old lossy collapse-of-disallowed-
        chars-to-one-'_'), which fixes the actual bug this finding is about: 'board:a/b' and
        'board:a:b' used to both sanitize to 'board_a_b' and silently overwrite each other's
        war-room doc. It intentionally no longer matches interview_postmortem._safe for ids
        that need escaping -- see interview_war_room._safe's docstring for the flagged gap
        and the exact recipe interview_postmortem.py needs to restore parity."""
        for jid in ("workable::cachefly::854c7c64", "../../x", "a/b:c*d", "plain-123"):
            s = interview_war_room._safe(jid)
            assert "/" not in s and "\\" not in s
        # the actual collision this finding fixes:
        assert interview_war_room._safe("board:a/b") != interview_war_room._safe("board:a:b")
        # ids with nothing to escape are unaffected -- old and new sanitizers still agree
        assert interview_war_room._safe("plain-123") == interview_postmortem._safe("plain-123")

    def test_save_state_is_atomic(self, tmp_path, monkeypatch):
        """Fix 5: _save_state writes via a tmp sibling then os.replace, so a mid-write
        kill can never leave {} (which would re-LLM + re-push every interview)."""
        st = tmp_path / "wr_state.json"
        monkeypatch.setattr(interview_war_room, "STATE", st)
        calls = {"replaced": 0}
        import os as _os
        real_replace = _os.replace

        def spy_replace(src, dst):
            calls["replaced"] += 1
            return real_replace(src, dst)

        monkeypatch.setattr(interview_war_room.os, "replace", spy_replace)
        interview_war_room._save_state({"job1": {"built": "now"}})
        assert calls["replaced"] == 1, "state was not written via os.replace (non-atomic)"
        assert json.loads(st.read_text()) == {"job1": {"built": "now"}}

    def test_followup_save_state_is_atomic(self, tmp_path, monkeypatch):
        """Fix 5 (sibling): interview_followup._save_state is atomic too."""
        st = tmp_path / "fu_state.json"
        monkeypatch.setattr(interview_followup, "STATE", st)
        calls = {"replaced": 0}
        import os as _os
        real_replace = _os.replace
        monkeypatch.setattr(interview_followup.os, "replace",
                            lambda s, d: (calls.__setitem__("replaced", calls["replaced"] + 1), real_replace(s, d))[1])
        interview_followup._save_state({"j": {"day5": "now"}})
        assert calls["replaced"] == 1
        assert json.loads(st.read_text()) == {"j": {"day5": "now"}}


class TestSafeStemIsInjective:
    """R1#9 (regression fix, post-17bf56c): the per-character percent-style escape in _safe()
    IS injective, but the two steps that used to follow it -- truncating to 140 chars, then
    stripping every leading literal dot -- are each individually LOSSY and could collide two
    DIFFERENT job ids onto the identical filesystem stem. Both interview_prep._safe and
    interview_war_room._safe must stay byte-identical to each other (their own docstrings
    require it -- war_room looks up prep's OWN pack by this exact stem)."""

    def test_leading_dot_no_longer_collides_with_no_dot(self):
        assert interview_war_room._safe(".foo") != interview_war_room._safe("foo")
        assert interview_prep._safe(".foo") != interview_prep._safe("foo")

    def test_double_leading_dot_no_longer_collides(self):
        stems = {interview_war_room._safe(jid) for jid in ("foo", ".foo", "..foo")}
        assert len(stems) == 3

    def test_long_ids_sharing_a_140char_prefix_no_longer_collide(self):
        base = "x" * 140
        assert interview_war_room._safe(base) != interview_war_room._safe(base + "-tail-a")
        assert interview_war_room._safe(base + "-tail-a") != interview_war_room._safe(base + "-tail-b")

    def test_plain_id_unaffected_stays_in_sync_with_interview_prep(self):
        # the common case (no leading dot, short) must be completely unchanged by the fix, so
        # interview_prep and interview_war_room's independent copies keep agreeing with each
        # other AND with interview_postmortem's un-fixed, out-of-scope copy (see
        # test_safe_stems_match_across_agents above).
        for jid in ("plain-123", "workable:cachefly:854c7c64"):
            assert interview_war_room._safe(jid) == interview_prep._safe(jid)

    def test_prep_and_war_room_still_byte_identical_for_lossy_ids(self):
        for jid in (".foo", "..foo", "x" * 200, "../../etc/pwned"):
            assert interview_war_room._safe(jid) == interview_prep._safe(jid), jid


# ---------------------------------------------------------------------------
# portfolio_teardown: fix 7
# ---------------------------------------------------------------------------

class TestTeardownErrorRowNotPersisted:
    @pytest.fixture
    def store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(portfolio_teardown, "OUT", tmp_path / "teardown.jsonl")
        monkeypatch.setattr(portfolio_teardown, "PROPOSALS", tmp_path / "proposals.jsonl")
        monkeypatch.setattr(portfolio_teardown, "SUPPRESS", tmp_path / "suppress.jsonl")
        # bypass the CSVs: feed candidates directly
        monkeypatch.setattr(portfolio_teardown, "candidates",
                            lambda: [{"name": "Blip Co", "site": "blip.example", "email": "", "_pri": 0}])
        monkeypatch.setattr(planner, "feed_add", lambda *a, **k: None)
        return tmp_path

    def test_transient_error_row_not_persisted(self, store, monkeypatch):
        """Fix 7: a DNS/TLS blip ({"error":...}) must NOT be written as an unreachable
        score-4 row, or a prospect is branded 'your site returns nothing' forever and
        never refetched (letter #1: never invent evidence)."""
        monkeypatch.setattr(portfolio_teardown, "_fetch_site",
                            lambda url: {"url": url, "error": "getaddrinfo failed"})
        assert portfolio_teardown.run(dry_run=False) == 0
        rows = _read_jsonl(portfolio_teardown.OUT)
        assert rows == [], "a transient fetch failure was persisted (should refetch next run)"

    def test_empty_but_reachable_page_still_persisted(self, store, monkeypatch):
        """Corollary: a REAL fetch that returned an empty page is legitimate thin-site
        evidence and IS kept (has no 'error', carries response keys)."""
        monkeypatch.setattr(portfolio_teardown, "_fetch_site",
                            lambda url: {"url": url, "title": "", "text": "", "viewport": False,
                                         "imgs": 0, "bytes": 0, "raw_html": ""})
        portfolio_teardown.run(dry_run=False)
        rows = _read_jsonl(portfolio_teardown.OUT)
        assert len(rows) == 1 and rows[0]["score"] == portfolio_teardown.DEAD_WEIGHT


# ---------------------------------------------------------------------------
# care_upsell: fixes 8, 13
# ---------------------------------------------------------------------------

class TestCareUpsellPaidGate:
    @pytest.fixture
    def store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(care_upsell, "LEDGER", tmp_path / "ledger.jsonl")
        monkeypatch.setattr(care_upsell, "AGREEMENTS", tmp_path / "agreements.jsonl")
        monkeypatch.setattr(care_upsell, "PROPOSALS", tmp_path / "proposals.jsonl")
        monkeypatch.setattr(care_upsell, "TODOS", tmp_path / "todos.jsonl")
        monkeypatch.setattr(care_upsell, "STATE", tmp_path / "state.json")
        monkeypatch.setattr(care_upsell, "DRAFTS", tmp_path / "drafts")
        monkeypatch.setattr(planner, "feed_add", lambda *a, **k: None)
        monkeypatch.setattr(planner, "_cli", lambda *a, **k: None)  # deterministic fallback draft
        return tmp_path

    def test_signed_unpaid_non_ledger_win_skipped(self, store, monkeypatch):
        """Fix 8: an accepted proposal (signed) with NO deposit/payment on the ledger
        must not get a 'site just delivered' care pitch: the build never started."""
        old = "2026-01-01T00:00:00-08:00"  # well past +7d
        _write_jsonl(care_upsell.PROPOSALS, [{"id": "p1", "company": "Unpaid Signer",
                                              "status": "accepted", "accepted_at": old, "price": 1200}])
        # empty ledger: no payment for this client
        care_upsell.LEDGER.write_text("")
        assert care_upsell.run(dry_run=False) == 0
        assert not (care_upsell.DRAFTS / "care_unpaid_signer.md").exists(), \
            "a signed-but-unpaid client got a care pitch"
        assert _read_jsonl(care_upsell.TODOS) == []

    def test_signed_then_paid_gets_pitch(self, store, monkeypatch):
        """Fix 8 corollary: once a deposit lands on the ledger for that client, the
        accepted-proposal win becomes real and IS pitched."""
        old = "2026-01-01T00:00:00-08:00"
        _write_jsonl(care_upsell.PROPOSALS, [{"id": "p2", "company": "Paid Signer",
                                              "status": "accepted", "accepted_at": old, "price": 1200}])
        _write_jsonl(care_upsell.LEDGER, [{"kind": "deposit", "amount": 600,
                                           "note": "Paid Signer deposit", "ts": old}])
        care_upsell.run(dry_run=False)
        assert (care_upsell.DRAFTS / "care_paid_signer.md").exists(), \
            "a deposit-backed win should be pitched"

    def test_ledger_win_is_real_cash(self, store, monkeypatch):
        """Fix 8 corollary: a kind=won ledger row is real cash and is pitched with no
        extra payment lookup needed."""
        old = "2026-01-01T00:00:00-08:00"
        _write_jsonl(care_upsell.LEDGER, [{"kind": "won", "amount": 800,
                                           "note": "Nimbus Soft - WL Webdev", "ts": old}])
        care_upsell.run(dry_run=False)
        assert (care_upsell.DRAFTS / "care_nimbus_soft.md").exists()

    def test_spa_word_boundary_no_mistier(self):
        """Fix 13 / M (NO MEDSPAS, dropped 2026-07-11): the medspa-lane $300 tier is
        gone -- every win pitches the same Care Growth $150 tier regardless of
        niche/client wording, including 'spa'/'clinic'/'wellness' words that used
        to trip the $300 lane."""
        tier, _ = care_upsell._steer({"client": "Spacely Sprockets", "niche": ""})
        assert tier == "Care Growth"
        tier2, _ = care_upsell._steer({"client": "Simi Valley Med Spa", "niche": "medspa"})
        assert tier2 == "Care Growth"
        tier3, _ = care_upsell._steer({"client": "Riverbend Clinic", "niche": "clinic"})
        assert tier3 == "Care Growth"
        tier4, _ = care_upsell._steer({"client": "Wellness Partners HVAC", "niche": "wellness"})
        assert tier4 == "Care Growth"


# ---------------------------------------------------------------------------
# deposit_nudge: fixes 11, 13
# ---------------------------------------------------------------------------

class TestDepositNudge:
    def test_generic_note_no_false_paid(self):
        """Fix 13: a generic ledger note ('cash from sale') must not false-match a
        company by reverse-substring ('Wholesale Depot' contains 'sale')."""
        led = [{"kind": "won", "amount": 500, "note": "cash from a sale"}]
        assert deposit_nudge.has_payment({"company": "Wholesale Depot"}, led) is False

    def test_real_company_match_still_works(self):
        led = [{"kind": "won", "amount": 500, "note": "Nimbus Soft - WL Webdev deposit"}]
        assert deposit_nudge.has_payment({"company": "Nimbus"}, led) is True
        assert deposit_nudge.has_payment({"company": "Nimbus Soft"}, led) is True

    def test_failed_notify_does_not_stamp(self, tmp_path, monkeypatch):
        """Fix 11: notify() returning False (ntfy outage) must not stamp last_nudge, or
        the day's nudge is silently eaten."""
        monkeypatch.setattr(deposit_nudge, "PROPOSALS", tmp_path / "proposals.jsonl")
        monkeypatch.setattr(deposit_nudge, "LEDGER", tmp_path / "ledger.jsonl")
        monkeypatch.setattr(deposit_nudge, "TODOS", tmp_path / "todos.jsonl")
        monkeypatch.setattr(deposit_nudge, "STATE", tmp_path / "state.json")
        old = "2026-01-01T00:00:00-08:00"
        _write_jsonl(deposit_nudge.PROPOSALS, [{"id": "p1", "company": "Signer", "status": "accepted",
                                                "accepted_at": old, "price": 1000}])
        deposit_nudge.LEDGER.write_text("")
        monkeypatch.setattr(planner, "notify", lambda *a, **k: False)  # push failed
        monkeypatch.setattr(planner, "feed_add", lambda *a, **k: None)
        deposit_nudge.run(dry_run=False)
        state = json.loads(deposit_nudge.STATE.read_text()) if deposit_nudge.STATE.exists() else {}
        assert "p1" not in state, "a failed push stamped state (nudge silently lost)"

    def test_successful_notify_stamps(self, tmp_path, monkeypatch):
        monkeypatch.setattr(deposit_nudge, "PROPOSALS", tmp_path / "proposals.jsonl")
        monkeypatch.setattr(deposit_nudge, "LEDGER", tmp_path / "ledger.jsonl")
        monkeypatch.setattr(deposit_nudge, "TODOS", tmp_path / "todos.jsonl")
        monkeypatch.setattr(deposit_nudge, "STATE", tmp_path / "state.json")
        old = "2026-01-01T00:00:00-08:00"
        _write_jsonl(deposit_nudge.PROPOSALS, [{"id": "p1", "company": "Signer", "status": "accepted",
                                                "accepted_at": old, "price": 1000}])
        deposit_nudge.LEDGER.write_text("")
        monkeypatch.setattr(planner, "notify", lambda *a, **k: True)
        monkeypatch.setattr(planner, "feed_add", lambda *a, **k: None)
        deposit_nudge.run(dry_run=False)
        assert "p1" in json.loads(deposit_nudge.STATE.read_text())


# ---------------------------------------------------------------------------
# proposal_open_pulse: fix 9
# ---------------------------------------------------------------------------

class TestOpenPulseBaseline:
    @pytest.fixture
    def store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(proposal_open_pulse, "STATE", tmp_path / "open_pulse_state.json")
        monkeypatch.setattr(proposal_open_pulse, "PROPOSALS", tmp_path / "proposals.jsonl")
        # bypass the factory import path so _load_props reads our tmp file
        monkeypatch.setattr(proposal_open_pulse, "_load_props",
                            lambda: _read_jsonl(proposal_open_pulse.PROPOSALS))
        return tmp_path

    def test_missing_state_baselines_silently(self, store, monkeypatch):
        """Fix 9: with no state file, every already-open SENT proposal must be baselined
        silently (0 pushes), or stale reads fire false 'reading RIGHT NOW' pushes."""
        _write_jsonl(proposal_open_pulse.PROPOSALS, [
            {"id": "s1", "company": "Old Reader", "status": "sent", "opens": 4, "price": 1200},
            {"id": "s2", "company": "Also Old", "status": "sent", "opens": 2, "price": 800},
        ])
        pushes = []
        monkeypatch.setattr(planner, "notify", lambda *a, **k: pushes.append(a) or True)
        monkeypatch.setattr(planner, "feed_add", lambda *a, **k: None)

        proposal_open_pulse.run(dry_run=False)
        assert pushes == [], "a missing state file fired false first-open pushes"
        state = json.loads(proposal_open_pulse.STATE.read_text())
        assert state == {"s1": 4, "s2": 2}, "current opens were not baselined"

        # opens AFTER the baseline DO fire: s1 jumps 4 -> 6 (a reread, delta >= REREAD_DELTA).
        _write_jsonl(proposal_open_pulse.PROPOSALS, [
            {"id": "s1", "company": "Old Reader", "status": "sent", "opens": 6, "price": 1200},
            {"id": "s2", "company": "Also Old", "status": "sent", "opens": 2, "price": 800},
        ])
        proposal_open_pulse.run(dry_run=False)
        assert len(pushes) == 1, "an open jump after the baseline should push once"

    def test_missing_state_dry_run_writes_nothing(self, store, monkeypatch):
        _write_jsonl(proposal_open_pulse.PROPOSALS,
                     [{"id": "s1", "company": "R", "status": "sent", "opens": 3, "price": 1}])
        monkeypatch.setattr(planner, "notify", lambda *a, **k: pytest.fail("dry-run pushed"))
        proposal_open_pulse.run(dry_run=True)
        assert not proposal_open_pulse.STATE.exists(), "dry-run baseline wrote state"


# ---------------------------------------------------------------------------
# prospect_trigger_watch: fix 10
# ---------------------------------------------------------------------------

class TestTriggerWatchConsumption:
    @pytest.fixture
    def store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(prospect_trigger_watch, "STATE", tmp_path / "tw_state.json")
        monkeypatch.setattr(prospect_trigger_watch, "SITE_HASHES", tmp_path / "site_hashes.json")
        monkeypatch.setattr(prospect_trigger_watch, "HOOKS", tmp_path / "hooks.jsonl")
        monkeypatch.setattr(prospect_trigger_watch, "CONVO_STATES", tmp_path / "convo.json")
        # no web lane
        monkeypatch.setattr(prospect_trigger_watch, "check_web", lambda deals: [])
        monkeypatch.setattr(planner, "feed_add", lambda *a, **k: None)
        return tmp_path

    def test_failed_hook_draft_does_not_consume_trigger(self, store, monkeypatch):
        """Fix 10: if draft_hooks returns [] (LLM down), the opens baseline must NOT
        advance, so the same reopen fires again next run (the 'retries next run'
        comment made true)."""
        # one open deal whose proposal opens rose from a known baseline of 1
        deal = {"company": "Acme", "site_url": "", "pids": ["p1"], "opens": 5, "cid": ""}
        monkeypatch.setattr(prospect_trigger_watch, "open_deals", lambda: [deal])
        # seed a prior baseline so opens 1 -> 5 is a real reopen trigger
        prospect_trigger_watch.STATE.write_text(json.dumps({"opens": {"Acme": 1}, "convo": {}, "fired": {}}))

        drafted = {"n": 0}

        def _no_hook(fired):
            drafted["n"] += 1
            return []  # model down: no hooks

        monkeypatch.setattr(prospect_trigger_watch, "draft_hooks", _no_hook)
        prospect_trigger_watch.run(dry_run=False, force=True)
        assert drafted["n"] == 1
        st = json.loads(prospect_trigger_watch.STATE.read_text())
        assert st["opens"].get("Acme") == 1, "baseline advanced despite a failed hook (trigger consumed)"
        assert _read_jsonl(prospect_trigger_watch.HOOKS) == []

        # next run, model back: it re-fires and stages the hook, THEN advances the baseline
        def _hook(fired):
            return [{"company": f["company"], "trigger": f["trigger"], "hook": "hi there", "ts": "now"}
                    for f in fired]

        monkeypatch.setattr(prospect_trigger_watch, "draft_hooks", _hook)
        prospect_trigger_watch.run(dry_run=False, force=True)
        st2 = json.loads(prospect_trigger_watch.STATE.read_text())
        assert st2["opens"].get("Acme") == 5, "baseline should advance once the hook stages"
        assert len(_read_jsonl(prospect_trigger_watch.HOOKS)) == 1

    def test_successful_hook_advances_baseline(self, store, monkeypatch):
        deal = {"company": "Beta", "site_url": "", "pids": ["p1"], "opens": 9, "cid": ""}
        monkeypatch.setattr(prospect_trigger_watch, "open_deals", lambda: [deal])
        prospect_trigger_watch.STATE.write_text(json.dumps({"opens": {"Beta": 2}, "convo": {}, "fired": {}}))
        monkeypatch.setattr(prospect_trigger_watch, "draft_hooks",
                            lambda fired: [{"company": f["company"], "trigger": f["trigger"],
                                            "hook": "h", "ts": "now"} for f in fired])
        prospect_trigger_watch.run(dry_run=False, force=True)
        st = json.loads(prospect_trigger_watch.STATE.read_text())
        assert st["opens"].get("Beta") == 9


# ---------------------------------------------------------------------------
# takehome_helper: fix 6
# ---------------------------------------------------------------------------

class TestTakehomeSinglePush:
    def test_single_summary_push_for_many_mentions(self, tmp_path, monkeypatch):
        """Fix 6: several live-stage companies with take-home mentions must collapse to
        ONE summary push, not N pushes machine-gunned at the phone."""
        monkeypatch.setattr(takehome_helper, "STATE", tmp_path / "state.json")
        monkeypatch.setattr(takehome_helper, "TODOS", tmp_path / "todos.jsonl")
        monkeypatch.setattr(takehome_helper, "OUT_DIR", tmp_path / "takehomes")
        monkeypatch.setattr(takehome_helper, "MAIL_STORES", [])  # lane 2 off
        # three live jobs, each with a take-home mention on its own record
        monkeypatch.setattr(jobs, "load_jobs", lambda: [
            {"id": "j1", "company": "Acme", "status": "interview", "reason": "please complete the assessment"},
            {"id": "j2", "company": "Beta", "status": "replied", "reason": "a take-home assignment awaits"},
            {"id": "j3", "company": "Gamma", "status": "applied", "reason": "coding challenge attached"},
        ])
        pushes = []
        monkeypatch.setattr(planner, "notify", lambda *a, **k: pushes.append(a) or True)

        staged = takehome_helper.run(dry_run=False)
        assert len(staged) == 3, "all three mentions should stage todos/scaffolds"
        assert len(pushes) == 1, f"expected ONE summary push, got {len(pushes)}"
        assert "3 take-home" in pushes[0][0], "the single push should summarize the count"
        assert len(_read_jsonl(takehome_helper.TODOS)) == 3


# ---------------------------------------------------------------------------
# interview_followup: fix 12
# ---------------------------------------------------------------------------

class TestFollowupFirstSeenClamp:
    def test_stale_applied_at_no_false_day10_on_first_sight(self, tmp_path, monkeypatch):
        """Fix 12: a job at interview whose only anchor is a 20-day-old applied_at must
        NOT fire day-10 'write it off' the first time we see it. first_seen clamps the
        silence clock to when WE saw it at interview."""
        monkeypatch.setattr(interview_followup, "STATE", tmp_path / "fu_state.json")
        monkeypatch.setattr(interview_followup, "DRAFTS", tmp_path / "thankyou.jsonl")
        monkeypatch.setattr(interview_followup, "PREP_DIR", tmp_path / "prep")
        # only weak fallback anchors present, both ~20 days old
        stale = "2026-06-17T00:00:00-07:00"  # 20d before 2026-07-07
        monkeypatch.setattr(jobs, "load_jobs", lambda: [
            {"id": "j1", "company": "Late Seen", "status": "interview",
             "applied_at": stale, "created": stale}])
        pushes = []
        monkeypatch.setattr(planner, "notify", lambda *a, **k: pushes.append(a) or True)
        monkeypatch.setattr(planner, "feed_add", lambda *a, **k: None)

        fired = interview_followup.run(dry_run=False)
        assert fired == [], "a stale applied_at fired a nudge on first sight"
        assert pushes == []
        st = json.loads(interview_followup.STATE.read_text())
        assert "first_seen" in st.get("j1", {}), "first_seen was not stamped on first sight"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
