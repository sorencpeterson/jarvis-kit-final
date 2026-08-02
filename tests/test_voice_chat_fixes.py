#!/usr/bin/env python3
"""2026-07-12 'do it all' pass: pins for the voice-stack + chat-agency fixes.
Voice: Whisper silence-hallucination filter (server-side). Chat: the 5 name->id actions
are wired (mutations ride gated localhost routes; sends live in OUTWARD = confirm-first),
the fuzzy resolver demands a unique match, and the INTERPRET catalog teaches the actions."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import commander  # noqa: E402
import server  # noqa: E402


class TestSttHallucinationFilter:
    def test_known_fillers_become_silence(self):
        for t in ("you", "Thank you.", "Thanks!", "Bye.", "Okay", "uh", "Thank you for watching"):
            assert server._stt_clean(t) == "", t

    def test_real_speech_passes(self):
        for t in ("skip the Client A proposal", "you should see the pipeline", "thank you note for Lisa"):
            assert server._stt_clean(t) == t, t


class TestChatActionWiring:
    def test_safe_actions_present(self):
        for a in ("proposal_skip", "reply_skip", "warm_dispo", "todo_complete", "content_approve"):
            assert a in commander.SAFE, a

    def test_sends_are_confirm_gated(self):
        # real sends must live in OUTWARD (confirm bubble), never SAFE
        for a in ("proposal_send", "reply_send"):
            assert a in commander.OUTWARD and a not in commander.SAFE, a

    def test_catalog_teaches_the_actions(self):
        for a in ("proposal_skip", "proposal_send", "reply_send", "warm_dispo",
                  "todo_complete", "content_approve"):
            assert a in commander.INTERPRET, a


class TestFuzzyResolver:
    ITEMS = [{"company": "Northwind Clinic", "id": "1"},
             {"company": "Reenvision Aesthetics", "id": "2"},
             {"company": "Transcend Aging", "id": "3"}]

    def test_unique_substring_matches(self):
        hit, err = commander._fuzzy_one("northwind", self.ITEMS, "company")
        assert hit["id"] == "1" and not err

    def test_ambiguous_refuses(self):
        hit, err = commander._fuzzy_one("e", self.ITEMS, "company")
        assert hit is None and "ambiguous" in err

    def test_no_match_refuses(self):
        hit, err = commander._fuzzy_one("zzz", self.ITEMS, "company")
        assert hit is None and "no match" in err

    def test_dispo_validates(self):
        assert "must be one of" in commander.a_warm_dispo({"who": "x", "dispo": "yes"})
