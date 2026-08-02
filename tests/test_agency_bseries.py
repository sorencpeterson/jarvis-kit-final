#!/usr/bin/env python3
"""Unit tests for the agency B-series agents (Section 5 + Section 1 leftovers):
prospect_trigger_watch, transcript_miner, portfolio_teardown, care_upsell,
deposit_nudge, reactivation_triage, dropoff_audit, tier_winrate.

Everything runs against tmp stores; planner._cli / planner.notify /
planner.feed_add and every network seam (net_guard.safe_urlopen, the agents'
own _fetch/_fetch_site, check_web) are monkeypatched. No LLM, no network,
no live-store writes.

Run: .venv/bin/python -m pytest tests/test_agency_bseries.py -q
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
import net_guard  # noqa: E402
import promises  # noqa: E402
import convo_context  # noqa: E402
import prospect_trigger_watch as ptw  # noqa: E402
import transcript_miner as tm  # noqa: E402
import portfolio_teardown as pt  # noqa: E402
import care_upsell as cu  # noqa: E402
import deposit_nudge as dn  # noqa: E402
import reactivation_triage as rt  # noqa: E402
import dropoff_audit as da  # noqa: E402
import tier_winrate as tw  # noqa: E402
from store_lib import LOCAL_TZ, load_todos  # noqa: E402


def _iso(hours_ago: float = 0.0) -> str:
    return (datetime.now(LOCAL_TZ) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


@pytest.fixture(autouse=True)
def guardrails(monkeypatch):
    """No test may reach the network or a real model: safe_urlopen raises,
    _cli returns None (tests that need canned output override it), pushes and
    feed lines are swallowed. Individual tests layer their own captures on top."""
    monkeypatch.setattr(net_guard, "safe_urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network hit")))
    monkeypatch.setattr(planner, "_cli", lambda *a, **k: None)
    monkeypatch.setattr(planner, "notify", lambda *a, **k: True)
    monkeypatch.setattr(planner, "feed_add", lambda *a, **k: None)


@pytest.fixture
def pushes(monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(planner, "notify",
                        lambda title, body, tags="brain", actions=None: calls.append((title, body)) or True)
    return calls


@pytest.fixture
def feed(monkeypatch):
    lines: list[tuple] = []
    monkeypatch.setattr(planner, "feed_add", lambda *a, **k: lines.append(a))
    return lines


# ------------------------------------------------- prospect_trigger_watch

@pytest.fixture
def ptw_env(tmp_path, monkeypatch):
    monkeypatch.setattr(ptw, "PROPOSALS", tmp_path / "proposals.jsonl")
    monkeypatch.setattr(ptw, "CONVO_STATES", tmp_path / "convo_states.json")
    monkeypatch.setattr(ptw, "SITE_HASHES", tmp_path / "site_hashes.json")
    monkeypatch.setattr(ptw, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(ptw, "HOOKS", tmp_path / "trigger_hooks.jsonl")
    # keep run() off the real queue loader (it would read the live store)
    monkeypatch.setattr(ptw, "_proposals_by_id", lambda: {
        r["id"]: r for r in _read(tmp_path / "proposals.jsonl")})
    monkeypatch.setattr(ptw, "check_web", lambda deals: [])
    return tmp_path


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


class TestTriggerWatch:
    def test_content_hash_ignores_scripts_comments_whitespace(self):
        a = "<html><script>var x=1;</script><!-- build 4 --><body>Hello   world</body></html>"
        b = "<html><script>var x=999;</script><!-- build 5 --><body>Hello world</body></html>"
        c = "<html><body>Hello brand new world</body></html>"
        assert ptw.content_hash(a) == ptw.content_hash(b)
        assert ptw.content_hash(a) != ptw.content_hash(c)

    def test_hash_change_detection_with_fake_fetcher(self, ptw_env, monkeypatch):
        # check_sites now returns (fired, pending_hashes); it no longer persists hashes
        # itself (fix 10: run() commits baselines only for companies whose hook staged).
        # To reproduce the across-run baseline the test drives it by hand.
        deals = [{"company": "Acme Spa", "site_url": "https://acmespa.com",
                  "pids": ["p1"], "opens": 0, "cid": "c1"}]
        monkeypatch.setattr(ptw, "_fetch", lambda url: (True, "<body>version one</body>"))
        hashes: dict = {}
        monkeypatch.setattr(ptw, "_read_json", lambda path, default: dict(hashes)
                            if path == ptw.SITE_HASHES else default)

        def _commit(pending):
            for url, info in pending.items():
                hashes[url] = info["entry"]

        # first sighting baselines, never fires
        fired, pending = ptw.check_sites(deals)
        assert fired == []
        _commit(pending)
        assert "https://acmespa.com" in hashes
        # unchanged content: still quiet
        fired, pending = ptw.check_sites(deals)
        assert fired == []
        _commit(pending)
        # changed content: fires
        monkeypatch.setattr(ptw, "_fetch", lambda url: (True, "<body>version TWO</body>"))
        fired, pending = ptw.check_sites(deals)
        assert len(fired) == 1 and fired[0]["trigger"] == "site_changed"
        assert fired[0]["company"] == "Acme Spa"

    def test_unreachable_site_never_fires(self, ptw_env, monkeypatch):
        deals = [{"company": "Dead Co", "site_url": "https://dead.example", "pids": [],
                  "opens": 0, "cid": ""}]
        monkeypatch.setattr(ptw, "_fetch", lambda url: (False, "timeout"))
        fired, pending = ptw.check_sites(deals)
        assert fired == [] and pending == {}

    def test_fetch_cap_respected(self, ptw_env, monkeypatch):
        calls = []
        monkeypatch.setattr(ptw, "_fetch", lambda url: calls.append(url) or (True, "x"))
        deals = [{"company": f"C{i}", "site_url": f"https://c{i}.com", "pids": [],
                  "opens": 0, "cid": ""} for i in range(ptw.FETCH_CAP + 5)]
        ptw.check_sites(deals)
        assert len(calls) == ptw.FETCH_CAP

    def test_local_signals_opens_and_convo_moves(self, ptw_env):
        # check_local_signals now returns (fired, pending_opens, pending_convo) and does
        # NOT mutate state in place (fix 10). The caller commits the pending baseline
        # only once a hook stages; here we commit by hand to reproduce "remembered".
        state = {"opens": {"Acme Spa": 1}, "convo": {"c1": "new"}}
        deals = [{"company": "Acme Spa", "site_url": "", "pids": ["p1"],
                  "opens": 3, "cid": "c1", "convo_state": "negotiating"}]
        fired, p_opens, p_convo = ptw.check_local_signals(deals, state)
        kinds = {f["trigger"] for f in fired}
        assert kinds == {"proposal_reopened", "convo_state_change"}
        # commit the pending baseline (simulating a staged hook), then the same signals stay quiet
        for k, info in p_opens.items():
            state["opens"][k] = info["val"]
        for k, info in p_convo.items():
            state["convo"][k] = info["val"]
        fired2, _, _ = ptw.check_local_signals(deals, state)
        assert fired2 == []

    def test_first_sighting_of_opens_only_baselines(self, ptw_env):
        # a first sighting fires nothing and yields a first=True pending baseline that is
        # always safe to commit (no trigger to lose).
        state: dict = {}
        deals = [{"company": "Acme", "site_url": "", "pids": [], "opens": 5, "cid": ""}]
        fired, p_opens, _ = ptw.check_local_signals(deals, state)
        assert fired == []
        assert p_opens["Acme"] == {"val": 5, "company": "Acme", "first": True}

    def test_refire_window(self):
        now = datetime.now(LOCAL_TZ)
        state = {"fired": {"acme::site_changed": (now - timedelta(days=2)).isoformat()}}
        fired = [{"company": "Acme", "trigger": "site_changed"},
                 {"company": "Acme", "trigger": "web_news"}]
        out = ptw._filter_recent(fired, state, now)
        assert [f["trigger"] for f in out] == ["web_news"]
        # outside the window it fires again
        state = {"fired": {"acme::site_changed": (now - timedelta(days=20)).isoformat()}}
        assert len(ptw._filter_recent(fired, state, now)) == 2

    def test_full_run_stages_hooks_and_records_state(self, ptw_env, monkeypatch, feed):
        _write_jsonl(ptw_env / "proposals.jsonl", [
            {"id": "p1", "status": "staged", "company": "Acme Spa",
             "site_url": "https://acmespa.com", "opens": 0}])
        monkeypatch.setattr(ptw, "_fetch", lambda url: (True, "<body>v1</body>"))
        assert ptw.run(force=True) == 0  # baseline run
        monkeypatch.setattr(ptw, "_fetch", lambda url: (True, "<body>v2 changed</body>"))
        monkeypatch.setattr(planner, "_cli", lambda *a, **k: json.dumps(
            [{"company": "Acme Spa", "hook": "Saw the new site copy — sharp. Want me to fold it into the plan?"}]))
        assert ptw.run(force=True) == 0
        hooks = _read(ptw_env / "trigger_hooks.jsonl")
        assert len(hooks) == 1
        assert set(hooks[0]) == {"company", "trigger", "hook", "ts"}
        assert hooks[0]["trigger"] == "site_changed"
        assert "—" not in hooks[0]["hook"]  # humanize() strips the em dash
        state = json.loads((ptw_env / "state.json").read_text())
        assert "acme spa::site_changed" in state["fired"]
        assert any("Trigger watch" in a[1] for a in feed)

    def test_failed_hook_draft_keeps_trigger_unrecorded(self, ptw_env, monkeypatch):
        _write_jsonl(ptw_env / "proposals.jsonl", [
            {"id": "p1", "status": "staged", "company": "Acme Spa",
             "site_url": "https://acmespa.com", "opens": 0}])
        monkeypatch.setattr(ptw, "_fetch", lambda url: (True, "<body>v1</body>"))
        ptw.run(force=True)
        monkeypatch.setattr(ptw, "_fetch", lambda url: (True, "<body>v2</body>"))
        # guardrails default: _cli returns None -> no hooks
        ptw.run(force=True)
        assert not (ptw_env / "trigger_hooks.jsonl").exists()
        state = json.loads((ptw_env / "state.json").read_text())
        assert state.get("fired", {}) == {}  # retried next run, not lost for 14d

    def test_weekly_self_gate(self, ptw_env, monkeypatch):
        (ptw_env / "state.json").write_text(json.dumps({"last_run": _iso(24)}))
        monkeypatch.setattr(ptw, "_fetch",
                            lambda url: (_ for _ in ()).throw(AssertionError("gated run fetched")))
        _write_jsonl(ptw_env / "proposals.jsonl", [
            {"id": "p1", "status": "staged", "company": "A", "site_url": "https://a.com"}])
        assert ptw.run() == 0  # inside the gate: no fetch, exit 0

    def test_dry_run_writes_nothing(self, ptw_env, monkeypatch):
        _write_jsonl(ptw_env / "proposals.jsonl", [
            {"id": "p1", "status": "staged", "company": "Acme",
             "site_url": "https://acme.com", "opens": 2}])
        monkeypatch.setattr(ptw, "_fetch", lambda url: (True, "<body>x</body>"))
        assert ptw.run(dry_run=True) == 0
        assert not (ptw_env / "site_hashes.json").exists()
        assert not (ptw_env / "state.json").exists()
        assert not (ptw_env / "trigger_hooks.jsonl").exists()

    def test_fresh_install_exit_0(self, ptw_env):
        assert ptw.run(force=True) == 0

    def test_open_deals_merges_proposals_and_convo(self, ptw_env):
        _write_jsonl(ptw_env / "proposals.jsonl", [
            {"id": "p1", "status": "staged", "company": "Acme Spa",
             "site_url": "https://acme.com", "opens": 2, "contact_id": "c1"},
            {"id": "p2", "status": "skipped", "company": "Skipped Co"}])
        (ptw_env / "convo_states.json").write_text(json.dumps({"states": {
            "c1": {"state": "negotiating", "name": "Acme Spa"},
            "c9": {"state": "engaged", "name": "Other Biz"},
            "c8": {"state": "dormant", "name": "Sleepy"}}}))
        deals = ptw.open_deals()
        names = {d["company"] for d in deals}
        assert names == {"Acme Spa", "Other Biz"}  # skipped + dormant excluded
        acme = next(d for d in deals if d["company"] == "Acme Spa")
        assert acme["convo_state"] == "negotiating" and acme["opens"] == 2

    def test_check_web_parses_and_filters(self, monkeypatch):
        # NOTE: no ptw_env here on purpose (that fixture stubs check_web itself)
        monkeypatch.setattr(planner, "_find_claude_cli", lambda: "/usr/bin/true", raising=False)

        class FakeProc:
            stdout = json.dumps([{"company": "Acme", "change": "opened a second location"},
                                 {"company": "Beta", "change": None},
                                 {"company": "NotOnList", "change": "invented"}])
        monkeypatch.setattr(ptw.subprocess, "run", lambda *a, **k: FakeProc())
        fired = ptw.check_web([{"company": "Acme"}, {"company": "Beta"}])
        assert len(fired) == 1
        assert fired[0] == {"company": "Acme", "trigger": "web_news",
                            "detail": "opened a second location"}


# ------------------------------------------------- transcript_miner

@pytest.fixture
def tm_env(tmp_path, monkeypatch):
    tdir = tmp_path / "coach_transcripts"
    tdir.mkdir()
    monkeypatch.setattr(tm, "TRANSCRIPTS", tdir)
    monkeypatch.setattr(tm, "STATE", tmp_path / "miner_state.json")
    monkeypatch.setattr(tm, "PROMISES", tmp_path / "promises.jsonl")
    monkeypatch.setattr(tm, "OBJECTIONS", tmp_path / "objections.jsonl")
    # convo_context is the canonical objection writer: point ITS store at tmp too
    monkeypatch.setattr(convo_context, "OBJECTIONS", tmp_path / "objections.jsonl")
    return tmp_path


def _write_transcript(tdir: Path, name: str, lines: list[tuple[str, str]],
                      epoch: float = 1783116559.0) -> Path:
    p = tdir / name
    p.write_text("".join(json.dumps({"ts": epoch + i, "who": who, "text": text}) + "\n"
                         for i, (who, text) in enumerate(lines)))
    return p


REAL_CALL = [
    ("THEM", "So what would this actually cost us to get moving"),
    ("ME", "Standard build is twelve hundred and I will send you the full plan by friday"),
    ("THEM", "Honestly that feels expensive for a website right now"),
    ("ME", "One missed booking a month pays for the whole thing and you are missing more than that"),
    ("THEM", "My nephew said he could do it on Wix for free"),
]

CANNED_EXTRACT = json.dumps({
    "commitments": [{"quote": "Standard build is twelve hundred and I will send you the full plan by friday",
                     "due": None}],
    "objections": [{"objection": "that feels expensive for a website right now"},
                   {"objection": "my nephew can do it on Wix for free"}]})


class TestTranscriptMiner:
    def test_promise_shape_parity_with_promises_py(self, tm_env, monkeypatch):
        _write_transcript(tm_env / "coach_transcripts", "1783116559.jsonl", REAL_CALL)
        monkeypatch.setattr(planner, "_cli", lambda *a, **k: CANNED_EXTRACT)
        assert tm.run() == 0
        mined = _read(tm_env / "promises.jsonl")
        assert len(mined) == 1
        rec = mined[0]
        # EXACT field parity with promises.build()'s records
        reference = promises.build(promises._fixture_candidates())[0]
        assert set(rec.keys()) == set(reference.keys())
        assert rec["source_kind"] == "call"
        assert rec["status"] == "open" and rec["warned_48h"] is False
        # dedup key follows promises._dedup_key exactly
        assert rec["dedup_key"] == promises._dedup_key(
            rec["source_id"], rec["phrase"], rec["due_date"])
        # "by friday" resolved by the tested grammar, relative to the CALL's date
        tdate = datetime.fromtimestamp(1783116559.0).astimezone().date()
        expected = promises.find_promises(
            "i will send you the full plan by friday", tdate)[0]["due_date"]
        assert rec["due_date"] == expected
        assert rec["phrase"].lower() == "by friday"

    def test_objections_written_in_existing_shape(self, tm_env, monkeypatch):
        _write_transcript(tm_env / "coach_transcripts", "call.jsonl", REAL_CALL)
        monkeypatch.setattr(planner, "_cli", lambda *a, **k: CANNED_EXTRACT)
        tm.run()
        objs = _read(tm_env / "objections.jsonl")
        assert len(objs) == 2
        # convo_context.log_objection's exact fields
        assert set(objs[0].keys()) == {"ts", "objection", "counter", "src",
                                       "contact_id", "name", "niche"}
        assert objs[0]["src"] == "call" and objs[0]["counter"] == ""

    def test_idempotent_per_file_mtime(self, tm_env, monkeypatch):
        _write_transcript(tm_env / "coach_transcripts", "call.jsonl", REAL_CALL)
        calls = []
        monkeypatch.setattr(planner, "_cli", lambda *a, **k: calls.append(1) or CANNED_EXTRACT)
        tm.run()
        assert len(calls) == 1
        tm.run()  # unchanged file: no second LLM call, no dup promises
        assert len(calls) == 1
        assert len(_read(tm_env / "promises.jsonl")) == 1

    def test_dedup_key_prevents_dup_even_on_reprocess(self, tm_env, monkeypatch):
        p = _write_transcript(tm_env / "coach_transcripts", "call.jsonl", REAL_CALL)
        monkeypatch.setattr(planner, "_cli", lambda *a, **k: CANNED_EXTRACT)
        tm.run()
        # file grows (same commitment still inside): reprocessed, but promise deduped
        with p.open("a") as f:
            f.write(json.dumps({"ts": 1783116999.0, "who": "THEM", "text": "ok sounds good then"}) + "\n")
        tm.run()
        assert len(_read(tm_env / "promises.jsonl")) == 1

    def test_noise_transcript_burns_no_llm_call(self, tm_env, monkeypatch):
        _write_transcript(tm_env / "coach_transcripts", "tv.jsonl", [
            ("THEM", "(dramatic music)"), ("ME", "Thanks."), ("THEM", "[INAUDIBLE]"),
            ("ME", "(water splashing)")])
        monkeypatch.setattr(planner, "_cli",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM hit")))
        assert tm.run() == 0
        state = json.loads((tm_env / "miner_state.json").read_text())
        assert state["tv.jsonl"]["skipped"] == "no_substance"

    def test_llm_cap(self, tm_env, monkeypatch):
        for i in range(5):
            _write_transcript(tm_env / "coach_transcripts", f"c{i}.jsonl", REAL_CALL)
        calls = []
        monkeypatch.setattr(planner, "_cli", lambda *a, **k: calls.append(1) or CANNED_EXTRACT)
        tm.run()
        assert len(calls) == tm.LLM_CAP

    def test_undated_commitment_keeps_shape_with_empty_due(self, tm_env):
        # commitment_to_promise now takes me_norm (fix 2): only a quote that is a
        # substring of an actual ME line becomes a promise. Pass the quote as the ME
        # corpus to simulate it having really been spoken by Alex.
        quote = "I will send over the references"
        rec = tm.commitment_to_promise(quote, None, datetime.now().date(), "call_x",
                                       set(), quote.lower())
        assert rec["due_date"] == "" and rec["resolved_from"] == "call_llm"
        assert rec["status"] == "open"

    def test_llm_iso_date_used_when_grammar_misses(self):
        quote = "I will send over the references"
        rec = tm.commitment_to_promise(quote, "2026-07-20", datetime(2026, 7, 6).date(),
                                       "call_x", set(), quote.lower())
        assert rec["due_date"] == "2026-07-20" and rec["resolved_from"] == "call_llm_date"

    def test_dry_run_writes_nothing(self, tm_env, monkeypatch):
        _write_transcript(tm_env / "coach_transcripts", "call.jsonl", REAL_CALL)
        monkeypatch.setattr(planner, "_cli", lambda *a, **k: CANNED_EXTRACT)
        assert tm.run(dry_run=True) == 0
        assert not (tm_env / "promises.jsonl").exists()
        assert not (tm_env / "objections.jsonl").exists()
        assert not (tm_env / "miner_state.json").exists()

    def test_fresh_install_exit_0(self, tm_env):
        assert tm.run() == 0


# ------------------------------------------------- portfolio_teardown

@pytest.fixture
def pt_env(tmp_path, monkeypatch):
    hooks_csv = tmp_path / "wl-hooks.csv"
    master_csv = tmp_path / "master.csv"
    monkeypatch.setattr(pt, "HOOKS_CSV", hooks_csv)
    monkeypatch.setattr(pt, "MASTER_CSV", master_csv)
    monkeypatch.setattr(pt, "PROPOSALS", tmp_path / "proposals.jsonl")
    monkeypatch.setattr(pt, "SUPPRESS", tmp_path / "suppress.jsonl")
    monkeypatch.setattr(pt, "OUT", tmp_path / "teardown_candidates.jsonl")
    return tmp_path


def _hooks_csv(path: Path, rows: list[dict]):
    cols = ["company", "email", "website", "status"]
    path.write_text(",".join(cols) + "\n" +
                    "".join(",".join(r.get(c, "") for c in cols) + "\n" for r in rows))


FAKE_SITES = {
    "https://bad.com": {"url": "https://bad.com", "title": "", "text": "hi",
                        "viewport": False, "imgs": 0, "bytes": 2_000_000,
                        "raw_html": "<html>powered by wix.com<p>hi</p></html>"},
    "https://ok.com": {"url": "https://ok.com", "title": "OK Co", "text": "x" * 900,
                       "viewport": True, "imgs": 3, "bytes": 90_000,
                       "raw_html": "<html><h1>OK</h1>" + "x" * 900 + "</html>"},
    "https://dead.com": {"url": "https://dead.com", "error": "blocked: could not resolve"},
}


def _fake_site(url: str) -> dict:
    """run() passes the raw CSV value (no scheme); mirror _fetch_site's prefixing."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return dict(FAKE_SITES[url])


class TestPortfolioTeardown:
    def test_audit_dead_site_weighs_like_rebuild_lane(self):
        faults, score = pt.audit({"url": "https://x.com", "error": "timeout"})
        assert score == pt.DEAD_WEIGHT and len(faults) == 1

    def test_audit_counts_concrete_faults(self):
        faults, score = pt.audit(FAKE_SITES["https://bad.com"])
        joined = " | ".join(faults)
        assert "no page title" in joined
        assert "no mobile viewport" in joined
        assert "heavy page" in joined
        assert "thin content" in joined
        assert "wix" in joined.lower()
        assert "no h1" in joined
        assert score == 6

    def test_audit_clean_site_scores_zero(self):
        faults, score = pt.audit(FAKE_SITES["https://ok.com"])
        assert faults == [] and score == 0

    def test_fault_ranking_with_fake_fetches(self, pt_env, monkeypatch, feed):
        _hooks_csv(pt_env / "wl-hooks.csv", [
            {"company": "Ok Co", "email": "a@ok.com", "website": "ok.com", "status": "send"},
            {"company": "Bad Co", "email": "b@bad.com", "website": "bad.com", "status": "send"},
            {"company": "Dead Co", "email": "c@dead.com", "website": "dead.com", "status": "send"}])
        monkeypatch.setattr(pt, "_fetch_site", _fake_site)
        assert pt.run() == 0
        rows = _read(pt_env / "teardown_candidates.jsonl")
        # fix 7: "Dead Co" returns {"error":...} (a transient/blocked fetch) and is NOT
        # persisted, so it refetches next run instead of being branded unreachable
        # forever. Only the two real fetches land, still ranked by score desc.
        assert [r["name"] for r in rows] == ["Bad Co", "Ok Co"]
        assert "Dead Co" not in {r["name"] for r in rows}
        assert rows[0]["score"] == 6 and rows[1]["score"] == 0  # Bad Co faults, Ok Co clean
        assert set(rows[0]) == {"name", "site", "faults", "score", "ts"}
        assert any("Teardown" in a[1] and "Bad Co" in a[1] for a in feed)

    def test_idempotent_across_runs_and_skips_covered(self, pt_env, monkeypatch):
        _hooks_csv(pt_env / "wl-hooks.csv", [
            {"company": "Ok Co", "email": "a@ok.com", "website": "ok.com", "status": "send"},
            {"company": "Prop Co", "email": "p@prop.com", "website": "prop.com", "status": "send"},
            {"company": "Supp Co", "email": "s@supp.com", "website": "supp.com", "status": "send"}])
        _write_jsonl(pt_env / "proposals.jsonl",
                     [{"id": "p1", "status": "staged", "site_url": "https://www.prop.com"}])
        _write_jsonl(pt_env / "suppress.jsonl", [{"email": "s@supp.com"}])
        fetched = []
        monkeypatch.setattr(pt, "_fetch_site",
                            lambda u: fetched.append(u) or dict(FAKE_SITES["https://ok.com"], url=u))
        pt.run()
        assert fetched == ["ok.com"]  # proposal-covered + suppressed skipped
        pt.run()  # second run: ok.com now in the store, nothing left
        assert fetched == ["ok.com"]

    def test_fetch_cap(self, pt_env, monkeypatch):
        _hooks_csv(pt_env / "wl-hooks.csv", [
            {"company": f"C{i}", "email": f"c{i}@x.com", "website": f"c{i}.com",
             "status": "send"} for i in range(pt.FETCH_CAP + 4)])
        fetched = []
        monkeypatch.setattr(pt, "_fetch_site",
                            lambda u: fetched.append(u) or dict(FAKE_SITES["https://ok.com"], url=u))
        pt.run()
        assert len(fetched) == pt.FETCH_CAP

    def test_dry_run_writes_nothing(self, pt_env, monkeypatch):
        _hooks_csv(pt_env / "wl-hooks.csv", [
            {"company": "Bad Co", "email": "b@bad.com", "website": "bad.com", "status": "send"}])
        monkeypatch.setattr(pt, "_fetch_site", _fake_site)
        assert pt.run(dry_run=True) == 0
        assert not (pt_env / "teardown_candidates.jsonl").exists()

    def test_fresh_install_exit_0(self, pt_env):
        assert pt.run() == 0


# ------------------------------------------------- care_upsell

@pytest.fixture
def cu_env(tmp_path, monkeypatch):
    monkeypatch.setattr(cu, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(cu, "AGREEMENTS", tmp_path / "agreements.jsonl")
    monkeypatch.setattr(cu, "PROPOSALS", tmp_path / "proposals.jsonl")
    monkeypatch.setattr(cu, "TODOS", tmp_path / "todos.jsonl")
    monkeypatch.setattr(cu, "STATE", tmp_path / "care_state.json")
    monkeypatch.setattr(cu, "DRAFTS", tmp_path / "drafts")
    return tmp_path


class TestCareUpsell:
    def test_seven_day_gate_and_once_ever(self, cu_env):
        _write_jsonl(cu_env / "ledger.jsonl", [
            {"ts": _iso(8 * 24), "kind": "won", "amount": 1200.0, "note": "Nimbus Soft - WL Webdev"},
            {"ts": _iso(2 * 24), "kind": "won", "amount": 800.0, "note": "Fresh Win Co - landing"}])
        assert cu.run() == 0
        todos = load_todos(cu_env / "todos.jsonl")
        assert len(todos) == 1  # 8d-old win staged, 2d-old win still inside the gate
        assert todos[0]["text"] == "Care plan pitch: Nimbus Soft (75/150)"
        assert (cu_env / "drafts" / "care_nimbus_soft.md").exists()
        # once per client EVER: nothing new on rerun
        assert cu.run() == 0
        assert len(load_todos(cu_env / "todos.jsonl")) == 1
        assert len(list((cu_env / "drafts").glob("care_*.md"))) == 1

    def test_fallback_draft_carries_true_prices(self, cu_env):
        # guardrails _cli -> None, so the deterministic fallback writes the draft
        _write_jsonl(cu_env / "ledger.jsonl", [
            {"ts": _iso(9 * 24), "kind": "won", "amount": 1200.0, "note": "Plain Plumbing - build"}])
        cu.run()
        draft = (cu_env / "drafts" / "care_plain_plumbing.md").read_text()
        assert "$150/mo" in draft and "$75/mo" in draft
        assert "—" not in draft

    def test_medspa_wording_no_longer_steers_price(self, cu_env):
        """M / NO MEDSPAS (dropped 2026-07-11): the medspa lane is gone -- niche/
        client wording that used to trip the $300 'Growth+' tier now gets the same
        $150 Care Growth as every other active niche."""
        _write_jsonl(cu_env / "ledger.jsonl", [
            {"ts": _iso(9 * 24), "kind": "won", "amount": 3500.0, "note": "Client A Medspa - whiteglove"}])
        cu.run()
        draft = (cu_env / "drafts" / "care_client_a_medspa.md").read_text()
        assert "$150" in draft
        assert "$300" not in draft

    def test_llm_draft_used_when_it_answers(self, cu_env, monkeypatch):
        monkeypatch.setattr(planner, "_cli",
                            lambda *a, **k: "Subject: keeping it fast\n\nSite is live — care keeps it that way. Alex")
        _write_jsonl(cu_env / "ledger.jsonl", [
            {"ts": _iso(9 * 24), "kind": "won", "amount": 1200.0, "note": "Nimbus - build"}])
        cu.run()
        draft = (cu_env / "drafts" / "care_nimbus.md").read_text()
        assert "keeping it fast" in draft
        assert "—" not in draft  # humanize applied to the model output too

    def test_wins_merge_across_ledger_and_agreements(self, cu_env):
        _write_jsonl(cu_env / "ledger.jsonl", [
            {"ts": _iso(10 * 24), "kind": "won", "amount": 1200.0, "note": "Nimbus Soft - WL"}])
        _write_jsonl(cu_env / "agreements.jsonl", [
            {"ts": _iso(9 * 24), "pid": "prop_x", "signed_name": "B", "company": "Nimbus Soft",
             "price": 1200}])
        wins = cu.collect_wins()
        assert len(wins) == 1  # one client, not two

    def test_accepted_proposal_counts_as_win(self, cu_env):
        _write_jsonl(cu_env / "proposals.jsonl", [
            {"id": "p1", "status": "accepted", "company": "Riverbend Clinic",
             "accepted_at": _iso(8 * 24), "price": 2500, "niche": "clinic"}])
        wins = cu.collect_wins()
        assert wins and wins[0]["client"] == "Riverbend Clinic"

    def test_slow_tier_not_pitched_before_its_own_promised_days(self, cu_env):
        """CX8: a White-Glove build (14 promised days) used to get the flat 7-day
        'site just delivered' pitch -- claiming a site is live while it's still
        under construction. A 10-day-old accepted White-Glove win must NOT be due
        yet; the same win crossing 16 days (14 + the 2-day grace buffer) must be.
        A ledger deposit row makes it payment-gate-clean so only the age gate
        (CX8's actual concern) is under test."""
        _write_jsonl(cu_env / "ledger.jsonl", [
            {"ts": _iso(10 * 24), "kind": "deposit", "amount": 1750.0,
             "note": "Slowbuild Co deposit"}])
        _write_jsonl(cu_env / "proposals.jsonl", [
            {"id": "p1", "status": "accepted", "company": "Slowbuild Co",
             "accepted_at": _iso(10 * 24), "price": 3500, "tier": "whiteglove"}])
        assert cu.run() == 0
        assert not (cu_env / "drafts" / "care_slowbuild_co.md").exists(), \
            "White-Glove pitched before its own 14-day promised build time elapsed"

        _write_jsonl(cu_env / "proposals.jsonl", [
            {"id": "p1", "status": "accepted", "company": "Slowbuild Co",
             "accepted_at": _iso(17 * 24), "price": 3500, "tier": "whiteglove"}])
        cu.run()
        assert (cu_env / "drafts" / "care_slowbuild_co.md").exists(), \
            "White-Glove never pitched even after its promised build time + grace elapsed"

    def test_unresolvable_tier_keeps_the_flat_fallback(self, cu_env):
        """CX8 corollary: a ledger-only win (no matching proposal, so no tier) keeps
        the original flat UPSELL_AFTER_DAYS=7 behavior -- unchanged from before."""
        _write_jsonl(cu_env / "ledger.jsonl", [
            {"ts": _iso(8 * 24), "kind": "won", "amount": 1200.0, "note": "No Tier Co - build"}])
        cu.run()
        assert (cu_env / "drafts" / "care_no_tier_co.md").exists()

    def test_non_won_ledger_kinds_ignored(self, cu_env):
        _write_jsonl(cu_env / "ledger.jsonl", [
            {"ts": _iso(9 * 24), "kind": "test", "amount": 0, "note": "wave1"},
            {"ts": _iso(9 * 24), "kind": "booked_call", "amount": 0, "note": "w_abc"}])
        assert cu.collect_wins() == []

    def test_dry_run_writes_nothing(self, cu_env):
        _write_jsonl(cu_env / "ledger.jsonl", [
            {"ts": _iso(9 * 24), "kind": "won", "amount": 1200.0, "note": "Acme - build"}])
        assert cu.run(dry_run=True) == 0
        assert not (cu_env / "drafts").exists()
        assert not (cu_env / "care_state.json").exists()
        assert not (cu_env / "todos.jsonl").exists()

    def test_fresh_install_exit_0(self, cu_env):
        assert cu.run() == 0


# ------------------------------------------------- deposit_nudge

@pytest.fixture
def dn_env(tmp_path, monkeypatch):
    monkeypatch.setattr(dn, "PROPOSALS", tmp_path / "proposals.jsonl")
    monkeypatch.setattr(dn, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(dn, "TODOS", tmp_path / "todos.jsonl")
    monkeypatch.setattr(dn, "STATE", tmp_path / "nudge_state.json")
    return tmp_path


def _accepted_prop(hours_ago: float, pid="p1", company="Nimbus Soft", price=1200):
    return {"id": pid, "status": "accepted", "company": company, "price": price,
            "accepted_at": _iso(hours_ago), "created": _iso(hours_ago + 4)}


class TestDepositNudge:
    def test_48h_window_fires_once(self, dn_env, pushes):
        _write_jsonl(dn_env / "proposals.jsonl", [_accepted_prop(50)])
        assert dn.run() == 0
        assert len(pushes) == 1
        assert "SIGNED but no deposit" in pushes[0][0]
        # CX9: the 50% deposit ($600 on a $1,200 build) is what's owed right now,
        # not the full build price.
        assert "Nimbus Soft" in pushes[0][1] and "$600" in pushes[0][1]
        assert "$1,200" not in pushes[0][1]
        todos = load_todos(dn_env / "todos.jsonl")
        assert len(todos) == 1 and "Nimbus Soft" in todos[0]["text"]
        # immediate rerun: inside the window, silent
        assert dn.run() == 0
        assert len(pushes) == 1
        assert len(load_todos(dn_env / "todos.jsonl")) == 1

    def test_renudges_after_window_but_todo_once_ever(self, dn_env, pushes):
        _write_jsonl(dn_env / "proposals.jsonl", [_accepted_prop(120)])
        (dn_env / "nudge_state.json").write_text(json.dumps(
            {"p1": {"last_nudge": _iso(50), "company": "Nimbus Soft"}}))
        dn.run()
        assert len(pushes) == 1  # 50h since last nudge > 48h window: fires again
        assert len(load_todos(dn_env / "todos.jsonl")) == 1

    def test_too_fresh_acceptance_stays_quiet(self, dn_env, pushes):
        _write_jsonl(dn_env / "proposals.jsonl", [_accepted_prop(10)])
        dn.run()
        assert pushes == []

    def test_ledger_payment_silences(self, dn_env, pushes):
        _write_jsonl(dn_env / "proposals.jsonl", [_accepted_prop(60)])
        _write_jsonl(dn_env / "ledger.jsonl", [
            {"ts": _iso(30), "kind": "won", "amount": 600.0, "note": "Nimbus Soft - deposit"}])
        dn.run()
        assert pushes == []

    def test_zero_amount_and_wrong_kind_do_not_count(self, dn_env):
        prop = _accepted_prop(60)
        assert dn.has_payment(prop, [{"kind": "won", "amount": 0, "note": "Nimbus Soft"}]) is False
        assert dn.has_payment(prop, [{"kind": "booked_call", "amount": 500, "note": "Nimbus Soft"}]) is False
        assert dn.has_payment(prop, [{"kind": "payment", "amount": 600, "note": "nimbus soft dep"}]) is True
        assert dn.has_payment(prop, [{"kind": "deposit", "amount": 600, "note": "p1 wire"}]) is True

    def test_dry_run_writes_nothing(self, dn_env, pushes):
        _write_jsonl(dn_env / "proposals.jsonl", [_accepted_prop(50)])
        assert dn.run(dry_run=True) == 0
        assert pushes == []
        assert not (dn_env / "nudge_state.json").exists()
        assert not (dn_env / "todos.jsonl").exists()

    def test_fresh_install_exit_0(self, dn_env):
        assert dn.run() == 0


# ------------------------------------------------- reactivation_triage

@pytest.fixture
def rt_env(tmp_path, monkeypatch):
    hitlist = tmp_path / "WARM-HITLIST.csv"
    monkeypatch.setattr(rt, "HITLIST", hitlist)
    monkeypatch.setattr(rt, "SUPPRESS", tmp_path / "suppress.jsonl")
    monkeypatch.setattr(rt, "DISPO", tmp_path / "warm_dispo.jsonl")
    monkeypatch.setattr(rt, "REPLIES", tmp_path / "replies.jsonl")
    monkeypatch.setattr(rt, "OUT", tmp_path / "reactivation_triage.json")
    return tmp_path


def _hitlist(path: Path, rows: list[dict]):
    cols = ["tier", "name", "company", "email", "phone", "location", "niche",
            "suggested_offer", "pipeline", "stage", "deal_age_days", "tags", "tz"]
    path.write_text(",".join(cols) + "\n" +
                    "".join(",".join(str(r.get(c, "")) for c in cols) + "\n" for r in rows))


class TestReactivationTriage:
    def test_bucketing_rules(self, rt_env):
        _write_jsonl(rt_env / "suppress.jsonl", [{"email": "no@x.com"}])
        row = lambda **kw: {"tier": "2", "name": "n", "company": "c", "tags": "", **kw}
        suppressed, dead = {"no@x.com"}, {rt._rid("+15551234", "")}
        assert rt.bucket(row(email="no@x.com", deal_age_days="10"), suppressed, dead) == "said_no"
        assert rt.bucket(row(email="a@x.com", tags="inst reply; unsub", deal_age_days="10"),
                         suppressed, dead) == "said_no"
        assert rt.bucket(row(email="a@x.com", phone="+15551234", deal_age_days="10"),
                         suppressed, dead) == "said_no"
        assert rt.bucket(row(email="a@x.com", deal_age_days="30"), suppressed, dead) == \
            "replied_recently_then_quiet"
        assert rt.bucket(row(email="a@x.com", deal_age_days="200"), suppressed, dead) == "old_warm"

    def test_build_counts_and_top_sorted_fresh_first(self, rt_env):
        rows = [{"tier": "2", "name": f"P{i}", "company": f"Co{i}", "email": f"p{i}@x.com",
                 "stage": "Hot Lead", "deal_age_days": str(age), "tags": "inst reply"}
                for i, age in enumerate([300, 20, 150, 40, 95])]
        rows.append({"tier": "1", "name": "Booked", "company": "B", "email": "b@x.com",
                     "stage": "Call booked", "deal_age_days": "100", "tags": ""})
        _hitlist(rt_env / "WARM-HITLIST.csv", rows)
        data = rt.build()
        assert data["source_rows"] == 5  # tier 1 excluded
        lanes = data["lanes"]
        assert lanes["replied_recently_then_quiet"]["count"] == 2   # 20, 40
        assert lanes["old_warm"]["count"] == 3                      # 95, 150, 300
        assert lanes["said_no"]["count"] == 0
        top = lanes["replied_recently_then_quiet"]["top"]
        assert [t["age_days"] for t in top] == [20, 40]

    def test_remove_intent_reply_lands_said_no(self, rt_env):
        _hitlist(rt_env / "WARM-HITLIST.csv", [
            {"tier": "2", "name": "Gone", "company": "Gone Co", "email": "gone@x.com",
             "stage": "Contacted", "deal_age_days": "30", "tags": "inst reply"}])
        _write_jsonl(rt_env / "replies.jsonl", [
            {"id": "r1", "intent": "remove", "email": "gone@x.com", "status": "sent"}])
        data = rt.build()
        assert data["lanes"]["said_no"]["count"] == 1

    def test_run_writes_json_and_feed(self, rt_env, feed):
        _hitlist(rt_env / "WARM-HITLIST.csv", [
            {"tier": "2", "name": "P", "company": "C", "email": "p@x.com",
             "stage": "Hot Lead", "deal_age_days": "10", "tags": ""}])
        assert rt.run() == 0
        out = json.loads((rt_env / "reactivation_triage.json").read_text())
        assert out["lanes"]["replied_recently_then_quiet"]["count"] == 1
        assert any("repliers triaged" in a[1] for a in feed)

    def test_dry_run_writes_nothing(self, rt_env):
        _hitlist(rt_env / "WARM-HITLIST.csv", [
            {"tier": "2", "name": "P", "company": "C", "email": "p@x.com",
             "stage": "Hot Lead", "deal_age_days": "10", "tags": ""}])
        assert rt.run(dry_run=True) == 0
        assert not (rt_env / "reactivation_triage.json").exists()

    def test_fresh_install_exit_0(self, rt_env):
        assert rt.run() == 0


# ------------------------------------------------- dropoff_audit

@pytest.fixture
def da_env(tmp_path, monkeypatch):
    monkeypatch.setattr(da, "HITLIST", tmp_path / "WARM-HITLIST.csv")
    monkeypatch.setattr(da, "DISPO", tmp_path / "warm_dispo.jsonl")
    monkeypatch.setattr(da, "PROPOSALS", tmp_path / "proposals.jsonl")
    monkeypatch.setattr(da, "OUT", tmp_path / "dropoff_audit.json")
    return tmp_path


class TestDropoffAudit:
    def test_join_email_and_name_matching(self, da_env, feed):
        _hitlist(da_env / "WARM-HITLIST.csv", [
            {"tier": "1", "name": "client_a medspa", "company": "Client A Medspa",
             "email": "client_a.medspa@gmail.com", "stage": "Call booked", "deal_age_days": "277"},
            {"tier": "1", "name": "kara ozen", "company": "Transcend Aging Medspa",
             "email": "other@x.com", "stage": "Call booked", "deal_age_days": "200"},
            {"tier": "1", "name": "leaked person", "company": "Leaked Clinic",
             "email": "leak@x.com", "stage": "Call booked", "deal_age_days": "100"},
            {"tier": "2", "name": "replier", "company": "Not Booked Co",
             "email": "r@x.com", "stage": "Hot Lead", "deal_age_days": "50"}])
        _write_jsonl(da_env / "proposals.jsonl", [
            {"id": "p1", "status": "staged", "company": "Client A Medspa",
             "email": "client_a.medspa@gmail.com"},                     # email match
            {"id": "p2", "status": "staged", "company": "Transcend Aging + Wellness Medspa",
             "email": "transcendrr@gmail.com"},                        # name containment match
            {"id": "p3", "status": "skipped", "company": "Leaked Clinic",
             "email": "leak@x.com"}])                                  # skipped never covers
        assert da.run() == 0
        out = json.loads((da_env / "dropoff_audit.json").read_text())
        assert out["booked_total"] == 3      # the tier-2 replier is not booked
        assert out["with_proposal"] == 2
        assert out["without_proposal"] == 1
        assert out["leak"][0]["company"] == "Leaked Clinic"
        assert any("never got a proposal" in a[1] and "Leaked Clinic" in a[1] for a in feed)

    def test_dispo_booked_lane_joins_by_rid(self, da_env):
        _hitlist(da_env / "WARM-HITLIST.csv", [
            {"tier": "2", "name": "quiet replier", "company": "Quiet Co",
             "email": "q@x.com", "phone": "+15550001111", "stage": "Hot Lead",
             "deal_age_days": "40"}])
        _write_jsonl(da_env / "warm_dispo.jsonl", [
            {"id": da._rid("+15550001111", "quiet replier"), "dispo": "booked", "ts": _iso(5)}])
        data = da.build()
        assert data["booked_total"] == 1
        assert data["leak"][0]["company"] == "Quiet Co"

    def test_short_names_never_wildcard_match(self, da_env):
        _hitlist(da_env / "WARM-HITLIST.csv", [
            {"tier": "1", "name": "spa", "company": "Spa",
             "email": "spa@x.com", "stage": "Call booked", "deal_age_days": "10"}])
        _write_jsonl(da_env / "proposals.jsonl", [
            {"id": "p1", "status": "staged", "company": "Some Other Spa Empire",
             "email": "z@z.com"}])
        data = da.build()
        assert data["without_proposal"] == 1  # 3-char norm never matches

    def test_leak_sorted_oldest_first(self, da_env):
        _hitlist(da_env / "WARM-HITLIST.csv", [
            {"tier": "1", "name": "newer", "company": "Newer Co", "email": "n@x.com",
             "stage": "Call booked", "deal_age_days": "50"},
            {"tier": "1", "name": "older", "company": "Older Co", "email": "o@x.com",
             "stage": "Call booked", "deal_age_days": "300"}])
        data = da.build()
        assert [c["company"] for c in data["leak"]] == ["Older Co", "Newer Co"]

    def test_dry_run_writes_nothing(self, da_env):
        _hitlist(da_env / "WARM-HITLIST.csv", [
            {"tier": "1", "name": "x", "company": "X Co", "email": "x@x.com",
             "stage": "Call booked", "deal_age_days": "10"}])
        assert da.run(dry_run=True) == 0
        assert not (da_env / "dropoff_audit.json").exists()

    def test_fresh_install_exit_0(self, da_env):
        assert da.run() == 0


# ------------------------------------------------- tier_winrate

class TestTierWinrate:
    def test_rate_math_with_enough_data(self):
        rows = ([{"id": f"s{i}", "tier": "standard", "price": 1200, "status": "sent"}
                 for i in range(6)]
                + [{"id": f"a{i}", "tier": "standard", "price": 1200, "status": "accepted"}
                   for i in range(2)]
                + [{"id": "g1", "tier": "standard", "price": 1200, "status": "staged"}])
        t = tw.build(rows)["tiers"]["standard"]
        assert t["n"] == 8 and t["sent"] == 6 and t["accepted"] == 2 and t["staged"] == 1
        assert t["acceptance_rate"] == 0.25
        assert t["caveat"] is None

    def test_n_under_5_gets_caveat_not_a_rate(self):
        rows = [{"id": "s1", "tier": "booking", "price": 2500, "status": "sent"},
                {"id": "a1", "tier": "booking", "price": 2500, "status": "accepted"}]
        t = tw.build(rows)["tiers"]["booking"]
        assert t["acceptance_rate"] is None
        assert t["caveat"] == "too little data to read (n=2)"

    def test_all_thin_sets_top_level_note(self):
        rows = [{"id": "x", "tier": "webfix", "price": 450, "status": "staged"}]
        data = tw.build(rows)
        assert data["note"] is not None and "under n=5" in data["note"]

    def test_staged_never_inflates_denominator(self):
        rows = ([{"id": f"g{i}", "tier": "whiteglove", "price": 3500, "status": "staged"}
                 for i in range(10)]
                + [{"id": "a1", "tier": "whiteglove", "price": 3500, "status": "accepted"}])
        t = tw.build(rows)["tiers"]["whiteglove"]
        assert t["n"] == 1 and t["acceptance_rate"] is None

    def test_sending_counts_with_sent_and_discards_tracked(self):
        rows = [{"id": "s1", "tier": "landing", "price": 800, "status": "sending"},
                {"id": "k1", "tier": "landing", "price": 800, "status": "skipped"},
                {"id": "k2", "tier": "landing", "price": 800, "status": "superseded"}]
        t = tw.build(rows)["tiers"]["landing"]
        assert t["sent"] == 1 and t["discarded"] == 2

    def test_last_write_wins_by_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tw, "PROPOSALS", tmp_path / "proposals.jsonl")
        _write_jsonl(tmp_path / "proposals.jsonl", [
            {"id": "p1", "tier": "standard", "price": 1200, "status": "staged"},
            {"id": "p1", "tier": "standard", "price": 1200, "status": "accepted"}])
        t = tw.build()["tiers"]["standard"]
        assert t["staged"] == 0 and t["accepted"] == 1

    def test_run_writes_json(self, tmp_path, monkeypatch, feed):
        monkeypatch.setattr(tw, "PROPOSALS", tmp_path / "proposals.jsonl")
        monkeypatch.setattr(tw, "OUT", tmp_path / "tier_winrate.json")
        _write_jsonl(tmp_path / "proposals.jsonl", [
            {"id": "p1", "tier": "standard", "price": 1200, "status": "staged"}])
        assert tw.run() == 0
        out = json.loads((tmp_path / "tier_winrate.json").read_text())
        assert out["tiers"]["standard"]["staged"] == 1
        assert any("Tier winrate" in a[1] for a in feed)

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tw, "PROPOSALS", tmp_path / "proposals.jsonl")
        monkeypatch.setattr(tw, "OUT", tmp_path / "tier_winrate.json")
        _write_jsonl(tmp_path / "proposals.jsonl", [
            {"id": "p1", "tier": "standard", "price": 1200, "status": "staged"}])
        assert tw.run(dry_run=True) == 0
        assert not (tmp_path / "tier_winrate.json").exists()

    def test_fresh_install_exit_0(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tw, "PROPOSALS", tmp_path / "nope.jsonl")
        assert tw.run() == 0
