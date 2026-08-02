#!/usr/bin/env python3
"""Mullvad US auto-connect (2026-07-11, [OWNER]: "I use mullvad, wire it up").

The evening apply chain's ONLY human dependency was toggling the US VPN on. This removes
it: the 19:00 chain calls ensure_us() first, which pins a US relay and connects, so the
geo-gate on /api/launch/job_apply passes without [OWNER] touching anything.

Fail-SOFT: if Mullvad is missing, logged out, or won't connect, ensure_us returns
{ok:False,...} and the caller just proceeds to POST — the server's own geo_check gate then
holds the run and notifies (the pre-existing safety net). This helper never RAISES and
never disconnects (the async apply operator needs the tunnel up for its whole run; [OWNER]
toggles Mullvad off himself when he wants his real location back).
"""
from __future__ import annotations

import shutil
import subprocess
import time

MULLVAD = shutil.which("mullvad") or "/usr/local/bin/mullvad"


def _run(args: list[str], timeout: int = 15) -> str:
    try:
        r = subprocess.run([MULLVAD, *args], capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return f"__err__ {type(e).__name__}: {e}"


def status() -> dict:
    """{connected, us, relay, raw} — 'us' is True only when the active relay is a US exit."""
    out = _run(["status"])
    first = out.splitlines()[0].strip() if out.strip() else ""
    connected = first.lower().startswith("connected")
    relay = ""
    for ln in out.splitlines():
        if "Relay:" in ln:
            relay = ln.split("Relay:", 1)[1].strip()
            break
    return {"connected": connected, "us": connected and relay.startswith("us-"),
            "relay": relay, "raw": first}


def ensure_us(timeout: int = 45) -> dict:
    """Pin a US relay and connect; wait until the active relay is a US exit. Returns
    {ok, relay, detail}. ok=True means a US tunnel is up (the apply geo-gate will pass)."""
    if not shutil.which(MULLVAD) and MULLVAD == "/usr/local/bin/mullvad":
        import os
        if not os.path.exists(MULLVAD):
            return {"ok": False, "detail": "mullvad CLI not found"}
    st = status()
    if st["us"]:
        return {"ok": True, "relay": st["relay"], "detail": "already on a US relay"}
    set_out = _run(["relay", "set", "location", "us"])
    if "__err__" in set_out:
        return {"ok": False, "detail": "relay set failed: " + set_out[:120]}
    _run(["connect"])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = status()
        if st["us"]:
            return {"ok": True, "relay": st["relay"], "detail": "connected"}
        time.sleep(2.5)
    st = status()
    return {"ok": st["us"], "relay": st.get("relay", ""),
            "detail": f"timeout after {timeout}s (status: {st.get('raw') or 'unknown'})"}


if __name__ == "__main__":
    import json
    import sys
    if "--status" in sys.argv:
        print(json.dumps(status()))
    else:
        print(json.dumps(ensure_us()))
