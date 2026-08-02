#!/usr/bin/env python3
"""Daily Money Session push (2026-07-12, STATE-OF-JARVIS fix #1). Pins the gating
(knob/window/once-a-day) and that the click list is live-data-driven, dollar-ordered,
and capped at 5. The push goes to Alex's own phone (a nudge, not an outward send)."""
from __future__ import annotations
import json
import sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import money_session as ms  # noqa: E402


def _wire(monkeypatch, tmp_path, *, cfg=None, hh=18, mm=45, clicks=None):
    monkeypatch.setattr(ms, "ROOT", tmp_path)
    (tmp_path / "store").mkdir(exist_ok=True)
    monkeypatch.setattr(ms.planner, "_config", lambda: (cfg if cfg is not None else {}))
    monkeypatch.setattr(ms, "_now", lambda: datetime(2026, 7, 12, hh, mm).astimezone())
    pushes = []
    # notify returns True (push landed) so the once-a-day stamp is written; a test that needs
    # the ntfy-down path overrides this to return False after _wire (2026-07-13 gating change).
    monkeypatch.setattr(ms.planner, "notify", lambda t, b: pushes.append((t, b)) or True)
    monkeypatch.setattr(ms.planner, "feed_add", lambda *a, **k: None)
    if clicks is not None:
        monkeypatch.setattr(ms, "build_clicks", lambda: clicks)
    return pushes


class TestGating:
    def test_knob_off_silent(self, monkeypatch, tmp_path):
        pushes = _wire(monkeypatch, tmp_path, cfg={"money_session": 0}, clicks=["x"])
        assert ms.run() == 0 and pushes == []

    def test_outside_window_silent(self, monkeypatch, tmp_path):
        for hh, mm in ((17, 45), (18, 15), (19, 0), (9, 0)):
            pushes = _wire(monkeypatch, tmp_path, hh=hh, mm=mm, clicks=["x"])
            ms.run()
            assert pushes == [], f"{hh}:{mm} must be silent"

    def test_pushes_once_then_stamps(self, monkeypatch, tmp_path):
        pushes = _wire(monkeypatch, tmp_path, clicks=["Send X ($3,500)", "Call Y"])
        ms.run()
        assert len(pushes) == 1 and "2 clicks" in pushes[0][0]
        assert (tmp_path / "store" / ".money-session-2026-07-12").exists()
        assert json.loads((tmp_path / "store" / "money_session.json").read_text())["clicks"]
        ms.run()  # second tick same evening
        assert len(pushes) == 1  # exactly once

    def test_nothing_pending_stays_quiet_but_stamps(self, monkeypatch, tmp_path):
        pushes = _wire(monkeypatch, tmp_path, clicks=[])
        ms.run()
        assert pushes == []
        assert (tmp_path / "store" / ".money-session-2026-07-12").exists()

    def test_failed_push_not_stamped_and_retries(self, monkeypatch, tmp_path):
        # 2026-07-13 hunt: a failed notify() (ntfy outage) must NOT stamp, or the push is
        # silently eaten and never retried that evening. Unstamped -> next tick re-fires.
        _wire(monkeypatch, tmp_path, clicks=["Send X ($3,500)"])
        monkeypatch.setattr(ms.planner, "notify", lambda t, b: False)
        ms.run()
        assert not (tmp_path / "store" / ".money-session-2026-07-12").exists()
        ms.run()  # still down: still no stamp, still eligible to retry
        assert not (tmp_path / "store" / ".money-session-2026-07-12").exists()


class TestClickBuilder:
    def test_dollar_ordered_and_capped(self, monkeypatch, tmp_path):
        import proposal_factory as pf, reply_watch as rw, content_gen
        monkeypatch.setattr(pf, "load_queue", lambda: [
            {"status": "staged", "company": "Small Co", "price": 800},
            {"status": "staged", "company": "Big Co", "price": 3500},
            {"status": "staged", "company": "Mid Co", "price": 2500},
        ])
        monkeypatch.setattr(rw, "_load", lambda: [{"status": "pending"}] * 3)
        monkeypatch.setattr(content_gen, "load_posts", lambda: [{"status": "approved"}] * 2)
        monkeypatch.setattr(ms, "ROOT", tmp_path)  # no attention.json -> skip that line
        monkeypatch.setattr(ms, "_dmarc_missing", lambda domain="x": True)
        clicks = ms.build_clicks()
        assert len(clicks) <= 5
        assert "Big Co" in clicks[0] and "$3,500" in clicks[0]
        assert "Mid Co" in clicks[1]
        assert any("reply draft" in c for c in clicks)
        assert any("approved post" in c for c in clicks)
