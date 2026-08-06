#!/usr/bin/env python3
"""Exit-IP geo gate for the job-apply operator.

[OWNER] applies to US-remote roles while physically in Europe. Applications go out from
whatever IP his Mac is on, so without a US VPN they leak a European origin that
contradicts his US profile. This checks the current public IP and exits non-zero unless
it resolves to the US, so the apply playbook can STOP before submitting a single form.

  python agents/geo_check.py         -> prints JSON {ok, country, region, city, ip}
                                        exit 0 = US (safe to apply), 2 = not US, 3 = lookup failed

Fail-closed: if the lookup fails, exit 3 (treat as NOT safe) so a network hiccup never
silently green-lights a Europe-origin apply run.
"""
from __future__ import annotations
import json
import sys
import urllib.request

# a couple of providers so one being down does not fail the gate open
PROVIDERS = ("https://ipinfo.io/json", "https://ifconfig.co/json")


def _lookup() -> dict | None:
    for url in PROVIDERS:
        try:
            d = json.loads(urllib.request.urlopen(url, timeout=8).read())
            country = (d.get("country") or d.get("country_iso") or "").upper()
            if country:
                return {"country": country, "region": d.get("region") or d.get("region_name"),
                        "city": d.get("city"), "ip": d.get("ip")}
        except Exception:  # noqa: BLE001
            continue
    return None


def check() -> dict:
    """Importable gate: {ok: bool, country, region, city, ip} or {ok: False, error}.
    Fail-closed — a failed lookup returns ok=False so callers never apply blind."""
    info = _lookup()
    if info is None:
        return {"ok": False, "error": "geo lookup failed"}
    return {"ok": info["country"] == "US", **info}


def main() -> int:
    r = check()
    print(json.dumps(r))
    return 0 if r.get("ok") else (2 if "country" in r else 3)


if __name__ == "__main__":
    sys.exit(main())
