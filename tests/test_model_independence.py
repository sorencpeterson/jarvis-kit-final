"""Model-independence pins (survivability, 2026-07-07): the system must run without
Fable. Fable is a session identity, not infrastructure — the day the window closes,
every agent, the planner, and the models map must still resolve to GA model ids
(claude-haiku-* / claude-sonnet-* / claude-opus-* families) that any future session
or bare `claude` CLI can serve.

Three pins:
  1. no fable-shaped MODEL ID anywhere in runtime code surfaces (py/sh/plist/html
     under agents/ app/ tools/ capture/ ingest/ dashboard/ coach/ schedule/, the
     root scripts, the launchd plists, store/config.json). Prose mentions of
     "FABLE-BUILD-QUEUE" etc. in comments/docstrings are fine and NOT matched:
     the regex targets model-id shapes (claude-fable, fable-<digit>, "fable",
     --model ...fable), not the word.
  2. every claude-* model token in those surfaces belongs to a GA family
     (claude-(haiku|sonnet|opus)-<version>), and the store/config.json models map
     resolves every role that way (tools/config_check.py only checks the "claude-"
     prefix, which a fable id would pass — this is the stricter family pin).
  3. app/planner.py's module-level MODEL constant (the _cli last-resort fallback
     when the models map is empty) is a GA id.

On failure every offending file:line is listed, so the fix is a grep away.
Docs/ledgers (*.md, store data files, FABLE-*.md) are deliberately exempt: they
record history, they do not feed runtime.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

RUNTIME_DIRS = ("agents", "app", "tools", "capture", "ingest", "dashboard",
                "coach", "schedule", "extras")
RUNTIME_SUFFIXES = {".py", ".sh", ".plist", ".html", ".js"}
ROOT_FILES = ("run.sh", "serve.sh", "install-autostart.sh", "triage.py",
              "store_lib.py", "Makefile", "com.riveracopy.brain-server.plist",
              "com.riveracopy.secondbrain.plist")
SKIP_PARTS = {".venv", "__pycache__", "node_modules", "vendor", "credentials",
              "tts-cache"}

CONFIG = ROOT / "store" / "config.json"

# Model-id shapes only. "FABLE-BUILD-QUEUE Section 5" / "Fable audit" prose does not
# match: fable-\d needs a digit right after the hyphen, the quoted form needs the
# bare word as a whole string, the --model form needs the flag.
FABLE_MODEL_RX = re.compile(
    r"""(?ix)
      claude-fable                       # claude-fable-anything
    | \bfable-\d                         # bare fable-5 style id
    | ["']fable["']                      # exact "fable" string literal (model alias)
    | --model[=\s]+["']?[\w./-]*fable    # --model pointed at a fable id
    """
)

# Any full model token must be a GA family. (Bare "claude-" prefix strings, e.g.
# config_check.py's MODEL_PREFIXES allowlist, are not full tokens and don't match.)
MODEL_TOKEN_RX = re.compile(r"claude-[a-z]+-[0-9][\w.-]*", re.IGNORECASE)
GA_MODEL_RX = re.compile(r"^claude-(haiku|sonnet|opus)-\d[\d.a-z-]*$", re.IGNORECASE)


def runtime_files() -> list[Path]:
    files: list[Path] = []
    for d in RUNTIME_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if (f.is_file() and f.suffix in RUNTIME_SUFFIXES
                    and not any(p in SKIP_PARTS for p in f.parts)):
                files.append(f)
    files.extend(ROOT / name for name in ROOT_FILES if (ROOT / name).exists())
    if CONFIG.exists():
        files.append(CONFIG)
    assert files, "runtime surface scan found no files — repo layout changed?"
    return files


def scan(rx: re.Pattern) -> list[str]:
    hits = []
    for f in runtime_files():
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append(f"{f.relative_to(ROOT)}:{n}: {line.strip()[:160]}")
    return hits


def test_no_fable_model_ids_in_runtime_code():
    hits = scan(FABLE_MODEL_RX)
    assert not hits, (
        "fable model id(s) found in RUNTIME code — the system must run on GA models "
        "without Fable:\n  " + "\n  ".join(hits)
    )


def test_every_model_token_is_a_ga_family():
    bad = []
    for f in runtime_files():
        text = f.read_text(errors="ignore")
        for n, line in enumerate(text.splitlines(), 1):
            for tok in MODEL_TOKEN_RX.findall(line):
                if not GA_MODEL_RX.match(tok):
                    bad.append(f"{f.relative_to(ROOT)}:{n}: {tok}  <- {line.strip()[:120]}")
    assert not bad, (
        "non-GA model token(s) in runtime code (must be claude-haiku/sonnet/opus "
        "family):\n  " + "\n  ".join(bad)
    )


def test_models_map_resolves_every_role_to_a_ga_model():
    if not CONFIG.exists():
        pytest.skip("store/config.json absent (fresh install) — models map falls back "
                    "to planner.MODEL, pinned GA by the test below")
    cfg = json.loads(CONFIG.read_text())
    models = cfg.get("models") or {}
    assert models, "store/config.json has no models map — planner routing would fall " \
                   "back to the single MODEL constant for every feature"
    assert "default" in models, "models map missing the 'default' role (planner's " \
                                "first fallback for unknown features)"
    bad = [f"models.{role} = {mid!r}" for role, mid in models.items()
           if not (isinstance(mid, str) and GA_MODEL_RX.match(mid))]
    jam = cfg.get("job_apply_model")
    if jam and not GA_MODEL_RX.match(str(jam)):
        bad.append(f"job_apply_model = {jam!r}")
    assert not bad, ("models map role(s) not resolving to a GA claude-haiku/sonnet/"
                     "opus id:\n  " + "\n  ".join(bad))


def test_planner_default_model_constant_is_ga():
    src = (ROOT / "app" / "planner.py").read_text()
    tree = ast.parse(src)
    model = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "MODEL" for t in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                model = node.value.value
    assert model, "app/planner.py has no module-level MODEL string constant — _cli's " \
                  "last-resort fallback is gone"
    assert GA_MODEL_RX.match(model) and "fable" not in model.lower(), (
        f"planner MODEL constant {model!r} is not a GA claude-haiku/sonnet/opus id"
    )
