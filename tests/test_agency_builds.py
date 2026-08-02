#!/usr/bin/env python3
"""B1/B4 agency builds (2026-07-07): quiet_worklist ranking + win_asset voice rails.

quiet_worklist merges opened-then-quiet proposals + dormant convos + stale warm into one
ranked re-engage list; the ranking is the whole value, so it's what we pin. win_asset
generates DRAFTS only and must never emit an em-dash (the brand rule).

Run: .venv/bin/python -m pytest tests/test_agency_builds.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import quiet_worklist  # noqa: E402
import win_asset  # noqa: E402


class TestQuietWorklistRanking:
    def test_opened_proposal_outranks_cold_convo(self, monkeypatch):
        # an opened-then-quiet $3,500 proposal must rank above a warm lead with no signal
        monkeypatch.setattr(quiet_worklist, "collect_proposals", lambda: [
            {"kind": "proposal", "id": "p1", "company": "Reenvision", "why": "opened",
             "signal": 10.0, "score": 51.9, "price": 3500, "days": 3.0}])
        monkeypatch.setattr(quiet_worklist, "collect_convos", lambda: [])
        monkeypatch.setattr(quiet_worklist, "collect_warm", lambda: [
            {"kind": "warm", "id": "w1", "company": "Stale Spa", "why": "never dispo'd",
             "signal": 0.0, "score": 19.0, "price": 0, "days": 200.0}])
        items = quiet_worklist.build()["items"]
        assert items[0]["kind"] == "proposal" and items[0]["id"] == "p1"
        assert items[0]["score"] > items[1]["score"]

    def test_higher_dollar_wins_at_equal_signal(self, monkeypatch):
        # collect_proposals is the real scorer; feed two staged proposals differing only in price
        import proposal_factory
        rows = [
            {"id": "big", "status": "staged", "company": "Big", "price": 3500,
             "opens": 1, "read_secs": 0, "scroll_pct": 0, "sent_at": "", "opened_at": "",
             "created": "2026-07-01T00:00:00+02:00"},
            {"id": "small", "status": "staged", "company": "Small", "price": 800,
             "opens": 1, "read_secs": 0, "scroll_pct": 0, "sent_at": "", "opened_at": "",
             "created": "2026-07-01T00:00:00+02:00"},
        ]
        monkeypatch.setattr(proposal_factory, "load_queue", lambda: rows)
        out = {r["id"]: r for r in quiet_worklist.collect_proposals()}
        assert out["big"]["score"] > out["small"]["score"]

    def test_unopened_proposal_excluded(self, monkeypatch):
        import proposal_factory
        monkeypatch.setattr(proposal_factory, "load_queue", lambda: [
            {"id": "cold", "status": "staged", "price": 3500, "opens": 0, "read_secs": 0,
             "created": "2026-07-01T00:00:00+02:00"}])
        assert quiet_worklist.collect_proposals() == []


_RAW_WIN = {"note": "Nimbus Soft - white-label web dev", "amount": 1200,
            "ts": "2026-07-04T10:00:00+02:00", "kind": "won"}


class TestWinAssetVoice:
    def test_generate_has_no_em_dash(self, monkeypatch):
        w = win_asset.enrich(_RAW_WIN)  # generate() consumes the enriched record
        # force the LLM path to return em-dashes; humanize()/rails must strip them
        monkeypatch.setattr(win_asset.planner, "_cli",
                            lambda *a, **k: "Closed in 3 days — no deck needed — just proof.")
        onepager, linkedin, gaps = win_asset.generate(w)
        assert "—" not in onepager and "—" not in linkedin

    def test_generate_falls_back_without_llm(self, monkeypatch):
        w = win_asset.enrich(_RAW_WIN)
        monkeypatch.setattr(win_asset.planner, "_cli", lambda *a, **k: "")
        onepager, linkedin, gaps = win_asset.generate(w)
        assert onepager.strip() and linkedin.strip()  # fallback templates fire
        assert "—" not in onepager and "—" not in linkedin
