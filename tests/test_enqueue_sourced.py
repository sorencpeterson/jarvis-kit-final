"""agents/enqueue_sourced.py — the sourcing-run enqueue step (commenter-first, 2026-07-15)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import enqueue_sourced  # noqa: E402
import li_budget  # noqa: E402
import networking  # noqa: E402
import planner  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(networking, "QUEUE", tmp_path / "network.jsonl")
    monkeypatch.setattr(planner, "_config", lambda: {})
    monkeypatch.setattr(li_budget, "weekend_paused", lambda now=None: False)
    monkeypatch.setattr(li_budget, "in_hours_window", lambda now=None: True)
    monkeypatch.setattr(li_budget, "budget_remaining_today", lambda: 10 ** 6)
    monkeypatch.setattr(planner, "_cli_json", lambda *a, **k: [])
    return tmp_path


def _commenter(**kw):
    base = {"commenter_name": "Ana Ruiz", "commenter_headline": "Founder at Ruiz Digital Agency",
            "commenter_url": "https://linkedin.com/in/ana-ruiz", "comment_text": "we hit this wall monthly",
            "post_author": "Big Creator", "post_url": "https://linkedin.com/posts/x",
            "post_context": "Agencies die by fulfillment"}
    base.update(kw)
    return base


class TestMergeConnectBatch:
    def test_commenter_reshaped_and_tagged(self):
        out = enqueue_sourced.merge_connect_batch({"commenters": [_commenter()]})
        assert len(out) == 1
        c = out[0]
        assert c["name"] == "Ana Ruiz" and c["is_commenter"] is True
        assert "commented on post by Big Creator" in c["headline"]
        assert "we hit this wall monthly" in c["post_context"]

    def test_commenter_wins_url_dedupe_over_plain_connection(self):
        out = enqueue_sourced.merge_connect_batch({
            "commenters": [_commenter()],
            "connections": [{"name": "Ana Ruiz", "headline": "plain search hit",
                             "url": "https://linkedin.com/in/ana-ruiz"}]})
        assert len(out) == 1 and out[0].get("is_commenter") is True

    def test_commenters_listed_before_connections(self):
        out = enqueue_sourced.merge_connect_batch({
            "commenters": [_commenter()],
            "connections": [{"name": "Cold Hit", "headline": "Agency Owner",
                             "url": "https://linkedin.com/in/cold"}]})
        assert [c["name"] for c in out] == ["Ana Ruiz", "Cold Hit"]

    def test_empty_and_missing_sections(self):
        assert enqueue_sourced.merge_connect_batch({}) == []
        assert enqueue_sourced.merge_connect_batch({"commenters": [], "connections": []}) == []


class TestRun:
    def test_end_to_end_queues_connect_items(self, tmp_path):
        src = tmp_path / "sourced.json"
        src.write_text(json.dumps({"commenters": [_commenter()],
                                   "connections": [{"name": "Cold Hit", "headline": "Agency Owner",
                                                    "url": "https://linkedin.com/in/cold"}],
                                   "comments": [], "replies": [], "likes": []}))
        counts = enqueue_sourced.run(str(src))
        assert counts["connections"] == 2
        q = networking.load_queue()
        assert {x["kind"] for x in q} == {"connect"}
        ana = next(x for x in q if x["author"] == "Ana Ruiz")
        assert "commented on post by Big Creator" in x_target(ana)

    def test_unreadable_file_is_graceful(self, tmp_path):
        assert enqueue_sourced.run(str(tmp_path / "missing.json")) == {}


def x_target(item):
    return item.get("target", "")
