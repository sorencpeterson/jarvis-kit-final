#!/usr/bin/env python3
"""Unit tests for the Section-5 jobs builds: agents/interview_war_room.py,
agents/interview_followup.py, agents/warmest_five.py.

Everything runs against tmp-path stores with planner.notify / planner.feed_add
/ planner._cli monkeypatched, so no push, no feed write, no LLM call, and no
touch of the real store/ ever happens in here.

Run: .venv/bin/python -m pytest tests/test_jobs_builds.py -v
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import interview_followup as fup  # noqa: E402
import interview_war_room as war  # noqa: E402
import warmest_five as w5  # noqa: E402
import jobs  # noqa: E402
import planner  # noqa: E402


def _iso_days_ago(n: float) -> str:
    return (datetime.now().astimezone() - timedelta(days=n)).isoformat(timespec="seconds")


def _interview_job(**over) -> dict:
    j = {"id": "j1", "title": "Marketing Manager", "company": "TestCo",
         "status": "interview", "salary": "$120k-$150k", "comp_max": 150000,
         "source": "workable", "seniority": "Senior Level",
         "apply_url": "https://example.com/job",
         "created": _iso_days_ago(9), "applied_at": _iso_days_ago(8)}
    j.update(over)
    return j


def _capture(monkeypatch):
    """Swap planner.notify/feed_add for recorders. Returns (pushes, feed)."""
    pushes, feed = [], []
    monkeypatch.setattr(planner, "notify",
                        lambda title, body, tags="brain", actions=None:
                        (pushes.append((title, body)), True)[1])
    monkeypatch.setattr(planner, "feed_add",
                        lambda kind, title, detail="": feed.append((kind, title)))
    return pushes, feed


# ---------------------------------------------------------------- war room
FIVE_LINES = "1. one\n2. two\n3. three\n4. four\n5. five"


def _wire_war(tmp_path, monkeypatch, jobs_list, prep_text="PREP-MARKER content",
              star="STAR-MARKER story", cli=FIVE_LINES):
    monkeypatch.setattr(war, "PREP_DIR", tmp_path / "prep")
    monkeypatch.setattr(war, "OUT_DIR", tmp_path / "war_room")
    monkeypatch.setattr(war, "STATE", tmp_path / "war_room_state.json")
    monkeypatch.setattr(jobs, "load_jobs", lambda: jobs_list)
    monkeypatch.setattr(jobs, "load_profile",
                        lambda: {"salary_expectation": "$125,000/year"})
    monkeypatch.setattr(war, "star_bank", lambda: star)
    monkeypatch.setattr(war, "_calendar_lines",
                        lambda days=war.CAL_DAYS: (["Calendar check SKIPPED: test stub"], False))
    monkeypatch.setattr(planner, "_cli", lambda *a, **k: cli)
    if prep_text is not None and jobs_list:
        (tmp_path / "prep").mkdir(exist_ok=True)
        (tmp_path / "prep" / (jobs_list[0]["id"] + ".md")).write_text(prep_text)
    return _capture(monkeypatch)


class TestWarRoom:
    def test_assembles_from_prep_jobs_star_and_anchor(self, tmp_path, monkeypatch):
        pushes, feed = _wire_war(tmp_path, monkeypatch, [_interview_job()])
        built = war.run()
        assert built == ["TestCo"]
        doc = (tmp_path / "war_room" / "j1.md").read_text()
        assert "PREP-MARKER" in doc          # prep pack merged
        assert "STAR-MARKER" in doc          # star bank merged
        assert "$142,500" in doc             # band-aware anchor: 95% of their posted $150k max
        assert "never give a number first" in doc.lower()
        assert "SKIPPED" in doc              # calendar skip is stated, not hidden
        assert "## Walk in with" in doc
        assert "1. one" in doc and "5. five" in doc
        assert "—" not in doc and "–" not in doc  # no em/en dashes anywhere

    def test_fires_push_and_feed_exactly_once(self, tmp_path, monkeypatch):
        pushes, feed = _wire_war(tmp_path, monkeypatch, [_interview_job()])
        war.run()
        assert len(pushes) == 1
        assert pushes[0][0] == "War room ready: TestCo"
        assert len(feed) == 1
        # second run: state guard skips, nothing fires again
        assert war.run() == []
        assert len(pushes) == 1 and len(feed) == 1

    def test_force_rebuilds_without_repushing(self, tmp_path, monkeypatch):
        pushes, _ = _wire_war(tmp_path, monkeypatch, [_interview_job()])
        war.run()
        (tmp_path / "war_room" / "j1.md").unlink()
        assert war.run(force=True) == ["TestCo"]
        assert (tmp_path / "war_room" / "j1.md").exists()
        assert len(pushes) == 1  # rebuild is silent: not a NEW assembly

    def test_dry_run_writes_and_pushes_nothing(self, tmp_path, monkeypatch, capsys):
        pushes, feed = _wire_war(tmp_path, monkeypatch, [_interview_job()])
        war.run(dry_run=True)
        assert not (tmp_path / "war_room").exists()
        assert not (tmp_path / "war_room_state.json").exists()
        assert pushes == [] and feed == []
        out = capsys.readouterr().out
        assert "DRY RUN" in out and "PREP-MARKER" in out

    def test_missing_prep_and_template_star_are_named_honestly(self, tmp_path, monkeypatch):
        _wire_war(tmp_path, monkeypatch, [_interview_job()], prep_text=None, star="")
        war.run()
        doc = (tmp_path / "war_room" / "j1.md").read_text()
        assert "No prep pack found" in doc
        assert "still the template" in doc

    def test_cli_offline_falls_back_to_deterministic_summary(self, tmp_path, monkeypatch):
        _wire_war(tmp_path, monkeypatch, [_interview_job()], cli=None)
        war.run()
        doc = (tmp_path / "war_room" / "j1.md").read_text()
        assert "Never name a number first" in doc  # fallback line 3

    def test_fresh_install_no_jobs(self, tmp_path, monkeypatch):
        pushes, _ = _wire_war(tmp_path, monkeypatch, [])
        assert war.run() == []
        assert pushes == []


# ---------------------------------------------------------------- follow-up
def _wire_fup(tmp_path, monkeypatch, jobs_list):
    monkeypatch.setattr(fup, "STATE", tmp_path / "followup_state.json")
    monkeypatch.setattr(fup, "DRAFTS", tmp_path / "thankyou_drafts.jsonl")
    monkeypatch.setattr(fup, "PREP_DIR", tmp_path / "prep")
    monkeypatch.setattr(jobs, "load_jobs", lambda: jobs_list)
    return _capture(monkeypatch)


class TestFollowup:
    def test_under_day5_fires_nothing(self, tmp_path, monkeypatch):
        pushes, _ = _wire_fup(tmp_path, monkeypatch,
                              [_interview_job(interview_at=_iso_days_ago(3))])
        assert fup.run() == []
        assert pushes == []

    def test_day5_fires_once_and_is_idempotent(self, tmp_path, monkeypatch):
        pushes, feed = _wire_fup(tmp_path, monkeypatch,
                                 [_interview_job(interview_at=_iso_days_ago(6))])
        assert fup.run() == ["TestCo:day5"]
        assert len(pushes) == 1
        assert "5 days since the TestCo interview" in pushes[0][1]
        assert feed == []  # day-5 is push-only per spec
        assert fup.run() == []  # second run: state gate holds
        assert len(pushes) == 1

    def test_day10_fires_firmer_nag_plus_feed(self, tmp_path, monkeypatch):
        pushes, feed = _wire_fup(tmp_path, monkeypatch,
                                 [_interview_job(interview_at=_iso_days_ago(11))])
        assert fup.run() == ["TestCo:day10"]
        assert len(pushes) == 1
        assert pushes[0][0] == "Interview gone quiet: TestCo"
        assert len(feed) == 1 and "overdue" in feed[0][1]
        assert fup.run() == []
        assert len(pushes) == 1 and len(feed) == 1

    def test_day10_supersedes_day5_no_stale_double_push(self, tmp_path, monkeypatch):
        # first ever run on a job already 12 days silent: ONLY the day-10 nag
        pushes, _ = _wire_fup(tmp_path, monkeypatch,
                              [_interview_job(interview_at=_iso_days_ago(12))])
        fup.run()
        assert len(pushes) == 1 and "gone quiet" in pushes[0][0].lower()
        assert fup.run() == []  # and day5 never fires afterwards either
        assert len(pushes) == 1

    def test_day5_then_day10_across_time(self, tmp_path, monkeypatch):
        pushes, _ = _wire_fup(tmp_path, monkeypatch,
                              [_interview_job(interview_at=_iso_days_ago(6))])
        fup.run()
        assert len(pushes) == 1  # day5
        monkeypatch.setattr(jobs, "load_jobs",
                            lambda: [_interview_job(interview_at=_iso_days_ago(11))])
        assert fup.run() == ["TestCo:day10"]
        assert len(pushes) == 2

    def test_flip_ts_priority_order(self, tmp_path, monkeypatch):
        _wire_fup(tmp_path, monkeypatch, [])
        # 1. explicit field on the record wins
        ts, src = fup._flip_ts(_interview_job(interview_at="2026-07-01T10:00:00+02:00"))
        assert (ts, src) == ("2026-07-01T10:00:00+02:00", "interview_at")
        # 2. thankyou draft ts next
        (tmp_path / "thankyou_drafts.jsonl").write_text(
            json.dumps({"job_id": "j1", "ts": "2026-07-02T09:00:00+02:00"}) + "\n")
        ts, src = fup._flip_ts(_interview_job())
        assert (ts, src) == ("2026-07-02T09:00:00+02:00", "thankyou_draft")
        # 3. prep pack mtime next (draft for a DIFFERENT job does not match)
        (tmp_path / "thankyou_drafts.jsonl").write_text(
            json.dumps({"job_id": "other", "ts": "2026-07-02T09:00:00+02:00"}) + "\n")
        (tmp_path / "prep").mkdir()
        (tmp_path / "prep" / "j1.md").write_text("pack")
        ts, src = fup._flip_ts(_interview_job())
        assert src == "prep_mtime" and ts
        # 4. applied_at as the last real fallback
        (tmp_path / "prep" / "j1.md").unlink()
        ts, src = fup._flip_ts(_interview_job())
        assert src == "applied_at"

    def test_dry_run_touches_nothing(self, tmp_path, monkeypatch):
        pushes, feed = _wire_fup(tmp_path, monkeypatch,
                                 [_interview_job(interview_at=_iso_days_ago(11))])
        fup.run(dry_run=True)
        assert pushes == [] and feed == []
        assert not (tmp_path / "followup_state.json").exists()
        # and the real run afterwards still fires (dry-run burned no state)
        assert fup.run() == ["TestCo:day10"]

    def test_fresh_install_no_jobs(self, tmp_path, monkeypatch):
        pushes, _ = _wire_fup(tmp_path, monkeypatch, [])
        assert fup.run() == []
        assert pushes == []


# ---------------------------------------------------------------- warmest five
def _approved(i: int, fit: int = 70, days_old: float = 2, comp: int | None = 120000,
              status: str = "approved") -> dict:
    return {"id": f"a{i}", "title": f"Role {i}", "company": f"Co{i}", "status": status,
            "fit": fit, "posted": _iso_days_ago(days_old), "comp_max": comp}


def _wire_w5(tmp_path, monkeypatch, jobs_list):
    monkeypatch.setattr(w5, "OUT", tmp_path / "warmest_five.json")
    monkeypatch.setattr(jobs, "load_jobs", lambda: jobs_list)
    return _capture(monkeypatch)


class TestWarmestFive:
    def test_caps_at_five(self, tmp_path, monkeypatch):
        _wire_w5(tmp_path, monkeypatch, [_approved(i, fit=90 - i * 5) for i in range(8)])
        data = w5.run()
        assert len(data["picks"]) == 5
        saved = json.loads((tmp_path / "warmest_five.json").read_text())
        assert len(saved["picks"]) == 5 and saved["generated"]

    def test_orders_by_composite_score(self, tmp_path, monkeypatch):
        rows = [
            _approved(1, fit=90, days_old=1, comp=150000),   # 90 + 13 + 15 = 118
            _approved(2, fit=95, days_old=20, comp=None),    # 95 +  0 +  0 =  95
            _approved(3, fit=80, days_old=2, comp=100000),   # 80 + 12 + 10 = 102
        ]
        data = w5.run.__wrapped__(rows) if hasattr(w5.run, "__wrapped__") else None
        _wire_w5(tmp_path, monkeypatch, rows)
        data = w5.build()
        assert [p["id"] for p in data["picks"]] == ["a1", "a3", "a2"]
        # fresh + paid beats a higher raw fit that went stale
        assert data["picks"][0]["fit"] == 90

    def test_excludes_non_approved(self, tmp_path, monkeypatch):
        rows = [_approved(1), _approved(2, status="pending"),
                _approved(3, status="applied"), _approved(4, status="interview")]
        _wire_w5(tmp_path, monkeypatch, rows)
        data = w5.build()
        assert [p["id"] for p in data["picks"]] == ["a1"]

    def test_pick_shape_and_feed_line(self, tmp_path, monkeypatch):
        pushes, feed = _wire_w5(tmp_path, monkeypatch, [_approved(1, fit=88)])
        data = w5.run()
        p = data["picks"][0]
        assert set(p) == {"id", "title", "company", "fit", "why"}
        assert p["fit"] == 88 and "fit 88" in p["why"]
        assert len(feed) == 1 and feed[0][1].startswith("Today's 5: Co1 (Role 1)")
        assert pushes == []  # no pushes by design, the brief reads the file

    def test_empty_queue_writes_empty_picks_no_feed(self, tmp_path, monkeypatch):
        pushes, feed = _wire_w5(tmp_path, monkeypatch, [])
        data = w5.run()
        assert data["picks"] == []
        assert json.loads((tmp_path / "warmest_five.json").read_text())["picks"] == []
        assert feed == [] and pushes == []

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch, capsys):
        pushes, feed = _wire_w5(tmp_path, monkeypatch, [_approved(1)])
        w5.run(dry_run=True)
        assert not (tmp_path / "warmest_five.json").exists()
        assert feed == [] and pushes == []
        assert "dry-run" in capsys.readouterr().out
