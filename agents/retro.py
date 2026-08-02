#!/usr/bin/env python3
"""Weekly auto-retro: read the week's stores, write what-worked / what-to-change, and propose
ONE concrete config change for [OWNER] to approve. Closes the learning loop: collected data ->
a specific tuning nudge.

Auto-apply rule ([OWNER]-approved shortcut): if he approved the SAME config key the last
two weeks running, the third same-key proposal applies itself and just tells him. Every
other proposal stays a human decision, now with one-tap Approve/Skip buttons on the push
when config public_base_url is set.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import planner  # noqa: E402
import jobs  # noqa: E402
import networking  # noqa: E402

RETRO_MD = ROOT / "store" / "retro.md"
PROPOSAL = ROOT / "store" / "retro_proposal.json"
HISTORY = ROOT / "store" / "retro_history.jsonl"


def _act_url(action: str) -> str | None:
    """Signed one-tap URL. Mirrors server.act_sig: one action, ~one day, token never leaks."""
    base = (planner._config().get("public_base_url") or "").rstrip("/")
    if not base:
        return None
    import hashlib
    import hmac
    from store_lib import sign_secret
    sig = hmac.new(sign_secret().encode(),
                   f"act:{action}:{now_iso()[:10]}".encode(), hashlib.sha256).hexdigest()[:20]
    return f"{base}/api/act/{action}?sig={sig}"


def _same_key_streak(key: str) -> int:
    """How many of the most recent applied-retro records touched this same config key."""
    try:
        lines = HISTORY.read_text().splitlines()
    except OSError:
        return 0
    n = 0
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if key in (rec.get("applied") or {}):
            n += 1
        else:
            break
    return n


def _auto_apply() -> bool:
    from store_lib import secret
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://127.0.0.1:8765/api/retro/apply", data=b"{}", method="POST",
            headers={"X-Brain-Token": secret("brain_token"), "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception:  # noqa: BLE001
        return False


def _gather() -> dict:
    js = jobs.load_jobs()
    sc = Counter(j.get("status") for j in js)
    src = {}
    for j in js:
        d = src.setdefault(j.get("source", "?"), {"good": 0, "captcha": 0, "total": 0})
        d["total"] += 1
        if j.get("status") in ("applied", "confirmed", "replied", "interview"):
            d["good"] += 1
        if j.get("status") == "skipped" and j.get("reason") == "captcha":
            d["captcha"] += 1
    ns = Counter(x.get("status") for x in networking.load_queue())
    usage = Counter()
    try:
        for ln in (ROOT / "store" / "usage.jsonl").read_text().splitlines():
            try:
                usage[json.loads(ln).get("feature", "?")] += 1
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    seen, worked, booked = set(), 0, 0
    try:
        for ln in (ROOT / "store" / "warm_dispo.jsonl").read_text().splitlines():
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if r.get("id") and r["id"] not in seen:
                seen.add(r["id"]); worked += 1
            if r.get("dispo") == "booked":
                booked += 1
    except OSError:
        pass
    return {"jobs": dict(sc), "skip_reasons": dict(getattr(jobs, "skip_reasons", lambda: {})()),
            "jobs_by_source": src, "network": dict(ns), "claude_calls_by_feature": dict(usage),
            "warm_calls": {"worked": worked, "booked": booked},
            "metrics_14d": _read_json_lines("store/metrics.jsonl")[-14:],
            "ats_stats": _read_json("store/ats_stats.json"),
            "win_loss": _read_json("store/winloss.json"),
            "insights_recent": _read_json_lines("store/insights.jsonl")[-5:],
            "cold_campaigns": _cold_stats()}


def _cold_stats():
    out = {}
    for r in _read_json_lines("store/cold_pipeline.jsonl"):
        c = r.get("campaign") or "wl"
        d = out.setdefault(c, {"staged": 0, "enrolled": 0})
        if r.get("status") == "staged":
            d["staged"] += 1
        elif r.get("status") == "enrolled":
            d["enrolled"] += 1
    return out


def _read_json(rel):
    try:
        return json.loads((ROOT / rel).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _read_json_lines(rel):
    out = []
    try:
        for line in (ROOT / rel).read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return out


RETRO_PROMPT = """You are [OWNER]'s chief-of-staff writing a short weekly retro from his automation
system's own data. DATA:
%s

Write a tight retro (~170 words, plain, first person as his advisor, no fluff, NO em-dashes):
CAUSAL RULE: never report a number without its likely cause from the linked data (e.g. 'confirms dropped because ashby volume rose and ashby confirms at 3 percent'). 
COUNTERFACTUAL RULE: include exactly ONE 'if you had...' line quantified from his real rates.
1) What worked this week (2-3 bullets)
2) What to change (2-3 bullets)
Then propose EXACTLY ONE concrete config change the numbers justify. Allowed changes + keys:
- Raise the job salary floor: {"job_min_yearly": <int, e.g. 120000>}
- Blacklist a captcha-heavy ATS source: {"job_blacklist_source": "<source name>"}
- Lower the LinkedIn daily connect cap: {"network_connect_cap": <int>}
- Raise the content auto-approve score bar: {"auto_approve_min": <int 0-10>}
Pick the single highest-leverage one the data supports.
Return ONLY JSON: {"retro":"<markdown>","proposal":{"label":"<one line>","why":"<one line>","change":{<key>:<value>}}}"""


def run():
    parsed = planner._extract_json(
        planner._cli(RETRO_PROMPT % json.dumps(_gather(), indent=1), timeout=180, feature="plan") or "")
    if not isinstance(parsed, dict) or not parsed.get("retro"):
        print("retro: no usable output")
        return
    RETRO_MD.write_text("# Weekly Retro, " + now_iso()[:10] + "\n\n" + parsed["retro"])
    prop = parsed.get("proposal") or {}
    has = bool(prop.get("change"))
    if has:
        prop.update(created=now_iso(), status="pending")
        PROPOSAL.write_text(json.dumps(prop, indent=2))
        key = next(iter(prop["change"]), "")
        if key and _same_key_streak(key) >= 2 and _auto_apply():
            planner.feed_add("retro", "Retro auto-applied (same change approved 2 weeks running): "
                             + prop.get("label", ""))
            planner.notify("Retro auto-applied", prop.get("label", "config change")
                           + ". You approved this same knob the last 2 weeks, so it went in.",
                           tags="chart_with_upwards_trend")
            print("retro written + auto-applied:", prop.get("label", ""))
            return
    actions = None
    if has:
        a, s = _act_url("retro_apply"), _act_url("retro_dismiss")
        if a and s:
            actions = [{"action": "view", "label": "Approve", "url": a},
                       {"action": "view", "label": "Skip", "url": s}]
    planner.feed_add("retro", "Weekly retro ready" + (" + 1 proposed change" if has else ""))
    planner.notify("Weekly retro ready", "Read it and approve or skip the proposed tuning change.",
                   tags="chart_with_upwards_trend", actions=actions)
    print("retro written." + (" proposal: " + prop.get("label", "") if has else ""))


if __name__ == "__main__":
    run()
