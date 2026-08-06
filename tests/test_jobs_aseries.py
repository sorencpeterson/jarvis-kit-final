#!/usr/bin/env python3
"""Unit tests for the Section-5 A-series jobs builds:
agents/company_risk.py, resume_ab.py, offer_compare.py, rejection_digest.py,
interview_postmortem.py, salary_ladder.py, stage_coach.py, takehome_helper.py.

Everything runs against tmp-path stores with planner._cli / planner.notify /
planner.feed_add monkeypatched, so no LLM call, no push, no feed write, and
no touch of the real store/ ever happens in here.

Run: .venv/bin/python -m pytest tests/test_jobs_aseries.py -v
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import company_risk  # noqa: E402
import interview_postmortem as pm  # noqa: E402
import offer_compare as oc  # noqa: E402
import rejection_digest as rd  # noqa: E402
import resume_ab as rab  # noqa: E402
import salary_ladder as sl  # noqa: E402
import stage_coach as sc  # noqa: E402
import takehome_helper as th  # noqa: E402
import jobs  # noqa: E402
import planner  # noqa: E402
from store_lib import LOCAL_TZ, load_todos  # noqa: E402


def _iso_days_ago(n: float) -> str:
    return (datetime.now().astimezone() - timedelta(days=n)).isoformat(timespec="seconds")


def _capture(monkeypatch):
    """Swap planner.notify/feed_add for recorders. Returns (pushes, feed)."""
    pushes, feed = [], []
    monkeypatch.setattr(planner, "notify",
                        lambda title, body, tags="brain", actions=None:
                        (pushes.append((title, body)), True)[1])
    monkeypatch.setattr(planner, "feed_add",
                        lambda kind, title, detail="": feed.append((kind, title)))
    return pushes, feed


def _job(i, status="approved", company=None, title=None, **over):
    j = {"id": f"j{i}", "title": title or f"Marketing Manager {i}",
         "company": company or f"Co{i}", "status": status,
         "source": "workable", "seniority": "Senior Level",
         "salary": "$120k-$150k", "comp_max": 150000, "fit": 70,
         "posted": _iso_days_ago(3), "created": _iso_days_ago(3)}
    j.update(over)
    return j


# ------------------------------------------------------------- company_risk
def _wire_risk(tmp_path, monkeypatch, jobs_list, cli="Risk note from the model.",
               mentions_text=""):
    monkeypatch.setattr(company_risk, "OUT", tmp_path / "company_risk.jsonl")
    mention = tmp_path / "mentions.jsonl"
    mention.write_text(mentions_text)
    monkeypatch.setattr(company_risk, "MENTION_STORES", [mention])
    monkeypatch.setattr(jobs, "load_jobs", lambda: jobs_list)
    monkeypatch.setattr(jobs, "load_profile",
                        lambda: {"salary_expectation": "$125,000/year"})
    calls = []
    monkeypatch.setattr(planner, "_cli",
                        lambda prompt, timeout=130, feature="default":
                        (calls.append(prompt), cli)[1])
    return calls


def _risk_rows(tmp_path):
    p = tmp_path / "company_risk.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


class TestCompanyRisk:
    def test_assesses_and_skips_already_assessed(self, tmp_path, monkeypatch):
        calls = _wire_risk(tmp_path, monkeypatch, [_job(1)])
        assert company_risk.run() == 1
        rows = _risk_rows(tmp_path)
        assert len(rows) == 1
        assert rows[0]["job_id"] == "j1" and rows[0]["company"] == "Co1"
        assert rows[0]["note"] == "Risk note from the model."
        assert len(calls) == 1
        # second run: already assessed, no new line, no new LLM call
        assert company_risk.run() == 0
        assert len(_risk_rows(tmp_path)) == 1
        assert len(calls) == 1

    def test_cap_five_per_run_then_drains(self, tmp_path, monkeypatch):
        calls = _wire_risk(tmp_path, monkeypatch, [_job(i) for i in range(7)])
        assert company_risk.run() == 5
        assert len(calls) == 5
        assert company_risk.run() == 2   # the remainder next run
        assert len(_risk_rows(tmp_path)) == 7

    def test_flags_repost_stale_comp_layoff(self, tmp_path, monkeypatch):
        rows = [
            _job(1, company="GhostCo", title="SEO Lead", posted=_iso_days_ago(40),
                 comp_max=100000),
            # same company+title again in history = repost signal
            _job(2, status="skipped", company="GhostCo", title="SEO Lead"),
        ]
        _wire_risk(tmp_path, monkeypatch, rows,
                   mentions_text='{"subject": "GhostCo announces layoff round"}\n')
        company_risk.run()
        flags = _risk_rows(tmp_path)[0]["risk_flags"]
        assert "reposted" in flags
        assert "stale_30d" in flags
        assert "comp_below_anchor" in flags
        assert "layoff_mentions" in flags

    def test_no_posted_comp_flag_and_clean_job(self, tmp_path, monkeypatch):
        _wire_risk(tmp_path, monkeypatch, [_job(1, comp_max=None)])
        company_risk.run()
        assert "no_posted_comp" in _risk_rows(tmp_path)[0]["risk_flags"]
        # clean: fresh, unique, above anchor
        _wire_risk(tmp_path, monkeypatch, [_job(2, comp_max=180000)])
        company_risk.run()
        rec = [r for r in _risk_rows(tmp_path) if r["job_id"] == "j2"][0]
        assert rec["risk_flags"] == []

    def test_note_is_humanized_no_em_dash(self, tmp_path, monkeypatch):
        _wire_risk(tmp_path, monkeypatch, [_job(1)],
                   cli="Reposted twice — likely a ghost job.")
        company_risk.run()
        note = _risk_rows(tmp_path)[0]["note"]
        assert "—" not in note and "–" not in note

    def test_cli_offline_falls_back(self, tmp_path, monkeypatch):
        _wire_risk(tmp_path, monkeypatch, [_job(1, comp_max=90000)], cli=None)
        company_risk.run()
        rec = _risk_rows(tmp_path)[0]
        assert "comp_below_anchor" in rec["note"] or "Flags:" in rec["note"]

    def test_dry_run_no_write_no_llm(self, tmp_path, monkeypatch, capsys):
        calls = _wire_risk(tmp_path, monkeypatch, [_job(1)])
        company_risk.run(dry_run=True)
        assert _risk_rows(tmp_path) == []
        assert calls == []
        assert "dry-run" in capsys.readouterr().out

    def test_fresh_install_no_jobs(self, tmp_path, monkeypatch):
        _wire_risk(tmp_path, monkeypatch, [])
        assert company_risk.run() == 0


# ---------------------------------------------------------------- resume_ab
def _wire_rab(tmp_path, monkeypatch, jobs_list, reg=None):
    monkeypatch.setattr(rab, "REG", tmp_path / "resume_variants.json")
    if reg is not None:
        (tmp_path / "resume_variants.json").write_text(json.dumps(reg))
    monkeypatch.setattr(jobs, "load_jobs", lambda: jobs_list)
    # resume_ab._status_history() reads jobs.QUEUE DIRECTLY (it needs the full
    # append-only history, which load_jobs folds away), so patching load_jobs
    # alone leaks the real store in: a live install with a job id matching a
    # fixture id fails this class. Point QUEUE at a temp log built from the
    # fixture so the test is isolated on any machine.
    q = tmp_path / "jobs.jsonl"
    q.write_text("".join(json.dumps(j) + "\n" for j in jobs_list))
    monkeypatch.setattr(jobs, "QUEUE", q)


class TestResumeAB:
    def test_scaffolds_default_and_backfills_applied_only(self, tmp_path, monkeypatch):
        rows = [_job(1, status="applied"), _job(2, status="confirmed"),
                _job(3, status="interview"), _job(4, status="rejected"),
                _job(5, status="replied"),
                _job(6, status="pending"), _job(7, status="skipped"),
                _job(8, status="approved")]
        _wire_rab(tmp_path, monkeypatch, rows)
        reg = rab.run()
        d = reg["default"]
        assert d["file"] == "store/resume.pdf"
        assert sorted(d["applied"]) == ["j1", "j2", "j3", "j4", "j5"]
        saved = json.loads((tmp_path / "resume_variants.json").read_text())
        assert sorted(saved["default"]["applied"]) == ["j1", "j2", "j3", "j4", "j5"]

    def test_rate_math(self, tmp_path, monkeypatch):
        rows = ([_job(i, status="applied") for i in range(1, 6)]
                + [_job(6, status="confirmed"), _job(7, status="confirmed")]
                + [_job(8, status="replied"), _job(9, status="interview"),
                   _job(10, status="rejected")])
        _wire_rab(tmp_path, monkeypatch, rows)
        o = rab.run()["default"]["outcomes"]
        assert o["applied"] == 10
        assert o["replied"] == 2          # replied + interview both count as a reply
        assert o["interviewed"] == 1
        assert o["rejected"] == 1
        assert o["reply_rate"] == 0.2
        assert o["interview_rate"] == 0.1

    def test_claimed_ids_stay_with_their_variant(self, tmp_path, monkeypatch):
        reg = {"punchy": {"file": "store/resume_punchy.pdf", "registered": "x",
                          "applied": ["j1"], "outcomes": {}}}
        _wire_rab(tmp_path, monkeypatch,
                  [_job(1, status="interview"), _job(2, status="applied")], reg=reg)
        out = rab.run()
        assert out["default"]["applied"] == ["j2"]      # j1 not stolen
        assert out["punchy"]["applied"] == ["j1"]
        assert out["punchy"]["outcomes"]["interviewed"] == 1

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        _wire_rab(tmp_path, monkeypatch, [_job(1, status="applied")])
        rab.run(dry_run=True)
        assert not (tmp_path / "resume_variants.json").exists()

    def test_fresh_install_no_jobs(self, tmp_path, monkeypatch):
        _wire_rab(tmp_path, monkeypatch, [])
        reg = rab.run()
        assert reg["default"]["outcomes"]["applied"] == 0


# ------------------------------------------------------------ offer_compare
def _wire_oc(tmp_path, monkeypatch):
    monkeypatch.setattr(oc, "STORE", tmp_path / "offers.jsonl")
    monkeypatch.setattr(jobs, "load_profile",
                        lambda: {"salary_expectation": "$125,000/year"})


class TestOfferCompare:
    def test_money_parse(self):
        assert oc._money("$140,000") == 140000
        assert oc._money("140k") == 140000
        assert oc._money("1.5k") == 1500
        assert oc._money(140000) == 140000
        assert oc._money("") == 0 and oc._money(None) == 0

    def test_effective_comp_is_base_plus_bonus(self, tmp_path, monkeypatch):
        _wire_oc(tmp_path, monkeypatch)
        rec = oc.add_offer("CacheFly", "140k", bonus="10k")
        assert rec["base"] == 140000 and rec["bonus"] == 10000
        assert oc.effective(rec) == 150000

    def test_empty_store_prints_how_to_add(self, tmp_path, monkeypatch, capsys):
        _wire_oc(tmp_path, monkeypatch)
        assert oc.run() == 0
        out = capsys.readouterr().out
        assert "--add" in out and "no offers on file" in out

    def test_table_and_anchor_and_recommendation(self, tmp_path, monkeypatch, capsys):
        _wire_oc(tmp_path, monkeypatch)
        oc.add_offer("CacheFly", "130k", bonus="10k", remote="yes", pto=20)
        oc.add_offer("8x8", "125k", remote="hybrid")
        capsys.readouterr()
        assert oc.run() == 0
        out = capsys.readouterr().out
        assert "CacheFly" in out and "8x8" in out
        assert "$140,000" in out                      # effective comp line
        assert "vs $125k anchor" in out
        assert "On money it is CacheFly" in out
        assert "$15,000 over" in out                  # gap over next best
        assert "—" not in out and "–" not in out

    def test_below_anchor_says_counter_first(self, tmp_path, monkeypatch, capsys):
        _wire_oc(tmp_path, monkeypatch)
        oc.add_offer("LowCo", "110k")
        oc.add_offer("LowerCo", "100k")
        capsys.readouterr()
        oc.run()
        assert "under" in capsys.readouterr().out.lower()

    def test_single_offer_never_accept_same_day(self, tmp_path, monkeypatch, capsys):
        _wire_oc(tmp_path, monkeypatch)
        oc.add_offer("OnlyCo", "150k")
        capsys.readouterr()
        oc.run()
        assert "Never accept same-day" in capsys.readouterr().out

    def test_add_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        _wire_oc(tmp_path, monkeypatch)
        oc.add_offer("CacheFly", "140k", dry_run=True)
        assert not (tmp_path / "offers.jsonl").exists()


# --------------------------------------------------------- rejection_digest
def _rejected(i, days_ago=2.0, source="ashby", seniority="Senior Level", fit=70):
    return _job(i, status="rejected", source=source, seniority=seniority, fit=fit,
                rejected_at=_iso_days_ago(days_ago),
                rejection_snippet=f"Thank you for your interest, Co{i} passes.")


def _wire_rd(tmp_path, monkeypatch, jobs_list, cli="Pattern read from the model."):
    monkeypatch.setattr(rd, "OUT", tmp_path / "rejection_digest.json")
    monkeypatch.setattr(jobs, "load_jobs", lambda: jobs_list)
    calls = []
    monkeypatch.setattr(planner, "_cli",
                        lambda prompt, timeout=130, feature="default":
                        (calls.append(prompt), cli)[1])
    return calls


class TestRejectionDigest:
    def test_weekday_gate_blocks_off_days(self, tmp_path, monkeypatch):
        _, feed = _capture(monkeypatch)
        _wire_rd(tmp_path, monkeypatch, [_rejected(1)])
        today_wd = datetime.now(LOCAL_TZ).weekday()
        monkeypatch.setattr(rd, "REPORT_WEEKDAY", (today_wd + 1) % 7)
        assert rd.run() == 0
        assert not (tmp_path / "rejection_digest.json").exists()
        assert feed == []

    def test_fires_on_report_day_and_daily_idempotent(self, tmp_path, monkeypatch):
        _, feed = _capture(monkeypatch)
        calls = _wire_rd(tmp_path, monkeypatch, [_rejected(1), _rejected(2)])
        monkeypatch.setattr(rd, "REPORT_WEEKDAY", datetime.now(LOCAL_TZ).weekday())
        assert rd.run() == 0
        rep = json.loads((tmp_path / "rejection_digest.json").read_text())
        assert rep["total"] == 2
        assert calls == []                       # 2 < LLM_MIN: no LLM call
        assert "no pattern" in rep["read"]
        assert len(feed) == 1 and "Rejection digest: 2" in feed[0][1]
        # same day again: idempotent
        assert rd.run() == 0
        assert len(feed) == 1

    def test_three_plus_calls_llm_once_and_humanizes(self, tmp_path, monkeypatch):
        _capture(monkeypatch)
        calls = _wire_rd(tmp_path, monkeypatch,
                         [_rejected(1), _rejected(2), _rejected(3), _rejected(4)],
                         cli="Ashby rejects everything — stop applying there.")
        monkeypatch.setattr(rd, "REPORT_WEEKDAY", datetime.now(LOCAL_TZ).weekday())
        rd.run()
        assert len(calls) == 1
        rep = json.loads((tmp_path / "rejection_digest.json").read_text())
        assert "—" not in rep["read"] and "stop applying there" in rep["read"]

    def test_groups_and_window(self, tmp_path, monkeypatch):
        _capture(monkeypatch)
        rows = [_rejected(1, source="ashby", seniority="Mid Level", fit=55),
                _rejected(2, source="ashby", seniority="Senior Level", fit=70),
                _rejected(3, source="grnhse", seniority="Senior Level", fit=None),
                _rejected(4, days_ago=10)]     # outside the 7d window
        _wire_rd(tmp_path, monkeypatch, rows)
        got = rd.group(rd.collect())
        assert got["by_source"] == {"ashby": 2, "grnhse": 1}
        assert got["by_seniority"] == {"Mid Level": 1, "Senior Level": 2}
        assert got["by_fit_band"] == {"<62": 1, "62-74": 1, "unknown": 1}

    def test_dry_run_skips_llm_and_writes_nothing(self, tmp_path, monkeypatch, capsys):
        _, feed = _capture(monkeypatch)
        calls = _wire_rd(tmp_path, monkeypatch,
                         [_rejected(1), _rejected(2), _rejected(3)])
        assert rd.run(dry_run=True) == 0
        assert calls == []
        assert not (tmp_path / "rejection_digest.json").exists()
        assert feed == []
        out = capsys.readouterr().out
        assert "dry-run" in out
        # 3 rejections is over the LLM threshold: the read must say the call was
        # skipped for dry-run, NOT claim the count is under the threshold
        assert "skipped on dry-run" in out
        assert "no pattern worth reading" not in out

    def test_force_overrides_gate(self, tmp_path, monkeypatch):
        _capture(monkeypatch)
        _wire_rd(tmp_path, monkeypatch, [_rejected(1)])
        today_wd = datetime.now(LOCAL_TZ).weekday()
        monkeypatch.setattr(rd, "REPORT_WEEKDAY", (today_wd + 1) % 7)
        assert rd.run(force=True) == 0
        assert (tmp_path / "rejection_digest.json").exists()


# ---------------------------------------------------- interview_postmortem
def _wire_pm(tmp_path, monkeypatch, jobs_list, doc_age_days=3.0):
    monkeypatch.setattr(pm, "WAR_DIR", tmp_path / "war_room")
    monkeypatch.setattr(pm, "OUT_DIR", tmp_path / "postmortems")
    monkeypatch.setattr(pm, "TODOS", tmp_path / "todos.jsonl")
    monkeypatch.setattr(jobs, "load_jobs", lambda: jobs_list)
    (tmp_path / "war_room").mkdir(exist_ok=True)
    for j in jobs_list:
        if j.get("status") == "interview" and doc_age_days is not None:
            # war_room writes the doc at _safe(id) (fix 4: sanitized to block path
            # traversal); postmortem reads at the SAME _safe(id). Mirror that here so
            # the doc the stager stats for age is where it actually looks.
            doc = tmp_path / "war_room" / (pm._safe(j["id"]) + ".md")
            doc.write_text("war room doc")
            t = time.time() - doc_age_days * 86400
            os.utime(doc, (t, t))


class TestInterviewPostmortem:
    def test_stages_todo_and_template_once(self, tmp_path, monkeypatch):
        _wire_pm(tmp_path, monkeypatch, [_job(1, status="interview", company="CacheFly")])
        assert pm.run() == ["CacheFly"]
        todos = load_todos(tmp_path / "todos.jsonl")
        assert len(todos) == 1
        assert todos[0]["text"] == "Post-mortem: CacheFly - what worked, what to fix"
        assert todos[0]["source_ref"] == "postmortem_j1"
        md = (tmp_path / "postmortems" / "j1.md").read_text()
        assert md.count("## ") >= 5 and "ONE thing to fix" in md
        assert "—" not in md and "–" not in md
        # dedup: second run stages nothing, still one todo
        assert pm.run() == []
        assert len(load_todos(tmp_path / "todos.jsonl")) == 1

    def test_young_war_room_doc_not_due(self, tmp_path, monkeypatch):
        _wire_pm(tmp_path, monkeypatch,
                 [_job(1, status="interview")], doc_age_days=1.0)
        assert pm.run() == []
        assert not (tmp_path / "todos.jsonl").exists()

    def test_no_war_room_doc_not_due(self, tmp_path, monkeypatch):
        _wire_pm(tmp_path, monkeypatch,
                 [_job(1, status="interview")], doc_age_days=None)
        assert pm.run() == []

    def test_never_overwrites_filled_template(self, tmp_path, monkeypatch):
        _wire_pm(tmp_path, monkeypatch, [_job(1, status="interview")])
        (tmp_path / "postmortems").mkdir()
        (tmp_path / "postmortems" / "j1.md").write_text("HIS ANSWERS")
        pm.run()
        assert (tmp_path / "postmortems" / "j1.md").read_text() == "HIS ANSWERS"

    def test_dry_run_stages_nothing(self, tmp_path, monkeypatch):
        _wire_pm(tmp_path, monkeypatch, [_job(1, status="interview")])
        assert pm.run(dry_run=True) == ["Co1"]
        assert not (tmp_path / "todos.jsonl").exists()
        assert not (tmp_path / "postmortems").exists()

    def test_non_interview_jobs_ignored(self, tmp_path, monkeypatch):
        _wire_pm(tmp_path, monkeypatch, [_job(1, status="applied")])
        assert pm.run() == []

    def test_safe_filename_for_weird_ids(self, tmp_path, monkeypatch):
        j = _job(1, status="interview")
        j["id"] = "jobicy:148886"
        _wire_pm(tmp_path, monkeypatch, [j])
        pm.run()
        assert (tmp_path / "postmortems" / "jobicy_148886.md").exists()


# ------------------------------------------------------------ salary_ladder
def _wire_sl(tmp_path, monkeypatch, jobs_list):
    monkeypatch.setattr(sl, "OUT", tmp_path / "salary_ladder.json")
    monkeypatch.setattr(jobs, "load_jobs", lambda: jobs_list)
    return _capture(monkeypatch)


class TestSalaryLadder:
    def test_band_of(self):
        assert sl.band_of(99000) == "<$100k"
        assert sl.band_of(120000) == "$120-140k"   # lo inclusive
        assert sl.band_of(139999) == "$120-140k"
        assert sl.band_of(250000) == "$200k+"
        assert sl.band_of(None) == "unknown comp"
        assert sl.band_of("nope") == "unknown comp"

    def test_band_math(self, tmp_path, monkeypatch):
        rows = [_job(1, status="applied", comp_max=130000),
                _job(2, status="interview", comp_max=130000),
                _job(3, status="replied", comp_max=130000),
                _job(4, status="rejected", comp_max=105000),
                _job(5, status="applied", comp_max=None),
                _job(6, status="pending", comp_max=130000)]   # never applied: excluded
        _wire_sl(tmp_path, monkeypatch, rows)
        data = sl.build(rows)
        bands = {b["range"]: b for b in data["bands"]}
        assert bands["$120-140k"] == {"range": "$120-140k", "applied": 3,
                                      "replied": 2, "interviewed": 1}
        assert bands["$100-120k"]["applied"] == 1
        assert bands["$100-120k"]["replied"] == 0   # rejection is not a human reply
        assert bands["unknown comp"]["applied"] == 1
        assert bands["$200k+"]["applied"] == 0

    def test_read_names_best_band(self, tmp_path, monkeypatch):
        rows = ([_job(i, status="applied", comp_max=130000) for i in range(1, 3)]
                + [_job(3, status="interview", comp_max=130000)]
                + [_job(i, status="applied", comp_max=90000) for i in range(4, 9)])
        _wire_sl(tmp_path, monkeypatch, rows)
        data = sl.build(rows)
        assert "Best band: $120-140k" in data["read"]
        assert "1 interview" in data["read"]

    def test_not_enough_data_read(self, tmp_path, monkeypatch):
        rows = [_job(1, status="applied", comp_max=130000)]
        _wire_sl(tmp_path, monkeypatch, rows)
        assert "Not enough per-band data" in sl.build(rows)["read"]

    def test_run_writes_and_feeds_the_read(self, tmp_path, monkeypatch):
        pushes, feed = _wire_sl(tmp_path, monkeypatch,
                                [_job(i, status="applied", comp_max=130000)
                                 for i in range(1, 5)])
        data = sl.run()
        saved = json.loads((tmp_path / "salary_ladder.json").read_text())
        assert saved["read"] == data["read"]
        assert len(feed) == 1 and feed[0][1].startswith("Salary ladder:")
        assert pushes == []

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        _, feed = _wire_sl(tmp_path, monkeypatch, [_job(1, status="applied")])
        sl.run(dry_run=True)
        assert not (tmp_path / "salary_ladder.json").exists()
        assert feed == []

    def test_fresh_install_no_jobs(self, tmp_path, monkeypatch):
        _wire_sl(tmp_path, monkeypatch, [])
        assert sl.run() is None
        assert not (tmp_path / "salary_ladder.json").exists()


# -------------------------------------------------------------- stage_coach
def _wire_sc(tmp_path, monkeypatch, jobs_list):
    monkeypatch.setattr(sc, "OUT", tmp_path / "stage_coach.json")
    monkeypatch.setattr(jobs, "load_jobs", lambda: jobs_list)
    return _capture(monkeypatch)


class TestStageCoach:
    def test_playbook_mapping(self, tmp_path, monkeypatch):
        rows = [_job(1, status="replied"), _job(2, status="interview"),
                _job(3, status="offer"), _job(4, status="applied")]
        _wire_sc(tmp_path, monkeypatch, rows)
        coach = sc.run()["coach"]
        assert set(coach) == {"j1", "j2", "j3"}       # applied is not a live stage
        assert "propose 2 concrete slots" in coach["j1"]["line"]
        assert "War room" in coach["j2"]["line"] and "day 5" in coach["j2"]["line"]
        assert "Never accept same-day" in coach["j3"]["line"]
        assert "[SALARY_ANCHOR]" in coach["j3"]["line"]
        saved = json.loads((tmp_path / "stage_coach.json").read_text())
        assert saved["coach"]["j2"]["status"] == "interview"

    def test_one_feed_line_total(self, tmp_path, monkeypatch):
        rows = [_job(1, status="interview"), _job(2, status="interview"),
                _job(3, status="replied")]
        pushes, feed = _wire_sc(tmp_path, monkeypatch, rows)
        sc.run()
        assert len(feed) == 1                          # ONE line, not per job
        line = feed[0][1]
        assert line.startswith("Stage coach: 2 interview, 1 replied")
        assert "Co1" in line                           # hottest stage's job leads
        assert pushes == []

    def test_no_live_jobs_no_feed(self, tmp_path, monkeypatch):
        _, feed = _wire_sc(tmp_path, monkeypatch, [_job(1, status="applied")])
        data = sc.run()
        assert data["coach"] == {} and feed == []
        assert json.loads((tmp_path / "stage_coach.json").read_text())["coach"] == {}

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        _, feed = _wire_sc(tmp_path, monkeypatch, [_job(1, status="interview")])
        sc.run(dry_run=True)
        assert not (tmp_path / "stage_coach.json").exists()
        assert feed == []

    def test_fresh_install_no_jobs(self, tmp_path, monkeypatch):
        _wire_sc(tmp_path, monkeypatch, [])
        assert sc.run() is None


# ---------------------------------------------------------- takehome_helper
def _wire_th(tmp_path, monkeypatch, jobs_list, mail_lines=()):
    monkeypatch.setattr(th, "STATE", tmp_path / "takehome_state.json")
    monkeypatch.setattr(th, "OUT_DIR", tmp_path / "takehomes")
    monkeypatch.setattr(th, "TODOS", tmp_path / "todos.jsonl")
    mail = tmp_path / "mail.jsonl"
    mail.write_text("\n".join(mail_lines) + ("\n" if mail_lines else ""))
    monkeypatch.setattr(th, "MAIL_STORES", [mail])
    monkeypatch.setattr(jobs, "load_jobs", lambda: jobs_list)
    return _capture(monkeypatch)


class TestTakehomeHelper:
    def test_detects_from_mail_by_company_and_stages(self, tmp_path, monkeypatch):
        pushes, _ = _wire_th(
            tmp_path, monkeypatch,
            [_job(1, status="replied", company="CacheFly")],
            mail_lines=['{"subject": "CacheFly next steps: a short take-home exercise"}'])
        assert th.run() == ["CacheFly"]
        todos = load_todos(tmp_path / "todos.jsonl")
        assert len(todos) == 1 and todos[0]["source_ref"] == "takehome_j1"
        md = (tmp_path / "takehomes" / "j1.md").read_text()
        assert "Requirements checklist" in md
        assert "Timebox" in md and "4 focused hours" in md
        assert "Submission checklist" in md
        assert "—" not in md and "–" not in md
        assert len(pushes) == 1 and "Take-home detected: CacheFly" in pushes[0][0]

    def test_dedup_forever(self, tmp_path, monkeypatch):
        pushes, _ = _wire_th(
            tmp_path, monkeypatch,
            [_job(1, status="replied", company="CacheFly")],
            mail_lines=['{"subject": "CacheFly take-home assignment attached"}'])
        assert th.run() == ["CacheFly"]
        # second run, mention still present: nothing re-stages
        assert th.run() == []
        assert len(load_todos(tmp_path / "todos.jsonl")) == 1
        assert len(pushes) == 1
        # even with the todos file gone, the state file still dedups
        (tmp_path / "todos.jsonl").unlink()
        assert th.run() == []
        assert not (tmp_path / "todos.jsonl").exists()

    def test_detects_from_job_record_fields(self, tmp_path, monkeypatch):
        _wire_th(tmp_path, monkeypatch,
                 [_job(1, status="interview", company="8x8",
                       reason="gmail:human, sent a take-home brief")])
        assert th.run() == ["8x8"]

    def test_no_match_without_company_in_line(self, tmp_path, monkeypatch):
        _wire_th(tmp_path, monkeypatch,
                 [_job(1, status="replied", company="CacheFly")],
                 mail_lines=['{"subject": "OtherCo sent a take-home"}'])
        assert th.run() == []

    def test_dead_stage_jobs_ignored(self, tmp_path, monkeypatch):
        _wire_th(tmp_path, monkeypatch,
                 [_job(1, status="rejected", company="CacheFly"),
                  _job(2, status="skipped", company="CacheFly")],
                 mail_lines=['{"subject": "CacheFly take-home"}'])
        assert th.run() == []

    def test_dry_run_no_write_no_push(self, tmp_path, monkeypatch):
        pushes, _ = _wire_th(
            tmp_path, monkeypatch,
            [_job(1, status="replied", company="CacheFly")],
            mail_lines=['{"subject": "CacheFly take-home exercise"}'])
        assert th.run(dry_run=True) == ["CacheFly"]
        assert not (tmp_path / "todos.jsonl").exists()
        assert not (tmp_path / "takehomes").exists()
        assert not (tmp_path / "takehome_state.json").exists()
        assert pushes == []
        # and the real run afterwards still stages (dry-run burned no state)
        assert th.run() == ["CacheFly"]

    def test_fresh_install_no_jobs_no_stores(self, tmp_path, monkeypatch):
        monkeypatch.setattr(th, "STATE", tmp_path / "takehome_state.json")
        monkeypatch.setattr(th, "OUT_DIR", tmp_path / "takehomes")
        monkeypatch.setattr(th, "TODOS", tmp_path / "todos.jsonl")
        monkeypatch.setattr(th, "MAIL_STORES", [tmp_path / "missing.jsonl"])
        monkeypatch.setattr(jobs, "load_jobs", lambda: [])
        _capture(monkeypatch)
        assert th.run() == []
