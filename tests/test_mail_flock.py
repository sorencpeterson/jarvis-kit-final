#!/usr/bin/env python3
"""Locking tests for the mail fleet's store writers (2026-07 P0: a LIVE duplicate
draft was reproduced from the unlocked read-modify-write window on mail_drafts.jsonl).

For every writer touched in the fix, this asserts:
  - store_lib._flock is actually entered (spied via each module's _flock binding,
    delegating to the real lock so the code path stays honest)
  - the write lands exactly once (the duplicate re-check INSIDE the lock works)
  - the file on disk stays intact (every line parses)
  - full-file JSON writes are atomic (result parses, no .tmp leftover)

No Gmail, no LLM: everything runs against tmp_path stores with stubbed fetchers.

Run: .venv/bin/python -m pytest tests/test_mail_flock.py -q
"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import store_lib  # noqa: E402
import daily_brief  # noqa: E402
import mail_brain  # noqa: E402
import mail_digest  # noqa: E402
import mail_drafts  # noqa: E402
import mail_sender_scores  # noqa: E402
import mail_signals  # noqa: E402
import mail_sync  # noqa: E402
import mail_threads  # noqa: E402


def _spy_flock(calls: list):
    """Wrap the REAL store_lib._flock so entry is recorded but locking still happens."""
    real = store_lib._flock

    @contextmanager
    def spy(path):
        calls.append(Path(path))
        with real(path):
            yield

    return spy


def _lines(path: Path) -> list[dict]:
    """Parse every line; a torn/corrupt line raises and fails the test (file-intact check)."""
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


class TestMailDraftsAppend:
    def test_locks_and_dedupes_by_id(self, tmp_path, monkeypatch):
        calls: list[Path] = []
        drafts = tmp_path / "mail_drafts.jsonl"
        monkeypatch.setattr(mail_drafts, "DRAFTS", drafts)
        monkeypatch.setattr(mail_drafts, "_flock", _spy_flock(calls))

        rec = {"id": "m1", "to": "a@b.com", "subject": "Re: x", "draft": "hi", "status": "pending"}
        assert mail_drafts._append(rec) is True
        assert mail_drafts._append(dict(rec)) is False  # in-lock re-check catches the dup

        rows = _lines(drafts)
        assert len(rows) == 1 and rows[0]["id"] == "m1"
        assert len(calls) == 2 and calls[0] == drafts  # lock entered on BOTH attempts

    def test_writeback_status_record_blocks_redraft(self, tmp_path, monkeypatch):
        """outbox.py appends {"id", "status": "sent"} records to the same store; an id
        present via writeback must still be treated as already-drafted under the lock."""
        drafts = tmp_path / "mail_drafts.jsonl"
        drafts.write_text(json.dumps({"id": "m9", "status": "sent", "via": "outbox"}) + "\n")
        monkeypatch.setattr(mail_drafts, "DRAFTS", drafts)
        monkeypatch.setattr(mail_drafts, "_flock", _spy_flock([]))
        assert mail_drafts._append({"id": "m9", "draft": "again"}) is False
        assert len(_lines(drafts)) == 1


class TestMailThreadsAppend:
    def test_dedupes_by_id(self, tmp_path, monkeypatch):
        calls: list[Path] = []
        monkeypatch.setattr(mail_threads, "_flock", _spy_flock(calls))
        out = tmp_path / "mail_task_suggestions.jsonl"

        assert mail_threads._append(out, {"id": "t1", "suggested_task": "x"}) is True
        assert mail_threads._append(out, {"id": "t1", "suggested_task": "x again"}) is False
        assert mail_threads._append(out, {"id": "t2", "suggested_task": "y"}) is True

        assert [r["id"] for r in _lines(out)] == ["t1", "t2"]
        assert len(calls) == 3

    def test_dedupes_by_thread_id_for_summaries(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mail_threads, "_flock", _spy_flock([]))
        out = tmp_path / "mail_thread_summaries.jsonl"
        rec = {"thread_id": "th1", "summary": "two lines"}
        assert mail_threads._append(out, rec, key="thread_id") is True
        assert mail_threads._append(out, dict(rec), key="thread_id") is False
        assert len(_lines(out)) == 1


class TestMailSignalsAppend:
    def test_dedupes_by_sender_for_archive_clusters(self, tmp_path, monkeypatch):
        calls: list[Path] = []
        monkeypatch.setattr(mail_signals, "_flock", _spy_flock(calls))
        out = tmp_path / "mail_archive_suggestions.jsonl"
        rec = {"sender": "promo@shop.com", "count": 3}

        assert mail_signals._append(out, rec, key="sender") is True
        assert mail_signals._append(out, {"sender": "promo@shop.com", "count": 5}, key="sender") is False

        rows = _lines(out)
        assert len(rows) == 1 and rows[0]["count"] == 3
        assert len(calls) == 2

    def test_dedupes_by_id_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mail_signals, "_flock", _spy_flock([]))
        out = tmp_path / "mail_meeting_suggestions.jsonl"
        assert mail_signals._append(out, {"id": "s1", "note": "meeting"}) is True
        assert mail_signals._append(out, {"id": "s1", "note": "meeting dup"}) is False
        assert len(_lines(out)) == 1


class TestMailBrainBatchAppend:
    def test_batch_locks_and_rechecks_inside_lock(self, tmp_path, monkeypatch):
        calls: list[Path] = []
        triage = tmp_path / "mail_triage.jsonl"
        monkeypatch.setattr(mail_brain, "TRIAGE", triage)
        monkeypatch.setattr(mail_brain, "_flock", _spy_flock(calls))

        first = mail_brain._append_triage_batch([{"id": "a", "lane": "vip"},
                                                 {"id": "b", "lane": "noise"}])
        assert [r["id"] for r in first] == ["a", "b"]

        # simulate a concurrent run having already landed "b": only "c" is written
        second = mail_brain._append_triage_batch([{"id": "b", "lane": "noise"},
                                                  {"id": "c", "lane": "jobs"}])
        assert [r["id"] for r in second] == ["c"]

        assert [r["id"] for r in _lines(triage)] == ["a", "b", "c"]
        assert len(calls) == 2 and calls[0] == triage  # one lock per batch, not per record

    def test_empty_batch_still_safe(self, tmp_path, monkeypatch):
        triage = tmp_path / "mail_triage.jsonl"
        monkeypatch.setattr(mail_brain, "TRIAGE", triage)
        monkeypatch.setattr(mail_brain, "_flock", _spy_flock([]))
        assert mail_brain._append_triage_batch([]) == []
        assert not triage.exists() or triage.read_text() == ""


class TestSenderScores:
    def test_save_is_atomic(self, tmp_path, monkeypatch):
        scores = tmp_path / "sender_scores.json"
        monkeypatch.setattr(mail_sender_scores, "SCORES", scores)
        mail_sender_scores._save({"a@b.com": {"score": 1.0}})
        assert json.loads(scores.read_text())["a@b.com"]["score"] == 1.0
        assert not scores.with_suffix(".json.tmp").exists()  # tmp replaced, not left behind

    def test_seed_merges_under_lock_and_keeps_manual_entries(self, tmp_path, monkeypatch):
        calls: list[Path] = []
        scores = tmp_path / "sender_scores.json"
        scores.write_text(json.dumps({"vip@x.com": {"score": 0.9, "manual": True,
                                                    "replied_count": 0, "unread_count": 0}}))
        monkeypatch.setattr(mail_sender_scores, "SCORES", scores)
        monkeypatch.setattr(mail_sender_scores, "_flock", _spy_flock(calls))

        class FakeGmail:
            @staticmethod
            def search(q, n):
                return [{"id": "s1"}] if q.startswith("in:sent") else [{"id": "u1"}]

            @staticmethod
            def get_messages_metadata(ids, fields=()):
                if ids == ["s1"]:
                    return [{"id": "s1", "to": "Client <client@x.com>"}]
                return [{"id": "u1", "from": "Pileup <vip@x.com>"}]

        monkeypatch.setattr(mail_sender_scores, "gmail_api", FakeGmail)
        out = mail_sender_scores.seed(window_days=30)

        assert calls and calls[0] == scores, "seed() must take the sender_scores lock"
        assert out["client@x.com"]["replied_count"] == 1
        assert out["vip@x.com"]["score"] == 0.9  # manual VIP survives the pileup signal
        assert json.loads(scores.read_text()) == out  # atomic write landed, matches memory

    def test_manual_flag_survives_when_vip_also_appears_in_replied_to(self, tmp_path, monkeypatch):
        """The scenario the test above doesn't cover: a manual VIP who Alex ALSO
        sent mail to this window hits the replied_to rewrite branch, not just the
        unread_from skip-guard. That branch used to rebuild the whole dict from
        scratch each run -- the elevated score survived once (via max(base, prior))
        but the `manual` key itself was dropped, so the NEXT re-seed no longer
        protected it. Assert `manual` (not just the score) is still present."""
        scores = tmp_path / "sender_scores.json"
        scores.write_text(json.dumps({"vip@x.com": {"score": 0.95, "manual": True,
                                                    "replied_count": 0, "unread_count": 0}}))
        monkeypatch.setattr(mail_sender_scores, "SCORES", scores)
        monkeypatch.setattr(mail_sender_scores, "_flock", _spy_flock([]))

        class FakeGmailVipReplied:
            @staticmethod
            def search(q, n):
                return [{"id": "s1"}] if q.startswith("in:sent") else []

            @staticmethod
            def get_messages_metadata(ids, fields=()):
                return [{"id": "s1", "to": "VIP <vip@x.com>"}]

        monkeypatch.setattr(mail_sender_scores, "gmail_api", FakeGmailVipReplied)
        out = mail_sender_scores.seed(window_days=30)

        assert out["vip@x.com"].get("manual") is True, "manual flag dropped on re-seed"
        assert out["vip@x.com"]["score"] >= 0.95  # elevated score also preserved
        assert out["vip@x.com"]["replied_count"] == 1  # the real signal still recorded

        # a SECOND re-seed (same scenario) must still see -- and keep -- manual
        out2 = mail_sender_scores.seed(window_days=30)
        assert out2["vip@x.com"].get("manual") is True, \
            "manual flag lost after a second re-seed (the actual regression)"


class TestMailDigestWrite:
    def test_build_writes_atomic_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mail_digest, "TRIAGE", tmp_path / "mail_triage.jsonl")
        monkeypatch.setattr(mail_digest, "SUMMARIES", tmp_path / "mail_thread_summaries.jsonl")
        monkeypatch.setattr(mail_digest, "DRAFTS", tmp_path / "mail_drafts.jsonl")
        out = tmp_path / "mail_digest.json"
        monkeypatch.setattr(mail_digest, "OUT", out)
        (tmp_path / "mail_triage.jsonl").write_text(json.dumps({
            "id": "x1", "thread_id": "t1", "lane": "response_needed",
            "sender_email": "c@x.com", "subject": "Q", "why": "asked directly",
            "deadline": None, "response_needed": True,
            "classified_at": "2026-07-07T08:00:00+02:00"}) + "\n")

        digest = mail_digest.build(fixture=False)

        on_disk = json.loads(out.read_text())
        assert on_disk["top_line"] == digest["top_line"] and digest["top_line"]
        assert not out.with_suffix(".json.tmp").exists()
        assert "—" not in digest["top_line"] and "–" not in digest["top_line"]


class TestDailyBriefMailSection:
    def _digest(self, generated: str) -> dict:
        return {"top_line": "Client needs a reply: invoice question",
                "generated": generated,
                "sections": {"response_needed": [{"id": "1"}], "vip": [],
                             "newsletter_count": 4}}

    def test_missing_file_skips_silently(self, tmp_path, monkeypatch):
        monkeypatch.setattr(daily_brief, "MAIL_DIGEST", tmp_path / "absent.json")
        assert daily_brief._mail_line() == ""

    def test_corrupt_file_skips_silently(self, tmp_path, monkeypatch):
        p = tmp_path / "mail_digest.json"
        p.write_text("{not json")
        monkeypatch.setattr(daily_brief, "MAIL_DIGEST", p)
        assert daily_brief._mail_line() == ""

    def test_fresh_digest_renders_topline_and_counts(self, tmp_path, monkeypatch):
        p = tmp_path / "mail_digest.json"
        p.write_text(json.dumps(self._digest(store_lib.now_iso())))
        monkeypatch.setattr(daily_brief, "MAIL_DIGEST", p)
        line = daily_brief._mail_line()
        assert "Client needs a reply" in line
        assert "1 need a reply" in line and "0 vip" in line and "4 newsletters" in line
        assert "—" not in line and "–" not in line  # voice rail: no em/en dashes

    def test_stale_digest_skipped(self, tmp_path, monkeypatch):
        old = (datetime.now(store_lib.LOCAL_TZ) - timedelta(hours=48)).isoformat(timespec="seconds")
        p = tmp_path / "mail_digest.json"
        p.write_text(json.dumps(self._digest(old)))
        monkeypatch.setattr(daily_brief, "MAIL_DIGEST", p)
        assert daily_brief._mail_line() == ""


class TestDailyBriefIdempotentStamp:
    """R2-53 regression: the push stamp is owned by daily_brief itself (keyed by
    today's date), not morning.sh's overall .morning-done-<date> completion
    stamp written ~100 steps later -- a crash later in that chain must not
    erase the fact that the brief already pushed today."""

    def test_no_stamp_means_not_sent_yet(self, tmp_path, monkeypatch):
        (tmp_path / "store").mkdir()
        monkeypatch.setattr(daily_brief, "ROOT", tmp_path)
        assert daily_brief.already_sent_today() is False

    def test_todays_stamp_means_already_sent(self, tmp_path, monkeypatch):
        store = tmp_path / "store"
        store.mkdir()
        monkeypatch.setattr(daily_brief, "ROOT", tmp_path)
        today = datetime.now(store_lib.LOCAL_TZ).strftime("%Y-%m-%d")
        (store / f".daily_brief_sent-{today}").write_text(store_lib.now_iso())
        assert daily_brief.already_sent_today() is True

    def test_a_stale_stamp_from_another_day_does_not_count(self, tmp_path, monkeypatch):
        store = tmp_path / "store"
        store.mkdir()
        monkeypatch.setattr(daily_brief, "ROOT", tmp_path)
        (store / ".daily_brief_sent-2020-01-01").write_text("x")
        assert daily_brief.already_sent_today() is False


class TestMailBrainCursorAdvance:
    """R2-46 regression: mail_brain.run() must advance the Gmail history cursor
    only after every fetched id has actually been classified, and must NOT
    advance past ids a [:limit] slice never even fetched -- otherwise
    unprocessed mail is permanently acked and never seen again."""

    def _stub(self, monkeypatch, tmp_path, message_ids):
        monkeypatch.setattr(mail_brain, "TRIAGE", tmp_path / "mail_triage.jsonl")
        delta = {"message_ids": message_ids, "mode": "history", "history_id": "h2"}
        monkeypatch.setattr(mail_sync, "peek", lambda: delta)
        advanced: list[dict] = []
        monkeypatch.setattr(mail_sync, "advance_cursor", lambda r: advanced.append(r))
        monkeypatch.setattr(
            mail_brain.gmail_api, "get_messages_metadata",
            lambda ids, fields=(): [{"id": i, "from": "a@b.com", "subject": "s", "date": ""} for i in ids])

        def fake_classify(batch):
            return [{"id": m["id"], "thread_id": "", "from": m["from"], "sender_email": "a@b.com",
                     "subject": "s", "date": "", "lane": "noise", "why": "x",
                     "response_needed": False, "deadline": None, "entities": [],
                     "legal_flag": False, "watchlist_hit": None, "intro_email": False,
                     "dup_subject": False, "sender_score": 0, "pitch_language": False,
                     "sales_pitch": False, "classified_at": "x"} for m in batch]
        monkeypatch.setattr(mail_brain, "classify_batch", fake_classify)
        return delta, advanced

    def test_full_delta_consumed_advances_cursor(self, tmp_path, monkeypatch):
        delta, advanced = self._stub(monkeypatch, tmp_path, ["m1", "m2"])
        result = mail_brain.run(limit=100)
        assert result["classified"] == 2
        assert advanced == [delta]  # nothing left unprocessed -- safe to ack

    def test_truncated_by_limit_does_not_advance_cursor(self, tmp_path, monkeypatch):
        delta, advanced = self._stub(monkeypatch, tmp_path, ["m1", "m2", "m3"])
        result = mail_brain.run(limit=2)  # only m1/m2 fetched+classified this run
        assert result["classified"] == 2
        assert advanced == []  # m3 was never touched -- must not ack past it

    def test_empty_delta_still_advances(self, tmp_path, monkeypatch):
        delta, advanced = self._stub(monkeypatch, tmp_path, [])
        result = mail_brain.run(limit=100)
        assert result["classified"] == 0
        assert advanced == [delta]  # genuinely nothing new, not a truncation

    def test_all_already_triaged_still_advances_when_not_truncated(self, tmp_path, monkeypatch):
        delta, advanced = self._stub(monkeypatch, tmp_path, ["m1"])
        mail_brain.TRIAGE.write_text(json.dumps({"id": "m1", "lane": "noise"}) + "\n")
        result = mail_brain.run(limit=100)
        assert result["classified"] == 0
        assert result["mode"] == "already_triaged"
        assert advanced == [delta]  # every fetched id accounted for -- safe to ack
