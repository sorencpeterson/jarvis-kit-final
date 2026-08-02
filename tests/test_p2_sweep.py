#!/usr/bin/env python3
"""P2 sweep regression pins (FABLE-MEGA-BACKLOG D4/D6/D7): fixtures only, no
network, no LLM, no sends. Each class pins one fixed defect:

  D4  mail_threads ghosted-thread reply detection (_has_inbound_reply_after)
  D6  proposal_factory webfix->standard 4+ fault routing override
  D6  proposal_factory.is_bot_open (bot/prefetch open filtering)
  D6  reply_watch.ghl_send_ok (fail-closed GHL send success parse)
  D7  day_plan all-day calendar events
  D7  promises bare-weekday commitment gate
  D7  commander.a_ghl_stats empty-response != success
  D7  owner_report stuck-deals filter
  D7  futures_check word-boundary/email-aware sender match
  R   cold_feeder._tag_ok (fail-closed GHL tag-POST success parse, audit finding R)
  L   proposal_factory.find_contact (no exact match -> {}, never a stranger)
  CX10 proposal_factory._has_site (fetch failure != confirmed no-site)

Run: .venv/bin/python -m pytest tests/test_p2_sweep.py -v
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import cold_feeder  # noqa: E402
import commander  # noqa: E402
import day_plan  # noqa: E402
import futures_check  # noqa: E402
import ghl_social  # noqa: E402
import mail_threads  # noqa: E402
import owner_report  # noqa: E402
import promises  # noqa: E402
import proposal_factory  # noqa: E402
import reply_watch  # noqa: E402


def _ms(dt: datetime) -> str:
    return str(int(dt.timestamp() * 1000))


SENT_DT = datetime(2026, 6, 20, 15, 0, tzinfo=timezone.utc)


# ---- D4: ghosted-thread resurrector reply detection ----
class TestHasInboundReplyAfter:
    def test_reply_after_send_is_detected(self):
        # thread with a real inbound reply AFTER the send = NOT ghosted
        msgs = [
            {"id": "m1", "internalDate": _ms(SENT_DT), "labelIds": ["SENT"]},
            {"id": "m2", "internalDate": _ms(SENT_DT + timedelta(days=2)), "labelIds": ["INBOX"]},
        ]
        assert mail_threads._has_inbound_reply_after(msgs, SENT_DT, "m1") is True

    def test_send_then_silence_is_ghosted(self):
        msgs = [
            {"id": "m0", "internalDate": _ms(SENT_DT - timedelta(days=3)), "labelIds": ["INBOX"]},
            {"id": "m1", "internalDate": _ms(SENT_DT), "labelIds": ["SENT"]},
        ]
        assert mail_threads._has_inbound_reply_after(msgs, SENT_DT, "m1") is False

    def test_own_later_sent_message_is_not_a_reply(self):
        # Alex following up with himself twice must still read as ghosted
        msgs = [
            {"id": "m1", "internalDate": _ms(SENT_DT), "labelIds": ["SENT"]},
            {"id": "m2", "internalDate": _ms(SENT_DT + timedelta(days=1)), "labelIds": ["SENT"]},
        ]
        assert mail_threads._has_inbound_reply_after(msgs, SENT_DT, "m1") is False

    def test_unparseable_date_does_not_count_as_reply(self):
        # the old fallback (`or cutoff`) made ANY undated message look like a
        # reply-after-send, silently suppressing real ghosts
        msgs = [
            {"id": "m1", "internalDate": _ms(SENT_DT), "labelIds": ["SENT"]},
            {"id": "m2", "internalDate": "", "labelIds": ["INBOX"]},
        ]
        assert mail_threads._has_inbound_reply_after(msgs, SENT_DT, "m1") is False

    def test_inbound_before_send_is_not_a_reply(self):
        msgs = [
            {"id": "m0", "internalDate": _ms(SENT_DT - timedelta(hours=2)), "labelIds": ["INBOX"]},
            {"id": "m1", "internalDate": _ms(SENT_DT), "labelIds": ["SENT"]},
        ]
        assert mail_threads._has_inbound_reply_after(msgs, SENT_DT, "m1") is False


# ---- D6: webfix -> standard routing override (pricing-tree.md 4+ faults rule) ----
class TestWebfixRoutingOverride:
    def test_webfix_with_4plus_faults_routes_standard(self):
        assert proposal_factory.route("webfix", faults_n=4) == "standard"
        assert proposal_factory.route("webfix", faults_n=5) == "standard"

    def test_webfix_with_few_faults_stays_webfix(self):
        assert proposal_factory.route("webfix", faults_n=1) == "webfix"
        assert proposal_factory.route("webfix", faults_n=3) == "webfix"

    def test_webfix_with_no_teardown_data_stays_webfix(self):
        # faults_n=0 means "no teardown ran", not "0 faults found": trust the lane tag
        assert proposal_factory.route("webfix", faults_n=0) == "webfix"

    def test_explicit_tier_override_still_wins(self):
        assert proposal_factory.route("webfix", tier_override="webfix", faults_n=5) == "webfix"

    def test_bare_fix_niche_semantics_unchanged(self):
        assert proposal_factory.route("site fix", faults_n=2) == "webfix"
        assert proposal_factory.route("site fix", faults_n=0) == "standard"
        assert proposal_factory.route("site fix", faults_n=5) == "standard"


# ---- D6: bot/prefetch open filtering ----
GMAIL_PROXY_UA = ("Mozilla/5.0 (Windows NT 5.1; rv:11.0) Gecko Firefox/11.0 "
                  "(via ggpht.com GoogleImageProxy)")
CHROME_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


class TestIsBotOpen:
    def test_google_image_proxy_is_a_real_open(self):
        assert proposal_factory.is_bot_open(GMAIL_PROXY_UA) is False

    def test_headless_chrome_is_a_bot(self):
        assert proposal_factory.is_bot_open(CHROME_UA.replace("Chrome/", "HeadlessChrome/")) is True

    def test_bot_and_crawler_uas_filtered(self):
        assert proposal_factory.is_bot_open("Slackbot-LinkExpanding 1.0") is True
        assert proposal_factory.is_bot_open("Googlebot/2.1 (+http://www.google.com/bot.html)") is True
        assert proposal_factory.is_bot_open("some-crawler/1.0") is True
        assert proposal_factory.is_bot_open("Mozilla/5.0 LinkPreview fetcher") is True
        assert proposal_factory.is_bot_open("curl/8.4.0") is True

    def test_normal_browser_counts(self):
        assert proposal_factory.is_bot_open(CHROME_UA) is False

    def test_open_within_60s_of_send_is_prefetch(self):
        sent = "2026-07-07T10:00:00-07:00"
        assert proposal_factory.is_bot_open(CHROME_UA, sent, "2026-07-07T10:00:30-07:00") is True

    def test_open_after_60s_counts(self):
        sent = "2026-07-07T10:00:00-07:00"
        assert proposal_factory.is_bot_open(CHROME_UA, sent, "2026-07-07T10:02:00-07:00") is False

    def test_gmail_proxy_exempt_even_inside_60s(self):
        # order matters: Gmail prefetches through the proxy but it IS the real-open UA
        sent = "2026-07-07T10:00:00-07:00"
        assert proposal_factory.is_bot_open(GMAIL_PROXY_UA, sent, "2026-07-07T10:00:10-07:00") is False

    def test_bad_sent_at_fails_open(self):
        assert proposal_factory.is_bot_open(CHROME_UA, "not-a-date") is False


# ---- D6: GHL send success check must fail closed ----
class TestGhlSendOk:
    def test_real_send_response_passes(self):
        assert reply_watch.ghl_send_ok('{"conversationId":"abc123","messageId":"m1"}') is True

    def test_success_true_passes(self):
        assert reply_watch.ghl_send_ok('{"success": true}') is True

    def test_plain_id_passes(self):
        assert reply_watch.ghl_send_ok('{"id":"xyz"}') is True

    def test_nested_message_id_passes(self):
        assert reply_watch.ghl_send_ok('{"message":{"id":"m1"}}') is True

    def test_noise_prefix_before_json_passes(self):
        assert reply_watch.ghl_send_ok('sending...\n{"messageId":"m1"}') is True

    def test_error_payload_echoing_messageid_fails(self):
        # the old substring check ('"messageId" in out') read this as a sent reply
        assert reply_watch.ghl_send_ok('{"statusCode":422,"message":"messageId is required"}') is False

    def test_error_payload_with_id_inside_errors_fails(self):
        assert reply_watch.ghl_send_ok('{"errors":{"id":"required field"}}') is False

    def test_401_fails(self):
        assert reply_watch.ghl_send_ok('{"statusCode":401,"message":"Invalid JWT"}') is False

    def test_non_json_fails_closed(self):
        assert reply_watch.ghl_send_ok("error: timeout after 40s") is False

    def test_empty_fails_closed(self):
        assert reply_watch.ghl_send_ok("") is False

    def test_json_array_fails_closed(self):
        assert reply_watch.ghl_send_ok('[{"id":"x"}]') is False


# ---- D7: day_plan all-day events ----
class TestDayPlanAllDay:
    def _midnight(self):
        from store_lib import LOCAL_TZ
        return datetime.now(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)

    def test_all_day_event_does_not_block_the_workday(self):
        ev = [{"text": "Conference", "dt": self._midnight(), "all_day": True}]
        free = day_plan._free_windows(ev)
        assert len(free) == 1
        s, e = free[0]
        assert s.hour == day_plan.WORKDAY_START_HOUR
        assert e.hour == day_plan.WORKDAY_END_HOUR

    def test_timed_event_still_blocks(self):
        ev = [{"text": "Call", "dt": self._midnight().replace(hour=10), "all_day": False}]
        free = day_plan._free_windows(ev)
        assert len(free) == 2  # split around the 10am pad

    def test_build_lists_all_day_entry(self, monkeypatch):
        events = [
            {"text": "Conference", "dt": self._midnight(), "all_day": True},
            {"text": "Call with Dana", "dt": self._midnight().replace(hour=10), "all_day": False},
        ]
        monkeypatch.setattr(day_plan, "_today_events", lambda: (events, None))
        result = day_plan.build()
        cal_lines = [l for l in result["lines"] if l.startswith("- ")]
        assert any("all day Conference" in l for l in cal_lines)
        assert any("10:00am Call with Dana" in l for l in cal_lines)
        assert not any("12:00am Conference" in l for l in cal_lines)


# ---- D7: promises bare-weekday commitment gate ----
WED = date(2026, 7, 1)


class TestBareWeekdayGate:
    def test_commitment_verb_still_tracks(self):
        out = promises.find_promises("I'll call you Friday.", WED)
        assert len(out) == 1
        assert out[0]["kind"] == "bare_weekday"
        assert out[0]["due_date"] == "2026-07-03"

    def test_have_it_done_friday_tracks(self):
        out = promises.find_promises("Should have it done Friday for you.", WED)
        assert len(out) == 1

    def test_quoted_reply_header_does_not_track(self):
        assert promises.find_promises("On Friday, John wrote:", WED) == []

    def test_unrelated_weekday_mention_does_not_track(self):
        assert promises.find_promises("Our office is closed Friday.", WED) == []

    def test_by_weekday_unaffected_by_gate(self):
        # explicit "by friday" carries its own intent, no verb needed
        out = promises.find_promises("by friday", WED)
        assert len(out) == 1
        assert out[0]["kind"] == "by_weekday"

    def test_tomorrow_unaffected_by_gate(self):
        assert len(promises.find_promises("tomorrow then", WED)) == 1


# ---- D7: a_ghl_stats empty response is not success ----
class TestGhlStatsUnavailable:
    def _patch(self, monkeypatch, out):
        monkeypatch.setattr(commander, "GHL", Path(__file__))  # exists() -> True
        monkeypatch.setattr(commander, "_run", lambda *a, **k: out)

    def test_real_total_reports_count(self, monkeypatch):
        self._patch(monkeypatch, '{"contacts": [], "total": 5321}')
        assert commander.a_ghl_stats({}) == "GHL: 5,321 contacts"

    def test_empty_response_reports_unavailable(self, monkeypatch):
        self._patch(monkeypatch, "")
        out = commander.a_ghl_stats({})
        assert "unavailable" in out
        assert "connected" not in out

    def test_error_response_reports_unavailable(self, monkeypatch):
        self._patch(monkeypatch, "error: timeout after 40s")
        out = commander.a_ghl_stats({})
        assert out.startswith("GHL stats unavailable")
        assert "timeout" in out


# ---- D7 / build-queue #25: owner_report stuck deals ----
TODAY = date(2026, 7, 7)


def _opp(status="open", updated="2026-06-01T12:00:00.000Z", name="Acme HVAC",
         value=1200, **extra):
    return {"id": "o1", "status": status, "updatedAt": updated, "name": name,
            "monetaryValue": value, **extra}


class TestStuckDeals:
    def test_old_open_deal_is_stuck(self):
        stuck = owner_report._stuck_from_opportunities([_opp()], TODAY)
        assert len(stuck) == 1
        assert stuck[0]["name"] == "Acme HVAC"
        assert stuck[0]["days"] == 36
        assert stuck[0]["value"] == 1200

    def test_fresh_deal_is_not_stuck(self):
        stuck = owner_report._stuck_from_opportunities(
            [_opp(updated="2026-07-05T12:00:00.000Z")], TODAY)
        assert stuck == []

    def test_closed_deal_is_not_stuck(self):
        assert owner_report._stuck_from_opportunities([_opp(status="won")], TODAY) == []

    def test_exactly_stuck_days_old_is_not_stuck(self):
        on_boundary = (TODAY - timedelta(days=owner_report.STUCK_DAYS)).isoformat() + "T00:00:00Z"
        assert owner_report._stuck_from_opportunities([_opp(updated=on_boundary)], TODAY) == []

    def test_bad_timestamp_skipped_not_guessed(self):
        assert owner_report._stuck_from_opportunities([_opp(updated="not a date")], TODAY) == []

    def test_sorted_stalest_first_and_name_fallback(self):
        opps = [
            _opp(updated="2026-06-15T00:00:00Z", name=None, value=None,
                 contact={"name": "Nimbus Soft"}),
            _opp(updated="2026-05-01T00:00:00Z", name="Older Deal"),
        ]
        stuck = owner_report._stuck_from_opportunities(opps, TODAY)
        assert [d["name"] for d in stuck] == ["Older Deal", "Nimbus Soft"]
        assert stuck[1]["value"] == 0.0

    def test_stuck_deals_fails_safe_when_ghl_down(self, monkeypatch):
        import ghl_social
        monkeypatch.setattr(ghl_social, "_api", lambda *a, **k: "error: boom")
        out = owner_report._stuck_deals()
        assert out is not None and "unavailable" in out

    def test_stuck_deals_none_when_nothing_stuck(self, monkeypatch):
        import ghl_social
        monkeypatch.setattr(ghl_social, "_api", lambda *a, **k: '{"opportunities": []}')
        assert owner_report._stuck_deals() is None

    def test_stuck_deals_formats_line(self, monkeypatch):
        import ghl_social
        import json as _json
        payload = _json.dumps({"opportunities": [_opp()]})
        monkeypatch.setattr(ghl_social, "_api", lambda *a, **k: payload)
        out = owner_report._stuck_deals()
        assert out is not None
        assert "Acme HVAC" in out and "untouched" in out


# ---- D7: futures_check sender matching ----
class TestSenderMatches:
    def test_whole_word_first_name_matches(self):
        assert futures_check._sender_matches("Dan", "Dan Brown") is True

    def test_prefix_of_longer_name_does_not_match(self):
        assert futures_check._sender_matches("Dan", "Danielle Smith") is False

    def test_suffix_inside_name_does_not_match(self):
        assert futures_check._sender_matches("dan", "Jordan Banks") is False

    def test_email_exact_match(self):
        assert futures_check._sender_matches("dan@x.com", "Dan@X.com") is True

    def test_email_vs_name_no_match(self):
        assert futures_check._sender_matches("dan@x.com", "Dan Brown") is False
        assert futures_check._sender_matches("dan", "danielle@x.com") is False

    def test_full_name_within_company_matches(self):
        assert futures_check._sender_matches("Dan Brown", "dan brown llc") is True

    def test_empty_never_matches(self):
        assert futures_check._sender_matches("", "Dan Brown") is False
        assert futures_check._sender_matches("Dan", "") is False


# ---- R: cold_feeder._tag_ok (fail-closed GHL tag-POST success parse) ----
class TestColdFeederTagOk:
    def test_real_success_shapes_pass(self):
        assert cold_feeder._tag_ok({"tags": ["wl-cold"]}) is True
        assert cold_feeder._tag_ok({"tagsAdded": ["wl-cold"]}) is True

    def test_error_body_with_no_raw_key_is_not_success(self):
        # R: a well-formed GHL error parses cleanly (no `_raw` fallback key), so
        # `not j.get("_raw")` used to read a 404/429/401 body as success.
        assert cold_feeder._tag_ok({"statusCode": 404, "message": "Contact not found"}) is False
        assert cold_feeder._tag_ok({"statusCode": 429, "message": "rate limited"}) is False
        assert cold_feeder._tag_ok({"statusCode": 401, "message": "Invalid JWT"}) is False

    def test_unparseable_raw_fallback_is_not_success(self):
        assert cold_feeder._tag_ok({"_raw": "connection reset"}) is False

    def test_empty_dict_is_not_success(self):
        assert cold_feeder._tag_ok({}) is False


# ---- L: proposal_factory.find_contact (no exact match -> {}, never a stranger) ----
class TestFindContactExactMatch:
    def test_no_exact_email_match_returns_empty_not_stranger(self, monkeypatch):
        monkeypatch.setattr(ghl_social, "_api",
                            lambda *a, **k: json.dumps({"contacts": [
                                {"id": "wrong1", "email": "someone-else@x.com",
                                 "contactName": "Stranger"}]}))
        assert proposal_factory.find_contact(email="target@x.com") == {}

    def test_exact_email_match_returned(self, monkeypatch):
        monkeypatch.setattr(ghl_social, "_api",
                            lambda *a, **k: json.dumps({"contacts": [
                                {"id": "wrong1", "email": "someone-else@x.com",
                                 "contactName": "Stranger"},
                                {"id": "right1", "email": "Target@X.com",
                                 "contactName": "Target"}]}))
        c = proposal_factory.find_contact(email="target@x.com")
        assert c.get("id") == "right1"

    def test_no_exact_name_match_returns_empty_not_stranger(self, monkeypatch):
        monkeypatch.setattr(ghl_social, "_api",
                            lambda *a, **k: json.dumps({"contacts": [
                                {"id": "wrong1", "contactName": "Totally Different Co"}]}))
        assert proposal_factory.find_contact(name="Legacy Plumbing") == {}

    def test_name_lookup_also_matches_company_name(self, monkeypatch):
        # R2#5: --name is documented for a BUSINESS name too (module docstring:
        # `--name "Legacy Plumbing"`), but contactName is the PERSON's name -- a
        # real GHL contact can match on companyName while carrying a totally
        # different contactName. Before the fix this returned {} for the exact
        # scenario the module's own usage example shows.
        monkeypatch.setattr(ghl_social, "_api",
                            lambda *a, **k: json.dumps({"contacts": [
                                {"id": "wrong1", "contactName": "Someone Else",
                                 "companyName": "Totally Different Co"},
                                {"id": "right1", "contactName": "John Smith",
                                 "companyName": "Legacy Plumbing"}]}))
        c = proposal_factory.find_contact(name="Legacy Plumbing")
        assert c.get("id") == "right1"

    def test_cid_lookup_unaffected(self, monkeypatch):
        monkeypatch.setattr(ghl_social, "_api",
                            lambda *a, **k: json.dumps({"contact": {"id": "c9", "email": "c9@x.com"}}))
        c = proposal_factory.find_contact(cid="c9")
        assert c.get("id") == "c9"


# ---- CX10: proposal_factory._has_site (fetch failure != confirmed no-site) ----
class TestHasSite:
    def test_no_url_is_confirmed_no_site(self):
        assert proposal_factory._has_site("", {}) is False

    def test_fetch_error_is_unknown_not_no_site(self):
        # CX10: a 403/JS-only page returns an "error" key, not "no site" -- treating
        # a fetch failure as absence silently underpriced by $400 (landing vs standard).
        assert proposal_factory._has_site("https://x.com", {"url": "https://x.com", "error": "403"}) is True

    def test_empty_text_but_fetched_is_unknown_not_no_site(self):
        # R2#8: a JS-only/SPA page that fetches successfully (no "error" key) but
        # extracts to empty text is the SAME "can't confirm" case as an explicit
        # fetch error right above -- not proof of a confirmed-empty site. Used to
        # fall through to bool(text) == False and underprice into the $800 tier
        # via a different path than the error case.
        assert proposal_factory._has_site("https://x.com", {"url": "https://x.com", "text": ""}) is True

    def test_real_content_has_site(self):
        assert proposal_factory._has_site("https://x.com", {"url": "https://x.com", "text": "hello"}) is True
