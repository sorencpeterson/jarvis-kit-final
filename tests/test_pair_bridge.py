#!/usr/bin/env python3
"""Apply->LinkedIn pairing bridge (2026-07-12). Pins: fit floor, daily cap, once-per-job
(paired flag), recency window, and that the handoff file carries company+title+job_id.
The bridge stages SOURCING targets only — nothing outward (connects stay behind Alex's
NETWORK-tab approval + the 6pm engage caps)."""
from __future__ import annotations
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import job_network_bridge as b  # noqa: E402


def _job(i, fit=80, hours_ago=2, status="applied", paired=False):
    return {"id": f"j{i}", "company": f"Co{i}", "title": f"Role{i}", "fit": fit,
            "status": status, "paired": paired,
            "applied_at": (datetime.now().astimezone() - timedelta(hours=hours_ago)).isoformat()}


def _wire(monkeypatch, tmp_path, rows, cfg=None):
    monkeypatch.setattr(b, "TARGETS", tmp_path / "pair_targets.json")
    monkeypatch.setattr(b.jobs, "load_jobs", lambda: rows)
    saved = []
    monkeypatch.setattr(b.jobs, "_save", lambda r: saved.append(r))
    monkeypatch.setattr(b.planner, "_config", lambda: (cfg or {}))
    monkeypatch.setattr(b.planner, "feed_add", lambda *a, **k: None)
    return saved


class TestBridge:
    def test_stages_high_fit_recent_applies(self, monkeypatch, tmp_path):
        saved = _wire(monkeypatch, tmp_path, [_job(1, fit=90), _job(2, fit=75), _job(3, fit=60)])
        b.run()
        data = json.loads((tmp_path / "pair_targets.json").read_text())
        assert [t["company"] for t in data["targets"]] == ["Co1", "Co2"]  # fit>=72, sorted
        assert all(s.get("paired") for s in saved) and len(saved) == 2

    def test_cap_and_paired_and_stale_excluded(self, monkeypatch, tmp_path):
        rows = ([_job(i, fit=95) for i in range(8)]              # 8 candidates
                + [_job(90, fit=95, paired=True)]                # already paired
                + [_job(91, fit=95, hours_ago=99)])              # outside 36h window
        saved = _wire(monkeypatch, tmp_path, rows, cfg={"pair_daily_cap": 3})
        b.run()
        data = json.loads((tmp_path / "pair_targets.json").read_text())
        assert len(data["targets"]) == 3                         # cap holds
        assert all(t["job_id"] not in ("j90", "j91") for t in data["targets"])

    def test_nothing_new_writes_nothing(self, monkeypatch, tmp_path):
        _wire(monkeypatch, tmp_path, [_job(1, fit=50)])
        b.run()
        assert not (tmp_path / "pair_targets.json").exists()

    def test_unconsumed_targets_survive_a_new_run(self, monkeypatch, tmp_path):
        # 2026-07-13 fix, R2-24: the OLD write replaced the whole file the moment ANY new job
        # qualified, dropping every pending target the LinkedIn sourcing operator hadn't
        # consumed yet -- and since those jobs were already paired=True, they were gone for
        # good (never re-picked, never in a targets file again).
        targets_path = tmp_path / "pair_targets.json"
        monkeypatch.setattr(b, "TARGETS", targets_path)
        targets_path.write_text(json.dumps({"date": "2026-07-12",
                                            "targets": [{"company": "Stale", "title": "Old",
                                                        "job_id": "jOld", "fit": 99}]}))
        saved = _wire(monkeypatch, tmp_path, [_job(1, fit=90)])
        b.run()
        data = json.loads(targets_path.read_text())
        job_ids = {t["job_id"] for t in data["targets"]}
        assert "jOld" in job_ids, "prior unconsumed target was dropped"
        assert "j1" in job_ids
        assert len(data["targets"]) == 2

    def test_merge_dedupes_by_job_id(self, monkeypatch, tmp_path):
        # if a job somehow reappears in both the existing file and this run's picks, it must
        # not be staged twice.
        targets_path = tmp_path / "pair_targets.json"
        monkeypatch.setattr(b, "TARGETS", targets_path)
        targets_path.write_text(json.dumps({"date": "2026-07-12",
                                            "targets": [{"company": "Co1", "title": "Role1",
                                                        "job_id": "j1", "fit": 80}]}))
        _wire(monkeypatch, tmp_path, [_job(1, fit=90)])
        b.run()
        data = json.loads(targets_path.read_text())
        assert [t["job_id"] for t in data["targets"]] == ["j1"]
