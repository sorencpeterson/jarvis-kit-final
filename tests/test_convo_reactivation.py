#!/usr/bin/env python3
"""Unit tests for agents/convo_reactivation.py (C214 reactivation lane demo batch).

classify_segment() is pure. build_batch() gets isolated-store integration tests with
GHL/proposal_factory calls mocked, covering: the 10-contact cap, suppress-first
(C187), recency ordering, and the honest unclassified/ambiguous handling.

Run: .venv/bin/python -m pytest tests/test_convo_reactivation.py -v
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import reply_watch  # noqa: E402
import proposal_factory  # noqa: E402
import convo_context  # noqa: E402
import convo_reactivation as cr  # noqa: E402


class TestClassifySegment:
    def test_price_signal(self):
        assert cr.classify_segment("that's too expensive for us") == "segment-price"

    def test_price_dollar_sign_signal(self):
        assert cr.classify_segment("what's the $ on this") == "segment-price"

    def test_timing_signal(self):
        assert cr.classify_segment("not right now, maybe next quarter") == "segment-timing"

    def test_timing_busy_season_signal(self):
        assert cr.classify_segment("call me back after busy season") == "segment-timing"

    def test_interest_signal(self):
        assert cr.classify_segment("sounds good, tell me more") == "segment-interest"

    def test_empty_text_unclassified(self):
        assert cr.classify_segment("") == "unclassified"

    def test_whitespace_only_unclassified(self):
        assert cr.classify_segment("   ") == "unclassified"

    def test_no_signal_unclassified(self):
        assert cr.classify_segment("thanks for reaching out have a nice day") == "unclassified"

    def test_multiple_signals_ambiguous(self):
        assert cr.classify_segment("too expensive and also not right now") == "ambiguous"

    def test_three_signals_still_ambiguous(self):
        assert cr.classify_segment(
            "price is too high, not right now, but sounds interesting") == "ambiguous"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    warm_csv = tmp_path / "WARM-HITLIST.csv"
    replies = tmp_path / "replies.jsonl"
    suppress = tmp_path / "suppress.jsonl"
    monkeypatch.setattr(cr, "WARM_CSV", warm_csv)
    monkeypatch.setattr(cr, "SUPPRESS", suppress)
    monkeypatch.setattr(reply_watch, "REPLIES", replies)
    # every store/replies.jsonl reference inside convo_reactivation.py itself uses
    # ROOT/"store"/"replies.jsonl" directly (not reply_watch.REPLIES) in _find_reply_text
    # and the already-queued check, so patch ROOT there too via a tmp store dir.
    (tmp_path / "store").mkdir()
    monkeypatch.setattr(cr, "ROOT", tmp_path)
    return tmp_path


def _write_csv(path: Path, rows: list[dict]):
    fields = ["tier", "name", "company", "email", "phone", "location", "niche",
             "suggested_offer", "pipeline", "stage", "deal_age_days", "deal_value",
             "tags", "tz", "cluster"]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            base = {k: "" for k in fields}
            base.update(r)
            w.writerow(base)


def _tier2_row(name, deal_age_days, email="", phone=""):
    return {"tier": "2", "name": name, "company": name, "email": email or f"{name}@x.com",
            "phone": phone, "deal_age_days": str(deal_age_days)}


class TestBuildBatch:
    def test_no_csv_file_returns_zero(self, isolated, monkeypatch):
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {})
        result = cr.build_batch(dry=True)
        assert result["total_tier2_in_csv"] == 0
        assert result["staged"] == 0

    def test_no_tier2_rows_returns_zero(self, isolated, monkeypatch):
        _write_csv(isolated / "WARM-HITLIST.csv", [
            {"tier": "1", "name": "TierOne", "deal_age_days": "10"},
            {"tier": "3", "name": "TierThree", "deal_age_days": "5"},
        ])
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {})
        result = cr.build_batch(dry=True)
        assert result["total_tier2_in_csv"] == 0

    def test_respects_10_contact_cap(self, isolated, monkeypatch):
        rows = [_tier2_row(f"Contact{i}", i) for i in range(15)]
        _write_csv(isolated / "WARM-HITLIST.csv", rows)
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {"id": "cid1"})
        monkeypatch.setattr(cr, "_find_reply_text", lambda *a, **k: "too expensive")
        result = cr.build_batch(dry=True)
        assert result["total_tier2_in_csv"] == 15
        assert result["candidates_considered"] == 10

    def test_recency_ordering_lowest_deal_age_first(self, isolated, monkeypatch):
        rows = [_tier2_row("Old", 90), _tier2_row("Recent", 3), _tier2_row("Mid", 30)]
        _write_csv(isolated / "WARM-HITLIST.csv", rows)
        ordered = cr._by_recency(cr._tier2_rows())
        assert [r["name"] for r in ordered] == ["Recent", "Mid", "Old"]

    def test_unparseable_age_sorts_last(self, isolated, monkeypatch):
        rows = [_tier2_row("NoAge", "n/a"), _tier2_row("HasAge", 5)]
        _write_csv(isolated / "WARM-HITLIST.csv", rows)
        ordered = cr._by_recency(cr._tier2_rows())
        assert ordered[0]["name"] == "HasAge"
        assert ordered[1]["name"] == "NoAge"

    def test_suppressed_contact_skipped_before_drafting(self, isolated, monkeypatch):
        rows = [_tier2_row("Suppressed", 1, email="blocked@x.com")]
        _write_csv(isolated / "WARM-HITLIST.csv", rows)
        with cr.SUPPRESS.open("w") as f:
            f.write(json.dumps({"ts": "x", "contact_id": "", "email": "blocked@x.com",
                                "why": "asked to be removed"}) + "\n")
        called = {"n": 0}

        def _fake_find_reply_text(*a, **k):
            called["n"] += 1
            return "too expensive"
        monkeypatch.setattr(cr, "_find_reply_text", _fake_find_reply_text)
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {"id": "cid1"})
        result = cr.build_batch(dry=True)
        assert result["skipped_suppressed"] == 1
        assert result["staged"] == 0
        # C187 audit: suppress check happens BEFORE any drafting work (reply-text lookup)
        assert called["n"] == 0

    def test_suppressed_by_name_also_skipped(self, isolated, monkeypatch):
        rows = [_tier2_row("Blocked Name", 1, email="")]
        _write_csv(isolated / "WARM-HITLIST.csv", rows)
        with cr.SUPPRESS.open("w") as f:
            f.write(json.dumps({"ts": "x", "contact_id": "", "email": "",
                                "name": "Blocked Name", "why": "removed"}) + "\n")
        monkeypatch.setattr(cr, "_find_reply_text", lambda *a, **k: "too expensive")
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {"id": "cid1"})
        result = cr.build_batch(dry=True)
        assert result["skipped_suppressed"] == 1

    def test_unclassified_contact_gets_no_draft(self, isolated, monkeypatch):
        rows = [_tier2_row("NoSignal", 1)]
        _write_csv(isolated / "WARM-HITLIST.csv", rows)
        monkeypatch.setattr(cr, "_find_reply_text", lambda *a, **k: "")
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {"id": "cid1"})
        result = cr.build_batch(dry=True)
        assert result["unclassified"] == 1
        assert result["staged"] == 0

    def test_ambiguous_contact_gets_no_draft(self, isolated, monkeypatch):
        rows = [_tier2_row("Mixed", 1)]
        _write_csv(isolated / "WARM-HITLIST.csv", rows)
        monkeypatch.setattr(cr, "_find_reply_text",
                            lambda *a, **k: "too expensive and not right now")
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {"id": "cid1"})
        result = cr.build_batch(dry=True)
        assert result["ambiguous_multi_signal"] == 1
        assert result["staged"] == 0

    def test_price_segment_produces_pending_draft(self, isolated, monkeypatch):
        rows = [_tier2_row("PriceGuy", 1)]
        _write_csv(isolated / "WARM-HITLIST.csv", rows)
        monkeypatch.setattr(cr, "_find_reply_text", lambda *a, **k: "too expensive for me")
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {"id": "cid1"})
        result = cr.build_batch(dry=True)
        assert result["staged"] == 1
        assert result["segments"]["segment-price"] == 1

    def test_dry_run_writes_nothing_to_replies_store(self, isolated, monkeypatch):
        rows = [_tier2_row("PriceGuy", 1)]
        _write_csv(isolated / "WARM-HITLIST.csv", rows)
        monkeypatch.setattr(cr, "_find_reply_text", lambda *a, **k: "too expensive")
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {"id": "cid1"})
        cr.build_batch(dry=True)
        assert not (isolated / "store" / "replies.jsonl").exists() or \
               (isolated / "store" / "replies.jsonl").read_text() == ""

    def test_staged_draft_status_is_pending_needs_his_click(self, isolated, monkeypatch):
        rows = [_tier2_row("PriceGuy", 1)]
        _write_csv(isolated / "WARM-HITLIST.csv", rows)
        monkeypatch.setattr(cr, "_find_reply_text", lambda *a, **k: "too expensive")
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {"id": "cid1"})
        staged_records = []
        monkeypatch.setattr(reply_watch, "_save", lambda rec: staged_records.append(rec))
        cr.build_batch(dry=False)
        assert len(staged_records) == 1
        assert staged_records[0]["status"] == "pending"
        assert staged_records[0]["src"] == "reactivation_demo"

    def test_result_note_explains_423_scale_stays_separate(self, isolated, monkeypatch):
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {})
        result = cr.build_batch(dry=True)
        assert "GHL DBR" in result["note"]
        assert "423" in result["note"] or "warm-reactivation-423" in result["note"]

    def test_limit_argument_respected_below_cap(self, isolated, monkeypatch):
        rows = [_tier2_row(f"Contact{i}", i) for i in range(15)]
        _write_csv(isolated / "WARM-HITLIST.csv", rows)
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {"id": "cid1"})
        monkeypatch.setattr(cr, "_find_reply_text", lambda *a, **k: "too expensive")
        result = cr.build_batch(limit=3, dry=True)
        assert result["candidates_considered"] == 3


class TestLooksLikeTheirOwnBlast:
    def test_sold_out_flagged(self):
        assert cr._looks_like_their_own_blast("SculpSure promo almost SOLD OUT!")

    def test_spots_taken_flagged(self):
        assert cr._looks_like_their_own_blast("19 spots taken, 6 left")

    def test_genuine_reply_not_flagged(self):
        assert not cr._looks_like_their_own_blast("that's too expensive for us right now")

    def test_empty_not_flagged(self):
        assert not cr._looks_like_their_own_blast("")


class TestFindReplyText:
    """_find_reply_text()'s live GHL path (branch 2), mocked at the ghl_social._api
    and convo_context.fetch_context boundary -- this is the internal logic every
    build_batch() test above bypasses via monkeypatching _find_reply_text itself, so
    it needs its own direct coverage, especially the outbound-evidence requirement
    added after a live run surfaced a real false-positive (see module docstring)."""

    def test_no_outbound_in_thread_returns_empty(self, monkeypatch):
        # the exact live scenario that motivated this check: a conversation that's
        # 100% inbound marketing from an unrelated business, zero outbound from Alex
        monkeypatch.setattr(proposal_factory, "find_contact", lambda **k: {"id": "c1"})
        monkeypatch.setattr(
            proposal_factory.ghl_social, "_api",
            lambda *a, **k: json.dumps({"conversations": [{"id": "convo1"}]}))
        monkeypatch.setattr(
            convo_context, "fetch_context",
            lambda *a, **k: [{"dir": "inbound", "body": "SculpSure promo SOLD OUT, $499", "ts": ""},
                             {"dir": "inbound", "body": "another blast, $499 special", "ts": ""}])
        result = cr._find_reply_text("Toll", "", "c1")
        assert result == ""

    def test_outbound_present_real_inbound_returned(self, monkeypatch):
        monkeypatch.setattr(
            proposal_factory.ghl_social, "_api",
            lambda *a, **k: json.dumps({"conversations": [{"id": "convo1"}]}))
        monkeypatch.setattr(
            convo_context, "fetch_context",
            lambda *a, **k: [{"dir": "outbound", "body": "Put together a plan for your site", "ts": ""},
                             {"dir": "inbound", "body": "that's too expensive honestly", "ts": ""}])
        result = cr._find_reply_text("Braydon", "", "c1")
        assert result == "that's too expensive honestly"

    def test_outbound_present_but_latest_inbound_is_own_blast_skips_to_earlier_real_one(self, monkeypatch):
        monkeypatch.setattr(
            proposal_factory.ghl_social, "_api",
            lambda *a, **k: json.dumps({"conversations": [{"id": "convo1"}]}))
        monkeypatch.setattr(
            convo_context, "fetch_context",
            lambda *a, **k: [{"dir": "outbound", "body": "Here's the plan", "ts": ""},
                             {"dir": "inbound", "body": "sounds interesting, tell me more", "ts": ""},
                             {"dir": "outbound", "body": "great, questions?", "ts": ""},
                             {"dir": "inbound", "body": "flash sale ends soon, buy now!", "ts": ""}])
        result = cr._find_reply_text("Braydon", "", "c1")
        assert result == "sounds interesting, tell me more"

    def test_no_convo_found_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            proposal_factory.ghl_social, "_api",
            lambda *a, **k: json.dumps({"conversations": []}))
        result = cr._find_reply_text("Nobody", "", "c1")
        assert result == ""

    def test_no_contact_id_skips_ghl_lookup_entirely(self, monkeypatch):
        called = {"n": 0}
        monkeypatch.setattr(
            proposal_factory.ghl_social, "_api",
            lambda *a, **k: called.__setitem__("n", called["n"] + 1) or "{}")
        result = cr._find_reply_text("Nobody", "", "")
        assert result == ""
        assert called["n"] == 0

    def test_replies_jsonl_match_does_not_require_outbound_check(self, tmp_path, monkeypatch):
        # branch 1 (existing replies.jsonl record) is itself evidence of a real
        # reply_watch-processed conversation -- no separate outbound check needed there
        monkeypatch.setattr(cr, "ROOT", tmp_path)
        (tmp_path / "store").mkdir()
        with (tmp_path / "store" / "replies.jsonl").open("w") as f:
            f.write(json.dumps({"name": "Braydon", "their_msg": "too expensive for me"}) + "\n")
        result = cr._find_reply_text("Braydon", "", "")
        assert result == "too expensive for me"
