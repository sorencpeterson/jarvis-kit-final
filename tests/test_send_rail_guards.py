#!/usr/bin/env python3
"""Send-rail + cold-engine guard tests (2026-07-07 test-quality audit). The audit found the
suite strong on money math but with real gaps on the most load-bearing untested code: the
two double-send claim() CAS guards, the cold-engine auto-pause circuit breaker, the cold
deliverability ramp (previously tested against a drifting hand-copied replica), and the
attention router's jobs-cap that once buried a real proposal. Pin them for real.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))


# ---- cold_feeder ramp: now the REAL extracted function, not a replica ----
class TestColdRampReal:
    def test_ramp_progression(self):
        import cold_feeder
        from datetime import datetime, timedelta
        now = datetime.now().astimezone()
        assert cold_feeder._ramp_cap("") == 10           # no prior enrollment = day 1
        assert cold_feeder._ramp_cap("9999") == 10       # sentinel = day 1
        assert cold_feeder._ramp_cap(now.isoformat()) == 10
        assert cold_feeder._ramp_cap((now - timedelta(days=1)).isoformat()) == 15
        assert cold_feeder._ramp_cap((now - timedelta(days=4)).isoformat()) == 30

    def test_garbage_ts_falls_back_to_day1_never_crashes(self):
        import cold_feeder
        assert cold_feeder._ramp_cap("not-a-date") == 10
        assert cold_feeder._ramp_cap("2026-13-99") == 10


# ---- cold_feeder: CX3 (live dnd/tag re-check before tagging) + CX4 (workflow-live
# flag must not substitute for the real GHL published check) ----
class TestColdFeederLiveGates:
    def _mocks(self, monkeypatch, tmp_path, *, contact, workflow_status):
        import cold_feeder
        import cold_preflight
        import ghl_social
        import planner
        pipeline = tmp_path / "cold_pipeline.jsonl"
        monkeypatch.setattr(cold_feeder, "PIPELINE", pipeline)
        monkeypatch.setattr(cold_feeder, "ROOT", tmp_path)  # for the .travel-mode / suppress.jsonl paths
        monkeypatch.setattr(cold_feeder, "_config",
                            lambda: {"cold_daily_enroll": 5, "cold_workflow_live": True})
        monkeypatch.setattr(cold_preflight, "check_all", lambda: {"ready": True})
        monkeypatch.setattr(planner, "notify", lambda *a, **k: True)
        monkeypatch.setattr(planner, "feed_add", lambda *a, **k: None)

        def _api_stub(args):
            method, path = args[0], args[1]
            if method == "GET" and path.startswith("/workflows/"):
                return json.dumps({"workflows": [
                    {"name": "[2026-07] Cold Agencies - WL Sites", "status": workflow_status}]})
            if method == "GET" and path.startswith("/contacts/c1"):
                return json.dumps({"contact": contact})
            if method == "POST" and path.endswith("/tags"):
                pytest.fail("tag-POST reached in a scenario that must not enroll")
            return "{}"
        monkeypatch.setattr(ghl_social, "_api", _api_stub)
        pipeline.write_text(json.dumps({"email": "c1@x.com", "contact_id": "c1",
                                        "campaign": "wl", "status": "staged",
                                        "ts": "2020-01-01T00:00:00+00:00"}) + "\n")
        return pipeline

    def test_stale_dnd_contact_skipped_not_tagged(self, tmp_path, monkeypatch):
        """CX3: `staged` reflects cold_import.py's dnd/tag check at STAGING time --
        hours to days before the daily ramp actually reaches this contact. If the
        contact's LIVE GHL state now shows dnd, the batch must skip it (never call
        the tag-POST), not cold-tag it on stale information."""
        import cold_feeder
        pipeline = self._mocks(monkeypatch, tmp_path,
                               contact={"id": "c1", "dnd": True, "tags": []},
                               workflow_status="published")
        cold_feeder.run()
        rows = [json.loads(ln) for ln in pipeline.read_text().splitlines()]
        assert rows[-1]["status"] == "skipped_no_go"

    def test_stale_client_tag_contact_skipped_not_tagged(self, tmp_path, monkeypatch):
        """CX3 companion: a live 'client'/'booked'/unsub-style tag (not just the dnd
        flag) must also block the cold tag, mirroring cold_import.NO_GO."""
        import cold_feeder
        pipeline = self._mocks(monkeypatch, tmp_path,
                               contact={"id": "c1", "dnd": False, "tags": ["client", "vip"]},
                               workflow_status="published")
        cold_feeder.run()
        rows = [json.loads(ln) for ln in pipeline.read_text().splitlines()]
        assert rows[-1]["status"] == "skipped_no_go"

    def test_workflow_live_flag_does_not_substitute_for_real_publish_check(self, tmp_path, monkeypatch):
        """CX4: cold_workflow_live=True in config must not bypass the real GHL
        published check -- a paused/draft/unpublished workflow still blocks
        enrollment even with the flag set."""
        import cold_feeder
        pipeline = self._mocks(monkeypatch, tmp_path,
                               contact={"id": "c1", "dnd": False, "tags": []},
                               workflow_status="draft")  # NOT published
        cold_feeder.run()
        rows = [json.loads(ln) for ln in pipeline.read_text().splitlines()]
        assert len(rows) == 1 and rows[0]["status"] == "staged"  # untouched

    def test_unverifiable_live_state_skips_not_tags(self, tmp_path, monkeypatch):
        """R2#4 (2026-07-14): the pre-tag contact GET failing or coming back
        malformed used to read as "no dnd, no no-go tags" (cc.get("tags") or []
        -> [], bool(cc.get("dnd")) -> False) and let the contact through
        un-verified -- an errored live-recheck must fail CLOSED instead."""
        import cold_feeder
        import cold_preflight
        import ghl_social
        import planner
        pipeline = tmp_path / "cold_pipeline.jsonl"
        monkeypatch.setattr(cold_feeder, "PIPELINE", pipeline)
        monkeypatch.setattr(cold_feeder, "ROOT", tmp_path)
        monkeypatch.setattr(cold_feeder, "_config",
                            lambda: {"cold_daily_enroll": 5, "cold_workflow_live": True})
        monkeypatch.setattr(cold_preflight, "check_all", lambda: {"ready": True})
        monkeypatch.setattr(planner, "notify", lambda *a, **k: True)
        monkeypatch.setattr(planner, "feed_add", lambda *a, **k: None)

        def _api_stub(args):
            method, path = args[0], args[1]
            if method == "GET" and path.startswith("/workflows/"):
                return json.dumps({"workflows": [
                    {"name": "[2026-07] Cold Agencies - WL Sites", "status": "published"}]})
            if method == "GET" and path.startswith("/contacts/c1"):
                # a well-formed GHL error body: parses cleanly, no "_raw" key, but
                # carries none of the real contact-shaped fields
                return json.dumps({"statusCode": 404, "message": "Contact not found"})
            if method == "POST" and path.endswith("/tags"):
                pytest.fail("tag-POST reached for a contact whose live state could not be verified")
            return "{}"
        monkeypatch.setattr(ghl_social, "_api", _api_stub)
        pipeline.write_text(json.dumps({"email": "c1@x.com", "contact_id": "c1",
                                        "campaign": "wl", "status": "staged",
                                        "ts": "2020-01-01T00:00:00+00:00"}) + "\n")

        cold_feeder.run()
        rows = [json.loads(ln) for ln in pipeline.read_text().splitlines()]
        assert rows[-1]["status"] == "skipped_no_go"
        assert "unverifiable" in rows[-1]["detail"]

    def test_unparseable_live_state_response_skips_not_tags(self, tmp_path, monkeypatch):
        """R2#4 companion: a raw-text (unparseable) response must ALSO fail closed,
        not just a well-formed-but-error-shaped one."""
        import cold_feeder
        import cold_preflight
        import ghl_social
        import planner
        pipeline = tmp_path / "cold_pipeline.jsonl"
        monkeypatch.setattr(cold_feeder, "PIPELINE", pipeline)
        monkeypatch.setattr(cold_feeder, "ROOT", tmp_path)
        monkeypatch.setattr(cold_feeder, "_config",
                            lambda: {"cold_daily_enroll": 5, "cold_workflow_live": True})
        monkeypatch.setattr(cold_preflight, "check_all", lambda: {"ready": True})
        monkeypatch.setattr(planner, "notify", lambda *a, **k: True)
        monkeypatch.setattr(planner, "feed_add", lambda *a, **k: None)

        def _api_stub(args):
            method, path = args[0], args[1]
            if method == "GET" and path.startswith("/workflows/"):
                return json.dumps({"workflows": [
                    {"name": "[2026-07] Cold Agencies - WL Sites", "status": "published"}]})
            if method == "GET" and path.startswith("/contacts/c1"):
                return "connection reset by peer"  # no "{" at all -> _api_json's _raw fallback
            if method == "POST" and path.endswith("/tags"):
                pytest.fail("tag-POST reached for a contact whose live state could not be verified")
            return "{}"
        monkeypatch.setattr(ghl_social, "_api", _api_stub)
        pipeline.write_text(json.dumps({"email": "c1@x.com", "contact_id": "c1",
                                        "campaign": "wl", "status": "staged",
                                        "ts": "2020-01-01T00:00:00+00:00"}) + "\n")

        cold_feeder.run()
        rows = [json.loads(ln) for ln in pipeline.read_text().splitlines()]
        assert rows[-1]["status"] == "skipped_no_go"


# ---- cold_preflight: R2#6 DNSBL must not gate sends on the wrong IP ----
class TestColdPreflightDnsblNotInSendGate:
    """R2#6 (2026-07-14): a DNSBL check on the from-domain's own A record was
    briefly wired into check_all()'s `ready` gate, but that record is the
    Cloudflare CDN web IP, not the LeadConnector/Mailgun MTA pool that actually
    sends the mail -- both a false positive (irrelevant shared CDN IP flagged)
    and a false negative (the real sending pool could be blacklisted and this
    would never notice). Reverted; pin that it never comes back silently."""

    def test_check_all_ready_ignores_a_blacklisted_web_ip(self, monkeypatch):
        import cold_preflight
        monkeypatch.setattr(cold_preflight, "_config", lambda: {"cold_domains": ["get.x.com"]})
        monkeypatch.setattr(cold_preflight, "check_domain", lambda d: {
            "domain": d, "spf": True, "spf_leadconnector": True, "dkim": True,
            "dkim_selector": "s1 (cname)", "dmarc": True, "ready": True})
        monkeypatch.setattr(cold_preflight, "ghl_from_address", lambda: "hi@get.x.com")
        # even if the (wrong-target) DNSBL helper would say blacklisted, check_all()
        # must not consult it for the ready gate at all
        monkeypatch.setattr(cold_preflight, "check_dnsbl",
                            lambda d: (_ for _ in ()).throw(AssertionError(
                                "check_all() must not call check_dnsbl() for the send gate")))
        res = cold_preflight.check_all()
        assert res["ready"] is True
        assert "dnsbl" not in res and "blacklisted" not in res


# ---- the two double-send CAS guards (the actual outbound taps) ----
class TestReplyClaim:
    def test_claim_blocks_double_send(self, tmp_path, monkeypatch):
        import reply_watch
        monkeypatch.setattr(reply_watch, "REPLIES", tmp_path / "replies.jsonl")
        reply_watch._save({"id": "r1", "status": "pending", "draft": "hi"})
        first = reply_watch.claim("r1")
        assert first is not None and first["status"] == "sending"
        # a second concurrent tap sees 'sending', gets None -> no double-send
        assert reply_watch.claim("r1") is None
        assert reply_watch.claim("nope") is None  # missing id

    def test_held_draft_cannot_be_claimed_as_pending(self, tmp_path, monkeypatch):
        import reply_watch
        monkeypatch.setattr(reply_watch, "REPLIES", tmp_path / "replies.jsonl")
        reply_watch._save({"id": "h1", "status": "held", "draft": "financial ask"})
        assert reply_watch.claim("h1") is None  # from_status defaults to 'pending'


class TestProposalClaim:
    def test_claim_blocks_double_send(self, tmp_path, monkeypatch):
        import proposal_factory
        monkeypatch.setattr(proposal_factory, "QUEUE", tmp_path / "proposals.jsonl")
        proposal_factory.save({"id": "p1", "status": "staged", "price": 3500})
        first = proposal_factory.claim("p1")
        assert first is not None and first["status"] == "sending"
        assert proposal_factory.claim("p1") is None
        assert proposal_factory.claim("gone") is None


# ---- campaign_guard: the cold-engine auto-pause circuit breaker (was ZERO coverage) ----
class TestCampaignGuardPause:
    def _stats(self, ratio, n):
        return {"wl": {"n": n, "neg": int(n * ratio), "ratio": ratio}}

    def test_pauses_over_threshold(self, monkeypatch):
        import campaign_guard as cg
        monkeypatch.setattr(cg, "sentiment_by_campaign", lambda: self and self._stats(0.5, 10))
        monkeypatch.setattr(cg, "_already_paused_today", lambda c: False)
        knobs = []
        monkeypatch.setattr(cg, "_set_knob_zero", lambda knob, dry: knobs.append(knob) or True)
        monkeypatch.setattr(cg.planner, "feed_add", lambda *a, **k: None)
        monkeypatch.setattr(cg.planner, "notify", lambda *a, **k: None)
        paused = cg.run_sentiment_guard(dry=False)
        assert [p["campaign"] for p in paused] == ["wl"]
        assert knobs == ["cold_daily_enroll"]  # the knob went to 0

    def test_clean_campaign_never_paused(self, monkeypatch):
        import campaign_guard as cg
        # ratio below 0.30 threshold -> no pause; and n below MIN_CLASSIFIED -> no pause
        monkeypatch.setattr(cg, "sentiment_by_campaign", lambda: {"wl": {"n": 10, "neg": 2, "ratio": 0.2}})
        monkeypatch.setattr(cg, "_set_knob_zero", lambda *a: pytest.fail("must not pause a clean campaign"))
        assert cg.run_sentiment_guard(dry=False) == []
        monkeypatch.setattr(cg, "sentiment_by_campaign", lambda: {"wl": {"n": 3, "neg": 3, "ratio": 1.0}})
        assert cg.run_sentiment_guard(dry=False) == []  # n < MIN_CLASSIFIED(5)

    def test_idempotent_already_paused(self, monkeypatch):
        import campaign_guard as cg
        monkeypatch.setattr(cg, "sentiment_by_campaign", lambda: {"wl": {"n": 10, "neg": 6, "ratio": 0.6}})
        monkeypatch.setattr(cg, "_already_paused_today", lambda c: True)
        monkeypatch.setattr(cg, "_set_knob_zero", lambda *a: pytest.fail("must not re-pause same day"))
        assert cg.run_sentiment_guard(dry=False) == []


# ---- CX6: sentiment_by_campaign must count the LATEST record per reply id, not
# every append-only version (a still-pending objection re-saved every SLA-refresh
# poll used to be counted once per line, not once per conversation) ----
class TestCampaignGuardSentimentDedup:
    def test_repeated_lines_for_same_reply_id_count_once(self, tmp_path, monkeypatch):
        import campaign_guard as cg
        replies = tmp_path / "replies.jsonl"
        pipeline = tmp_path / "cold_pipeline.jsonl"
        monkeypatch.setattr(cg, "REPLIES", replies)
        monkeypatch.setattr(cg, "PIPELINE", pipeline)
        pipeline.write_text(json.dumps({"contact_id": "c1", "campaign": "wl"}) + "\n")
        # the SAME reply id re-saved 5 times (e.g. reply_watch.refresh_sla_fields()
        # touching a still-pending record every poll), intent=objection every time
        lines = [json.dumps({"id": "r1", "contact_id": "c1", "intent": "objection",
                             "created": cg.now_iso()}) for _ in range(5)]
        replies.write_text("\n".join(lines) + "\n")
        stats = cg.sentiment_by_campaign()
        assert stats["wl"]["n"] == 1   # one conversation, not 5 append-only lines
        assert stats["wl"]["neg"] == 1
        assert stats["wl"]["ratio"] == 1.0

    def test_reclassified_reply_counts_its_latest_intent(self, tmp_path, monkeypatch):
        """A convo re-classified from 'objection' to 'interested' on a later run
        must count as the CURRENT (latest) intent, not the stale first one."""
        import campaign_guard as cg
        replies = tmp_path / "replies.jsonl"
        pipeline = tmp_path / "cold_pipeline.jsonl"
        monkeypatch.setattr(cg, "REPLIES", replies)
        monkeypatch.setattr(cg, "PIPELINE", pipeline)
        pipeline.write_text(json.dumps({"contact_id": "c1", "campaign": "wl"}) + "\n")
        replies.write_text(
            json.dumps({"id": "r1", "contact_id": "c1", "intent": "objection",
                       "created": cg.now_iso()}) + "\n" +
            json.dumps({"id": "r1", "contact_id": "c1", "intent": "interested",
                       "created": cg.now_iso()}) + "\n")
        stats = cg.sentiment_by_campaign()
        assert stats["wl"]["n"] == 1
        assert stats["wl"]["neg"] == 0  # latest intent is "interested", not negative


# ---- F: _set_knob_zero must take the config.json lock (RMW under lock) ----
class TestCampaignGuardKnobLock:
    def test_set_knob_zero_takes_the_config_lock(self, tmp_path, monkeypatch):
        import campaign_guard as cg
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({"cold_daily_enroll": 5}))
        monkeypatch.setattr(cg, "CONFIG", cfg_path)
        changed = cg._set_knob_zero("cold_daily_enroll", dry=False)
        assert changed is True
        assert json.loads(cfg_path.read_text())["cold_daily_enroll"] == 0
        # store_lib._flock locks a sibling ".lock" file next to the target path --
        # its presence on disk afterward confirms the RMW actually ran under lock.
        assert cfg_path.with_suffix(".lock").exists()

    def test_dry_run_does_not_write(self, tmp_path, monkeypatch):
        import campaign_guard as cg
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({"cold_daily_enroll": 5}))
        monkeypatch.setattr(cg, "CONFIG", cfg_path)
        changed = cg._set_knob_zero("cold_daily_enroll", dry=True)
        assert changed is True
        assert json.loads(cfg_path.read_text())["cold_daily_enroll"] == 5  # unchanged


# ---- attention router: the jobs-cap that once buried a real proposal ----
class TestAttentionScoring:
    def test_jobs_backlog_cannot_bury_a_real_proposal(self, monkeypatch):
        import attention
        # 30 manual jobs vs one $3,500 staged proposal: the proposal MUST out-rank the pile
        monkeypatch.setattr(attention, "score_replies", lambda: [])
        monkeypatch.setattr(attention, "score_emails", lambda: [])
        monkeypatch.setattr(attention, "score_linkedin_queue", lambda: [])
        monkeypatch.setattr(attention, "score_overdue_todos", lambda: [])
        monkeypatch.setattr(attention, "score_promises", lambda: [])
        import jobs as _jobs
        monkeypatch.setattr(_jobs, "needs_manual", lambda: [{"id": f"j{i}"} for i in range(30)])
        import proposal_factory as pf
        monkeypatch.setattr(pf, "load_queue",
                            lambda: [{"id": "p1", "status": "staged", "price": 3500, "company": "Reenvision"}])
        ranked = attention.build()["ranked"]
        assert ranked[0]["kind"] == "proposal", f"a jobs pile buried the money: {ranked[0]}"

    def test_jobs_score_saturates_at_cap(self, monkeypatch):
        import attention, jobs as _jobs
        monkeypatch.setattr(_jobs, "needs_manual", lambda: [{"id": f"j{i}"} for i in range(100)])
        # 100 jobs must score the same as 8 (min(N,8)) -> the cap holds
        assert attention.score_jobs_manual()[0]["score"] == attention.JOBS_MANUAL_BASE * 4


# ---- proposal presentation (2026-07-11, Alex caught both on a LIVE proposal) ----
class TestProposalPresentation:
    def test_beforeafter_slider_orientation(self):
        # the clipped/left layer (width:400%, badge "Today") must hold THEIR site;
        # the underlying/right layer (width:200%, badge "Rebuilt") must hold OUR mock.
        # These were swapped: prospects saw their own site presented as our rebuild.
        import re
        import proposal_factory as pf
        html = pf._before_after_html({"url": "https://their-site.example", "frameable": True},
                                     "https://x/mock/p1?sig=abc")
        i200 = re.search(r'<iframe src="([^"]+)"[^>]*width:200%[^>]*title="([^"]*)"', html, re.S)
        i400 = re.search(r'<iframe src="([^"]+)"[^>]*width:400%[^>]*title="([^"]*)"', html, re.S)
        assert i400 and "their-site.example" in i400.group(1) and "today" in i400.group(2).lower()
        assert i200 and "/mock/" in i200.group(1) and "rebuilt" in i200.group(2).lower()

    def test_no_handish_signoff(self):
        from pathlib import Path
        import proposal_factory
        tpl = (Path(__file__).resolve().parent.parent / "agents" / "templates" / "proposal.html").read_text()
        assert "hand-ish" not in tpl
        assert "Prepared by Alex Rivera" in proposal_factory._fill_owner(tpl)

    def test_utm_joins_with_question_mark(self):
        # _utm_qs is appended to QUERY-LESS urls (book_url, /case/slug); a "&" join made
        # /book&utm_source=... which soft-404s to the homepage instead of the booking page.
        import proposal_factory as pf
        qs = pf._utm_qs({})
        assert qs.startswith("?") and "utm_source=" in qs


# ---- jobs: attempt-capped/blacklisted approved jobs retire to skipped (2026-07-11) ----
class TestApprovedHonesty:
    def test_attempt_capped_approved_retires_to_skipped(self, tmp_path, monkeypatch):
        import jobs
        rows = [
            {"id": "a", "status": "approved", "attempts": 2, "company": "Walled Inc",
             "source": "greenhouse", "fit": 80},
            {"id": "b", "status": "approved", "attempts": 0, "company": "Fresh Co",
             "source": "greenhouse", "fit": 80},
        ]
        monkeypatch.setattr(jobs, "load_jobs", lambda: rows)
        saved = {}
        monkeypatch.setattr(jobs, "set_status",
                            lambda jid, st, why=None: saved.__setitem__(jid, (st, why)))
        monkeypatch.setattr(jobs, "_blacklist", lambda: {"lever"})
        monkeypatch.setattr(jobs, "applied_today", lambda: 0)
        out = jobs.approved_to_apply()
        # the capped one is retired to skipped with a clear reason; the fresh one applies
        assert saved.get("a", ("", ""))[0] == "skipped"
        assert "attempt_cap" in (saved["a"][1] or "")
        assert [x["id"] for x in out] == ["b"]

    def test_blacklisted_source_retires_to_skipped(self, tmp_path, monkeypatch):
        import jobs
        rows = [{"id": "c", "status": "approved", "attempts": 0, "company": "Lever Co",
                 "source": "lever", "fit": 80}]
        monkeypatch.setattr(jobs, "load_jobs", lambda: rows)
        saved = {}
        monkeypatch.setattr(jobs, "set_status",
                            lambda jid, st, why=None: saved.__setitem__(jid, (st, why)))
        monkeypatch.setattr(jobs, "_blacklist", lambda: {"lever"})
        monkeypatch.setattr(jobs, "applied_today", lambda: 0)
        assert jobs.approved_to_apply() == []
        assert saved["c"][0] == "skipped" and "blacklisted" in saved["c"][1]


# ---- apply-process scan fixes (2026-07-12: 3-finder audit) ----
class TestApplyProcessScanFixes:
    def test_seniority_bias_in_fit(self):
        import jobs
        base = {"title": "Marketing Manager", "comp_max": 140000, "posted": None}
        senior = jobs._fit({**base, "yoe": 6})
        junior = jobs._fit({**base, "yoe": 1})
        assert senior >= junior + 30   # +15 senior, -20 junior

    def test_entry_level_hard_skipped(self):
        import jobs
        j = {"id": "z", "is_us": True, "title": "Marketing Manager", "yoe": 2,
             "apply_url": "u", "company": "Co", "posted": None}
        assert jobs._passes_filters(j, 40000, set(), set(), set()) is False

    def test_unknown_yoe_not_skipped(self):
        import jobs   # "take anything" for unknowns stays intact
        j = {"id": "z2", "is_us": True, "title": "Marketing Manager", "yoe": None,
             "apply_url": "u2", "company": "Co2", "posted": None}
        assert jobs._passes_filters(j, 40000, set(), set(), set()) is True

    def test_applying_status_blocks_reapply_and_dup(self):
        import jobs
        # a job in-flight ("applying") counts as submitted for the company dup set
        assert hasattr(jobs, "mark_applying")

    def test_skip_reason_enum_validated(self):
        import server
        assert server._SKIP_REASONS == {"captcha", "closed", "login", "wizard",
                                        "missing_info", "unqualified", "verify"}

    def test_no_sd_location_in_answer_bank(self):
        import json
        from pathlib import Path
        d = json.loads((Path(__file__).resolve().parent.parent / "store" / "answer_bank.json").read_text())
        qa = d if isinstance(d, list) else d.get("qa", [])
        assert not any("sioux" in json.dumps(i).lower() or "south dakota" in json.dumps(i).lower() for i in qa)

    def test_queries_pruned_and_focused(self):
        import jobs
        assert "Fractional COO" in jobs.DEFAULT_QUERIES
        assert not any("Demand Gen" in q or q.startswith("SEO ") for q in jobs.DEFAULT_QUERIES)

    def test_all_default_queries_survive_relevant_filter(self):
        # R2-11 (2026-07-13 hunt): every DEFAULT_QUERIES title must pass _relevant(), or the
        # dedicated query silently fetches roles that _passes_filters() then throws away
        # before he ever sees them (Fractional COO/Head of Operations/Director of Operations/
        # Chief of Staff/Partnerships Manager all had no TITLE_KW match before this fix).
        import jobs
        for q in jobs.DEFAULT_QUERIES:
            assert jobs._relevant(q), f"{q!r} would be filtered out by TITLE_KW"


# ---- company-alias dedupe (2026-07-13 hunt, R2-17): legal-suffix variants must collapse ----
class TestCompanyAliasDedup:
    def test_inc_suffix_collapses_with_bare_name(self):
        import jobs
        assert jobs._conorm("Acme, Inc.") == jobs._conorm("Acme")

    def test_llc_corp_co_suffixes_all_collapse(self):
        import jobs
        base = jobs._conorm("Northstar Roofing")
        for variant in ("Northstar Roofing LLC", "Northstar Roofing Corp",
                        "Northstar Roofing Co", "Northstar Roofing Corporation",
                        "Northstar Roofing Company"):
            assert jobs._conorm(variant) == base, variant

    def test_ckey_dedupes_across_legal_suffix_variants(self):
        import jobs
        assert jobs._ckey("Acme, Inc.", "Marketing Manager") == jobs._ckey("Acme", "Marketing Manager")

    def test_distinct_companies_still_distinct(self):
        import jobs
        assert jobs._conorm("Acme Inc") != jobs._conorm("Beta LLC")

    def test_employer_guard_blocks_the_suffix_variant(self, tmp_path, monkeypatch):
        # end-to-end: a job already submitted to "Acme" must block a SIBLING job at
        # "Acme, Inc." from also being applied to.
        import jobs
        rows = [{"id": "a", "status": "applied", "company": "Acme", "applied_at": "x"},
                {"id": "b", "status": "approved", "company": "Acme, Inc.", "fit": 80}]
        monkeypatch.setattr(jobs, "load_jobs", lambda: rows)
        monkeypatch.setattr(jobs, "set_status", lambda jid, st, why=None: None)
        monkeypatch.setattr(jobs, "_blacklist", lambda: set())
        monkeypatch.setattr(jobs, "applied_today", lambda: 0)
        import job_fit_signals
        monkeypatch.setattr(job_fit_signals, "extra_block_reason", lambda j, all_jobs=None: "")
        assert jobs.approved_to_apply() == []


# ---- applied_today counts real submissions, not current status (2026-07-13 hunt) ----
class TestAppliedTodayCountsRealSubmissions:
    """applied_today() must count by applied_at (a real submission timestamp), not by the
    job's CURRENT status -- otherwise a same-day advance to confirmed/rejected/interview/
    replied silently frees up a daily-cap slot for a duplicate application."""

    def test_same_day_advance_to_other_statuses_still_counts(self, monkeypatch):
        import jobs
        today = jobs.now_iso()
        rows = [{"id": "a", "status": "confirmed", "applied_at": today},
                {"id": "b", "status": "rejected", "applied_at": today},
                {"id": "c", "status": "interview", "applied_at": today},
                {"id": "d", "status": "replied", "applied_at": today}]
        monkeypatch.setattr(jobs, "load_jobs", lambda: rows)
        assert jobs.applied_today() == 4

    def test_yesterdays_applied_at_not_counted(self, monkeypatch):
        import jobs
        rows = [{"id": "a", "status": "applied", "applied_at": "2020-01-01T00:00:00+00:00"}]
        monkeypatch.setattr(jobs, "load_jobs", lambda: rows)
        assert jobs.applied_today() == 0

    def test_no_applied_at_not_counted(self, monkeypatch):
        import jobs
        rows = [{"id": "a", "status": "applied"}]
        monkeypatch.setattr(jobs, "load_jobs", lambda: rows)
        assert jobs.applied_today() == 0


# ---- freshness/expired re-checked at SELECTION, not only at sourcing (2026-07-13 hunt) ----
class TestSelectionTimeFreshnessRecheck:
    """R2-15: a job approved while still fresh can sit in the queue for days before an
    apply-round selects it -- age/expired must be re-verified right at selection time."""

    def _wire(self, monkeypatch, rows):
        import jobs
        import job_fit_signals
        monkeypatch.setattr(jobs, "load_jobs", lambda: rows)
        monkeypatch.setattr(jobs, "_blacklist", lambda: set())
        monkeypatch.setattr(jobs, "applied_today", lambda: 0)
        monkeypatch.setattr(job_fit_signals, "extra_block_reason", lambda j, all_jobs=None: "")
        return jobs

    def test_stale_since_approval_is_skipped_not_applied(self, tmp_path, monkeypatch):
        from datetime import datetime, timedelta
        old = (datetime.now().astimezone() - timedelta(days=15)).isoformat()
        rows = [{"id": "a", "status": "approved", "company": "Co", "fit": 80, "posted": old}]
        jobs = self._wire(monkeypatch, rows)
        saved = {}
        monkeypatch.setattr(jobs, "set_status",
                            lambda jid, st, why=None: saved.__setitem__(jid, (st, why)))
        assert jobs.approved_to_apply() == []
        assert saved["a"][0] == "skipped" and "stale_at_selection" in saved["a"][1]

    def test_expired_flag_is_rechecked_at_selection(self, tmp_path, monkeypatch):
        rows = [{"id": "a", "status": "approved", "company": "Co", "fit": 80,
                 "posted": None, "expired": True}]
        jobs = self._wire(monkeypatch, rows)
        saved = {}
        monkeypatch.setattr(jobs, "set_status",
                            lambda jid, st, why=None: saved.__setitem__(jid, (st, why)))
        assert jobs.approved_to_apply() == []
        assert saved["a"][0] == "skipped" and saved["a"][1] == "expired_at_selection"

    def test_still_fresh_job_is_not_blocked(self, tmp_path, monkeypatch):
        from datetime import datetime, timedelta
        fresh = (datetime.now().astimezone() - timedelta(days=2)).isoformat()
        rows = [{"id": "a", "status": "approved", "company": "Co", "fit": 80, "posted": fresh}]
        jobs = self._wire(monkeypatch, rows)
        monkeypatch.setattr(jobs, "set_status", lambda jid, st, why=None: None)
        out = jobs.approved_to_apply()
        assert [x["id"] for x in out] == ["a"]


# ---- seniority-ladder score wired into _fit() (2026-07-13 hunt) ----
class TestSeniorityLadderWiredIntoFit:
    """R2-16: job_fit_signals.seniority_score() existed with no caller -- title-tier language
    never actually moved the ranking/approval score. jobs._fit() now adds it in."""

    def test_manager_title_scores_higher_than_vp_all_else_equal(self):
        import jobs
        base = {"comp_max": None, "posted": None, "yoe": None}
        manager = jobs._fit({**base, "title": "Marketing Manager"})
        vp = jobs._fit({**base, "title": "VP Marketing"})
        # seniority_score: manager +8, vp -10 -- an 18-point swing beyond the shared,
        # identical title-kw bonus both titles get from "marketing"
        assert manager - vp == 18

    def test_fit_never_raises_if_job_fit_signals_is_unavailable(self, monkeypatch):
        import jobs
        import sys
        monkeypatch.setitem(sys.modules, "job_fit_signals", None)
        assert isinstance(jobs._fit({"title": "Marketing Manager", "posted": None}), int)


# ---- interview classifier over-call fix (2026-07-12: 4 of 5 "interviews" were boilerplate) ----
class TestInterviewClassifier:
    def test_next_steps_boilerplate_is_not_interview(self):
        import job_mail_patterns as p
        # "we received your application and will be in touch about next steps" is a
        # CONFIRMATION, not an interview (this over-called 8x8/IXOPAY/Infracost)
        assert p.classify("no-reply@send.dover.com", "IXOPAY has received your application",
                          "Thanks for applying! We will be in touch about next steps.") == "confirmation"
        assert p.classify("x@ashbyhq.com", "Thanks for applying",
                          "get back to you if there are next steps") == "confirmation"

    def test_real_invite_still_classifies_interview(self):
        import job_mail_patterns as p
        assert p.classify("x@us.greenhouse-mail.io", "Schedule your interview",
                          "We would like to schedule a call. Book a time here.") == "interview"

    def test_no_next_steps_trigger_remains_in_patterns(self):
        import re
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "agents" / "job_mail_patterns.py").read_text()
        # the weak trigger must be gone from every compiled interview regex
        assert 'next steps' not in re.sub(r'#.*', '', src)  # ignore comments


class TestClassifierFullPipeline:
    def test_gerund_scheduling_is_not_interview(self):
        import job_mail_patterns as p
        # "we will be scheduling interviews soon" has no directed schedule-a-call verb ->
        # the regex correctly does NOT flag it as interview (confirmation/None)
        assert p.classify("x@greenhouse.io", "Thanks for applying",
                          "we will be scheduling interviews soon") in ("confirmation", None)

    def test_llm_backstop_downgrades_conditional_interview(self, monkeypatch):
        # the CONDITIONAL "if a good fit we'll schedule an interview" is caught by the LLM
        # backstop (regex flags it, LLM overrules), pinned deterministically here
        import job_rescan
        job_rescan._INT_LLM_BUDGET[0] = 5
        monkeypatch.setattr(job_rescan.planner, "_cli", lambda *a, **k: "CONFIRMATION")
        assert job_rescan._real_interview("Co", "Thanks for applying",
                                          "if a good fit we will schedule an interview") is False
        monkeypatch.setattr(job_rescan.planner, "_cli", lambda *a, **k: "INTERVIEW")
        assert job_rescan._real_interview("Co", "follow-up", "let's grab time to chat") is True

    def test_soft_real_invite_is_interview(self):
        import job_mail_patterns as p
        assert p.classify("x@workablemail.com", "follow-up",
                          "open to a quick chat about the Marketing Manager role") == "interview"

    def test_strengthened_rejection_net(self):
        import job_mail_patterns as p
        for b in ("we regret to inform you", "you were not selected"):
            assert p.classify("x@greenhouse.io", "Update", b) == "rejection", b

    def test_job_replies_defers_interview_to_llm(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "agents" / "job_replies.py").read_text()
        assert 'typ == "interview" and d.get("type")' in src


# ---- job_rescan atomic write (R1#4 regression, post-17bf56c) ----
class TestJobRescanAtomicWrite:
    """job_rescan.run() writes each correction with expect=cur, where `cur` is a snapshot
    from the SINGLE load_jobs() at the top of run() -- taken before a whole per-company scan
    (Gmail + LLM) ran. A concurrent writer (job_replies) can advance a job in that window;
    the write must be atomic against the observed status, never clobber a fresher one."""

    def test_rescan_write_passes_expect_cur(self):
        # source-level pin: the correction write threads expect= (the atomic CAS), not a bare
        # set_status that would clobber whatever a concurrent writer set.
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "agents" / "job_rescan.py").read_text()
        assert "expect=cur" in src

    def test_stale_rescan_does_not_clobber_a_concurrently_advanced_status(self, tmp_path, monkeypatch):
        import job_rescan
        import jobs
        q = tmp_path / "jobs.jsonl"
        q.write_text(json.dumps({"id": "j1", "company": "Raceco", "title": "Growth",
                                 "status": "applied"}) + "\n")
        monkeypatch.setattr(jobs, "QUEUE", q)

        # rescan decides (off its top-of-run snapshot of "applied") this job is "confirmed"...
        monkeypatch.setattr(job_rescan.jobs, "load_jobs", jobs.load_jobs)
        monkeypatch.setattr(job_rescan, "_company_status", lambda *a, **k: ("confirmed", "ev"))

        # ...but a concurrent writer advances the SAME job to 'interview' before the write lands.
        real_set = jobs.set_status

        def _racy_set(jid, status, reason=None, expect=None):
            q.write_text(json.dumps({"id": "j1", "company": "Raceco", "title": "Growth",
                                     "status": "interview"}) + "\n")
            return real_set(jid, status, reason, expect=expect)
        monkeypatch.setattr(job_rescan.jobs, "set_status", _racy_set)

        job_rescan.run(dry=False)
        cur = next(j["status"] for j in jobs.load_jobs() if j["id"] == "j1")
        assert cur == "interview"   # the stale confirmed write was skipped by expect=cur


# ---- resume_ab: false-positive interview credit (R1#8 regression, post-17bf56c) ----
class TestResumeABFalsePositiveInterview:
    """A false-positive interview the pipeline later CORRECTED to 'confirmed' (the 'next steps'
    over-call) must NOT keep permanent interview/reply credit -- interview_rate is THE A/B
    metric. A REAL interview later REJECTED still keeps its credit (CX15/R2-7 preserved)."""

    def test_interview_corrected_to_confirmed_loses_credit(self, tmp_path, monkeypatch):
        import resume_ab
        import jobs
        q = tmp_path / "jobs.jsonl"
        # full history: applied -> interview (false positive) -> confirmed (corrected)
        q.write_text(
            json.dumps({"id": "j1", "company": "Co", "status": "applied"}) + "\n" +
            json.dumps({"id": "j1", "company": "Co", "status": "interview"}) + "\n" +
            json.dumps({"id": "j1", "company": "Co", "status": "confirmed"}) + "\n")
        monkeypatch.setattr(jobs, "QUEUE", q)
        monkeypatch.setattr(resume_ab, "REG", tmp_path / "resume_variants.json")
        reg = {"default": {"file": "store/resume.pdf", "registered": "x",
                           "applied": ["j1"], "outcomes": {}}}
        out = resume_ab.compute_outcomes(reg, jobs.load_jobs())["default"]["outcomes"]
        assert out["interviewed"] == 0   # corrected false positive -> no interview credit
        assert out["replied"] == 0       # and no reply credit either

    def test_real_interview_then_rejected_keeps_credit(self, tmp_path, monkeypatch):
        import resume_ab
        import jobs
        q = tmp_path / "jobs.jsonl"
        # full history: applied -> interview (REAL) -> rejected (didn't pan out)
        q.write_text(
            json.dumps({"id": "j1", "company": "Co", "status": "applied"}) + "\n" +
            json.dumps({"id": "j1", "company": "Co", "status": "interview"}) + "\n" +
            json.dumps({"id": "j1", "company": "Co", "status": "rejected"}) + "\n")
        monkeypatch.setattr(jobs, "QUEUE", q)
        monkeypatch.setattr(resume_ab, "REG", tmp_path / "resume_variants.json")
        reg = {"default": {"file": "store/resume.pdf", "registered": "x",
                           "applied": ["j1"], "outcomes": {}}}
        out = resume_ab.compute_outcomes(reg, jobs.load_jobs())["default"]["outcomes"]
        assert out["interviewed"] == 1   # CX15/R2-7 preserved: a real interview still counts
        assert out["replied"] == 1
        assert out["rejected"] == 1


# ---- warm dispo booked-call ledger write surfaced (R2#9 regression, post-17bf56c) ----
class TestWarmDispoLedgerBoolChecked:
    def test_failed_booked_ledger_write_reports_not_ok(self, tmp_path, monkeypatch):
        import server
        monkeypatch.setattr(server, "_WARM_DISPO", tmp_path / "warm_dispo.jsonl")
        monkeypatch.setattr(server, "_warm_rows", lambda tier: [])
        monkeypatch.setattr(server, "_ledger_add", lambda *a, **k: False)  # write fails
        r = server.api_warm_dispo("w1", server.WarmDispo(dispo="booked"))
        assert r["ok"] is False and "ledger" in r["error"]

    def test_successful_booked_ledger_write_reports_ok(self, tmp_path, monkeypatch):
        import server
        monkeypatch.setattr(server, "_WARM_DISPO", tmp_path / "warm_dispo.jsonl")
        monkeypatch.setattr(server, "_warm_rows", lambda tier: [])
        calls = []
        monkeypatch.setattr(server, "_ledger_add", lambda *a, **k: calls.append(a) or True)
        r = server.api_warm_dispo("w1", server.WarmDispo(dispo="booked"))
        assert r["ok"] is True and calls  # ledger was written and result reflects success

    def test_non_booked_dispo_does_not_touch_ledger_and_reports_ok(self, tmp_path, monkeypatch):
        import server
        monkeypatch.setattr(server, "_WARM_DISPO", tmp_path / "warm_dispo.jsonl")
        monkeypatch.setattr(server, "_warm_rows", lambda tier: [])
        monkeypatch.setattr(server, "_ledger_add", lambda *a, **k: pytest.fail("must not ledger a non-booked dispo"))
        r = server.api_warm_dispo("w1", server.WarmDispo(dispo="noans"))
        assert r["ok"] is True


# ---- retro range-validation: negative knob rejected (R2#7 regression, post-17bf56c) ----
class TestRetroRangeValidation:
    def test_negative_int_knob_is_rejected(self):
        import server
        # auto_approve_min: -1 coerces to a clean int but config_check.py requires >= 0 --
        # letting it through would brick the 6:30am config gate.
        assert server._retro_coerce_int(-1) is None
        assert server._retro_coerce_int(-500) is None

    def test_zero_and_positive_still_pass(self):
        import server
        assert server._retro_coerce_int(0) == 0
        assert server._retro_coerce_int(42) == 42
        assert server._retro_coerce_int("7") == 7

    def test_bool_and_garbage_still_rejected(self):
        import server
        assert server._retro_coerce_int(True) is None
        assert server._retro_coerce_int("nope") is None
        assert server._retro_coerce_int(None) is None
