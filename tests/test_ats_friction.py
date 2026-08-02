"""agents/ats_friction.py — ATS detection, learned walled-rate, divert (2026-07-15)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import ats_friction as af  # noqa: E402


class TestDetectAts:
    def test_host_based(self):
        assert af.detect_ats("https://boards.greenhouse.io/acme/jobs/123") == "greenhouse"
        assert af.detect_ats("https://job-boards.greenhouse.io/acme/jobs/1") == "greenhouse"
        assert af.detect_ats("https://jobs.lever.co/acme/abc") == "lever"
        assert af.detect_ats("https://jobs.ashbyhq.com/acme/x") == "ashby"
        assert af.detect_ats("https://acme.myworkdayjobs.com/careers/job/1") == "workday"
        assert af.detect_ats("https://apply.workable.com/acme/j/AB") == "workable"
        assert af.detect_ats("https://acme.applytojob.com/apply/x") == "jazzhr"
        assert af.detect_ats("https://acme.breezy.hr/p/abc") == "breezy"

    def test_source_fallback_when_url_unknown(self):
        # a board-listing URL that redirects: host is unknown, source names the ATS
        assert af.detect_ats("https://weworkremotely.com/listing/x", "ashby") == "ashby"
        assert af.detect_ats("", "Lever") == "lever"

    def test_unknown(self):
        assert af.detect_ats("https://remoteok.com/remote-jobs/123", "remoteok") == "unknown"
        assert af.detect_ats("", "") == "unknown"
        assert af.detect_ats("not a url", "nonsense") == "unknown"

    def test_malformed_url_never_raises(self):
        assert af.detect_ats("http://[::bad", "greenhouse") == "greenhouse"

    def test_hostile_lookalike_domain_is_not_matched(self):
        # substring matching would call this greenhouse; suffix matching must not
        assert af.detect_ats("https://greenhouse.io.evil.com/apply") == "unknown"
        assert af.detect_ats("https://notlever.co.attacker.net/x") == "unknown"


class TestWalledRate:
    def test_no_history_equals_prior(self):
        assert af.walled_rate("greenhouse", []) == af._PRIOR["greenhouse"]
        assert af.walled_rate("workday", None) == af._PRIOR["workday"]

    def test_unknown_ats_uses_unknown_prior(self):
        assert af.walled_rate("some-new-ats", []) == af._PRIOR["unknown"]

    def test_history_moves_rate_toward_empirical(self):
        # 8 walled, 0 applied on jazzhr should push its rate well above its 0.45 prior
        jobs = [{"apply_url": "https://x.applytojob.com/a", "status": "skipped",
                 "reason": "captcha"} for _ in range(8)]
        r = af.walled_rate("jazzhr", jobs)
        assert r > af._PRIOR["jazzhr"]
        assert r > 0.6

    def test_clean_history_pulls_rate_down(self):
        jobs = [{"apply_url": "https://jobs.lever.co/a/%d" % i, "status": "applied"}
                for i in range(10)]
        r = af.walled_rate("lever", jobs)
        assert r < af._PRIOR["lever"]

    def test_reason_prefix_match_counts_verbose_reasons(self):
        # operator may write "captcha (image challenge)" — prefix match must still count it
        jobs = [{"apply_url": "https://x.applytojob.com/a", "status": "skipped",
                 "reason": "captcha (forced hCaptcha image challenge)"} for _ in range(6)]
        assert af.walled_rate("jazzhr", jobs) > 0.6

    def test_non_wall_skips_not_counted(self):
        # "closed"/"unqualified" are real disqualifiers, not walls
        jobs = [{"apply_url": "https://jobs.lever.co/a/%d" % i, "status": "skipped",
                 "reason": r} for i, r in enumerate(["closed", "unqualified", "missing_info"])]
        assert af.walled_rate("lever", jobs) == af._PRIOR["lever"]  # no walls, no applies -> prior


class TestShouldDivert:
    def test_no_divert_below_min_samples(self):
        # 8 walls is a lot, but under the 10-sample floor -> still no divert
        jobs = [{"apply_url": "https://x.applytojob.com/a%d" % i, "status": "skipped",
                 "reason": "captcha"} for i in range(8)]
        divert, why = af.should_divert({"apply_url": "https://x.applytojob.com/z"}, jobs)
        assert divert is False and why == ""

    def test_divert_when_earned(self):
        jobs = [{"apply_url": "https://x.applytojob.com/a%d" % i, "status": "skipped",
                 "reason": "captcha"} for i in range(11)]
        divert, why = af.should_divert({"apply_url": "https://x.applytojob.com/z"}, jobs)
        assert divert is True and "jazzhr" in why

    def test_no_divert_on_prior_alone(self):
        # workday has a high 0.70 prior but ZERO history -> never diverted on the guess
        divert, why = af.should_divert({"apply_url": "https://a.myworkdayjobs.com/x"}, [])
        assert divert is False

    def test_unknown_never_diverts_even_with_high_wall_rate(self):
        # 'unknown' pools unrelated jobs; a high aggregate rate must NOT strand them
        jobs = [{"apply_url": "https://remoteok.com/j%d" % i, "source": "remoteok",
                 "status": "skipped", "reason": "captcha"} for i in range(15)]
        divert, why = af.should_divert({"apply_url": "https://remoteok.com/z", "source": "remoteok"}, jobs)
        assert divert is False

    def test_mixed_history_below_threshold_no_divert(self):
        jobs = ([{"apply_url": "https://x.applytojob.com/a%d" % i, "status": "applied"}
                 for i in range(7)]
                + [{"apply_url": "https://x.applytojob.com/b%d" % i, "status": "skipped",
                    "reason": "captcha"} for i in range(5)])
        divert, why = af.should_divert({"apply_url": "https://x.applytojob.com/z"}, jobs)
        assert divert is False  # 5/12 = 0.42 < 0.80
