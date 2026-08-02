#!/usr/bin/env python3
"""Content engine bulk-up (2026-07-11, Alex: "lots of high value, different and fresh
content"). Pins the fixes: the buffer-full deadlock is gone (daily-additive generation,
idempotent across self-heal re-runs), stale drafts auto-archive instead of starving the
generator, angles rotate least-recently-used, and the objection bank parses + rotates.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import content_gen  # noqa: E402


def _iso(days_ago=0):
    return (datetime.now().astimezone() - timedelta(days=days_ago)).isoformat()


def _wire_posts(monkeypatch, tmp_path, rows):
    f = tmp_path / "posts.jsonl"
    f.write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr(content_gen, "POSTS", f)
    return f


class TestStaleArchive:
    def test_old_drafts_archive_but_approved_survive(self, monkeypatch, tmp_path):
        _wire_posts(monkeypatch, tmp_path, [
            {"id": "old1", "status": "draft", "created": _iso(20)},
            {"id": "old2", "status": "approved", "created": _iso(20)},   # HIS call: keep
            {"id": "new1", "status": "draft", "created": _iso(2)},
        ])
        n = content_gen.archive_stale(content_gen.load_posts(), 14)
        assert n == 1
        st = {p["id"]: p["status"] for p in content_gen.load_posts()}
        assert st == {"old1": "stale", "old2": "approved", "new1": "draft"}


class TestSavePostCas:
    """R3#5 (2026-07-14): save_post's lock only serializes writes -- it does not
    stop a writer holding a STALE in-memory copy from reverting a post that has
    already moved further along (e.g. a push already marked it 'scheduled') back
    to an earlier status, which would make the next push sweep repost it."""

    def test_stale_approved_write_does_not_revert_scheduled(self, monkeypatch, tmp_path):
        _wire_posts(monkeypatch, tmp_path, [
            {"id": "p1", "status": "scheduled", "created": _iso(1), "ghl_id": "gh1"},
        ])
        stale_copy = {"id": "p1", "status": "approved", "created": _iso(1), "img_check": {"ok": True}}
        content_gen.save_post(stale_copy)  # simulates a late write from before the push
        st = {p["id"]: p["status"] for p in content_gen.load_posts()}
        assert st["p1"] == "scheduled"  # NOT reverted

    def test_stale_draft_write_does_not_revert_posted(self, monkeypatch, tmp_path):
        _wire_posts(monkeypatch, tmp_path, [
            {"id": "p1", "status": "posted", "created": _iso(3)},
        ])
        content_gen.save_post({"id": "p1", "status": "draft", "created": _iso(3)})
        st = {p["id"]: p["status"] for p in content_gen.load_posts()}
        assert st["p1"] == "posted"

    def test_forward_transition_still_writes(self, monkeypatch, tmp_path):
        _wire_posts(monkeypatch, tmp_path, [
            {"id": "p1", "status": "approved", "created": _iso(1)},
        ])
        content_gen.save_post({"id": "p1", "status": "scheduled", "created": _iso(1), "ghl_id": "gh1"})
        st = {p["id"]: p["status"] for p in content_gen.load_posts()}
        assert st["p1"] == "scheduled"

    def test_same_rank_metadata_only_write_still_lands(self, monkeypatch, tmp_path):
        # a metadata-only update (img_check) that keeps the SAME status must still
        # persist -- the guard only blocks a genuine backward status move
        _wire_posts(monkeypatch, tmp_path, [
            {"id": "p1", "status": "approved", "created": _iso(1)},
        ])
        content_gen.save_post({"id": "p1", "status": "approved", "created": _iso(1),
                               "img_check": {"ok": True}})
        latest = next(p for p in content_gen.load_posts() if p["id"] == "p1")
        assert latest["status"] == "approved" and latest["img_check"] == {"ok": True}

    def test_brand_new_post_is_unaffected(self, monkeypatch, tmp_path):
        _wire_posts(monkeypatch, tmp_path, [])
        content_gen.save_post({"id": "new1", "status": "draft", "created": _iso(0)})
        st = {p["id"]: p["status"] for p in content_gen.load_posts()}
        assert st == {"new1": "draft"}


class TestAngleRotation:
    def test_least_recently_used_first(self, monkeypatch, tmp_path):
        # playbook + contrarian-take used most recently -> must NOT be in the next 3 picks
        rows = [{"id": f"p{i}", "status": "posted", "created": _iso(1),
                 "angle": a} for i, a in enumerate(
                    ["playbook", "contrarian-take", "objection-column"])]
        _wire_posts(monkeypatch, tmp_path, rows)
        picks = [k for k, _ in content_gen._pick_angles(content_gen.load_posts(), 9)]
        assert "playbook" not in picks and "contrarian-take" not in picks
        assert len(picks) == len(set(picks)) == 9

    def test_never_used_angles_beat_used_ones(self, monkeypatch, tmp_path):
        _wire_posts(monkeypatch, tmp_path,
                    [{"id": "p1", "status": "posted", "created": _iso(1), "angle": "myth-bust"}])
        picks = [k for k, _ in content_gen._pick_angles(content_gen.load_posts(), len(content_gen.ANGLES) - 1)]
        assert "myth-bust" not in picks


class TestObjectionBank:
    def test_bank_parses_the_real_playbook(self):
        bank = content_gen._objection_bank()
        # the real 50-objection file ships with this repo's business-library
        assert len(bank) >= 30
        nums = [n for n, _ in bank]
        assert len(nums) == len(set(nums))
        assert all('"' in block for _, block in bank[:5])   # blocks carry the verbatim objection

    def test_rotation_prefers_unused(self, monkeypatch, tmp_path):
        _wire_posts(monkeypatch, tmp_path,
                    [{"id": "p1", "status": "posted", "created": _iso(1),
                      "angle": "objection-column", "objection_n": 1}])
        pick = content_gen._pick_objection(content_gen.load_posts())
        assert pick is not None and pick[0] != 1


class TestDailyAdditive:
    def _run_main(self, monkeypatch, tmp_path, rows, cfg=None, gen_result=None):
        _wire_posts(monkeypatch, tmp_path, rows)
        monkeypatch.setattr(content_gen, "_config", lambda: (cfg or {}))
        made = []
        monkeypatch.setattr(content_gen, "generate",
                            lambda n: made.extend([{"id": f"g{i}", "score": 8, "hook": "h",
                                                    "angle": "playbook", "status": "draft",
                                                    "created": content_gen.now_iso()}
                                                   for i in range(n)]) or made[-n:])
        monkeypatch.setattr(content_gen.planner, "feed_add", lambda *a, **k: None)
        monkeypatch.setattr(sys, "argv", ["content_gen.py"])
        content_gen.main()
        return made

    def test_generates_daily_even_with_a_full_old_buffer(self, monkeypatch, tmp_path):
        # 20 live-but-old drafts used to mean "Buffer full, nothing to generate" forever
        rows = [{"id": f"d{i}", "status": "draft", "created": _iso(5)} for i in range(20)]
        made = self._run_main(monkeypatch, tmp_path, rows)
        assert len(made) == content_gen.DAILY_NEW_DEFAULT

    def test_idempotent_within_a_day(self, monkeypatch, tmp_path):
        # a self-heal rerun the same day must NOT double today's quota
        rows = [{"id": f"t{i}", "status": "draft", "created": _iso(0)}
                for i in range(content_gen.DAILY_NEW_DEFAULT)]
        made = self._run_main(monkeypatch, tmp_path, rows)
        assert made == []

    def test_inventory_ceiling_holds(self, monkeypatch, tmp_path):
        rows = [{"id": f"d{i}", "status": "draft", "created": _iso(3)} for i in range(29)]
        made = self._run_main(monkeypatch, tmp_path, rows,
                              cfg={"content_max_fresh": 30})
        assert len(made) == 1   # room for exactly one under the ceiling

    def test_content_daily_new_zero_pauses_generation(self, monkeypatch, tmp_path):
        # R2-31: `cfg.get(...) or DEFAULT` treated an explicit 0 the same as "unset" (0 is
        # falsy), so content_daily_new=0 fell through to DAILY_NEW_DEFAULT (6) -- pausing
        # daily generation via config was impossible. Empty buffer too, so the OLD code's
        # "buffer full" path can't be what's stopping it here -- only the explicit 0 should.
        made = self._run_main(monkeypatch, tmp_path, [], cfg={"content_daily_new": 0})
        assert made == []

    def test_content_max_fresh_zero_respected(self, monkeypatch, tmp_path):
        # same or-vs-None-check bug, one line down: an explicit 0 ceiling must mean "no
        # room," not fall back to MAX_FRESH_DEFAULT (30).
        rows = [{"id": "d0", "status": "draft", "created": _iso(3)}]
        made = self._run_main(monkeypatch, tmp_path, rows, cfg={"content_max_fresh": 0})
        assert made == []


class TestNoMedspas:
    """Alex, 2026-07-11: "no medspas." The niche is dropped everywhere in content."""

    def test_no_medspa_angle(self):
        assert not any("medspa" in k for k, _ in content_gen.ANGLES)
        assert not any("medspa" in b.lower() for _, b in content_gen.ANGLES)

    def test_niche_rotation_never_serves_the_medspa_book(self, monkeypatch):
        # whatever day it is, the rotating excerpt must never come from medspa.md
        for day in range(1, 366):
            class _FakeDT:
                @staticmethod
                def now():
                    import datetime as _d
                    base = _d.datetime(2026, 1, 1) + _d.timedelta(days=day - 1)
                    return base
            monkeypatch.setattr(content_gen, "_dt", _FakeDT)
            note = content_gen._niche_notes()
            assert "[medspa]" not in note
        # and the art/prompt scaffolding carries no medspa either
        assert "medspa" not in json.dumps(content_gen.ART).lower()

    def test_every_angle_has_art_direction(self):
        assert {k for k, _ in content_gen.ANGLES} == set(content_gen.ART)


class TestAutoApprove:
    def test_nine_plus_auto_approves_eight_stays_draft(self, monkeypatch, tmp_path):
        _wire_posts(monkeypatch, tmp_path, [])
        monkeypatch.setattr(content_gen, "_config", lambda: {"auto_approve_min": 9})
        monkeypatch.setattr(content_gen, "make_card", lambda rec: None)
        monkeypatch.setattr(content_gen.planner, "_cli_json", lambda *a, **k: [
            {"text": "nine post", "hook": "h", "topic": "t", "angle": "playbook",
             "score": 9, "image_prompt": "x"},
            {"text": "eight post", "hook": "h", "topic": "t", "angle": "myth-bust",
             "score": 8, "image_prompt": "x"},
        ])
        recs = content_gen.generate(2)
        st = {r["text"]: r["status"] for r in recs}
        assert st == {"nine post": "approved", "eight post": "draft"}
