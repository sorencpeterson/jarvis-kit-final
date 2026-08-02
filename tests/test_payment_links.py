#!/usr/bin/env python3
"""Tests for tools/verify_payment_links.py, the pure parts only. No network:
head_check is always replaced with a stub. Loaded via importlib since tools/
is not on the default module path (same pattern as test_pull_reminders.py).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "verify_payment_links", ROOT / "tools" / "verify_payment_links.py")
vpl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vpl)


# ---- tier expectations: these numbers are load-bearing (pricing-tree.md) ----

class TestTierExpectations:
    def test_core_tier_keys_match_config_block(self):
        assert set(vpl.CORE_TIERS) == {"landing", "standard", "booking",
                                       "whiteglove", "webfix"}

    def test_core_prices_are_the_ladder(self):
        assert vpl.CORE_TIERS["landing"]["price"] == 800
        assert vpl.CORE_TIERS["standard"]["price"] == 1200
        assert vpl.CORE_TIERS["booking"]["price"] == 2500
        assert vpl.CORE_TIERS["whiteglove"]["price"] == 3500
        assert vpl.CORE_TIERS["webfix"]["price"] == 450

    def test_prices_agree_with_proposal_factory(self):
        import sys
        for p in (ROOT, ROOT / "app", ROOT / "agents"):
            sys.path.insert(0, str(p))
        import proposal_factory
        for key, t in vpl.CORE_TIERS.items():
            assert proposal_factory.PRICING[key]["price"] == t["price"], key

    def test_all_tiers_cover_every_config_payment_links_key(self):
        cfg_path = ROOT / "store" / "config.json"
        if not cfg_path.exists():  # fresh-install restore has no store/ yet (DR drill)
            import pytest
            pytest.skip("no store/config.json (fresh install)")
        cfg = json.loads(cfg_path.read_text())
        block = cfg.get("payment_links") or {}
        assert set(block).issubset(set(vpl.ALL_TIERS))


# ---- host allowlist / URL shape ----

class TestUrlShape:
    def test_accepts_stripe_payment_link(self):
        ok, _ = vpl.check_url_shape("https://buy.stripe.com/abc123XYZ")
        assert ok

    def test_accepts_stripe_checkout(self):
        ok, _ = vpl.check_url_shape("https://checkout.stripe.com/c/pay/cs_live_x")
        assert ok

    def test_rejects_http(self):
        ok, reason = vpl.check_url_shape("http://buy.stripe.com/abc")
        assert not ok and "https" in reason

    def test_rejects_unknown_host(self):
        ok, reason = vpl.check_url_shape("https://evil.example.com/pay")
        assert not ok and "not a known payment host" in reason

    def test_rejects_lookalike_subdomain(self):
        ok, _ = vpl.check_url_shape("https://buy.stripe.com.evil.com/abc")
        assert not ok

    def test_rejects_bare_host_no_scheme(self):
        ok, _ = vpl.check_url_shape("buy.stripe.com/abc")
        assert not ok

    def test_rejects_empty(self):
        ok, reason = vpl.check_url_shape("")
        assert not ok and reason == "empty"

    def test_host_check_is_case_insensitive(self):
        ok, _ = vpl.check_url_shape("https://BUY.STRIPE.COM/abc")
        assert ok


# ---- redirect judgement (never follow blindly) ----

class TestJudge:
    def test_200_alive(self):
        alive, _ = vpl.judge(200, "")
        assert alive

    def test_404_dead(self):
        alive, detail = vpl.judge(404, "")
        assert not alive and "404" in detail

    def test_redirect_to_allowed_host_alive(self):
        alive, _ = vpl.judge(302, "https://checkout.stripe.com/c/pay/x")
        assert alive

    def test_redirect_offsite_rejected_loudly(self):
        alive, detail = vpl.judge(302, "https://phishing.example.com/x")
        assert not alive and "REJECTED" in detail

    def test_redirect_without_location_dead(self):
        alive, _ = vpl.judge(301, "")
        assert not alive


# ---- missing-config paths ----

class TestLoadLinks:
    def test_missing_file(self, tmp_path):
        assert vpl.load_links(tmp_path / "nope.json") == {}

    def test_missing_block(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"plan": {}}))
        assert vpl.load_links(p) == {}

    def test_block_wrong_type(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"payment_links": "oops"}))
        assert vpl.load_links(p) == {}

    def test_unparseable_file(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text("{not json")
        assert vpl.load_links(p) == {}

    def test_real_block_round_trips(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"payment_links": {"standard": "https://buy.stripe.com/x"}}))
        assert vpl.load_links(p) == {"standard": "https://buy.stripe.com/x"}


class TestClassify:
    def test_empty_strings_count_as_missing(self):
        configured, missing = vpl.classify({k: "" for k in vpl.ALL_TIERS})
        assert configured == {} and set(missing) == set(vpl.ALL_TIERS)

    def test_whitespace_counts_as_missing(self):
        _, missing = vpl.classify({"standard": "   "})
        assert "standard" in missing

    def test_configured_url_is_stripped(self):
        configured, _ = vpl.classify({"standard": " https://buy.stripe.com/x "})
        assert configured["standard"] == "https://buy.stripe.com/x"


# ---- run(): exit codes with a mocked network ----

def _cfg(tmp_path, links):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"payment_links": links}))
    return p


class TestRunExitCodes:
    def test_all_missing_exits_zero(self, tmp_path):
        lines = []
        code = vpl.run(_cfg(tmp_path, {}),
                       checker=lambda u: (_ for _ in ()).throw(AssertionError("no network")),
                       out=lines.append)
        assert code == 0
        text = "\n".join(lines)
        assert "MISSING" in text and "WHAT TO DO" in text
        # the exact keys Alex pastes into config.json
        for key in vpl.CORE_TIERS:
            assert f'"{key}"' in text

    def test_alive_links_exit_zero(self, tmp_path):
        cfgp = _cfg(tmp_path, {"standard": "https://buy.stripe.com/x"})
        code = vpl.run(cfgp, checker=lambda u: (True, "HTTP 200"), out=lambda s: None)
        assert code == 0

    def test_one_dead_link_exits_nonzero(self, tmp_path):
        cfgp = _cfg(tmp_path, {"standard": "https://buy.stripe.com/x",
                               "landing": "https://buy.stripe.com/dead"})
        checker = lambda u: (False, "HTTP 404") if "dead" in u else (True, "HTTP 200")
        lines = []
        code = vpl.run(cfgp, checker=checker, out=lines.append)
        assert code == 1
        assert any("DEAD" in ln for ln in lines)

    def test_bad_host_rejected_without_network(self, tmp_path):
        cfgp = _cfg(tmp_path, {"standard": "https://evil.example.com/pay"})
        lines = []
        code = vpl.run(cfgp,
                       checker=lambda u: (_ for _ in ()).throw(AssertionError("must not hit network")),
                       out=lines.append)
        assert code == 1
        assert any("REJECTED" in ln for ln in lines)

    def test_no_em_dashes_in_output(self, tmp_path):
        lines = []
        vpl.run(_cfg(tmp_path, {}), checker=lambda u: (True, "HTTP 200"), out=lines.append)
        joined = "\n".join(lines)
        assert "—" not in joined and "–" not in joined


def test_no_em_dashes_in_tool_source():
    src = (ROOT / "tools" / "verify_payment_links.py").read_text()
    assert "—" not in src and "–" not in src
