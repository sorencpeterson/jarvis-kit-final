#!/usr/bin/env python3
"""Evening job lane (2026-07-11). Pins the self-gating: knob off = no-op, outside the
window = no-op, once-a-day stamp, geo-held = notify once + NO stamp (so later ticks
retry once the VPN is on), ran = stamp + notify, empty-queue = stamp (don't rescan for
3 hours). The morning lane's failure mode (knob shipped 0, VPN off at 6:30) is why this
lane exists; these tests keep its retry semantics honest.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import evening_chain  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode()

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _wire(monkeypatch, tmp_path, *, cfg, hour, post=None):
    (tmp_path / "store").mkdir(exist_ok=True)
    monkeypatch.setattr(evening_chain, "ROOT", tmp_path)
    monkeypatch.setattr(evening_chain.planner, "_config", lambda: cfg)
    monkeypatch.setattr(evening_chain, "_now",
                        lambda: datetime(2026, 7, 11, hour, 30).astimezone())
    monkeypatch.setattr(evening_chain, "secret", lambda k: "tok")
    monkeypatch.setattr(evening_chain.subprocess, "run", lambda *a, **k: None)  # scan = no-op
    calls = []
    notes = []
    monkeypatch.setattr(evening_chain.planner, "feed_add", lambda *a, **k: None)
    monkeypatch.setattr(evening_chain.planner, "notify", lambda t, m: notes.append(t))
    if post is not None:
        monkeypatch.setattr(evening_chain.urllib.request, "urlopen",
                            lambda req, timeout=0: calls.append(req) or _Resp(post))
    else:
        monkeypatch.setattr(evening_chain.urllib.request, "urlopen",
                            lambda req, timeout=0: (_ for _ in ()).throw(AssertionError("must not POST")))
    return calls, notes, tmp_path / "store"


CFG_ON = {"job_evening_chain": 1, "evening_hour": 19}


class TestGating:
    def test_knob_off_never_posts(self, monkeypatch, tmp_path):
        _wire(monkeypatch, tmp_path, cfg={"job_evening_chain": 0}, hour=19)
        assert evening_chain.run() == 0

    def test_outside_window_never_posts(self, monkeypatch, tmp_path):
        for h in (7, 12, 18, 22, 23):
            _wire(monkeypatch, tmp_path, cfg=CFG_ON, hour=h)
            assert evening_chain.run() == 0

    def test_stamped_day_never_posts(self, monkeypatch, tmp_path):
        _, _, store = _wire(monkeypatch, tmp_path, cfg=CFG_ON, hour=19)
        (store / ".evening-done-2026-07-11").touch()
        assert evening_chain.run() == 0


class TestRunPaths:
    def test_ran_stamps_and_notifies(self, monkeypatch, tmp_path):
        calls, notes, store = _wire(monkeypatch, tmp_path, cfg=CFG_ON, hour=19,
                                    post={"ok": True, "ran": True})
        evening_chain.run()
        assert len(calls) == 1
        assert (store / ".evening-done-2026-07-11").exists()
        assert any("running" in n.lower() for n in notes)

    def test_geo_held_no_stamp_notifies_once(self, monkeypatch, tmp_path):
        calls, notes, store = _wire(monkeypatch, tmp_path, cfg=CFG_ON, hour=19,
                                    post={"ok": False, "error": "Held: not on a US IP (currently Berlin)"})
        evening_chain.run()
        assert not (store / ".evening-done-2026-07-11").exists()   # retried next tick
        assert (store / ".evening-held-2026-07-11").exists()
        assert len(notes) == 1
        evening_chain.run()                                        # second tick, still held
        assert len(notes) == 1                                     # nag exactly once

    def test_empty_queue_stamps_to_stop_rescans(self, monkeypatch, tmp_path):
        calls, notes, store = _wire(monkeypatch, tmp_path, cfg=CFG_ON, hour=20,
                                    post={"ok": False, "error": "no approved jobs to apply to"})
        evening_chain.run()
        assert (store / ".evening-done-2026-07-11").exists()

    def test_server_down_no_stamp(self, monkeypatch, tmp_path):
        _, _, store = _wire(monkeypatch, tmp_path, cfg=CFG_ON, hour=19)
        monkeypatch.setattr(evening_chain.urllib.request, "urlopen",
                            lambda req, timeout=0: (_ for _ in ()).throw(OSError("down")))
        assert evening_chain.run() == 0
        assert not (store / ".evening-done-2026-07-11").exists()   # next tick retries
