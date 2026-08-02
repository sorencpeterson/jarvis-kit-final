#!/usr/bin/env python3
"""J193: startup self-test. 10 smoke checks that catch a broken deploy before it costs a
morning run. Prints one PASS/FAIL line per check, exits 0 only if all 10 pass.

Checks:
  1. stores parseable      — every *.jsonl in store/ has only valid-JSON lines (or is empty)
  2. config valid           — store/config.json passes tools/config_check.check()
  3. GHL .env present        — the gohighlevel-cli .env ghl_social.py reads from exists
  4. disk space              — >5GB free on the filesystem holding this repo
  5. venv python OK          — .venv/bin/python runs and reports a 3.x version
  6. templates exist         — agents/templates/{proposal,mockup,agreement}.html present
  7. playbooks exist         — business-library/playbooks/*.md present (non-empty dir)
  8. launchd jobs loaded     — the com.jarvis.* plists this repo ships are loaded
  9. port 8765 listening     — the brain server is actually up
  10. token present          — BRAIN_TOKEN is resolvable via store_lib.secret()

Usage: tools/selftest.py   (or via `make doctor`)
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

GHL_ENV = Path.home() / "Claude" / "playwright-project" / "automations" / "ghl" / "gohighlevel-cli" / ".env"
LAUNCHD_LABELS = (
    "com.jarvis.morning",
    "com.jarvis.secondbrain",
    "com.jarvis.autocommit",
    "com.jarvis.watchdog",
    "com.jarvis.retro",
    "com.jarvis.replywatch",
    "com.jarvis.brain-server",
)


def _result(name: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    line = f"{status} {name}"
    if detail:
        line += f" - {detail}"
    print(line)
    return ok


def check_stores_parseable() -> bool:
    store = ROOT / "store"
    bad = []
    for path in sorted(store.glob("*.jsonl")):
        for i, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as e:
                bad.append(f"{path.name}:{i} {e}")
    return _result("stores_parseable", not bad,
                    f"{len(bad)} bad line(s)" if bad else f"all *.jsonl in {store} OK")


def check_config_valid() -> bool:
    try:
        import config_check
    except ImportError as e:
        return _result("config_valid", False, f"cannot import tools/config_check.py: {e}")
    cfg_path = ROOT / "store" / "config.json"
    try:
        cfg = json.loads(cfg_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return _result("config_valid", False, f"cannot read/parse {cfg_path}: {e}")
    errors = config_check.check(cfg)
    return _result("config_valid", not errors,
                    f"{len(errors)} problem(s)" if errors else str(cfg_path))


def check_ghl_env_present() -> bool:
    return _result("ghl_env_present", GHL_ENV.is_file(), str(GHL_ENV))


def check_disk_space(min_gb: float = 5.0) -> bool:
    usage = shutil.disk_usage(ROOT)
    free_gb = usage.free / (1024 ** 3)
    return _result("disk_space", free_gb > min_gb, f"{free_gb:.1f}GB free (need >{min_gb}GB)")


def check_venv_python() -> bool:
    py = ROOT / ".venv" / "bin" / "python"
    if not py.is_file():
        return _result("venv_python", False, f"{py} not found")
    try:
        out = subprocess.run([str(py), "--version"], capture_output=True, text=True, timeout=10)
    except Exception as e:  # noqa: BLE001
        return _result("venv_python", False, f"failed to run: {e}")
    ver = (out.stdout or out.stderr).strip()
    ok = out.returncode == 0 and ver.startswith("Python 3")
    return _result("venv_python", ok, ver)


def check_templates_exist() -> bool:
    tdir = ROOT / "agents" / "templates"
    needed = ("proposal.html", "mockup.html", "agreement.html")
    missing = [n for n in needed if not (tdir / n).is_file()]
    return _result("templates_exist", not missing,
                    f"missing {missing}" if missing else f"{tdir} has all {needed}")


def check_playbooks_exist() -> bool:
    pdir = Path.home() / "Claude" / "business-library" / "playbooks"
    files = list(pdir.glob("*.md")) if pdir.is_dir() else []
    return _result("playbooks_exist", bool(files),
                    f"{len(files)} playbook(s) in {pdir}" if files else f"{pdir} missing or empty")


def check_launchd_loaded() -> bool:
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10)
    except Exception as e:  # noqa: BLE001
        return _result("launchd_loaded", False, f"launchctl list failed: {e}")
    listing = out.stdout
    missing = [label for label in LAUNCHD_LABELS if label not in listing]
    return _result("launchd_loaded", not missing,
                    f"missing {missing}" if missing else f"all {len(LAUNCHD_LABELS)} jobs loaded")


def check_port_listening(port: int = 8765) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(3)
        try:
            result = s.connect_ex(("127.0.0.1", port))
        except OSError as e:
            return _result("port_listening", False, f"connect error: {e}")
    return _result("port_listening", result == 0, f"127.0.0.1:{port} {'open' if result == 0 else 'closed'}")


def check_token_present() -> bool:
    try:
        import store_lib
    except ImportError as e:
        return _result("token_present", False, f"cannot import store_lib: {e}")
    tok = store_lib.secret("brain_token")
    return _result("token_present", bool(tok), "BRAIN_TOKEN resolvable" if tok else "BRAIN_TOKEN not set anywhere")


CHECKS = [
    check_stores_parseable,
    check_config_valid,
    check_ghl_env_present,
    check_disk_space,
    check_venv_python,
    check_templates_exist,
    check_playbooks_exist,
    check_launchd_loaded,
    check_port_listening,
    check_token_present,
]


def main() -> int:
    print(f"selftest: running {len(CHECKS)} checks")
    results = [fn() for fn in CHECKS]
    passed = sum(results)
    print(f"selftest: {passed}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
