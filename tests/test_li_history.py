#!/usr/bin/env python3
"""Unit tests for agents/li_history.py (A4 full-history dedupe, A9 decline decay
/company cooldown, A19 stale sweeper). All tests monkeypatch networking.QUEUE to
a pytest tmp_path fixture file — NEVER the real store/network.jsonl. This mirrors
the manual verification already run against a disposable copy of the real store
(agents/li_history.py's mutation path was exercised there first; these are the
committed regression tests).

Run: .venv/bin/python -m pytest tests/test_li_history.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import networking  # noqa: E402
import li_history  # noqa: E402


@pytest.fixture
def fake_queue(tmp_path, monkeypatch):
    """Point networking.QUEUE (and therefore li_history, which reads through
    networking.load_queue()) at an isolated tmp file. Restores the original
    QUEUE path after the test via monkeypatch's automatic teardown."""
    q = tmp_path / "network.jsonl"
    monkeypatch.setattr(networking, "QUEUE", q)
    return q


def _write(path: Path, records: list[dict]):
    with path.open("a") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _rec(**kw) -> dict:
    base = {"id": "id1", "kind": "connect", "author": "Test Person",
            "target": "headline", "url": "https://linkedin.com/in/test",
            "draft": "", "status": "pending", "created": "2026-06-01T08:00:00-07:00"}
    base.update(kw)
    return base


class TestUrlKey:
    def test_strips_protocol_and_www(self):
        assert li_history._url_key("https://www.linkedin.com/in/x") == "linkedin.com/in/x"

    def test_strips_query_string(self):
        assert li_history._url_key("https://linkedin.com/in/x?a=1&b=2") == "linkedin.com/in/x"

    def test_strips_trailing_slash(self):
        assert li_history._url_key("https://linkedin.com/in/x/") == "linkedin.com/in/x"

    def test_lowercases(self):
        assert li_history._url_key("https://LinkedIn.com/in/X") == "linkedin.com/in/x"

    def test_variants_produce_same_key(self):
        a = li_history._url_key("https://www.linkedin.com/in/jordan-ross/?x=1")
        b = li_history._url_key("linkedin.com/in/jordan-ross")
        assert a == b

    def test_empty_input(self):
        assert li_history._url_key("") == ""


class TestFullHistoryIndex:
    def test_empty_queue_empty_index(self, fake_queue):
        assert li_history.full_history_index() == {}

    def test_single_record_indexed(self, fake_queue):
        _write(fake_queue, [_rec(id="a1", url="https://linkedin.com/in/alice", status="done")])
        idx = li_history.full_history_index()
        assert "linkedin.com/in/alice" in idx
        assert idx["linkedin.com/in/alice"]["status"] == "done"

    def test_url_variants_collapse_to_one_entry(self, fake_queue):
        _write(fake_queue, [
            _rec(id="a1", url="https://www.linkedin.com/in/alice/", status="approved",
                 created="2026-06-01T08:00:00-07:00"),
            _rec(id="a2", url="https://linkedin.com/in/alice?x=1", status="done",
                 created="2026-06-02T08:00:00-07:00"),
        ])
        idx = li_history.full_history_index()
        assert len(idx) == 1
        assert idx["linkedin.com/in/alice"]["status"] == "done"  # later created wins

    def test_records_without_url_ignored(self, fake_queue):
        _write(fake_queue, [_rec(id="a1", url="")])
        assert li_history.full_history_index() == {}


class TestAlreadyTouched:
    def test_never_seen_returns_none(self, fake_queue):
        _write(fake_queue, [_rec(id="a1", url="https://linkedin.com/in/alice")])
        assert li_history.already_touched("https://linkedin.com/in/bob") is None

    def test_seen_any_status_returns_record(self, fake_queue):
        _write(fake_queue, [_rec(id="a1", url="https://linkedin.com/in/alice", status="skipped")])
        result = li_history.already_touched("https://linkedin.com/in/alice")
        assert result is not None
        assert result["status"] == "skipped"


class TestFilterUnattempted:
    def test_drops_previously_touched_targets(self, fake_queue):
        _write(fake_queue, [_rec(id="a1", url="https://linkedin.com/in/alice", status="done")])
        targets = [{"url": "https://linkedin.com/in/alice"}, {"url": "https://linkedin.com/in/bob"}]
        out = li_history.filter_unattempted(targets)
        assert len(out) == 1
        assert out[0]["url"] == "https://linkedin.com/in/bob"

    def test_empty_history_keeps_everything(self, fake_queue):
        targets = [{"url": "https://linkedin.com/in/alice"}, {"url": "https://linkedin.com/in/bob"}]
        out = li_history.filter_unattempted(targets)
        assert len(out) == 2


class TestCompanyExtraction:
    def test_at_pattern(self):
        assert li_history._company_from_target_text("", "Founder @ Acme Digital") == "Acme Digital"

    def test_at_word_pattern(self):
        assert li_history._company_from_target_text("Director of Ops at Widgets Co", "") == "Widgets Co"

    def test_no_pattern_returns_empty_never_guesses(self):
        assert li_history._company_from_target_text("just some random text", "") == ""


class TestDeclineDecay:
    def test_below_threshold_no_cooldown(self, fake_queue, tmp_path, monkeypatch):
        monkeypatch.setattr(li_history, "COOLDOWNS", tmp_path / "cooldowns.jsonl")
        _write(fake_queue, [_rec(id="a1", kind="connect", status="skipped",
                                  target="Founder @ Nimbusrp")])
        new = li_history.apply_decline_decay(threshold=2)
        assert new == []

    def test_at_threshold_writes_cooldown(self, fake_queue, tmp_path, monkeypatch):
        monkeypatch.setattr(li_history, "COOLDOWNS", tmp_path / "cooldowns.jsonl")
        _write(fake_queue, [
            _rec(id="a1", kind="connect", status="skipped", target="Founder @ Nimbusrp"),
            _rec(id="a2", kind="connect", status="skipped", target="Owner @ Nimbusrp"),
        ])
        new = li_history.apply_decline_decay(threshold=2)
        assert len(new) == 1
        assert new[0]["company"] == "nimbusrp"
        assert new[0]["ignored_count"] == 2

    def test_does_not_double_write_if_already_on_cooldown(self, fake_queue, tmp_path, monkeypatch):
        monkeypatch.setattr(li_history, "COOLDOWNS", tmp_path / "cooldowns.jsonl")
        _write(fake_queue, [
            _rec(id="a1", kind="connect", status="skipped", target="Founder @ Nimbusrp"),
            _rec(id="a2", kind="connect", status="skipped", target="Owner @ Nimbusrp"),
        ])
        first = li_history.apply_decline_decay(threshold=2)
        second = li_history.apply_decline_decay(threshold=2)
        assert len(first) == 1
        assert len(second) == 0  # already active, no duplicate cooldown record

    def test_non_connect_kind_not_counted(self, fake_queue, tmp_path, monkeypatch):
        monkeypatch.setattr(li_history, "COOLDOWNS", tmp_path / "cooldowns.jsonl")
        _write(fake_queue, [
            _rec(id="a1", kind="comment", status="skipped", target="Founder @ Nimbusrp"),
            _rec(id="a2", kind="comment", status="skipped", target="Owner @ Nimbusrp"),
        ])
        new = li_history.apply_decline_decay(threshold=2)
        assert new == []


class TestCompanyOnCooldown:
    def test_active_cooldown_blocks(self, tmp_path, monkeypatch):
        cd_file = tmp_path / "cooldowns.jsonl"
        monkeypatch.setattr(li_history, "COOLDOWNS", cd_file)
        from datetime import date, timedelta
        future = (date.today() + timedelta(days=30)).isoformat()
        li_history._save_cooldown({"company": "nimbusrp", "until": future})
        assert li_history.company_on_cooldown("Nimbusrp")  # case-insensitive

    def test_expired_cooldown_does_not_block(self, tmp_path, monkeypatch):
        cd_file = tmp_path / "cooldowns.jsonl"
        monkeypatch.setattr(li_history, "COOLDOWNS", cd_file)
        from datetime import date, timedelta
        past = (date.today() - timedelta(days=1)).isoformat()
        li_history._save_cooldown({"company": "nimbusrp", "until": past})
        assert not li_history.company_on_cooldown("Nimbusrp")

    def test_unknown_company_not_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setattr(li_history, "COOLDOWNS", tmp_path / "cooldowns.jsonl")
        assert not li_history.company_on_cooldown("Nobody Inc")


class TestFilterCooldownCompanies:
    def test_drops_targets_from_cooled_company(self, tmp_path, monkeypatch):
        monkeypatch.setattr(li_history, "COOLDOWNS", tmp_path / "cooldowns.jsonl")
        from datetime import date, timedelta
        future = (date.today() + timedelta(days=30)).isoformat()
        li_history._save_cooldown({"company": "nimbusrp", "until": future})
        targets = [{"headline": "Founder @ Nimbusrp", "target": ""},
                   {"headline": "Founder @ Other Inc", "target": ""}]
        out = li_history.filter_cooldown_companies(targets)
        assert len(out) == 1
        assert "Other Inc" in out[0]["headline"]

    def test_no_extractable_company_passes_through(self, tmp_path, monkeypatch):
        monkeypatch.setattr(li_history, "COOLDOWNS", tmp_path / "cooldowns.jsonl")
        targets = [{"headline": "no company pattern here", "target": ""}]
        out = li_history.filter_cooldown_companies(targets)
        assert len(out) == 1


class TestFindStale:
    def test_no_pending_items_no_stale(self, fake_queue):
        _write(fake_queue, [_rec(id="a1", status="done")])
        assert li_history.find_stale(days=21) == []

    def test_recent_pending_not_stale(self, fake_queue):
        from datetime import date
        _write(fake_queue, [_rec(id="a1", status="pending", created=date.today().isoformat() + "T08:00:00-07:00")])
        assert li_history.find_stale(days=21) == []

    def test_old_pending_is_stale(self, fake_queue):
        _write(fake_queue, [_rec(id="a1", status="pending", created="2026-01-01T08:00:00-07:00")])
        stale = li_history.find_stale(days=21)
        assert len(stale) == 1
        assert stale[0]["id"] == "a1"

    def test_missing_created_never_crashes(self, fake_queue):
        _write(fake_queue, [_rec(id="a1", status="pending", created="")])
        assert li_history.find_stale(days=21) == []


class TestSweepStale:
    def test_dry_run_writes_nothing(self, fake_queue):
        _write(fake_queue, [_rec(id="a1", status="pending", created="2026-01-01T08:00:00-07:00")])
        before = fake_queue.read_text()
        result = li_history.sweep_stale(days=21, dry=True)
        after = fake_queue.read_text()
        assert len(result) == 1  # reports what WOULD be swept
        assert before == after  # but writes nothing

    def test_real_sweep_is_additive_and_marks_expired(self, fake_queue):
        _write(fake_queue, [_rec(id="a1", status="pending", created="2026-01-01T08:00:00-07:00")])
        lines_before = len(fake_queue.read_text().splitlines())
        swept = li_history.sweep_stale(days=21, dry=False)
        lines_after = len(fake_queue.read_text().splitlines())
        assert lines_after > lines_before  # additive (append), never rewrite in place
        assert swept[0]["status"] == "expired"
        reloaded = {r["id"]: r["status"] for r in networking.load_queue()}
        assert reloaded["a1"] == "expired"

    def test_non_stale_items_untouched(self, fake_queue):
        from datetime import date
        _write(fake_queue, [
            _rec(id="a1", status="pending", created="2026-01-01T08:00:00-07:00"),
            _rec(id="a2", status="pending", created=date.today().isoformat() + "T08:00:00-07:00"),
            _rec(id="a3", status="done", created="2026-01-01T08:00:00-07:00"),
        ])
        li_history.sweep_stale(days=21, dry=False)
        reloaded = {r["id"]: r["status"] for r in networking.load_queue()}
        assert reloaded["a1"] == "expired"
        assert reloaded["a2"] == "pending"  # too recent to be stale
        assert reloaded["a3"] == "done"  # not pending, never touched
