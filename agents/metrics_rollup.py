#!/usr/bin/env python3
"""Nightly metrics rollup — snapshots today's key numbers into store/metrics.jsonl.

Why: the dashboard panels (money, jobs, cold) all show live "right now" state, but
nothing remembers what "right now" looked like yesterday, so there's no way to see
trend (is warm outreach actually converting more over time? is cold pipeline
growing?) without re-deriving it from raw stores. This agent hits the server's own
APIs — the single source of truth the dashboard itself reads — once a night and
appends a date-keyed snapshot. Re-running the same day replaces that day's record
(idempotent), so a re-run after a late-day fix doesn't create duplicate history.

Deliberately does NOT parse store/*.jsonl directly: the server already has the
logic (dispo joins, funnel counts, cache windows) to turn raw stores into the
numbers that matter, and duplicating that logic here would drift from it.

Run standalone: .venv/bin/python agents/metrics_rollup.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
METRICS = ROOT / "store" / "metrics.jsonl"
BASE_URL = "http://127.0.0.1:8765"
TIMEOUT = 15


def _brain_token() -> str:
    """Read BRAIN_TOKEN=... straight out of .env (no store_lib dependency, no env
    inheritance assumed — launchd/cron jobs often start with a near-empty env)."""
    try:
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("BRAIN_TOKEN="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _get(path: str, token: str) -> dict | None:
    """GET one server endpoint. Returns None (not raises) on any failure — server
    down, timeout, bad auth, bad JSON — so one dead endpoint can't sink the whole
    snapshot; a partial record still beats no record."""
    req = urllib.request.Request(BASE_URL + path, headers={"X-Brain-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError, OSError):
        return None


def build_record() -> dict:
    token = _brain_token()
    today = datetime.now().astimezone().strftime("%Y-%m-%d")

    money = _get("/api/money", token)
    jobs = _get("/api/jobs", token)
    cold = _get("/api/cold", token)
    usage = _get("/api/usage", token)

    funnel = (jobs or {}).get("funnel") if jobs is not None else None
    cold_pipeline = (cold or {}).get("pipeline") if cold is not None else None
    cold_enrichment = (cold or {}).get("enrichment") if cold is not None else None

    return {
        "date": today,
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "pipeline_value": (money or {}).get("pipeline_value") if money is not None else None,
        "pipeline_open": (money or {}).get("pipeline_open") if money is not None else None,
        "warm_worked": (money or {}).get("warm_worked") if money is not None else None,
        "warm_booked": (money or {}).get("warm_booked") if money is not None else None,
        "replies_waiting": (money or {}).get("replies_waiting") if money is not None else None,
        "jobs": funnel,
        "cold_staged": (cold_pipeline or {}).get("staged") if cold_pipeline is not None else None,
        "cold_enrolled": (cold_pipeline or {}).get("enrolled") if cold_pipeline is not None else None,
        "cold_hooks": (cold_enrichment or {}).get("send") if cold_enrichment is not None else None,
        "tokens": (usage or {}).get("total_tokens") if usage is not None else None,
        "calls": (usage or {}).get("total_calls") if usage is not None else None,
    }


def write_record(record: dict) -> None:
    """Idempotent append: read all existing lines, drop any for today's date, append
    the fresh record, atomic rewrite via tmp+os.replace so a crash mid-write can
    never truncate/corrupt the file readers see."""
    lines = []
    if METRICS.exists():
        for line in METRICS.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # drop unparseable lines rather than let them poison history
            if row.get("date") != record["date"]:
                lines.append(row)
    lines.append(record)

    METRICS.parent.mkdir(parents=True, exist_ok=True)
    tmp = METRICS.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in lines))
    os.replace(tmp, METRICS)


def main() -> int:
    record = build_record()
    write_record(record)
    missing = [k for k, v in record.items() if v is None]
    print(f"metrics_rollup: wrote {record['date']} -> {METRICS}")
    if missing:
        print(f"metrics_rollup: fields missing this run (endpoint down?): {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
