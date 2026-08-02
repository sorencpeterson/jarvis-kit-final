#!/usr/bin/env python3
"""J188: file-based circuit breaker. Guards against hammering a flaky external dependency
(GHL API is the first candidate) with a repeated tripped call.

State lives in a tiny file per name: store/.breaker_<name> containing one JSON object
{"tripped_until": "<iso ts>", "failures": N}. No daemon, no lock server: a single small
file read/write per check, safe for the cron-driven agent style this codebase uses.

ADOPTION (opt-in; ghl_social.py is not this sprint's file to touch):

    from breaker import allow, trip, record_success

    if not allow("ghl"):
        print("ghl breaker open, skipping this call")
        return cached_or_skip()
    try:
        result = call_ghl_api(...)
    except SomeGHLError:
        trip("ghl", cooldown_s=600)   # or call record_failure("ghl") to trip after N fails
        raise
    else:
        record_success("ghl")        # clears the failure counter on a good call

Two ways to trip:
  1. trip(name, cooldown_s) — immediate, caller already knows it's bad (e.g. HTTP 5xx).
  2. record_failure(name, threshold=3, cooldown_s=600) — counts consecutive failures,
     trips automatically once `threshold` is hit. record_success() resets the counter.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "store"


def _path(name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return STORE / f".breaker_{safe}"


def _now() -> datetime:
    return datetime.now().astimezone()


def _read(name: str) -> dict:
    p = _path(name)
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write(name: str, state: dict) -> None:
    p = _path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(p)


def allow(name: str) -> bool:
    """True if calls to `name` should proceed (breaker closed or cooldown expired)."""
    state = _read(name)
    until = state.get("tripped_until")
    if not until:
        return True
    try:
        return _now() >= datetime.fromisoformat(until)
    except ValueError:
        return True  # malformed state file: fail open, don't permanently block on a typo


def trip(name: str, cooldown_s: int = 600) -> None:
    """Open the breaker for `name` for cooldown_s seconds, starting now."""
    state = _read(name)
    state["tripped_until"] = (_now() + timedelta(seconds=cooldown_s)).isoformat(timespec="seconds")
    state["failures"] = state.get("failures", 0) + 1
    state["last_tripped"] = _now().isoformat(timespec="seconds")
    _write(name, state)


def record_failure(name: str, threshold: int = 3, cooldown_s: int = 600) -> bool:
    """Increment the consecutive-failure counter; trip once it reaches `threshold`.
    Returns True if this call caused a trip."""
    state = _read(name)
    state["failures"] = state.get("failures", 0) + 1
    tripped = False
    if state["failures"] >= threshold:
        state["tripped_until"] = (_now() + timedelta(seconds=cooldown_s)).isoformat(timespec="seconds")
        state["last_tripped"] = _now().isoformat(timespec="seconds")
        tripped = True
    _write(name, state)
    return tripped


def record_success(name: str) -> None:
    """Clear the failure counter and any trip (a good call means the dependency is back)."""
    state = _read(name)
    state["failures"] = 0
    state.pop("tripped_until", None)
    _write(name, state)


def status(name: str) -> dict:
    state = _read(name)
    return {
        "name": name,
        "allowed": allow(name),
        "failures": state.get("failures", 0),
        "tripped_until": state.get("tripped_until"),
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("name", nargs="?", default="ghl", help="breaker name to inspect")
    a = ap.parse_args()
    print(status(a.name))
