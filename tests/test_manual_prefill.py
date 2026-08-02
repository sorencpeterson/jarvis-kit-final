"""job_pipeline_quality manual-apply prefill companion + batch notify (2026-07-15)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import job_pipeline_quality as jpq  # noqa: E402
import jobs  # noqa: E402

PROFILE = {"full_name": "Alex Rivera", "email": "s@x.io", "phone": "555",
           "city_state": "Portland, OR", "linkedin": "in/x", "_note": "hidden",
           "default_cover": "Fallback cover."}


@pytest.fixture(autouse=True)
def stub(monkeypatch):
    monkeypatch.setattr(jobs, "load_profile", lambda: PROFILE)
    monkeypatch.setattr(jobs, "salary_target", lambda job, floor=0: (95000, "state $95k"))
    monkeypatch.setattr(jpq, "ROOT", ROOT)  # answer_bank read tolerates missing


class TestBuildPrefillCompanion:
    def test_core_and_new_fields(self):
        job = {"id": "j1", "title": "Ops Lead", "company": "Acme",
               "apply_url": "https://x.applytojob.com/a", "source": "jazzhr", "reason": "captcha"}
        c = jpq.build_prefill_companion(job)
        assert c["job_id"] == "j1" and c["ats"] == "jazzhr" and c["reason"] == "captcha"
        assert c["salary_directive"] == "state $95k"
        assert c["resume_path"].endswith("resume.pdf")
        assert c["applied_link"].startswith("http://localhost:8765/api/jobs/j1/applied?cb=")
        assert c["verify_before_submit"] is False
        assert "US VPN" in c["geo_note"] or "Mullvad" in c["geo_note"]
        assert "_note" not in c["profile_fields"]  # underscore keys stripped

    def test_uncertain_reason_flags_verify(self):
        for r in ("attempt_cap (2 tries, walled by captcha/login)", "inflight_timeout (operator died)"):
            c = jpq.build_prefill_companion({"id": "j2", "title": "T", "company": "C",
                                             "apply_url": "https://x/y", "reason": r})
            assert c["verify_before_submit"] is True

    def test_email_path_detected(self):
        c = jpq.build_prefill_companion({"id": "j3", "title": "T", "company": "C",
                                         "apply_url": "mailto:jobs@c.com", "reason": "login"})
        assert c["apply_email"] == "jobs@c.com"


class TestWritePrefillCollision:
    def test_colliding_ids_get_distinct_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(jpq, "PREFILL_DIR", tmp_path)
        # two ids that a bare regex sanitize would collapse to the same name
        j1 = {"id": "role:a", "title": "A", "company": "X", "apply_url": "u1", "reason": "captcha"}
        j2 = {"id": "role?a", "title": "B", "company": "Y", "apply_url": "u2", "reason": "captcha"}
        monkeypatch.setattr(jobs, "load_jobs", lambda: [j1, j2])
        monkeypatch.setattr(jobs, "needs_manual", lambda: [
            {"id": "role:a", "apply_url": "u1", "reason": "captcha"},
            {"id": "role?a", "apply_url": "u2", "reason": "captcha"}])
        n = jpq.write_prefill_companions()
        assert n == 2
        files = list(tmp_path.glob("*.json"))
        assert len(files) == 2  # distinct filenames despite the ':'/'?' collision


class TestNotifyManualPile:
    def _state(self, tmp_path, monkeypatch, armed, last_ts=""):
        monkeypatch.setattr(jpq, "NOTIFY_STATE", tmp_path / "s.json")
        (tmp_path / "s.json").write_text(json.dumps({"armed": armed, "last_ts": last_ts}))

    def test_fires_at_threshold_when_armed(self, tmp_path, monkeypatch):
        self._state(tmp_path, monkeypatch, armed=True)
        sent = {}
        import planner
        monkeypatch.setattr(planner, "notify", lambda *a, **k: sent.setdefault("hit", True))
        assert jpq.notify_manual_pile(count=5) is True and sent.get("hit")

    def test_no_fire_below_threshold(self, tmp_path, monkeypatch):
        self._state(tmp_path, monkeypatch, armed=True)
        assert jpq.notify_manual_pile(count=4) is False

    def test_no_double_fire_until_rearm(self, tmp_path, monkeypatch):
        self._state(tmp_path, monkeypatch, armed=False)  # already fired
        assert jpq.notify_manual_pile(count=6) is False   # stays quiet

    def test_rearms_below_low_water(self, tmp_path, monkeypatch):
        self._state(tmp_path, monkeypatch, armed=False)
        assert jpq.notify_manual_pile(count=2) is False    # under re-arm -> re-arm, no push
        st = json.loads((tmp_path / "s.json").read_text())
        assert st["armed"] is True
