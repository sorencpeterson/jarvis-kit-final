#!/usr/bin/env python3
"""Money-math pins (FABLE-MEGA-BACKLOG D8 missing-tests #2/#3): channel_cac,
ltv_model, close_prob pure computations with hand-computed fixtures, plus the
a_win -> /api/ledger path (negative / non-numeric amounts must not corrupt the
ledger total -- current behavior is DOCUMENTED where it does, not fixed).

These modules produce REAL dollar numbers Alex acts on; every expected value
below is hand-derived in a comment next to the assert.

Run: .venv/bin/python -m pytest tests/test_money_math.py -q
"""
from __future__ import annotations

import json
import math
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import channel_cac  # noqa: E402
import close_prob  # noqa: E402
import commander  # noqa: E402
import ltv_model  # noqa: E402
import server  # noqa: E402


# ---------------------------------------------------------------- channel_cac
class TestChannelCacTokenCost:
    def test_rates_resolve_by_model_family(self):
        assert channel_cac._rate_for("claude-haiku-4-5") == {"in": 1.00, "out": 5.00}
        assert channel_cac._rate_for("claude-sonnet-4-6") == {"in": 3.00, "out": 15.00}
        assert channel_cac._rate_for("claude-opus-4-1") == {"in": 15.00, "out": 75.00}
        # unknown / blank model -> sonnet default, never a KeyError
        assert channel_cac._rate_for("gpt-x") == channel_cac.DEFAULT_RATE
        assert channel_cac._rate_for("") == channel_cac.DEFAULT_RATE

    def test_token_cost_hand_computed(self):
        rows = [
            # sonnet: 1M in @ $3 + 1M out @ $15 = $18.00 -> feature "reply" -> warm
            {"feature": "reply", "model": "claude-sonnet-4-6", "in": 1_000_000, "out": 1_000_000},
            # haiku: 2M in @ $1 = $2.00 -> feature "job_scan" -> jobs
            {"feature": "job_scan", "model": "claude-haiku-4-5", "in": 2_000_000, "out": 0},
            # haiku: 1M out @ $5 = $5.00 -> unknown feature -> internal
            {"feature": "mystery", "model": "claude-haiku-4-5", "in": 0, "out": 1_000_000},
        ]
        cost = channel_cac._token_cost_by_lane(rows)
        assert cost["warm"] == pytest.approx(18.00)
        assert cost["jobs"] == pytest.approx(2.00)
        assert cost["internal"] == pytest.approx(5.00)

    def test_missing_token_fields_cost_zero_not_crash(self):
        cost = channel_cac._token_cost_by_lane([{"feature": "reply", "model": "claude-sonnet-4-6"}])
        assert cost["warm"] == 0.0


class TestChannelCacActivityProxy:
    def test_empty_runs_returns_none_not_empty_dict(self):
        # None means "no rows yet" so run() falls back to the events proxy;
        # {} would silently claim "genuinely zero activity". The distinction
        # is documented in the source and load-bearing for hours_proxy_kind.
        assert channel_cac._machine_seconds_by_lane([]) is None

    def test_seconds_sum_per_lane_by_agent_prefix(self):
        rows = [{"agent": "cold_feeder", "dur_s": 10.0}, {"agent": "cold_scan", "dur_s": 5.0},
                {"agent": "warm_block", "dur_s": 3.0}, {"agent": "janitor", "dur_s": 2.0},
                {"agent": "cold_x", "dur_s": None}]  # None dur_s -> 0, not a crash
        out = channel_cac._machine_seconds_by_lane(rows)
        assert out["cold"] == pytest.approx(15.0)  # 10 + 5 + 0
        assert out["warm"] == pytest.approx(3.0)
        assert out["internal"] == pytest.approx(2.0)  # unknown agent -> internal


class TestChannelCacRevenue:
    LEDGER = [
        {"kind": "won", "amount": 2500, "note": "Acme HVAC signed the build"},
        {"kind": "payment", "amount": 1000, "note": "Bright Salon care month 1"},
        {"kind": "won", "amount": 500, "note": "Ace Co closed"},
        {"kind": "note", "amount": 99999, "note": "Acme HVAC reminder"},  # not revenue
        {"kind": "won", "amount": None, "note": "Acme HVAC amendment"},   # None -> $0
    ]

    def test_revenue_matched_by_lane_hand_computed(self):
        cold_idx = {"acmehvac"}
        warm_idx = {"brightsalon", "ace"}  # "ace" is 3 chars -> below the >=4 guard
        rev = channel_cac._closed_by_lane(self.LEDGER, cold_idx, warm_idx)
        # cold: 2500 (Acme) + 0 (None amount) = 2500; warm: 1000 (Bright Salon)
        assert rev["cold"] == pytest.approx(2500.0)
        assert rev["warm"] == pytest.approx(1000.0)
        # "Ace Co closed" must NOT fuzzy-match warm's 3-char "ace" -> unmatched.
        # (The >=4 length guard is what keeps short names from vacuuming up revenue.)
        assert rev["(unmatched)"] == pytest.approx(500.0)

    def test_non_revenue_kinds_excluded(self):
        rev = channel_cac._closed_by_lane([{"kind": "note", "amount": 5000, "note": "Acme HVAC"}],
                                          {"acmehvac"}, set())
        assert rev == {}


class TestChannelCacBuild:
    def test_build_totals_and_net_hand_computed(self):
        result = channel_cac.build(
            token_cost={"warm": 18.0},
            activity={"cold": 15.0},
            revenue={"cold": 2500.0, "warm": 1000.0, "(unmatched)": 500.0},
            event_volume=7, hours_proxy_kind="runs_seconds")
        lanes = result["lanes"]
        assert "(unmatched)" not in lanes  # never presented as a lane
        assert lanes["warm"]["net"] == pytest.approx(982.0)   # 1000 - 18
        assert lanes["cold"]["net"] == pytest.approx(2500.0)  # 2500 - 0
        assert result["unmatched_revenue"] == pytest.approx(500.0)
        assert result["total_token_cost_usd"] == pytest.approx(18.0)
        # total EXCLUDES unmatched: 2500 + 1000 = 3500, not 4000
        assert result["total_closed_revenue"] == pytest.approx(3500.0)
        # cac_per_close starts None everywhere (filled by run() only where closes exist)
        assert all(v["cac_per_close"] is None for v in lanes.values())

    def test_fixture_run_zero_division_guard(self, tmp_path, monkeypatch):
        # run(fixture=True) only writes OUT; point it at a sandbox file.
        monkeypatch.setattr(channel_cac, "OUT", tmp_path / "cac.json")
        result = channel_cac.run(fixture=True)
        lanes = result["lanes"]
        # fixture ledger closes: 1 cold (Acme HVAC), 1 warm (Bright Salon)
        assert lanes["cold"]["cac_per_close"] == pytest.approx(lanes["cold"]["token_cost_usd"])
        assert lanes["warm"]["cac_per_close"] == pytest.approx(lanes["warm"]["token_cost_usd"])
        # lanes with token cost but ZERO closes keep None -- never a divide-by-zero
        assert lanes["jobs"]["cac_per_close"] is None
        assert lanes["content"]["cac_per_close"] is None
        assert lanes["internal"]["cac_per_close"] is None
        assert json.loads((tmp_path / "cac.json").read_text())["source"] == "FIXTURE"


# ------------------------------------------------------------------ ltv_model
class TestLtvModel:
    def test_project_ltv_hand_computed_defaults(self):
        # care_expected = 0.30 attach * $110/mo * 14 months = $462.00
        # ltv = 2500 build + 462 = $2962.00
        r = ltv_model.project_ltv(2500)
        assert r["care_expected_value"] == pytest.approx(462.0)
        assert r["projected_ltv"] == pytest.approx(2962.0)

    def test_project_ltv_zero_build_still_projects_care(self):
        r = ltv_model.project_ltv(0)
        assert r["projected_ltv"] == pytest.approx(462.0)  # 0 + 0.30*110*14

    def test_project_ltv_override_params(self):
        # 0.5 * 200 * 10 = 1000 care; 1000 build + 1000 = 2000
        r = ltv_model.project_ltv(1000, care_attach_prob=0.5, care_mrr=200, retention_months=10)
        assert r["care_expected_value"] == pytest.approx(1000.0)
        assert r["projected_ltv"] == pytest.approx(2000.0)

    def test_build_empty_is_honest_zero_no_division_crash(self):
        r = ltv_model.build([])
        assert r["client_count"] == 0
        assert r["total_projected_ltv"] == 0
        assert r["avg_projected_ltv"] is None  # zero-division guard: None, not NaN/crash

    def test_build_three_clients_hand_computed(self):
        r = ltv_model.build(ltv_model._fixture_data())
        # per-client ltv = price + 462: 3500->3962, 2500->2962, 800->1262
        assert [c["projected_ltv"] for c in r["clients"]] == [3962.0, 2962.0, 1262.0]  # sorted desc
        assert r["total_projected_ltv"] == pytest.approx(8186.0)   # 3962+2962+1262
        assert r["avg_projected_ltv"] == pytest.approx(2728.67)    # 8186/3 = 2728.666..
        assert r["client_count"] == 3

    def test_missing_price_treated_as_zero(self):
        r = ltv_model.build([{"id": "x", "company": "NoPrice Co", "status": "accepted"}])
        assert r["clients"][0]["build_value"] == 0.0
        assert r["clients"][0]["projected_ltv"] == pytest.approx(462.0)

    def test_dedup_by_id_last_write_wins_keeps_order(self):
        rows = [{"id": "a", "price": 1}, {"id": "b", "price": 2},
                {"id": "a", "price": 9}, {"price": 5}]  # no id -> dropped
        out = ltv_model._dedup_by_id(rows)
        assert [r["id"] for r in out] == ["a", "b"]  # first-seen order
        assert out[0]["price"] == 9  # the LATER a row won


# ----------------------------------------------------------------- close_prob
class TestCloseProbFactors:
    def test_logistic_midpoint_and_saturation(self):
        assert close_prob._logistic(0) == pytest.approx(0.5)
        assert close_prob._logistic(50) == pytest.approx(1.0, abs=1e-9)
        assert close_prob._logistic(-50) == pytest.approx(0.0, abs=1e-9)
        # OverflowError path (math.exp(800) overflows): saturates, never raises
        assert close_prob._logistic(-800.0) == 0.0
        assert close_prob._logistic(800.0) == 1.0

    def test_age_factor_curve(self):
        assert close_prob._age_factor(0) == 1.0
        assert close_prob._age_factor(-3) == 1.0  # clock skew never over-scores
        # at 30d: 1 / (1 + (30/30)^1.3) = 1/2 = 0.5 exactly
        assert close_prob._age_factor(30) == pytest.approx(0.5)
        assert close_prob._age_factor(100000) == 0.05  # floor, functionally-dead deal

    def test_value_factor_buckets(self):
        assert close_prob._value_factor(0) == 0.9
        assert close_prob._value_factor(1500) == 0.9      # low bucket is inclusive
        assert close_prob._value_factor(1500.01) == 0.5
        assert close_prob._value_factor(5000) == 0.5      # mid bucket is inclusive
        assert close_prob._value_factor(5000.01) == 0.25

    def test_unknown_or_garbage_updated_assumes_middling_age(self):
        # "" or unparseable -> 30d, NOT 0d (0 would over-score every unknown deal)
        assert close_prob._deal_age_days("") == 30.0
        assert close_prob._deal_age_days("not-a-date") == 30.0

    def test_opens_factor_matching(self):
        assert close_prob._opens_factor("Acme HVAC LLC", {"acmehvac"}) == 1.0
        assert close_prob._opens_factor("", {"acmehvac"}) == 0.0
        assert close_prob._opens_factor("Acme", {"abc"}) == 0.0  # <4 char keys ignored


class TestCloseProbScore:
    def test_score_hand_computed_no_open(self):
        # updated="" -> age 30d -> age_factor 0.5; value 900 -> value_factor 0.9
        # x = 1.1*0.5 + 0.9*0.9 + 1.4*0 - 2.35 = 0.55 + 0.81 - 2.35 = -0.99
        # prob = 1/(1+e^0.99) = 1/3.69123 = 0.2709
        s = close_prob.score_deal({"id": "d1", "name": "Acme HVAC", "value": 900, "updated": ""}, set())
        assert s["factors"]["age_factor"] == pytest.approx(0.5)
        assert s["factors"]["value_factor"] == pytest.approx(0.9)
        assert s["factors"]["had_proposal_open"] is False
        assert s["prob"] == pytest.approx(0.2709, abs=5e-4)

    def test_score_hand_computed_with_proposal_open(self):
        # same deal + open: x = -0.99 + 1.4 = 0.41
        # prob = 1/(1+e^-0.41) = 1/1.66365 = 0.6011  (an open is worth ~2.2x here)
        s = close_prob.score_deal({"id": "d1", "name": "Acme HVAC", "value": 900, "updated": ""},
                                  {"acmehvac"})
        assert s["factors"]["had_proposal_open"] is True
        assert s["prob"] == pytest.approx(0.6011, abs=5e-4)

    def test_build_expected_value_hand_computed(self):
        deals = [{"id": "a", "name": "Acme HVAC", "value": 900, "updated": ""},
                 {"id": "b", "name": "Legacy Corp", "value": 12000, "updated": ""}]
        r = close_prob.build(deals, set())
        # b: value_factor 0.25 -> x = 0.55 + 0.225 - 2.35 = -1.575 -> prob 0.1715
        # expected = 0.2709*900 + 0.1715*12000 = 243.81 + 2058.00 = 2301.81
        assert r["pipeline_value"] == pytest.approx(12900.0)
        assert r["expected_value"] == pytest.approx(2301.81, abs=2.0)
        assert [d["id"] for d in r["deals"]] == ["a", "b"]  # sorted prob desc

    def test_build_empty_pipeline_is_zero_not_crash(self):
        r = close_prob.build([], set())
        assert r["deal_count"] == 0 and r["pipeline_value"] == 0 and r["expected_value"] == 0

    def test_proposal_open_index_reads_only_real_opens(self, tmp_path, monkeypatch):
        p = tmp_path / "proposals.jsonl"
        p.write_text(json.dumps({"company": "Acme HVAC", "opens": 2}) + "\n"
                     + json.dumps({"company": "Never Opened", "opens": 0}) + "\n"
                     + "{corrupt line\n"
                     + json.dumps({"name": "Bright Salon", "opens": 1}) + "\n")
        monkeypatch.setattr(close_prob, "PROPOSALS", p)
        idx = close_prob._proposal_open_index()
        assert idx == {"acmehvac", "brightsalon"}

    def test_proposal_open_index_missing_file_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(close_prob, "PROPOSALS", tmp_path / "nope.jsonl")
        assert close_prob._proposal_open_index() == set()


# ------------------------------------------- a_win + the ledger (D8 item #2)
@pytest.fixture()
def win_capture(monkeypatch):
    """Capture what a_win POSTs to /api/ledger without any network or server."""
    posted = []

    def fake_urlopen(req, timeout=None, **kw):
        posted.append(json.loads(req.data))

        class _R:
            def read(self):
                return b"{}"
        return _R()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return posted


@pytest.fixture()
def ledger_sandbox(tmp_path, monkeypatch):
    (tmp_path / "store").mkdir()
    monkeypatch.setattr(server, "ROOT", tmp_path)
    return tmp_path


class TestAWinGuards:
    def test_non_numeric_amount_never_reaches_ledger(self, win_capture):
        out = commander.a_win({"amount": "a lot", "note": "x"})
        assert "how much" in out  # asks for a number instead of writing garbage
        assert win_capture == []  # ledger untouched

    def test_zero_or_missing_amount_never_reaches_ledger(self, win_capture):
        assert "how much" in commander.a_win({"amount": 0})
        assert "how much" in commander.a_win({})
        assert "how much" in commander.a_win({"amount": None})
        assert win_capture == []

    def test_valid_win_posts_kind_won_with_float_amount(self, win_capture):
        out = commander.a_win({"amount": "2500", "note": "Acme closed"})
        assert "$2,500" in out
        assert win_capture == [{"kind": "won", "amount": 2500.0, "note": "Acme closed"}]

    def test_note_is_truncated_to_200(self, win_capture):
        commander.a_win({"amount": 1, "note": "n" * 500})
        assert len(win_capture[0]["note"]) == 200

    def test_negative_amount_rejected_by_guard(self, win_capture):
        # was BUG (found by this sweep, FIXED same day): `if not amt:` let a
        # typo'd -500 through to the ledger and silently shrank the plan bar.
        # Guard is now `not math.isfinite(amt) or amt <= 0`.
        assert "positive" in commander.a_win({"amount": -500, "note": "typo'd"})
        assert win_capture == []

    def test_nan_and_inf_rejected_by_guard(self, win_capture):
        # was BUG (FIXED same day): float("nan") parsed and NaN is truthy, so one
        # NaN row poisoned the whole ledger total forever. inf behaved the same.
        assert "positive" in commander.a_win({"amount": "nan", "note": "poison"})
        assert "positive" in commander.a_win({"amount": "inf", "note": "poison"})
        assert win_capture == []


class TestLedgerTotal:
    def test_total_sums_wins_and_survives_corrupt_line(self, ledger_sandbox):
        server._ledger_add("won", 2500, "Acme")
        (ledger_sandbox / "store" / "ledger.jsonl").open("a").write("{corrupt\n")
        server._ledger_add("payment", 1000, "Bright Salon")
        r = server.api_ledger()
        assert r["total"] == pytest.approx(3500.0)  # bad line skipped, not zeroing all
        assert len(r["rows"]) == 2

    def test_non_numeric_amount_row_skipped_not_crash(self, ledger_sandbox):
        server._ledger_add("won", 100, "ok")
        (ledger_sandbox / "store" / "ledger.jsonl").open("a").write(
            json.dumps({"ts": "t", "kind": "won", "amount": "lots"}) + "\n")
        assert server.api_ledger()["total"] == pytest.approx(100.0)

    def test_negative_amount_still_sums_but_guard_upstream_blocks_it(self, ledger_sandbox):
        # negatives are blocked at a_win (the human entry point); a direct ledger
        # append with a negative still sums (deliberate: refunds/corrections stay
        # possible via API). This pins that split so changing it is conscious.
        server._ledger_add("won", 2500, "real win")
        server._ledger_add("won", -500, "manual correction")
        assert server.api_ledger()["total"] == pytest.approx(2000.0)

    def test_nan_row_no_longer_poisons_the_total(self, ledger_sandbox):
        # was BUG (FIXED same day): json round-trips NaN, float(NaN) raises nothing,
        # so one NaN row made the total NaN for every later read. api_ledger now
        # skips non-finite amounts (defense in depth behind the a_win guard).
        server._ledger_add("won", 2500, "real win")
        server._ledger_add("won", float("nan"), "poison")
        assert server.api_ledger()["total"] == pytest.approx(2500.0)

    def test_ledger_add_rejects_nonfinite_and_reports_false(self, ledger_sandbox):
        # CX21 (2026-07-13 hunt): non-finite amounts must be rejected AT THE WRITE, not
        # merely filtered back out on read -- _ledger_add now returns a bool so a caller
        # (api_ledger_add) can tell success from silent rejection.
        assert server._ledger_add("won", float("nan"), "poison") is False
        assert server._ledger_add("won", float("inf"), "poison") is False
        assert server.api_ledger()["rows"] == []  # neither row landed

    def test_ledger_add_returns_true_on_real_write(self, ledger_sandbox):
        assert server._ledger_add("won", 2500, "Acme") is True
        assert server.api_ledger()["total"] == pytest.approx(2500.0)

    def test_api_ledger_add_surfaces_nonfinite_as_ok_false(self, ledger_sandbox):
        # R2-50 companion: the ROUTE must not swallow the rejection and report ok:true.
        b = server.LedgerAdd(kind="won", amount=float("nan"), note="poison")
        r = server.api_ledger_add(b)
        assert r["ok"] is False
        assert "finite" in r["error"]
        assert server.api_ledger()["rows"] == []

    def test_api_ledger_add_ok_true_on_real_write(self, ledger_sandbox):
        b = server.LedgerAdd(kind="won", amount=1500, note="Bright Salon")
        r = server.api_ledger_add(b)
        assert r == {"ok": True}
        assert server.api_ledger()["total"] == pytest.approx(1500.0)
