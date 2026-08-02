#!/usr/bin/env python3
"""30-day LinkedIn batch CSV (2026-07-11). Pins the GHL Social Planner Basic-CSV contract
(headers scheduleDate/content/imageUrls/link, verified against GHL's own docs) and that
the writer emits complete, dated rows in schedule order.

Also pins R2-37 (2026-07-13): the campaign id is derived from the current month (not
hardcoded to July 2026), and a resumable rerun of assign_dates() must not re-date posts
that already have a scheduled_for_csv -- only newly-generated stragglers get a fresh
date, continuing the sequence after the latest already-assigned slot."""
from __future__ import annotations
import csv, sys
from datetime import datetime, timedelta
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
import gen_month  # noqa: E402


class TestCampaignId:
    def test_campaign_derived_from_current_month_not_hardcoded(self):
        assert gen_month.CAMPAIGN == f"LI-30D-{datetime.now():%Y-%m}"
        assert gen_month.CAMPAIGN != "LI-30D-2026-07" or datetime.now().strftime("%Y-%m") == "2026-07"


class TestAssignDates:
    def test_undated_posts_get_sequential_daily_dates_from_tomorrow(self, monkeypatch):
        posts = [{"id": f"p{i}", "campaign": gen_month.CAMPAIGN, "created": f"t{i}"} for i in range(3)]
        monkeypatch.setattr(gen_month.content_gen, "load_posts", lambda: posts)
        saved = {}
        monkeypatch.setattr(gen_month.content_gen, "save_post",
                             lambda r: saved.__setitem__(r["id"], r["scheduled_for_csv"]))
        gen_month.assign_dates()
        assert len(saved) == 3
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        assert saved["p0"].startswith(tomorrow)
        days = sorted(datetime.strptime(v, "%Y-%m-%d %H:%M:%S") for v in saved.values())
        assert (days[1] - days[0]).days == 1
        assert (days[2] - days[1]).days == 1

    def test_rerun_does_not_shift_already_dated_posts(self, monkeypatch):
        # R2-37: this is the actual duplicate-import shape -- p0/p1 are from an earlier
        # run and may already be imported into GHL under these dates. A rerun (topping up
        # p2, a straggler) must leave them untouched, or re-uploading the regenerated CSV
        # double-schedules them at new dates.
        posts = [
            {"id": "p0", "campaign": gen_month.CAMPAIGN, "created": "t0",
             "scheduled_for_csv": "2026-08-01 09:00:00"},
            {"id": "p1", "campaign": gen_month.CAMPAIGN, "created": "t1",
             "scheduled_for_csv": "2026-08-02 09:00:00"},
            {"id": "p2", "campaign": gen_month.CAMPAIGN, "created": "t2"},  # new straggler, undated
        ]
        monkeypatch.setattr(gen_month.content_gen, "load_posts", lambda: posts)
        saved = {}
        monkeypatch.setattr(gen_month.content_gen, "save_post",
                             lambda r: saved.__setitem__(r["id"], r["scheduled_for_csv"]))
        gen_month.assign_dates()
        assert list(saved.keys()) == ["p2"]              # only the undated straggler was written
        assert saved["p2"] == "2026-08-03 09:00:00"       # continues right after p1's date

    def test_second_rerun_with_nothing_new_writes_nothing(self, monkeypatch):
        posts = [{"id": "p0", "campaign": gen_month.CAMPAIGN, "created": "t0",
                  "scheduled_for_csv": "2026-08-01 09:00:00"}]
        monkeypatch.setattr(gen_month.content_gen, "load_posts", lambda: posts)
        calls = []
        monkeypatch.setattr(gen_month.content_gen, "save_post", lambda r: calls.append(r))
        gen_month.assign_dates()
        assert calls == []  # fully-dated campaign -- a rerun is a pure no-op


def test_csv_headers_and_rows(tmp_path, monkeypatch):
    posts = [{"id": f"p{i}", "campaign": gen_month.CAMPAIGN, "status": "exported",
              "text": f"post {i} body", "ghl_media": f"https://cdn/x{i}.png",
              "scheduled_for_csv": f"2026-07-{12+i:02d} 09:00", "created": f"t{i}"}
             for i in range(3)]
    monkeypatch.setattr(gen_month.content_gen, "load_posts", lambda: posts)
    monkeypatch.setattr(gen_month, "CSV_OUT", tmp_path / "out.csv")
    n = gen_month.write_csv()
    assert n == 3
    rows = list(csv.DictReader(open(tmp_path / "out.csv", encoding="utf-8")))
    assert list(rows[0].keys()) == ["postAtSpecificTime (YYYY-MM-DD HH:mm:ss)", "content",
                                    "imageUrls", "link (OGmetaUrl)", "gifUrl", "videoUrls"]
    # sorted by schedule date
    DK = "postAtSpecificTime (YYYY-MM-DD HH:mm:ss)"
    assert all(r[DK] and r["content"] and r["imageUrls"] for r in rows)
    assert [r[DK] for r in rows] == sorted(r[DK] for r in rows)
