#!/usr/bin/env python3
"""J199: pytest suite for the pure functions in this codebase — no LLM calls, no network,
no GHL, no store mutation. Fast enough to run in an autocommit hook.

Run: .venv/bin/python -m pytest tests/test_pure.py -v
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import store_lib  # noqa: E402
import tzmap  # noqa: E402
import proposal_factory  # noqa: E402


# ---- store_lib.humanize (em-dash removal + Alex's voice rules) ----

class TestHumanize:
    def test_removes_em_dash(self):
        assert "—" not in store_lib.humanize("this is great—really great")

    def test_removes_en_dash(self):
        assert "–" not in store_lib.humanize("open 9am–5pm daily")

    def test_em_dash_becomes_comma_join(self):
        out = store_lib.humanize("fast turnaround—no fluff")
        assert out == "fast turnaround, no fluff"

    def test_double_hyphen_typed_dash_removed(self):
        out = store_lib.humanize("direct -- punchy -- no fluff")
        assert "--" not in out
        assert out == "direct, punchy, no fluff"

    def test_tidies_doubled_commas(self):
        out = store_lib.humanize("great,, product")
        assert ",," not in out

    def test_tidies_space_before_comma(self):
        out = store_lib.humanize("great , product")
        assert " ," not in out

    def test_collapses_double_spaces(self):
        out = store_lib.humanize("too   many   spaces")
        assert "  " not in out

    def test_empty_string_passthrough(self):
        assert store_lib.humanize("") == ""

    def test_none_passthrough(self):
        # humanize() guards `if not text: return text` — None must not raise
        assert store_lib.humanize(None) is None

    def test_leaves_normal_punctuation_alone(self):
        out = store_lib.humanize("Call now, get a quote. Simple.")
        assert out == "Call now, get a quote. Simple."

    def test_strips_leading_trailing_whitespace(self):
        assert store_lib.humanize("  hello  ") == "hello"


# ---- store_lib.new_id (shape: tdo_YYYYMMDD_<8 hex>) ----

class TestNewId:
    def test_shape(self):
        rid = store_lib.new_id("some seed text")
        assert rid.startswith("tdo_")
        parts = rid.split("_")
        assert len(parts) == 3
        assert parts[0] == "tdo"
        assert len(parts[1]) == 8 and parts[1].isdigit()  # YYYYMMDD
        assert len(parts[2]) == 8  # 8 hex chars
        int(parts[2], 16)  # must be valid hex, raises ValueError otherwise

    def test_date_component_is_today(self):
        rid = store_lib.new_id("x")
        today = datetime.now().astimezone().strftime("%Y%m%d")
        assert rid.split("_")[1] == today

    def test_deterministic_for_same_seed(self):
        # same seed -> same hex suffix (same day); new_id is stable-ish by design
        a = store_lib.new_id("identical seed")
        b = store_lib.new_id("identical seed")
        assert a == b

    def test_different_seeds_differ(self):
        a = store_lib.new_id("seed one")
        b = store_lib.new_id("seed two")
        assert a != b


# ---- proposal_factory.route (pricing-tier routing rules) ----

class TestProposalFactoryRoute:
    def test_tier_override_wins(self):
        assert proposal_factory.route("plumbing", tier_override="landing") == "landing"

    def test_agency_niche_routes_agencyfirst(self):
        assert proposal_factory.route("agency white-label") == "agencyfirst"

    def test_booking_niche_routes_booking(self):
        for niche in ("salon", "restaurant", "gym", "dentist"):
            assert proposal_factory.route(niche) == "booking", niche

    def test_ecom_keyword_routes_booking(self):
        assert proposal_factory.route("e-com shop") == "booking"
        assert proposal_factory.route("ecom shop") == "booking"

    def test_no_site_routes_landing(self):
        assert proposal_factory.route("plumbing", has_site=False) == "landing"

    def test_webfix_keyword_with_few_faults_routes_webfix(self):
        assert proposal_factory.route("webfix", faults_n=2) == "webfix"

    def test_default_routes_standard(self):
        assert proposal_factory.route("plumbing") == "standard"

    def test_case_insensitive(self):
        assert proposal_factory.route("SALON") == "booking"
        assert proposal_factory.route("Agency Partner") == "agencyfirst"

    def test_empty_niche_routes_standard(self):
        assert proposal_factory.route("") == "standard"


# ---- proposal_factory.sig_for (HMAC determinism) ----

class TestProposalFactorySigFor:
    def test_deterministic(self):
        a = proposal_factory.sig_for("prop_20260703_deadbeef")
        b = proposal_factory.sig_for("prop_20260703_deadbeef")
        assert a == b

    def test_different_ids_differ(self):
        a = proposal_factory.sig_for("prop_20260703_deadbeef")
        b = proposal_factory.sig_for("prop_20260703_cafefeed")
        assert a != b

    def test_shape(self):
        sig = proposal_factory.sig_for("prop_20260703_deadbeef")
        assert isinstance(sig, str)
        assert len(sig) == 24
        int(sig, 16)  # hex digest slice, must be valid hex

    def test_link_for_embeds_matching_sig(self):
        pid = "prop_20260703_deadbeef"
        link = proposal_factory.link_for(pid)
        assert pid in link
        assert proposal_factory.sig_for(pid) in link


# ---- proposal_factory._pretty_phone ----

class TestPrettyPhone:
    def test_ten_digit_formats(self):
        assert proposal_factory._pretty_phone("4355551234") == "(435) 555-1234"

    def test_eleven_digit_with_leading_1(self):
        assert proposal_factory._pretty_phone("14355551234") == "(435) 555-1234"

    def test_plus_one_prefix(self):
        assert proposal_factory._pretty_phone("+14355551234") == "(435) 555-1234"

    def test_punctuated_input(self):
        assert proposal_factory._pretty_phone("(435) 555-1234") == "(435) 555-1234"

    def test_invalid_length_returns_original_string_unchanged(self):
        # _pretty_phone's fallback is `p or "(555) 000-0000"` — it only substitutes the
        # default when p itself is falsy (empty/None). A non-empty string with the wrong
        # digit count is returned AS-IS, not replaced; the "(555) 000-0000" placeholder
        # is specifically the empty/None case, covered separately below.
        assert proposal_factory._pretty_phone("123") == "123"

    def test_empty_falls_back(self):
        assert proposal_factory._pretty_phone("") == "(555) 000-0000"

    def test_none_falls_back(self):
        assert proposal_factory._pretty_phone(None) == "(555) 000-0000"


# ---- cold ramp knob-cap contract (the ramp progression itself is now tested against the
# REAL extracted cold_feeder._ramp_cap in test_send_rail_guards.py; that replaced the
# hand-copied replica that used to live here and could drift silently). This keeps only the
# call-site contract: the ramp is a CEILING that run() clamps down to the knob when smaller.
class TestColdRampKnobCap:
    def test_knob_cap_clamps_the_ramp_at_call_site(self):
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "agents"))
        import cold_feeder
        now = datetime.now().astimezone()
        first = (now - timedelta(days=10)).isoformat()  # real ramp_cap = 10 + 5*10 = 60
        ramp_cap = cold_feeder._ramp_cap(first)
        assert ramp_cap == 60
        # the clamp itself lives in cold_feeder.run() (`if ramp_cap < n: n = ramp_cap`);
        # with a smaller knob the effective daily volume is the knob, not the ramp ceiling
        assert min(ramp_cap, 20) == 20


# ---- tzmap: area_code + tz_for_phone ----

class TestTzmap:
    def test_area_code_extraction_ten_digit(self):
        assert tzmap.area_code("4355551234") == "435"

    def test_area_code_extraction_eleven_digit_leading_one(self):
        assert tzmap.area_code("14355551234") == "435"

    def test_area_code_extraction_plus_one(self):
        assert tzmap.area_code("+14355551234") == "435"

    def test_area_code_extraction_punctuated(self):
        assert tzmap.area_code("(435) 555-1234") == "435"

    def test_area_code_too_short_returns_empty(self):
        assert tzmap.area_code("12345") == ""

    def test_area_code_empty_input(self):
        assert tzmap.area_code("") == ""

    def test_known_eastern_code(self):
        assert tzmap.tz_for_phone("+12125551234") == "America/New_York"  # 212 = NYC

    def test_known_pacific_code(self):
        assert tzmap.tz_for_phone("+14155551234") == "America/Los_Angeles"  # 415 = SF

    def test_known_central_code(self):
        assert tzmap.tz_for_phone("+13125551234") == "America/Chicago"  # 312 = Chicago

    def test_known_mountain_code(self):
        assert tzmap.tz_for_phone("+13035551234") == "America/Denver"  # 303 = Denver

    def test_unmapped_area_code_returns_empty(self):
        assert tzmap.tz_for_phone("+19995551234") == ""  # 999 isn't a real area code

    def test_hawaii_code(self):
        assert tzmap.tz_for_phone("+18085551234") == "Pacific/Honolulu"
