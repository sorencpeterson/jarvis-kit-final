"""Content-engager DM lane (agents/li_engager_dm.py, 2026-07-15) + the dm-kind
release wiring in networking.py it depends on.

Same isolation discipline as test_networking.py / test_li_conveyor.py: every store
path redirected to tmp_path, planner._cli_json mocked (no real LLM), li_budget gate
forced open, planner._config forced empty so _net_caps() uses its documented defaults.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import li_budget  # noqa: E402
import li_engager_dm  # noqa: E402
import networking  # noqa: E402
import planner  # noqa: E402

CLEAN_DRAFT = ("Hey Sam,\n\nI noticed your agency has been putting out strong work "
               "lately.\n\nQuick question. When website projects come in, do you build "
               "them in-house, or do you partner with outside teams when capacity gets "
               "tight?")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(networking, "QUEUE", tmp_path / "network.jsonl")
    monkeypatch.setattr(li_engager_dm, "ENGAGERS", tmp_path / "li_engagers.jsonl")
    monkeypatch.setattr(planner, "_config", lambda: {})
    monkeypatch.setattr(li_budget, "weekend_paused", lambda now=None: False)
    monkeypatch.setattr(li_budget, "in_hours_window", lambda now=None: True)
    monkeypatch.setattr(li_budget, "budget_remaining_today", lambda: 10 ** 6)
    monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: {"draft": CLEAN_DRAFT})
    return tmp_path


def _engager(**kw) -> dict:
    base = {"url": "https://linkedin.com/in/sam-agency", "name": "Sam Rivera",
            "headline": "Founder at Rivera Marketing", "degree": "1st",
            "interaction": "commented: this is exactly what we ran into last quarter",
            "ts": "2026-07-15T08:00:00-07:00"}
    base.update(kw)
    return base


def _write_engagers(rows):
    with li_engager_dm.ENGAGERS.open("a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _queue():
    return networking.load_queue()


class TestEngagerLane:
    def test_no_file_reports_gap_note(self):
        r = li_engager_dm.run()
        assert r["engagers"] == 0 and "operator" in r["note"]

    def test_first_degree_queues_pending_dm(self):
        _write_engagers([_engager()])
        r = li_engager_dm.run()
        assert len(r["queued_dms"]) == 1
        item = _queue()[0]
        assert item["kind"] == "dm" and item["status"] == "pending"
        assert item["source"] == "engager_fit"
        assert item["author"] == "Sam Rivera"
        assert "—" not in item["draft"] and "–" not in item["draft"]

    def test_non_first_degree_becomes_connect_not_dm(self):
        _write_engagers([_engager(degree="2nd")])
        r = li_engager_dm.run()
        assert not r["queued_dms"]
        kinds = {x["kind"] for x in _queue()}
        assert "dm" not in kinds and "connect" in kinds
        con = next(x for x in _queue() if x["kind"] == "connect")
        assert "engaged:" in con["target"]  # interaction context rides in the headline

    def test_dm_never_drafted_twice_for_same_person(self):
        _write_engagers([_engager()])
        assert len(li_engager_dm.run()["queued_dms"]) == 1
        # second sweep (same row still in the jsonl) must not re-queue
        assert len(li_engager_dm.run()["queued_dms"]) == 0
        assert sum(1 for x in _queue() if x["kind"] == "dm") == 1

    def test_conveyor_dm_for_url_blocks_engager_dm(self):
        # a day-2 conveyor dm already queued for this person: one opener per human, ever
        networking.save_item({"id": "dm_prior", "kind": "dm", "author": "Sam Rivera",
                              "target": "", "url": _engager()["url"],
                              "draft": "hi", "status": "done",
                              "created": "2026-07-10T08:00:00-07:00"})
        _write_engagers([_engager()])
        assert len(li_engager_dm.run()["queued_dms"]) == 0

    def test_dry_mode_writes_nothing(self, monkeypatch):
        called = {"n": 0}
        monkeypatch.setattr(planner, "_cli_json",
                            lambda *a, **k: called.__setitem__("n", called["n"] + 1))
        _write_engagers([_engager(), _engager(url="https://linkedin.com/in/two",
                                              name="Jo Two", degree="2nd")])
        r = li_engager_dm.run(dry=True)
        assert r["dry"] and called["n"] == 0 and not _queue()
        assert len(r["queued_dms"]) == 1 and len(r["queued_connects"]) == 1

    def test_template_rotation_stable_per_url(self):
        u = "https://linkedin.com/in/stable-check"
        assert li_engager_dm._template_for(u) == li_engager_dm._template_for(u)
        assert li_engager_dm._template_for(u) in li_engager_dm.TEMPLATES

    def test_skip_marked_row_never_contacts(self):
        # first real capture (2026-07-15) was Alex's own dad: a row annotated with
        # skip=<reason> must produce neither a dm nor a connect, whatever its degree
        _write_engagers([_engager(skip="family: never pitch"),
                         _engager(url="https://linkedin.com/in/other", name="Kim Other",
                                  degree="2nd", skip="family too")])
        r = li_engager_dm.run()
        assert not r["queued_dms"] and not r["queued_connects"] and not _queue()

    def test_failed_quality_gate_never_queues(self, monkeypatch):
        monkeypatch.setattr(li_engager_dm.li_quality, "validate_draft",
                            lambda *a, **k: {"ok": False, "text": ""})
        _write_engagers([_engager()])
        assert len(li_engager_dm.run()["queued_dms"]) == 0 and not _queue()

    def test_last_write_wins_by_url(self):
        _write_engagers([_engager(degree="2nd"), _engager(degree="1st")])
        rows = li_engager_dm.load_engagers()
        assert len(rows) == 1 and rows[0]["degree"] == "1st"


class TestDmReleaseWiring:
    def test_dm_capped_at_8_by_default(self):
        assert networking.allowance()["dm"] == 8

    def test_partial_config_without_dm_key_keeps_cap(self, monkeypatch):
        # a config.json written before the dm kind existed must NOT uncap dms
        monkeypatch.setattr(planner, "_config",
                            lambda: {"network": {"daily": {"connect": 5}}})
        allow = networking.allowance()
        assert allow["dm"] == 8 and allow["connect"] == 5

    def test_explicit_zero_uncaps(self, monkeypatch):
        monkeypatch.setattr(planner, "_config",
                            lambda: {"network": {"daily": {"dm": 0}}})
        assert networking.allowance()["dm"] == 10 ** 6

    def test_approved_dm_releases_and_claims(self):
        networking.save_item({"id": "dm1", "kind": "dm", "author": "A", "target": "",
                              "url": "https://linkedin.com/in/a", "draft": "hello",
                              "status": "approved", "created": "2026-07-15T08:00:00-07:00"})
        out = networking.approved_to_run()
        assert [x["id"] for x in out] == ["dm1"]
        assert all(x["status"] == "running" for x in out)

    def test_dm_release_respects_daily_cap(self, monkeypatch):
        monkeypatch.setattr(planner, "_config",
                            lambda: {"network": {"daily": {"dm": 2}}})
        for i in range(4):
            networking.save_item({"id": f"dm{i}", "kind": "dm", "author": "A",
                                  "target": "", "url": f"https://linkedin.com/in/p{i}",
                                  "draft": "hello", "status": "approved",
                                  "created": "2026-07-15T08:00:00-07:00"})
        assert len(networking.approved_to_run()) == 2
        # the two claimed ones now count as running-today: nothing more releases
        assert len(networking.approved_to_run()) == 0
