#!/usr/bin/env python3
"""Unit tests for the five send-finger pusher agents (Section 1 HIGH):
one_thing, proposal_open_pulse, send_finger_nag, call_escalator, honesty_agent.

Pure-logic tests against tmp stores; planner.notify/feed_add are monkeypatched
(captured, no network, nothing reaches the phone). Store paths are pointed at
tmp_path so nothing touches the live stores.

Run: .venv/bin/python -m pytest tests/test_pushers.py -q
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import planner  # noqa: E402
import proposal_factory  # noqa: E402
import reply_watch  # noqa: E402
import one_thing  # noqa: E402
import proposal_open_pulse  # noqa: E402
import send_finger_nag  # noqa: E402
import call_escalator  # noqa: E402
import honesty_agent  # noqa: E402
from store_lib import LOCAL_TZ, load_todos  # noqa: E402


def _iso(hours_ago: float = 0.0) -> str:
    return (datetime.now(LOCAL_TZ) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


@pytest.fixture
def pushes(monkeypatch):
    """Capture planner.notify calls; silence feed_add. No network, no real feed."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(planner, "notify",
                        lambda title, body, tags="brain", actions=None: calls.append((title, body)) or True)
    monkeypatch.setattr(planner, "feed_add", lambda *a, **k: None)
    return calls


@pytest.fixture
def feed(monkeypatch):
    """Capture planner.feed_add calls (layered on top of `pushes` when both used)."""
    lines: list[tuple] = []
    monkeypatch.setattr(planner, "feed_add", lambda *a, **k: lines.append(a))
    return lines


# ---------------------------------------------------------------- one_thing

class TestOneThing:
    def test_money_bias_beats_busywork_at_near_tie(self):
        ranked = [
            {"kind": "jobs_manual", "id": "j", "label": "29 job application(s) need manual finish", "score": 100},
            {"kind": "proposal", "id": "p", "label": "Proposal staged, not sent: Acme", "score": 85},
        ]
        # 85 * 1.25 = 106.25 > 100: the money item wins the near-tie
        assert one_thing.pick(ranked)["kind"] == "proposal"

    def test_money_bias_does_not_overturn_a_big_gap(self):
        ranked = [
            {"kind": "jobs_manual", "id": "j", "label": "jobs", "score": 200},
            {"kind": "proposal", "id": "p", "label": "Proposal staged, not sent: Acme", "score": 85},
        ]
        assert one_thing.pick(ranked)["kind"] == "jobs_manual"

    def test_pick_empty_and_garbage_scores(self):
        assert one_thing.pick([]) is None
        assert one_thing.pick([{"kind": "reply", "score": "not-a-number"}]) is None

    def test_action_line_is_imperative(self):
        item = {"kind": "proposal", "label": "Proposal staged, not sent: Acme",
                "score": 42.5, "why": "staged, $3500 tier"}
        line = one_thing.action_line(item)
        assert line == "send the Acme proposal ($3500 tier)"

    def test_run_pushes_once_per_day(self, tmp_path, pushes, monkeypatch):
        att = tmp_path / "attention.json"
        att.write_text(json.dumps({"ranked": [
            {"kind": "proposal", "id": "p", "label": "Proposal staged, not sent: Acme",
             "score": 42.5, "why": "staged, $3500 tier"}]}))
        monkeypatch.setattr(one_thing, "ATTENTION", att)
        monkeypatch.setattr(one_thing, "STORE_DIR", tmp_path)
        assert one_thing.run() == 0
        assert len(pushes) == 1
        assert "If you do one thing today" in pushes[0][1]
        assert "Acme" in pushes[0][1]
        # same day rerun: sentinel blocks the second push
        assert one_thing.run() == 0
        assert len(pushes) == 1
        # --force overrides
        assert one_thing.run(force=True) == 0
        assert len(pushes) == 2

    def test_dry_run_no_push_no_sentinel(self, tmp_path, pushes, monkeypatch):
        att = tmp_path / "attention.json"
        att.write_text(json.dumps({"ranked": [
            {"kind": "reply", "id": "r", "label": "Reply pending: Cara (interested)", "score": 60}]}))
        monkeypatch.setattr(one_thing, "ATTENTION", att)
        monkeypatch.setattr(one_thing, "STORE_DIR", tmp_path)
        assert one_thing.run(dry_run=True) == 0
        assert pushes == []
        assert not list(tmp_path.glob(".one_thing_sent-*"))

    def test_missing_store_exit0(self, tmp_path, pushes, monkeypatch):
        monkeypatch.setattr(one_thing, "ATTENTION", tmp_path / "nope.json")
        monkeypatch.setattr(one_thing, "STORE_DIR", tmp_path)
        assert one_thing.run() == 0
        assert pushes == []


# ------------------------------------------------------- proposal_open_pulse

def _prop(pid, status, opens, company="Acme", price=3500):
    return {"id": pid, "status": status, "opens": opens, "company": company,
            "price": price, "created": _iso(50)}


class TestOpenPulse:
    def test_first_open_detected(self):
        events, state = proposal_open_pulse.detect([_prop("p1", "sent", 1)], {})
        assert len(events) == 1
        assert events[0]["kind"] == "first_open"
        assert state == {"p1": 1}

    def test_staged_never_pushes_but_is_baselined(self):
        events, state = proposal_open_pulse.detect([_prop("p1", "staged", 3)], {})
        assert events == []
        assert state == {"p1": 3}
        # the preview opens while staged cannot fire a false first-open after send
        events2, _ = proposal_open_pulse.detect([_prop("p1", "sent", 3)], state)
        assert events2 == []

    def test_reread_needs_delta_of_two(self):
        events, _ = proposal_open_pulse.detect([_prop("p1", "sent", 3)], {"p1": 1})
        assert len(events) == 1 and events[0]["kind"] == "reread"
        events, _ = proposal_open_pulse.detect([_prop("p1", "sent", 2)], {"p1": 1})
        assert events == []

    def test_first_opens_sort_ahead_of_rereads(self):
        props = [_prop("p1", "sent", 9, company="Reread Co"),
                 _prop("p2", "sent", 1, company="Fresh Co", price=800)]
        events, _ = proposal_open_pulse.detect(props, {"p1": 1})
        assert [e["kind"] for e in events] == ["first_open", "reread"]

    def test_run_caps_at_three_pushes(self, tmp_path, pushes, monkeypatch):
        # fix 9: a missing state file baselines silently (0 pushes) so stale reads never
        # fire false "reading RIGHT NOW". So the FIRST run here just baselines the five
        # sent-but-unopened proposals; only opens that happen AFTER that baseline push,
        # and the per-run cap of MAX_PUSHES still holds.
        q = tmp_path / "proposals.jsonl"
        monkeypatch.setattr(proposal_factory, "QUEUE", q)
        monkeypatch.setattr(proposal_open_pulse, "STATE", tmp_path / "open_pulse_state.json")
        _write_jsonl(q, [_prop(f"p{i}", "sent", 0, company=f"Co{i}") for i in range(5)])
        assert proposal_open_pulse.run() == 0
        assert pushes == []  # first-ever run baselines silently
        # now all five get opened (0 -> 1): five first-opens, capped to three pushes
        _write_jsonl(q, [_prop(f"p{i}", "sent", 1, company=f"Co{i}") for i in range(5)])
        assert proposal_open_pulse.run() == 0
        assert len(pushes) == 3
        assert "reading your proposal right now" in pushes[0][0]
        assert "Call now, do not email." in pushes[0][1]
        # state recorded ALL five, so a third run is silent (no double-fire)
        assert proposal_open_pulse.run() == 0
        assert len(pushes) == 3

    def test_dry_run_writes_nothing(self, tmp_path, pushes, monkeypatch):
        q = tmp_path / "proposals.jsonl"
        _write_jsonl(q, [_prop("p1", "sent", 1)])
        monkeypatch.setattr(proposal_factory, "QUEUE", q)
        state = tmp_path / "open_pulse_state.json"
        monkeypatch.setattr(proposal_open_pulse, "STATE", state)
        assert proposal_open_pulse.run(dry_run=True) == 0
        assert pushes == []
        assert not state.exists()

    def test_missing_store_exit0(self, tmp_path, pushes, monkeypatch):
        monkeypatch.setattr(proposal_factory, "QUEUE", tmp_path / "nope.jsonl")
        monkeypatch.setattr(proposal_open_pulse, "STATE", tmp_path / "state.json")
        assert proposal_open_pulse.run() == 0
        assert pushes == []


# --------------------------------------------------------- send_finger_nag

@pytest.fixture
def nag_stores(tmp_path, monkeypatch):
    q = tmp_path / "proposals.jsonl"
    _write_jsonl(q, [
        {"id": "p1", "status": "staged", "company": "Acme", "price": 1000, "created": _iso(30)},
        {"id": "p2", "status": "staged", "company": "Bravo", "price": 2000, "created": _iso(60)},
        {"id": "p3", "status": "staged", "company": "Fresh", "price": 500, "created": _iso(2)},
        {"id": "p4", "status": "sent", "company": "Gone", "price": 9000, "created": _iso(90)},
    ])
    r = tmp_path / "replies.jsonl"
    _write_jsonl(r, [
        {"id": "r1", "status": "pending", "name": "Cara", "created": _iso(55)},
        {"id": "r2", "status": "pending", "name": "Newbie", "created": _iso(1)},
        {"id": "r3", "status": "sent", "name": "Done", "created": _iso(80)},
    ])
    monkeypatch.setattr(proposal_factory, "QUEUE", q)
    monkeypatch.setattr(reply_watch, "REPLIES", r)
    monkeypatch.setattr(send_finger_nag, "NAG_STATE", tmp_path / ".nag_state.json")
    monkeypatch.setattr(send_finger_nag, "TODOS", tmp_path / "todos.jsonl")
    return tmp_path


class TestSendFingerNag:
    def test_collect_levels_and_total(self, nag_stores):
        items, total = send_finger_nag.collect()
        by_id = {i["id"]: i for i in items}
        assert by_id["p1"]["level"] == 1          # 30h
        assert by_id["p2"]["level"] == 2          # 60h
        assert by_id["r1"]["level"] == 2          # 55h pending reply
        assert "p3" not in by_id and "p4" not in by_id and "r2" not in by_id
        assert total == 3500.0                    # ALL staged $: 1000 + 2000 + 500
        assert items[0]["id"] == "p2"             # oldest first

    def test_run_pushes_both_levels_and_stages_todos(self, nag_stores, pushes):
        assert send_finger_nag.run() == 0
        assert len(pushes) == 2                   # one L2 push + one L1 push
        l2_title, l2_body = pushes[0]
        assert "Bravo" in l2_body and "$3,500" in l2_body
        assert "Acme" in pushes[1][1]             # L1 names its oldest item
        todos = load_todos(nag_stores / "todos.jsonl")
        texts = {t["text"] for t in todos}
        assert "Send the Bravo proposal, staged 2 days" in texts
        assert "Answer Cara's reply, pending 2 days" in texts
        assert len(todos) == 2                    # only the L2 items become todos
        # shape matches what store_lib.load_todos consumers expect
        for t in todos:
            assert t["status"] == "inbox" and t["source"] == "send_finger_nag"
            assert t["id"] and t["created"] and t["source_ref"]

    def test_once_per_level_per_day(self, nag_stores, pushes):
        send_finger_nag.run()
        n = len(pushes)
        send_finger_nag.run()                     # same day: state blocks a repeat
        assert len(pushes) == n
        assert len(load_todos(nag_stores / "todos.jsonl")) == 2  # no duplicate todos

    def test_todo_deduped_even_across_days(self, nag_stores, pushes):
        send_finger_nag.run()
        # wipe the daily state (simulates tomorrow) but keep the todos file
        (nag_stores / ".nag_state.json").unlink()
        send_finger_nag.run()
        assert len(load_todos(nag_stores / "todos.jsonl")) == 2  # source_ref dedupe holds

    def test_dry_run_writes_nothing(self, nag_stores, pushes):
        assert send_finger_nag.run(dry_run=True) == 0
        assert pushes == []
        assert not (nag_stores / ".nag_state.json").exists()
        assert not (nag_stores / "todos.jsonl").exists()

    def test_missing_stores_exit0(self, tmp_path, pushes, monkeypatch):
        monkeypatch.setattr(proposal_factory, "QUEUE", tmp_path / "no_p.jsonl")
        monkeypatch.setattr(reply_watch, "REPLIES", tmp_path / "no_r.jsonl")
        monkeypatch.setattr(send_finger_nag, "NAG_STATE", tmp_path / ".nag_state.json")
        monkeypatch.setattr(send_finger_nag, "TODOS", tmp_path / "todos.jsonl")
        assert send_finger_nag.run() == 0
        assert pushes == []


# ---------------------------------------------------------- call_escalator

def _freeze(monkeypatch, hour, minute=30):
    fixed = datetime.now(LOCAL_TZ).replace(hour=hour, minute=minute, second=0)
    monkeypatch.setattr(call_escalator, "_now", lambda: fixed)
    return fixed


@pytest.fixture
def warm_stores(tmp_path, monkeypatch):
    today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    picks = [{"id": f"w_{i}", "name": f"Lead {i}", "niche": "medspa"} for i in range(10)]
    block = tmp_path / "warm_block.json"
    block.write_text(json.dumps({"date": today, "ids": [p["id"] for p in picks], "picks": picks}))
    monkeypatch.setattr(call_escalator, "WARM_BLOCK", block)
    monkeypatch.setattr(call_escalator, "DISPO", tmp_path / "warm_dispo.jsonl")
    monkeypatch.setattr(call_escalator, "STATE", tmp_path / ".call_escalator_state.json")
    monkeypatch.setattr(call_escalator, "HITLIST", tmp_path / "no_hitlist.csv")
    return tmp_path


class TestCallEscalator:
    def test_before_3pm_stands_down(self, warm_stores, pushes, monkeypatch):
        _freeze(monkeypatch, 14)
        assert call_escalator.run() == 0
        assert pushes == []

    def test_fires_after_3pm_with_zero_dispos(self, warm_stores, pushes, monkeypatch):
        _freeze(monkeypatch, 15)
        assert call_escalator.run() == 0
        assert len(pushes) == 1
        assert "0 of 10" in pushes[0][1]
        assert "Lead 0" in pushes[0][1]            # names the first uncalled lead
        # idempotent: same stage, same day, no second push
        assert call_escalator.run() == 0
        assert len(pushes) == 1

    def test_any_dispo_today_silences(self, warm_stores, pushes, monkeypatch):
        fixed = _freeze(monkeypatch, 16)
        # dispo ts must derive from the FROZEN clock, not real wall-time: _iso(1) = real now
        # minus 1h, which lands on YESTERDAY when the suite runs between 00:00-00:59, so the
        # dispo stopped matching the frozen "today" and this test failed nightly (found 00:26,
        # 2026-07-13). Anchoring to `fixed` makes it deterministic at any hour.
        _write_jsonl(warm_stores / "warm_dispo.jsonl",
                     [{"id": "w_0", "dispo": "noans", "note": "",
                       "ts": fixed.isoformat(timespec="seconds")}])
        assert call_escalator.run() == 0
        assert pushes == []

    def test_eod_pushes_and_writes_accountability_note(self, warm_stores, pushes, feed, monkeypatch):
        _freeze(monkeypatch, 20)
        assert call_escalator.run() == 0
        assert len(pushes) == 1
        assert "0 of 10 warm calls today" in pushes[0][1]
        assert any("Self-accountability" in str(f) for f in feed)
        # eod stage fires once per day too
        assert call_escalator.run() == 0
        assert len(pushes) == 1

    def test_stale_block_means_nothing_to_escalate(self, warm_stores, pushes, monkeypatch):
        _freeze(monkeypatch, 16)
        block = json.loads((warm_stores / "warm_block.json").read_text())
        block["date"] = "2020-01-01"
        (warm_stores / "warm_block.json").write_text(json.dumps(block))
        assert call_escalator.run() == 0
        assert pushes == []

    def test_dry_run_writes_nothing(self, warm_stores, pushes, monkeypatch):
        _freeze(monkeypatch, 15)
        assert call_escalator.run(dry_run=True) == 0
        assert pushes == []
        assert not (warm_stores / ".call_escalator_state.json").exists()


# ----------------------------------------------------------- honesty_agent

@pytest.fixture
def honesty_stores(tmp_path, monkeypatch):
    _write_jsonl(tmp_path / "ledger.jsonl", [
        {"ts": _iso(72), "kind": "won", "amount": 1200.0, "note": "Nimbus"},
        {"ts": _iso(24 * 20), "kind": "won", "amount": 500.0, "note": "old, outside week"},
        {"ts": _iso(10), "kind": "test", "amount": 999.0, "note": "not a won"},
    ])
    _write_jsonl(tmp_path / "warm_dispo.jsonl",
                 [{"id": f"w_{i}", "dispo": "noans", "ts": _iso(20)} for i in range(4)]
                 + [{"id": "w_old", "dispo": "booked", "ts": _iso(24 * 30)}])
    q = tmp_path / "proposals.jsonl"
    _write_jsonl(q, [
        {"id": "p1", "status": "staged", "company": "A", "price": 3500, "created": _iso(80)},
        {"id": "p2", "status": "staged", "company": "B", "price": 3500, "created": _iso(80)},
        {"id": "p3", "status": "skipped", "company": "C", "price": 9999, "created": _iso(80)},
    ])
    monkeypatch.setattr(honesty_agent, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(honesty_agent, "DISPO", tmp_path / "warm_dispo.jsonl")
    monkeypatch.setattr(honesty_agent, "OUT", tmp_path / "honesty_report.json")
    monkeypatch.setattr(proposal_factory, "QUEUE", q)
    return tmp_path


class TestHonestyAgent:
    def test_math(self, honesty_stores):
        s = honesty_agent.compute(commits=41)
        assert s["commits"] == 41
        assert s["closed"] == 1200.0              # only this week's wons
        assert s["staged_total"] == 7000.0        # staged only, skipped excluded
        assert s["staged_count"] == 2
        assert s["warm_calls"] == 4               # this week's dispos only

    def test_compose_is_blunt_and_dash_free(self):
        text = honesty_agent.compose({"commits": 41, "closed": 0.0, "staged_total": 46800.0,
                                      "staged_count": 15, "warm_calls": 0})
        assert "You built 41 commits and closed $0 this week." in text
        assert "$46,800 is staged and unsent" in text
        assert "The phone is the bottleneck, not the machine." in text
        assert "—" not in text and "–" not in text  # no em/en dashes, ever

    def test_compose_acknowledges_a_working_loop(self):
        text = honesty_agent.compose({"commits": 10, "closed": 1200.0, "staged_total": 0.0,
                                      "staged_count": 0, "warm_calls": 12})
        assert "$1,200" in text
        assert "—" not in text

    def test_weekday_gate_blocks_off_days(self, honesty_stores, pushes, monkeypatch):
        today_wd = datetime.now(LOCAL_TZ).weekday()
        monkeypatch.setattr(honesty_agent, "REPORT_WEEKDAY", (today_wd + 1) % 7)
        assert honesty_agent.run() == 0
        assert pushes == []
        assert not (honesty_stores / "honesty_report.json").exists()

    def test_fires_on_report_day_and_is_daily_idempotent(self, honesty_stores, pushes, monkeypatch):
        monkeypatch.setattr(honesty_agent, "REPORT_WEEKDAY", datetime.now(LOCAL_TZ).weekday())
        monkeypatch.setattr(honesty_agent, "_commits_this_week", lambda: 41)
        assert honesty_agent.run() == 0
        assert len(pushes) == 1
        rep = json.loads((honesty_stores / "honesty_report.json").read_text())
        assert rep["closed"] == 1200.0 and rep["staged_total"] == 7000.0
        assert rep["text"].startswith("You built 41 commits")
        # rerun the same day: the report's date blocks a duplicate push
        assert honesty_agent.run() == 0
        assert len(pushes) == 1

    def test_force_overrides_gate(self, honesty_stores, pushes, monkeypatch):
        today_wd = datetime.now(LOCAL_TZ).weekday()
        monkeypatch.setattr(honesty_agent, "REPORT_WEEKDAY", (today_wd + 1) % 7)
        monkeypatch.setattr(honesty_agent, "_commits_this_week", lambda: 5)
        assert honesty_agent.run(force=True) == 0
        assert len(pushes) == 1

    def test_missing_stores_exit0(self, tmp_path, pushes, monkeypatch):
        monkeypatch.setattr(honesty_agent, "LEDGER", tmp_path / "no.jsonl")
        monkeypatch.setattr(honesty_agent, "DISPO", tmp_path / "no2.jsonl")
        monkeypatch.setattr(honesty_agent, "OUT", tmp_path / "out.json")
        monkeypatch.setattr(proposal_factory, "QUEUE", tmp_path / "no3.jsonl")
        monkeypatch.setattr(honesty_agent, "_commits_this_week", lambda: 0)
        assert honesty_agent.run(force=True) == 0
        assert len(pushes) == 1                   # still reports honestly: all zeros
