"""Regression pins for the 2026-08-12 sibling-install field report.

A second install of this kit produced a rigorous failure analysis
(REPORT-auto-apply-2026-08-12) and roughly half its findings were SHARED-CODE
defects live in every copy. Each test here pins one of them by its report ID:

  D1  apply-chain idle guard misread a failing round as progress (80 jobs burned)
  D2  in-flight jobs stranded in 'applying' on any non-normal chain exit
  D3  preflight spent a full operator session rediscovering an HTTP 403
  B1  unresolved [TOKEN]s in executable bash inside browser-agent skills
  B2  the experience gate compared against the ORIGINAL owner's hardcoded years
  Bx  the apply prompt carried the original owner's background and raw tokens
  C1  resume tailoring paid its LLM call then silently discarded every render
  C2  the answer bank ships empty and nothing warns (largest recurring LLM cost)
  C3  resume attribution recorded whatever file existed at callback time
  E10 'applied' rested entirely on the operator's unverified self-report
  F1  one requisition posted under two company names evaded every dedupe
  A1  upgrade.sh cloned over local commits without a word
  A3  a stale server process ran 10-day-old code under a green test suite

Where a defect is orchestration-shaped (a thread loop / a shell script), the pin
is a source assertion -- same technique the report's own self-check script used.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import jobs  # noqa: E402
import job_fit_signals  # noqa: E402

SERVER_SRC = (ROOT / "app" / "server.py").read_text()


# ---------------------------------------------------------------- D1 / D2

def test_d1_idle_guard_measures_progress_not_queue_movement():
    # The broken arm: a failing round consumes jobs, so approved_after drops and
    # `approved_after >= approved_before` reads the failure as progress forever.
    assert "if approved_after >= approved_before" not in SERVER_SRC
    # the progress-only guard exists
    assert "if jobs.applied_today() <= applied_before:" in SERVER_SRC


def test_d2_inflight_release_runs_on_every_exit_path():
    # the finally block of _apply_chain releases anything still 'applying'
    tail = SERVER_SRC.split("def _apply_chain()", 1)[1][:12000]
    fin = tail.split("finally:", 1)
    assert len(fin) == 2, "apply chain lost its finally block"
    assert "chain exited with this job in flight" in fin[1][:2000]
    assert 'expect="applying"' in fin[1][:2000]


# ---------------------------------------------------------------- D3

class _FakeHTTPError(Exception):
    pass


def _mk_net_guard(monkeypatch, head_code=None, get_code=None):
    """Install a stub net_guard whose safe_urlopen raises HTTPError(code) per method."""
    import types
    import urllib.error

    def _err(code):
        return urllib.error.HTTPError("https://x.example/j", code, "err", {}, None)

    def safe_urlopen(u, method="GET", timeout=8):
        code = head_code if method == "HEAD" else get_code
        if code:
            raise _err(code)
        return object()

    ng = types.SimpleNamespace(
        public_url_ok=lambda u: (True, ""),
        safe_urlopen=safe_urlopen,
    )
    monkeypatch.setitem(sys.modules, "net_guard", ng)
    return ng


def _tmp_queue(monkeypatch, tmp_path, recs):
    q = tmp_path / "jobs.jsonl"
    q.write_text("".join(json.dumps(r) + "\n" for r in recs))
    monkeypatch.setattr(jobs, "QUEUE", q)
    return q


def test_d3_confirmed_403_diverts_to_manual_pile(monkeypatch, tmp_path):
    _tmp_queue(monkeypatch, tmp_path, [
        {"id": "j1", "status": "approved", "title": "Mgr", "company": "Acme",
         "apply_url": "https://x.example/j"}])
    _mk_net_guard(monkeypatch, head_code=403, get_code=403)
    alive = jobs.preflight_drop([{"id": "j1", "apply_url": "https://x.example/j"}])
    assert alive == []
    rec = next(x for x in jobs.load_jobs() if x["id"] == "j1")
    assert rec["status"] == "skipped"
    assert rec["reason"].startswith("ats_wall_divert")
    # and the prefix routes it into the human-finishable pile
    assert any(m["id"] == "j1" for m in jobs.needs_manual())


def test_d3_head_only_403_is_kept_for_the_operator(monkeypatch, tmp_path):
    # some ATSes 403 the HEAD verb only; a passing GET must keep the job alive
    _tmp_queue(monkeypatch, tmp_path, [
        {"id": "j1", "status": "approved", "apply_url": "https://x.example/j"}])
    _mk_net_guard(monkeypatch, head_code=403, get_code=None)
    alive = jobs.preflight_drop([{"id": "j1", "apply_url": "https://x.example/j"}])
    assert [j["id"] for j in alive] == ["j1"]


def test_d3_404_still_expires(monkeypatch, tmp_path):
    _tmp_queue(monkeypatch, tmp_path, [
        {"id": "j1", "status": "approved", "apply_url": "https://x.example/j"}])
    _mk_net_guard(monkeypatch, head_code=404)
    alive = jobs.preflight_drop([{"id": "j1", "apply_url": "https://x.example/j"}])
    assert alive == []
    assert next(x for x in jobs.load_jobs() if x["id"] == "j1")["status"] == "expired"


# ---------------------------------------------------------------- B2

def test_b2_yoe_gate_reads_the_owner_not_a_constant(monkeypatch):
    import owner
    monkeypatch.setattr(owner, "_cache", {**owner._cache, "years_experience": "3"})
    assert jobs._stated_yoe() == 3
    # and the old baked-in phrasing is gone from the source
    assert "vs 6 stated" not in (ROOT / "agents" / "jobs.py").read_text()


def test_b2_stated_yoe_survives_garbage(monkeypatch):
    import owner
    monkeypatch.setattr(owner, "_cache", {**owner._cache, "years_experience": "a few"})
    assert jobs._stated_yoe() == 6  # falls back, never raises


# ---------------------------------------------------------------- B1

def test_b1_no_tokens_in_executable_skill_bash():
    # the report's own self-check #2: a [TOKEN] on an executable line never
    # resolves (owner.personalize only rewrites LLM prompts, never files)
    bad = []
    for md in (ROOT / "browser-agent").rglob("*.md"):
        for i, line in enumerate(md.read_text().splitlines(), 1):
            if re.match(r"^(cd|bash|python|\.venv)[^|]*\[[A-Z_]+\]", line):
                bad.append(f"{md.name}:{i}")
    assert not bad, f"unresolved tokens in executable bash: {bad}"


# ---------------------------------------------------------------- Bx (prompt)

def test_prompt_is_personalized_and_carries_no_previous_owner():
    # the spawn boundary resolves tokens (raw `claude -p`, not planner._cli) ...
    assert "owner.personalize(text)" in SERVER_SRC
    # ... and the first owner's baked-in background is out of the PROMPT BODY
    # (the docstring explaining the old bug may still name it)
    body = SERVER_SRC.split("def _build_prompt", 1)[1].split("\ndef ", 1)[0]
    assert "fractional COO" not in body
    assert "GoHighLevel + HubSpot" not in body
    assert "_owner_background()" in body


def test_prompt_carries_the_new_operator_rules():
    for marker in ("state_abbrev", "zip5", "read back the filename",
                   "cover-letter field gets FILLED", "decline to answer",
                   "HEADLESS and unattended", "&note=", "REASON SEMANTICS"):
        assert marker in SERVER_SRC, f"operator rule missing from prompt: {marker}"


# ---------------------------------------------------------------- C1

def test_c1_no_renderer_means_no_llm_spend(monkeypatch, capsys):
    import resume_tailor
    monkeypatch.setattr(resume_tailor, "_renderer_available", lambda: "")
    # any LLM touch would blow up loudly
    import planner
    monkeypatch.setattr(planner, "_cli",
                        lambda *a, **k: pytest.fail("LLM called with no renderer"))
    assert resume_tailor.run(limit=5) == 0
    assert "NO PDF renderer" in capsys.readouterr().out


def test_c1_renderer_probe_reports_a_backend(monkeypatch, tmp_path):
    import resume_tailor
    monkeypatch.setattr(resume_tailor, "PW_DIR", tmp_path / "nope")
    monkeypatch.setattr(resume_tailor, "_chrome_bin", lambda: None)
    assert resume_tailor._renderer_available() == ""
    monkeypatch.setattr(resume_tailor, "_chrome_bin", lambda: "/x/chrome")
    assert resume_tailor._renderer_available() == "chrome"


# ---------------------------------------------------------------- C2

def test_c2_seed_populates_the_bank_without_an_llm(monkeypatch, tmp_path):
    import answer_bank
    bank = tmp_path / "answer_bank.json"
    monkeypatch.setattr(answer_bank, "BANK", bank)
    monkeypatch.setattr(answer_bank.planner, "_cli",
                        lambda *a, **k: pytest.fail("seed must not call the LLM"))
    assert answer_bank.seed(auto=True) == 0
    qa = json.loads(bank.read_text())["qa"]
    assert len(qa) >= 6
    # salary and EEO stay out of the bank by design
    assert not any("salary" in x["q"].lower() for x in qa)
    assert not any("veteran" in x["q"].lower() or "disability" in x["q"].lower() for x in qa)


# ---------------------------------------------------------------- C3

def test_c3_note_fields_stamps_without_touching_status(monkeypatch, tmp_path):
    _tmp_queue(monkeypatch, tmp_path, [{"id": "j1", "status": "applying"}])
    jobs.note_fields("j1", resume_file="store/resume_tailored/x.pdf")
    rec = next(x for x in jobs.load_jobs() if x["id"] == "j1")
    assert rec["resume_file"] == "store/resume_tailored/x.pdf"
    assert rec["status"] == "applying"


def test_c3_attribution_reads_the_stamp_not_the_disk():
    # the callback attributes from the stamped record, not a file-existence probe
    seg = SERVER_SRC.split("def api_jobs_applied", 1)[1][:3000]
    assert 'res.get("resume_file")' in seg
    assert "safe_name(jid)}.pdf\").exists()" not in seg


# ---------------------------------------------------------------- E10 / 9.3

def test_e10_unconfirmed_applied_lands_in_needs_verify(monkeypatch, tmp_path):
    _tmp_queue(monkeypatch, tmp_path, [
        {"id": "a", "status": "applying"},
        {"id": "b", "status": "applying"},
        {"id": "c", "status": "approved"},
        {"id": "d", "status": "approved"},
    ])
    jobs.set_status("a", "applied", "confirm: Thank you for applying to Acme")
    jobs.set_status("b", "applied", "unconfirmed (operator quoted no submission confirmation; verify in ATS)")
    jobs.set_status("c", "skipped", "inflight_timeout (operator ended without callback)")
    jobs.set_status("d", "skipped", "attempt_cap (2 tries, walled)")
    ids = {x["id"] for x in jobs.needs_verify()}
    assert ids == {"b", "c", "d"}, "confirmed applied must stay out; all three uncertain kinds in"


def test_e10_applied_endpoint_never_lets_a_replay_rewrite_the_confirmation():
    # a note-less replay building a reason would stomp 'confirm:' with 'unconfirmed'
    seg = SERVER_SRC.split("def api_jobs_applied", 1)[1][:3000]
    assert "if not already_applied:" in seg
    assert "unconfirmed (operator quoted no submission confirmation" in seg


# ---------------------------------------------------------------- F1

def _rq(title, jid, status="approved", created=""):
    return {"id": jid, "title": title, "company": f"co-{jid}", "status": status,
            "created": created}


def test_f1_shared_req_number_across_companies_is_a_dupe():
    a = _rq("Marketing Manager (32693)", "a", status="applied")
    b = _rq("Marketing Manager (32693)", "b", status="approved")
    why = job_fit_signals.req_number_dupe_reason(b, [a, b])
    assert why.startswith("req_number_dupe")
    assert "a" in why


def test_f1_two_approved_siblings_do_not_mutually_block():
    a = _rq("Ops Lead (55501)", "a", created="2026-08-01")
    b = _rq("Ops Lead (55501)", "b", created="2026-08-02")
    both = [a, b]
    blocked = [j["id"] for j in both
               if job_fit_signals.req_number_dupe_reason(j, both)]
    assert blocked == ["b"], "exactly one survivor (the earlier-created record)"


def test_f1_bare_years_are_not_req_numbers():
    a = _rq("Marketing Manager 2026", "a", status="applied")
    b = _rq("Marketing Manager 2026", "b")
    assert job_fit_signals.req_number_dupe_reason(b, [a, b]) == ""


def test_f1_wired_into_extra_block_reason():
    import inspect
    assert "req_number_dupe_reason" in inspect.getsource(job_fit_signals.extra_block_reason)


# ---------------------------------------------------------------- A1 / A3

def test_a1_upgrade_refuses_to_bury_local_commits():
    src = (ROOT / "upgrade.sh").read_text()
    assert "rev-list --count HEAD --not --remotes" in src
    assert "--force" in src
    assert "Stopped. Nothing changed." in src
    r = subprocess.run(["bash", "-n", str(ROOT / "upgrade.sh")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ------------------------------------------------- inherited-business audit
# owner.py retargets IDENTITY. It cannot retarget the BUSINESS MODEL, which is
# baked into agent prompts and business-library/ as prose. A second install ran
# for weeks generating fluent, correct-sounding output aimed at the original
# owner's market, and its handoff doc recorded "a recurring task all session was
# finding and retargeting his data" -- done by hand, with no map. That is what
# tools/retarget_audit.py produces.

def test_retarget_audit_finds_and_ranks_by_runtime_impact():
    sys.path.insert(0, str(ROOT / "tools"))
    import retarget_audit as ra

    hits = ra.scan()
    assert hits, "audit found nothing; the term list has probably rotted"
    # an agent prompt outranks a doc, regardless of hit count
    assert ra._tier("agents/content_gen.py")[0] == 1
    assert ra._tier("business-library/offers.md")[0] == 2
    assert ra._tier("skills/yours/money-proposal/SKILL.md")[0] == 3
    assert ra._tier("README.md")[0] == 4


def test_retarget_audit_ignores_identity_and_vendored_skills():
    sys.path.insert(0, str(ROOT / "tools"))
    import retarget_audit as ra

    # identity tokens are owner.py's job; flagging them here would bury the signal
    pats = " ".join(p for p, _ in ra.TERMS)
    assert "OWNER" not in pats
    # third-party skills are not ours to retarget
    assert not any(f.startswith("skills/third-party/") for f in ra._tracked_files())


def test_retarget_audit_never_writes():
    src = (ROOT / "tools" / "retarget_audit.py").read_text()
    for banned in ("write_text(", "open(", "unlink(", "replace(", "rmtree"):
        assert banned not in src.split('"""', 2)[2], f"audit tool must be read-only: {banned}"


# --------------------------------------------- morning-chain lane gating
# `lane` was declared but only the money lane was ever gated, so `lite` skipped
# money and then ran 53 ungated steps anyway, including tests/run_golden.py and
# its ~12 real LLM calls. On a $20 plan that is the whole budget, gone before the
# job search runs.

def _morning_steps(profile: str) -> int:
    """Count steps morning.sh would run under a profile, honouring lane gates."""
    def lane(n):
        if profile == "jobs":
            return n in ("core", "jobs")
        if profile == "lite":
            return n in ("core", "jobs", "jobsx")
        return True

    stack, count = [], 0
    for ln in (ROOT / "agents" / "morning.sh").read_text().splitlines():
        m = re.match(r"\s*if lane (\w+); then", ln)
        m2 = re.match(r'\s*if \[ "\$\(date \+%u\)" = "\d" \] && lane (\w+); then', ln)
        if m or m2:
            stack.append(lane((m or m2).group(1)))
            continue
        if re.match(r"\s*fi\s+# end", ln):
            if stack:
                stack.pop()
            continue
        if ln.strip().startswith("#"):
            continue
        if "$RUN " in ln or "bash agents/" in ln or "bash tools/" in ln:
            if all(stack):
                count += 1
    return count


def test_lane_gates_actually_reduce_the_morning_chain():
    jobs, lite, full = (_morning_steps(p) for p in ("jobs", "lite", "full"))
    assert jobs < lite < full, (jobs, lite, full)
    # the whole point: a small-plan profile must be a LARGE reduction, not a trim
    assert jobs < full * 0.45, f"jobs profile runs {jobs}/{full}; barely a saving"


def test_every_declared_lane_actually_gates_something():
    src = (ROOT / "agents" / "morning.sh").read_text()
    for lane_name in ("money", "jobs", "jobsx", "outreach", "analytics"):
        assert f"if lane {lane_name}" in src or f"&& lane {lane_name}" in src, \
            f"lane '{lane_name}' is declared but never gates anything"


def test_real_llm_calls_never_run_under_a_small_profile():
    # tests/run_golden.py makes ~12 real LLM calls; on a $20 plan that is the budget
    assert "run_golden.py" not in _reachable_steps("jobs")
    assert "run_golden.py" not in _reachable_steps("lite")
    assert "run_golden.py" in _reachable_steps("full")
    # content generation is the other big spender with no job-hunt value
    assert "content_gen.py" not in _reachable_steps("jobs")


def _reachable_steps(profile: str) -> set:
    """Which step scripts a profile would actually execute."""
    def lane(n):
        if profile == "jobs":
            return n in ("core", "jobs")
        if profile == "lite":
            return n in ("core", "jobs", "jobsx")
        return True

    stack, out = [], set()
    for ln in (ROOT / "agents" / "morning.sh").read_text().splitlines():
        m = re.match(r"\s*if lane (\w+); then", ln)
        m2 = re.match(r'\s*if \[ "\$\(date \+%u\)" = "\d" \] && lane (\w+); then', ln)
        if m or m2:
            stack.append(lane((m or m2).group(1)))
            continue
        if re.match(r"\s*fi\s+# end", ln):
            if stack:
                stack.pop()
            continue
        if ln.strip().startswith("#"):
            continue
        hit = re.search(r"(?:\$RUN|bash) ([A-Za-z0-9/_.]+)", ln)
        if hit and all(stack):
            out.add(Path(hit.group(1)).name)
    return out


def test_the_brief_survives_every_profile():
    # a profile that produces no daily brief reads as a broken system
    src = (ROOT / "agents" / "morning.sh").read_text()
    brief = next(l for l in src.splitlines() if "daily_brief.py" in l and "$RUN" in l)
    before = src.split(brief)[0]
    opened = len(re.findall(r"^\s*if lane ", before, re.M))
    closed = len(re.findall(r"^\s*fi\s+# end", before, re.M))
    assert opened == closed, "daily_brief.py is inside a lane gate; it must run always"


def test_jobhunt_profile_exists_and_selects_the_jobs_lane():
    sys.path.insert(0, str(ROOT / "tools"))
    import tune_for_plan as t
    assert "jobhunt" in t.PROFILES
    assert t.PROFILES["jobhunt"]["morning_profile"] == "jobs"
    assert t.PROFILES["jobhunt"]["job_apply_model"].startswith("claude-haiku")
    for k in t.KEYS:
        assert k in t.PROFILES["jobhunt"], f"jobhunt profile missing {k}"


# ------------------------------------------------------ entry-context hygiene

def test_context_hygiene_reads_entry_docs_and_stays_read_only():
    sys.path.insert(0, str(ROOT / "tools"))
    import context_hygiene as ch

    rows = ch.audit()
    assert rows, "no entry documents found"
    names = {r["path"] for r in rows}
    assert "CLAUDE.md" in names
    assert all("/" not in p or p in ch.ENTRY_ALWAYS for p in names), \
        "entry audit must cover repo-root docs only"
    src = (ROOT / "tools" / "context_hygiene.py").read_text()
    body = src.split('"""', 2)[2]
    for banned in ("write_text(", "unlink(", "rmtree", "os.replace"):
        assert banned not in body, f"hygiene tool must never write: {banned}"


def test_entry_context_stays_small_enough_to_read_every_session():
    sys.path.insert(0, str(ROOT / "tools"))
    import context_hygiene as ch

    words = sum(r["words"] for r in ch.audit())
    # This kit sits near 10k words. A sibling install reached 63k across 29 root
    # docs, which is ~84k tokens of prior loaded before any work on a $20 plan.
    # The ceiling is deliberately generous; it exists to catch drift toward that.
    assert words < 30000, (
        f"entry context is {words} words. Root docs are read every session: "
        "move history and dated changelogs into an -ARCHIVE.md.")


def test_every_doubt_pattern_explains_its_own_false_positives():
    sys.path.insert(0, str(ROOT / "tools"))
    import context_hygiene as ch

    # a linter that cannot be argued with gets ignored, then disabled
    for _pat, kind, why in ch.PATTERNS:
        assert len(why) > 80, f"{kind} needs a real explanation, not a label"


# ------------------------------------------------------ changelog archiving

DOC = """# NOTES

Preamble stays.

## PART 1 - OPEN TO-DOS

### 1.1 Still broken
Fix the thing.

## PART 3 - DONE / RESOLVED

### old item
It got fixed.

## 2026-08-12 - the day it broke
A long story about an incident.

### sub-detail of that day
More of the story.

## PART 4 - LIMITATIONS

Deliberate, not bugs.
"""


def test_archive_split_keeps_open_work_and_moves_history():
    sys.path.insert(0, str(ROOT / "tools"))
    import archive_changelog as ac

    live, arch = ac.split(DOC)
    assert "PART 1 - OPEN TO-DOS" in live
    assert "Fix the thing." in live
    assert "PART 4 - LIMITATIONS" in live
    assert "Preamble stays." in live
    assert "PART 3 - DONE / RESOLVED" in arch
    assert "2026-08-12" in arch
    # a deeper heading under a dated section belongs to that section
    assert "sub-detail of that day" in arch
    assert "sub-detail of that day" not in live


def test_archive_split_loses_nothing():
    sys.path.insert(0, str(ROOT / "tools"))
    import archive_changelog as ac

    live, arch = ac.split(DOC)
    kept = set((live + arch).splitlines())
    lost = [ln for ln in DOC.splitlines() if ln.strip() and ln not in kept]
    assert not lost, f"content lost in the split: {lost}"


def test_archive_split_is_a_noop_without_dated_sections():
    sys.path.insert(0, str(ROOT / "tools"))
    import archive_changelog as ac

    live, arch = ac.split("# Doc\n\n## Open\n\nstuff\n")
    assert arch.strip() == ""
    assert "stuff" in live


def test_a3_stale_server_check_exists_and_doctor_runs_it():
    import ast
    tool = ROOT / "tools" / "check_server_fresh.py"
    ast.parse(tool.read_text())
    mk = (ROOT / "Makefile").read_text()
    assert "server-fresh" in mk.split("doctor:", 1)[1].splitlines()[0], \
        "doctor must include the stale-process check"
