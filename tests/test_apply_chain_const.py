#!/usr/bin/env python3
"""Guard against the 2026-07-12 outage: `_OP_ROUND_TIMEOUT_S` was referenced in
_apply_chain but its module-level definition had been lost, so the evening apply
chain thread died with NameError the instant it spawned operators, silently
stranding 23 real jobs in 'applying' with no retry.

This test AST-parses app/server.py (no import side effects) and asserts that
EVERY bare name used inside _apply_chain that looks like a module constant
(_UPPER_SNAKE) is actually bound at module level. It would have caught the
outage before deploy, and catches the whole class (any future
referenced-but-undefined apply-chain constant), not just this one name.

Run: .venv/bin/python -m pytest tests/test_apply_chain_const.py -v
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "app" / "server.py"


def _module():
    return ast.parse(SERVER.read_text())


def _module_level_names(mod: ast.Module) -> set[str]:
    """Every name bound at module scope: assignments, imports, def/class."""
    names: set[str] = set()
    for node in mod.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, (ast.AnnAssign,)) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add(a.asname or a.name.split(".")[0])
    return names


def _find_func(mod: ast.Module, name: str) -> ast.FunctionDef | None:
    return next((n for n in ast.walk(mod)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name), None)


def test_op_round_timeout_defined_and_sane():
    mod = _module()
    consts = {n.targets[0].id: n.value for n in mod.body
              if isinstance(n, ast.Assign) and len(n.targets) == 1
              and isinstance(n.targets[0], ast.Name)}
    assert "_OP_ROUND_TIMEOUT_S" in consts, "_OP_ROUND_TIMEOUT_S must be defined at module level"
    val = consts["_OP_ROUND_TIMEOUT_S"]
    assert isinstance(val, ast.Constant) and isinstance(val.value, int) and val.value > 0


def test_apply_chain_uses_no_undefined_module_constants():
    mod = _module()
    fn = _find_func(mod, "_apply_chain")
    assert fn is not None, "_apply_chain not found"
    mod_names = _module_level_names(mod)
    # names bound locally inside _apply_chain (assignments, for-targets, imports, args)
    local: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                for nm in ast.walk(t):
                    if isinstance(nm, ast.Name):
                        local.add(nm.id)
        elif isinstance(node, (ast.For, ast.comprehension)):
            tgt = node.target if isinstance(node, ast.For) else node.target
            for nm in ast.walk(tgt):
                if isinstance(nm, ast.Name):
                    local.add(nm.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                local.add(a.asname or a.name.split(".")[0])
    # every _UPPER_SNAKE name LOADed in the function must resolve module-level or local
    undefined = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            nm = node.id
            if nm.startswith("_") and nm.isupper() is False and nm.upper() == nm.replace("_", "").upper() == "":
                continue
            if nm.startswith("_") and nm[1:].replace("_", "").isupper() and any(c.isalpha() for c in nm):
                if nm not in mod_names and nm not in local and nm not in dir(__builtins__):
                    undefined.append(nm)
    assert not undefined, f"apply chain references undefined module constants: {sorted(set(undefined))}"


def test_apply_chain_refilters_batch_to_jobs_that_actually_became_applying():
    """R1#3 (regression, post-17bf56c): jobs.mark_applying() is a real CAS (expect="approved")
    that silently no-ops any job which raced out of 'approved' since the pre-check. The chain
    must RE-READ after mark_applying and keep only the jobs that actually became 'applying',
    so a job that lost that race is never handed to an operator to apply to anyway. Pin the
    re-filter's presence at source level (the full chain spawns real operators, so it isn't
    unit-testable here)."""
    src = SERVER.read_text()
    # the re-read after mark_applying, and the keep-only-'applying' filter
    assert "jobs.mark_applying(" in src
    assert 'jobs.load_jobs()}' in src and '== "applying"]' in src
