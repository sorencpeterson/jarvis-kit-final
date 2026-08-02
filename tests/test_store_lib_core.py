#!/usr/bin/env python3
"""store_lib core guarantees (FABLE-MEGA-BACKLOG D8 missing-test #5).

sign_secret(): the HMAC key behind every capability link (/prop /mock /agree
/delivered) must NEVER degrade to a hardcodable constant -- a fresh install
with no brain_token gets a persisted random key, stable across calls.

compact_jsonl(): last-write-wins-by-id, first-seen order preserved, corrupt
lines survive the rewrite, and the sibling .lock flock is ACTUALLY taken
(a concurrent appender must block, or an append inside the read-then-replace
window is silently erased -- the 2026-07-06 janitor race).

Run: .venv/bin/python -m pytest tests/test_store_lib_core.py -q
"""
from __future__ import annotations

import fcntl
import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import store_lib  # noqa: E402


@pytest.fixture()
def fresh_install(tmp_path, monkeypatch):
    """Simulate a clone with NO configured brain_token: empty env, empty .env
    cache, no config.json, and store/ pointed at a throwaway dir."""
    monkeypatch.setattr(store_lib, "ROOT", tmp_path)
    monkeypatch.setattr(store_lib, "_ENV_CACHE", {})  # real .env never consulted
    monkeypatch.delenv("BRAIN_TOKEN", raising=False)
    return tmp_path


class TestSignSecret:
    def test_fresh_install_gets_random_persisted_key(self, fresh_install):
        k = store_lib.sign_secret()
        # never the pre-2026-07-05 footgun constant, never empty
        assert k and k != "no-token"
        # a real random key: 32 bytes hex = 64 chars
        assert len(k) == 64
        int(k, 16)  # raises if not hex
        # persisted so links survive a restart
        assert (fresh_install / "store" / ".sign_secret").read_text().strip() == k

    def test_key_is_stable_across_calls(self, fresh_install):
        assert store_lib.sign_secret() == store_lib.sign_secret()

    def test_two_fresh_installs_get_different_keys(self, fresh_install, tmp_path_factory, monkeypatch):
        # If any hardcoded fallback creeps back in, two independent installs
        # would share a key and every signed link becomes forgeable from source.
        k1 = store_lib.sign_secret()
        monkeypatch.setattr(store_lib, "ROOT", tmp_path_factory.mktemp("install2"))
        k2 = store_lib.sign_secret()
        assert k1 != k2

    def test_configured_brain_token_wins(self, fresh_install, monkeypatch):
        # a configured install keeps signature stability across processes
        monkeypatch.setenv("BRAIN_TOKEN", "tok-abc-123")
        assert store_lib.sign_secret() == "tok-abc-123"
        # and no random key file is minted when the token exists
        assert not (fresh_install / "store" / ".sign_secret").exists()

    def test_existing_key_file_is_reused_not_regenerated(self, fresh_install):
        key_file = fresh_install / "store" / ".sign_secret"
        key_file.parent.mkdir(parents=True)
        key_file.write_text("deadbeef" * 8)
        assert store_lib.sign_secret() == "deadbeef" * 8

    def test_empty_key_file_regenerates_never_returns_empty(self, fresh_install):
        key_file = fresh_install / "store" / ".sign_secret"
        key_file.parent.mkdir(parents=True)
        key_file.write_text("")  # truncated/corrupt key file
        k = store_lib.sign_secret()
        assert k and len(k) == 64  # an empty HMAC key would sign nothing safely

    def test_key_file_is_owner_only(self, fresh_install):
        store_lib.sign_secret()
        mode = (fresh_install / "store" / ".sign_secret").stat().st_mode & 0o777
        assert mode == 0o600


class TestCompactJsonl:
    def _write(self, tmp_path, *recs) -> Path:
        p = tmp_path / "data.jsonl"
        p.write_text("\n".join(r if isinstance(r, str) else json.dumps(r) for r in recs) + "\n")
        return p

    def test_last_write_wins_by_id_preserves_first_seen_order(self, tmp_path):
        p = self._write(tmp_path,
                        {"id": "a", "v": 1}, {"id": "b", "v": 1}, {"id": "a", "v": 2})
        assert store_lib.compact_jsonl(p) == 2
        rows = [json.loads(x) for x in p.read_text().splitlines()]
        assert rows == [{"id": "a", "v": 2}, {"id": "b", "v": 1}]  # a first (first seen), latest value

    def test_survives_corrupt_and_blank_lines(self, tmp_path):
        p = self._write(tmp_path,
                        {"id": "a", "v": 1}, "{corrupt not json", "", {"id": "b", "v": 1})
        assert store_lib.compact_jsonl(p) == 2
        rows = [json.loads(x) for x in p.read_text().splitlines()]
        assert [r["id"] for r in rows] == ["a", "b"]  # corrupt line gone, data intact

    def test_records_without_id_are_dropped(self, tmp_path):
        p = self._write(tmp_path, {"id": "a"}, {"note": "no id"})
        assert store_lib.compact_jsonl(p) == 1

    def test_custom_id_field(self, tmp_path):
        p = self._write(tmp_path, {"convo": "c1", "v": 1}, {"convo": "c1", "v": 2})
        assert store_lib.compact_jsonl(p, id_field="convo") == 1
        assert json.loads(p.read_text())["v"] == 2

    def test_missing_file_returns_zero(self, tmp_path):
        assert store_lib.compact_jsonl(tmp_path / "nope.jsonl") == 0
        assert not (tmp_path / "nope.jsonl").exists()

    def test_no_tmp_file_left_behind(self, tmp_path):
        p = self._write(tmp_path, {"id": "a"})
        store_lib.compact_jsonl(p)
        assert not p.with_suffix(".jsonl.tmp").exists()

    def test_flock_is_actually_taken(self, tmp_path):
        """Hold the sibling .lock exclusively; compact_jsonl must BLOCK until
        it is released (flock conflicts across file descriptions even within
        one process). If compaction ever stops taking the lock, this catches
        the janitor race regressing."""
        p = self._write(tmp_path, {"id": "a", "v": 1}, {"id": "a", "v": 2})
        holder = open(tmp_path / "data.lock", "w")
        fcntl.flock(holder, fcntl.LOCK_EX)
        result = {}
        t = threading.Thread(target=lambda: result.setdefault("n", store_lib.compact_jsonl(p)),
                             daemon=True)
        t.start()
        t.join(0.4)
        assert t.is_alive(), "compact_jsonl did not wait for the .lock -- flock not taken"
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()
        t.join(5)
        assert not t.is_alive() and result["n"] == 1
        assert json.loads(p.read_text())["v"] == 2  # and it still compacted correctly


class TestLocalTzPerInstantDst:
    """2026-07-14 fix: the `_LocalTZ` singleton briefly (commit 17bf56c) resolved
    'now's offset inside utcoffset()/dst()/tzname() regardless of the datetime it
    was asked about, so a January datetime got the CURRENT (e.g. July, CEST +2)
    offset instead of its own correct (CET +1) one -- VERIFIED wrong whenever
    "now" and the datetime being asked about are in different DST states. A real
    zoneinfo.ZoneInfo resolves the correct offset per-instant, so this must never
    regress: a Jan and a Jul datetime must get DIFFERENT offsets, matching the
    real DST calendar for the machine's own local zone."""

    def test_january_and_july_get_different_offsets(self):
        import datetime as _dt
        jan = _dt.datetime(2026, 1, 15, 10, 0, 0, tzinfo=store_lib.LOCAL_TZ)
        jul = _dt.datetime(2026, 7, 15, 10, 0, 0, tzinfo=store_lib.LOCAL_TZ)
        jan_off, jul_off = jan.utcoffset(), jul.utcoffset()
        assert jan_off is not None and jul_off is not None
        if jul_off == jan_off:
            pytest.skip("machine's local zone has no DST (offset is genuinely constant "
                        "year-round) -- the per-instant fix still needs a zone that "
                        "observes DST to exercise the regression")
        assert jan_off != jul_off

    def test_offset_tracks_the_datetime_not_the_call_time(self):
        # the actual regression shape: ask for a PAST winter offset while "now"
        # (whenever the test happens to run) is in a different DST state.
        import datetime as _dt
        winter = _dt.datetime(2024, 1, 15, tzinfo=store_lib.LOCAL_TZ)
        summer = _dt.datetime(2024, 7, 15, tzinfo=store_lib.LOCAL_TZ)
        if winter.utcoffset() == summer.utcoffset():
            pytest.skip("machine's local zone has no DST")
        # fromtimestamp must also resolve per-instant, not per-call-time
        winter_ts = _dt.datetime.fromtimestamp(1700000000, store_lib.LOCAL_TZ)  # 2023-11-14
        summer_ts = _dt.datetime.fromtimestamp(1721000000, store_lib.LOCAL_TZ)  # 2024-07-15
        assert winter_ts.utcoffset() == winter.utcoffset()
        assert summer_ts.utcoffset() == summer.utcoffset()

    def test_utcoffset_none_still_returns_a_usable_offset(self):
        # backward compat: triage.py / capture/pull_reminders.py call
        # LOCAL_TZ.utcoffset(None) directly to get "some usable current offset"
        off = store_lib.LOCAL_TZ.utcoffset(None)
        assert off is not None and hasattr(off, "total_seconds")

    def test_now_and_astimezone_and_fromtimestamp_all_still_work(self):
        # every documented call-site pattern must keep working after the fix
        import datetime as _dt
        assert _dt.datetime.now(store_lib.LOCAL_TZ).tzinfo is store_lib.LOCAL_TZ
        naive = _dt.datetime(2026, 1, 1, 12, 0, 0)
        aware = naive.astimezone(store_lib.LOCAL_TZ)
        assert aware.tzinfo is store_lib.LOCAL_TZ
        assert _dt.datetime.fromtimestamp(0, store_lib.LOCAL_TZ).tzinfo is store_lib.LOCAL_TZ
