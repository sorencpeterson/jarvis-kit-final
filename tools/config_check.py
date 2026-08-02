#!/usr/bin/env python3
"""J184: validate store/config.json against a schema, so a bad knob fails loudly at
6:30am instead of silently breaking a feature mid-run.

Schema is intentionally hand-written against the REAL config (see SCHEMA below), not
aspirational — if a key exists in config.json and is reasonable, the schema accepts it.
Unknown extra keys are allowed (config.json grows via ad-hoc notes like `_note`, `_jobs_note`);
this only enforces the keys/types we actually depend on elsewhere in the codebase.

Usage:
  tools/config_check.py            # validate store/config.json, print PASS/FAIL, exit 0/1
  tools/config_check.py --file X   # validate a different file (used by tests)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "store" / "config.json"

# Known models in use across the fleet as of the 2026-07 window. Keep this list loose
# (allow any claude-* / gpt-* / dall-e-* id) rather than an exact-match allowlist, since
# [OWNER] bumps model versions from the CLI and this check must not become the reason a
# perfectly valid new model id fails the gate.
MODEL_PREFIXES = ("claude-", "gpt-", "dall-e-", "o1-", "o3-", "o4-")

# Required top-level keys and their expected python type(s). A key absent from config.json
# is only an ERROR if listed here; anything else is optional.
REQUIRED_KEYS: dict[str, type | tuple[type, ...]] = {
    "ntfy_topic": str,
    "push_full": bool,
    "auto_approve_min": int,
    "job_scan_target": int,
    "job_daily_apply_cap": int,
    "job_apply_batch": int,
    "job_apply_concurrency": int,
    "job_apply_model": str,
    "job_auto": bool,
    "job_min_yearly": int,   # drop jobs POSTING a max below this (0 = keep all); pool size
    "salary_floor": int,     # minimum salary to ASK for on forms (0 = match each posting)
    "job_evening_chain": int,  # >0 = auto scan+apply in the evening window ([OWNER]'s 7pm lane)
    "evening_hour": int,       # local hour the evening lane opens (default 19; closes 22)
    "content_daily_new": int,   # fresh LinkedIn drafts generated per day (default 6)
    "content_max_fresh": int,   # ceiling on live drafts+approved (default 30)
    "content_stale_days": int,  # drafts unapproved this many days auto-archive (default 14)
    "money_session": int,       # >0 = 18:30 daily push of his 5 highest-value clicks (default on)
    "pair_fit_min": int,        # min fit for the apply->LinkedIn pairing bridge (default 72)
    "pair_daily_cap": int,      # max paired sourcing targets/day (connect budget guard, default 5)
    "models": dict,
    "network": dict,
    "cold_daily_enroll": int,
    "cold_domains": list,
    "job_morning_chain": int,
    "webfix_daily_enroll": int,
    "plan": dict,
    "payment_links": dict,
}

# Optional keys we still type-check IF present (never required, so older/newer configs
# that haven't grown a knob yet still pass).
OPTIONAL_KEYS: dict[str, type | tuple[type, ...]] = {
    "openai_api_key": str,
    "image_model": str,
    "image_quality": str,
    "elevenlabs_api_key": str,
    "elevenlabs_voice_id": str,
    "job_blacklist": list,
    "public_base_url": str,
    "daily_token_budget": (int, float),
    "job_tailor_resume": int,   # 1 = per-job tailored resume PDFs (resume_tailor.py); 0 = static only
}

# models{} sub-keys we know callers look up by name (planner._models(), _cli feature
# routing). A missing feature key just means that feature falls back to "default", so
# these are checked for TYPE if present, and we only require "default" to exist.
KNOWN_MODEL_FEATURES = (
    "default", "interpret", "plan", "tone_screen", "brief", "content",
    "networking", "reply", "jarvis", "proposal", "over_budget", "tailor",
)

PRICING_TIERS = ("landing", "standard", "booking", "whiteglove", "agencyfirst", "webfix",
                  "care_growth", "install")


def _type_name(t) -> str:
    if isinstance(t, tuple):
        return " or ".join(x.__name__ for x in t)
    return t.__name__


def check(cfg: dict) -> list[str]:
    """Return a list of human-readable errors. Empty list = valid."""
    errors = []

    if not isinstance(cfg, dict):
        return ["config root must be a JSON object"]

    # 1. required keys present + correctly typed
    for key, typ in REQUIRED_KEYS.items():
        if key not in cfg:
            errors.append(f"missing required key: {key!r}")
            continue
        val = cfg[key]
        # bool is a subclass of int in python; don't let a bool silently pass an int check
        if typ is int and isinstance(val, bool):
            errors.append(f"{key!r}: expected int, got bool")
        elif not isinstance(val, typ):
            errors.append(f"{key!r}: expected {_type_name(typ)}, got {type(val).__name__}")

    # 2. optional keys, type-checked only if present
    for key, typ in OPTIONAL_KEYS.items():
        if key in cfg and cfg[key] is not None:
            val = cfg[key]
            if typ is int and isinstance(val, bool):
                errors.append(f"{key!r}: expected int, got bool")
            elif not isinstance(val, typ):
                errors.append(f"{key!r}: expected {_type_name(typ)}, got {type(val).__name__}")

    # 3. models dict: keys are strings, values are model-name-shaped strings; "default" required
    models = cfg.get("models")
    if isinstance(models, dict):
        if "default" not in models:
            errors.append("models: missing 'default' entry (feature routing falls back to it)")
        for feat, mval in models.items():
            if not isinstance(mval, str) or not mval:
                errors.append(f"models[{feat!r}]: expected non-empty string, got {mval!r}")
                continue
            if not mval.startswith(MODEL_PREFIXES):
                errors.append(
                    f"models[{feat!r}] = {mval!r}: doesn't look like a known model id "
                    f"(expected prefix one of {MODEL_PREFIXES})")

    # 4. plan dict: keys look like YYYY-MM, values are numeric targets
    plan = cfg.get("plan")
    if isinstance(plan, dict):
        for k, v in plan.items():
            if not (isinstance(k, str) and len(k) == 7 and k[4] == "-"
                    and k[:4].isdigit() and k[5:].isdigit()):
                errors.append(f"plan key {k!r}: expected 'YYYY-MM' shape")
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                errors.append(f"plan[{k!r}] = {v!r}: expected a number")

    # 5. payment_links: dict of tier -> string (empty string allowed = not yet set up)
    pay = cfg.get("payment_links")
    if isinstance(pay, dict):
        for k, v in pay.items():
            if not isinstance(v, str):
                errors.append(f"payment_links[{k!r}]: expected string (possibly empty), got {type(v).__name__}")
        # unknown tier keys aren't an error (new tiers get added ahead of code sometimes);
        # only warn-via-error if a KNOWN tier is malformed, which the loop above covers.

    # 6. network dict shape: daily/weekly sub-dicts of int counters, plus the
    # A54-59 volume/efficiency knobs li_budget.py actually reads (Y: these were
    # live in config.json and gate real LinkedIn execution, but unvalidated).
    net = cfg.get("network")
    if isinstance(net, dict):
        for period in ("daily", "weekly"):
            sub = net.get(period)
            if sub is not None and not isinstance(sub, dict):
                errors.append(f"network[{period!r}]: expected dict, got {type(sub).__name__}")
            elif isinstance(sub, dict):
                for k, v in sub.items():
                    if not isinstance(v, int) or isinstance(v, bool):
                        errors.append(f"network[{period!r}][{k!r}] = {v!r}: expected int")
                    # R3#10: a negative daily/weekly cap is a typo, not a valid "off"
                    # (0 already means unlimited per _net_caps()'s own convention).
                    elif v < 0:
                        errors.append(f"network[{period!r}][{k!r}] = {v!r}: must be >= 0")

        # R3#10 (2026-07-14): type-checking alone let a negative budget/count or an
        # out-of-range hour/percentage sit in config.json silently (e.g.
        # hours_window.start=25 or a -5 daily_action_budget) until li_budget.py
        # misbehaved on it live. Range-check every network knob that's actually a
        # bound/cap/hour/percentage, not just its type.
        net_int_keys = ("daily_action_budget", "queue_depth_floor", "max_per_company_week",
                        "max_per_niche_week", "sourcing_runs_per_week")
        for k in net_int_keys:
            if k in net and net[k] is not None:
                v = net[k]
                if not isinstance(v, int) or isinstance(v, bool):
                    errors.append(f"network[{k!r}] = {v!r}: expected int")
                elif v < 0:
                    errors.append(f"network[{k!r}] = {v!r}: must be >= 0")

        if "source_mix_commenter_pct" in net and net["source_mix_commenter_pct"] is not None:
            v = net["source_mix_commenter_pct"]
            if not isinstance(v, int) or isinstance(v, bool):
                errors.append(f"network['source_mix_commenter_pct'] = {v!r}: expected int")
            elif not 0 <= v <= 100:
                errors.append(f"network['source_mix_commenter_pct'] = {v!r}: must be 0..100")

        if "score_floor" in net and net["score_floor"] is not None:
            v = net["score_floor"]
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                errors.append(f"network['score_floor'] = {v!r}: expected int or float")
            elif v < 0:
                errors.append(f"network['score_floor'] = {v!r}: must be >= 0")

        if "weekend_pause" in net and net["weekend_pause"] is not None:
            if not isinstance(net["weekend_pause"], bool):
                errors.append(f"network['weekend_pause'] = {net['weekend_pause']!r}: expected bool")

        hw = net.get("hours_window")
        if hw is not None:
            if not isinstance(hw, dict):
                errors.append(f"network['hours_window']: expected dict, got {type(hw).__name__}")
            else:
                for k in ("start", "end"):
                    if k in hw:
                        v = hw[k]
                        if not isinstance(v, int) or isinstance(v, bool):
                            errors.append(f"network['hours_window'][{k!r}] = {v!r}: expected int")
                        elif not 0 <= v <= 23:
                            errors.append(f"network['hours_window'][{k!r}] = {v!r}: must be 0..23")

    # 7. numeric knobs that must be >= 0 (negative would be a typo, not a valid "off")
    for key in ("cold_daily_enroll", "webfix_daily_enroll", "job_morning_chain",
                "auto_approve_min", "job_scan_target", "job_daily_apply_cap",
                "job_apply_batch", "job_apply_concurrency"):
        val = cfg.get(key)
        if isinstance(val, int) and not isinstance(val, bool) and val < 0:
            errors.append(f"{key!r} = {val}: must be >= 0")

    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", default=str(CONFIG), help="config file to validate")
    ap.add_argument("--quiet", action="store_true", help="only print on failure")
    a = ap.parse_args()

    path = Path(a.file)
    try:
        cfg = json.loads(path.read_text())
    except OSError as e:
        print(f"FAIL config_check: cannot read {path}: {e}")
        return 1
    except json.JSONDecodeError as e:
        print(f"FAIL config_check: {path} is not valid JSON: {e}")
        return 1

    errors = check(cfg)
    if errors:
        print(f"FAIL config_check: {len(errors)} problem(s) in {path}")
        for e in errors:
            print(f"  - {e}")
        return 1
    if not a.quiet:
        print(f"PASS config_check: {path} is valid ({len(cfg)} top-level keys)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
