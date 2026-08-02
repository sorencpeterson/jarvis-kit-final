#!/usr/bin/env python3
"""Lock agents/archiver.py's behavior before wiring it into the daily chain (2026-07-13):
it rewrites the hot feed/insights/events files, so it must never lose a record. Verifies
old records move to a quarter bucket, recent + no-ts + unparseable records stay in the live
file, and the live file remains valid jsonl.

Run: .venv/bin/python -m pytest tests/test_archiver.py -v
"""
from __future__ import annotations

import fcntl
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import archiver  # noqa: E402


def _wire(tmp_path, monkeypatch):
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setattr(archiver, "STORE", store)
    monkeypatch.setattr(archiver, "ARCHIVE", store / "archive")
    return store


def test_archive_file_moves_old_keeps_recent(tmp_path, monkeypatch):
    store = _wire(tmp_path, monkeypatch)
    now = datetime.now().astimezone()
    old = (now - timedelta(days=200)).isoformat(timespec="seconds")
    recent = (now - timedelta(days=5)).isoformat(timespec="seconds")
    lines = [
        json.dumps({"ts": old, "kind": "a", "n": 1}),
        json.dumps({"ts": recent, "kind": "b", "n": 2}),
        json.dumps({"kind": "c", "n": 3}),          # no ts -> keep (can't age it)
        "{not valid json",                          # unparseable -> keep (don't risk losing)
    ]
    (store / "feed.jsonl").write_text("\n".join(lines) + "\n")

    cutoff = now - timedelta(days=archiver.CUTOFF_DAYS)
    res = archiver.archive_file("feed.jsonl", cutoff)

    assert res["moved"] == 1
    assert res["kept"] == 3
    assert res["unparseable_kept"] == 1

    # live file: 3 lines, still parseable-where-it-should-be, old record gone
    live = [l for l in (store / "feed.jsonl").read_text().splitlines() if l.strip()]
    assert len(live) == 3
    kinds = {json.loads(l)["kind"] for l in live if l.startswith("{") and l.endswith("}")}
    assert "a" not in kinds and "b" in kinds and "c" in kinds
    assert "{not valid json" in live                # unparseable preserved verbatim

    # archive bucket exists and holds exactly the old record
    buckets = list((store / "archive").glob("feed-*.jsonl"))
    assert len(buckets) == 1
    arch = [json.loads(l) for l in buckets[0].read_text().splitlines() if l.strip()]
    assert len(arch) == 1 and arch[0]["kind"] == "a"


def test_missing_file_is_noop(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    res = archiver.archive_file("feed.jsonl", datetime.now().astimezone())
    assert res["status"] == "missing" and res["moved"] == 0


def test_nothing_old_leaves_file_untouched(tmp_path, monkeypatch):
    store = _wire(tmp_path, monkeypatch)
    now = datetime.now().astimezone()
    recent = json.dumps({"ts": (now - timedelta(days=1)).isoformat(), "kind": "x"})
    (store / "feed.jsonl").write_text(recent + "\n")
    res = archiver.archive_file("feed.jsonl", now - timedelta(days=archiver.CUTOFF_DAYS))
    assert res["moved"] == 0
    assert (store / "feed.jsonl").read_text() == recent + "\n"   # byte-identical, no rewrite churn
    assert not (store / "archive").exists()


def test_archive_file_takes_the_lock(tmp_path, monkeypatch):
    """R2-48 regression: archive_file must hold store_lib._flock across the whole
    read -> decide -> replace, not just the final os.replace, or a line appended
    by another writer in that window is silently erased when the tmp file (built
    from a stale read) overwrites it. Holds the sibling .lock externally first;
    archive_file must BLOCK until it's released (same pattern as
    test_store_lib_core.py's test_flock_is_actually_taken)."""
    store = _wire(tmp_path, monkeypatch)
    now = datetime.now().astimezone()
    old = (now - timedelta(days=200)).isoformat(timespec="seconds")
    (store / "feed.jsonl").write_text(json.dumps({"ts": old, "kind": "a", "n": 1}) + "\n")

    holder = open(store / "feed.lock", "w")
    fcntl.flock(holder, fcntl.LOCK_EX)
    result = {}
    cutoff = now - timedelta(days=archiver.CUTOFF_DAYS)
    t = threading.Thread(
        target=lambda: result.setdefault("res", archiver.archive_file("feed.jsonl", cutoff)),
        daemon=True)
    t.start()
    t.join(0.4)
    assert t.is_alive(), "archive_file did not wait for the .lock -- flock not taken"
    fcntl.flock(holder, fcntl.LOCK_UN)
    holder.close()
    t.join(5)
    assert not t.is_alive()
    assert result["res"]["moved"] == 1
