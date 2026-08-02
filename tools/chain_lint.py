#!/usr/bin/env python3
"""Chain lint for agents/morning.sh (survivability tool, 2026-07-07).

morning.sh runs ~100 `$RUN agents/x.py` lines at 06:30 with nobody watching. A single
renamed file, missing venv package, or dropped argparse flag silently breaks a lane
until the cadence checker notices days later. This lints the whole chain statically:

  1. every $RUN target file exists and ast-parses
  2. every import statement in every target resolves inside .venv
     (top-level imports = HARD failure; lazy/try-guarded imports = warning only.
     Deliberately importlib.util.find_spec, never exec_module: importing an agent
     for real can touch stores or network, a linter must be safe to run any time.
     Agents that extend sys.path at runtime (the `for p in (ROOT, ..., Path.home()
     / "Claude" / "gmail")` convention) get those dirs resolved from the ast; an
     import that only resolves via a path OUTSIDE this repo is a WARN: it works on
     this Mac but a git-clone restore of second-brain alone will NOT bring it back)
  3. every flag used on a $RUN line (e.g. cold_preflight.py --daily) is actually
     accepted by that file's argparse add_argument() calls (ast-inspected, no
     execution, so no --help probe is needed)
  4. runtime estimate: joins each target to its median duration in store/runs.jsonl
     (runlog is opt-in, so coverage is partial; the report says how partial)
  5. duplicate targets in the chain
  6. crash-guard audit: reads how $RUN is defined and which `set` flags are active.
     The chain's guard is the ABSENCE of `set -e` (a crashing agent cannot abort the
     chain) plus the `RUN=.venv/bin/python || python3` fallback. Flags:
       - `set -e` present (would turn every agent crash into a chain abort) -> HARD
       - a $RUN line coupled to other commands with && / ; / pipe (failure of one
         then skips or feeds the next) -> WARN
       - python invoked directly instead of via $RUN (bypasses the venv fallback) -> WARN
       - `bash x.sh` steps: file must exist (HARD) and pass bash -n (HARD)

Exit 1 on hard failures, 0 otherwise. Read-only except nothing: writes no files.

Usage: .venv/bin/python tools/chain_lint.py [path/to/morning.sh]
"""
from __future__ import annotations

import ast
import json
import re
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHAIN = ROOT / "agents" / "morning.sh"
RUNS = ROOT / "store" / "runs.jsonl"
VENV_PY = ROOT / ".venv" / "bin" / "python"

RUN_LINE_RX = re.compile(r"^\s*\$RUN\s+(\S+)((?:\s+\S+)*?)\s*$")
BASH_LINE_RX = re.compile(r"^\s*bash\s+(\S+)")
RUN_DEF_RX = re.compile(r"\bRUN=(\"[^\"]*\"|'[^']*'|\S+)")
SET_RX = re.compile(r"^\s*set\s+(-\S+)")
WEEKLY_RX = re.compile(r"date \+%u.*=\s*\"?(\d)\"?")

DOW = {"1": "Monday-only", "7": "Sunday-only"}


class Finding:
    def __init__(self, level: str, msg: str):
        self.level = level  # HARD | WARN | INFO
        self.msg = msg


def strip_comment(line: str, blank_quotes: bool = False) -> str:
    """Drop a trailing shell comment; with blank_quotes=True also blank out quoted
    spans (so `echo "... if ..."` can't confuse the if/fi tracker)."""
    out, in_s, in_d = [], False, False
    for ch in line:
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d:
            break
        out.append(" " if blank_quotes and (in_s or in_d) else ch)
    return "".join(out).rstrip()


def parse_chain(path: Path):
    """Return (invocations, bash_steps, run_defs, set_flags, guard_findings).
    invocation = dict(lineno, target, flags, context, raw, coupled)"""
    invocations, bash_steps, run_defs, set_flags, guards = [], [], [], [], []
    ctx_stack: list[str] = []
    # if/fi tracked as TOKENS in order, so a one-line `if ...; then ...; fi` (like the
    # RUN= definition) pushes and pops on the same line instead of poisoning the stack.
    if_fi_rx = re.compile(r"(?<![\w.$/-])(if|fi)(?![\w-])")
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        line = strip_comment(raw)
        stripped = line.strip()
        if m := SET_RX.match(stripped):
            set_flags.append(m.group(1))
        for tok in if_fi_rx.finditer(strip_comment(raw, blank_quotes=True).strip()):
            if tok.group(1) == "if":
                m = WEEKLY_RX.search(stripped)
                ctx_stack.append(DOW.get(m.group(1), f"dow={m.group(1)}") if m else "conditional")
            elif ctx_stack:
                ctx_stack.pop()
        if m := RUN_DEF_RX.search(stripped):
            if "$RUN" not in stripped:  # the definition, not a use
                run_defs.append((lineno, stripped))
        if "$RUN" in line:
            coupled = bool(re.search(r"&&|;|(?<!\|)\|(?!\|)", line))
            # `|| echo ...` / `|| true` is an EXTRA guard, not coupling
            or_tail = re.search(r"\|\|\s*(\S+)", line)
            or_ok = bool(or_tail and or_tail.group(1) in ("echo", "true", ":"))
            m = RUN_LINE_RX.match(line if not or_tail else line[: line.index("||")].rstrip())
            if m:
                target, rest = m.group(1), m.group(2) or ""
                flags = [t for t in rest.split() if t.startswith("-")]
                invocations.append({
                    "lineno": lineno, "target": target, "flags": flags,
                    "context": ctx_stack[-1] if ctx_stack else "daily",
                    "raw": stripped, "coupled": coupled,
                })
                if coupled:
                    guards.append(Finding("WARN",
                        f"line {lineno}: $RUN coupled to another command "
                        f"(&&/;/pipe) — one failure changes the other's behavior: {stripped}"))
                elif or_tail and not or_ok:
                    guards.append(Finding("WARN",
                        f"line {lineno}: $RUN has a non-trivial || fallback: {stripped}"))
            else:
                guards.append(Finding("WARN",
                    f"line {lineno}: $RUN in a shape this lint can't parse: {stripped}"))
        elif m := BASH_LINE_RX.match(stripped):
            bash_steps.append({"lineno": lineno, "target": m.group(1),
                               "context": ctx_stack[-1] if ctx_stack else "daily"})
        elif re.search(r"^\s*(python3?|\.venv/bin/python)\s+\S+\.py", stripped):
            guards.append(Finding("WARN",
                f"line {lineno}: python invoked directly, bypassing the $RUN venv "
                f"fallback: {stripped}"))
    return invocations, bash_steps, run_defs, set_flags, guards


def top_level_and_nested_imports(tree: ast.Module):
    """Return (hard_roots, soft_roots): module-root names imported at top level
    (hard) vs inside functions / try-blocks (soft, usually optional deps)."""
    hard, soft = set(), set()

    def roots(node):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module.split(".")[0]]
        return names

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            hard.update(roots(node))
        elif isinstance(node, ast.Try):
            for sub in ast.walk(node):
                soft.update(roots(sub))
    for node in ast.walk(tree):
        for name in roots(node):
            if name not in hard:
                soft.add(name)
    return hard, soft - hard


def argparse_flags(tree: ast.Module) -> tuple[set[str], set[str]]:
    """(flags declared via add_argument, all string literals) — the literal set is
    the fallback for `"--daily" in sys.argv` style agents."""
    declared, literals = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.add(node.value)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            declared.update(a.value for a in node.args
                            if isinstance(a, ast.Constant) and isinstance(a.value, str))
    return declared, literals


REPO_PATH_DIRS = ("", "agents", "app", "tools", "dashboard", "capture", "ingest",
                  "schedule", "coach")


def _resolve_path_expr(node: ast.expr) -> Path | None:
    """Statically evaluate the repo's path-expression conventions:
    Path.home() / "Claude" / "gmail", ROOT / "app", Name('ROOT'), literal strings."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        p = Path(node.value)
        return p if p.is_absolute() else None
    if isinstance(node, ast.Name) and node.id in ("ROOT", "BASE", "REPO", "HERE"):
        return ROOT  # repo convention: ROOT = Path(__file__).resolve().parent.parent
    if isinstance(node, ast.Call):
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr == "home"
                and isinstance(f.value, ast.Name) and f.value.id == "Path"):
            return Path.home()
        if isinstance(f, ast.Name) and f.id == "str" and node.args:
            return _resolve_path_expr(node.args[0])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _resolve_path_expr(node.left)
        if left is not None and isinstance(node.right, ast.Constant) \
                and isinstance(node.right.value, str):
            return left / node.right.value
    return None


def syspath_dirs(tree: ast.Module) -> set[Path]:
    """Dirs an agent adds to sys.path at runtime, recovered from the ast: both direct
    sys.path.insert/append(…) calls and the `for p in (…): sys.path.insert(0, str(p))`
    loop convention (resolve the loop iterable's elements)."""
    found: set[Path] = set()

    def is_syspath_call(n) -> bool:
        return (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("insert", "append")
                and isinstance(n.func.value, ast.Attribute)
                and n.func.value.attr == "path")

    for node in ast.walk(tree):
        if is_syspath_call(node):
            for arg in node.args:
                if (p := _resolve_path_expr(arg)) is not None:
                    found.add(p)
        if isinstance(node, ast.For) and any(is_syspath_call(n) for n in ast.walk(node)):
            elts = node.iter.elts if isinstance(node.iter, (ast.Tuple, ast.List)) else []
            for e in elts:
                if (p := _resolve_path_expr(e)) is not None:
                    found.add(p)
    return found


def resolve_imports_in_venv(roots: set[str], extra_dirs: set[Path]) -> tuple[set[str], set[str]]:
    """One venv subprocess, find_spec every root twice: with repo paths only, then
    with repo + runtime-extended paths. Returns (missing_everywhere, external_only):
    external_only = resolves ONLY via a dir outside this repo (works today, but a
    git-clone restore of the repo alone will not bring it back).
    find_spec never executes agent code."""
    if not roots:
        return set(), set()
    py = str(VENV_PY) if VENV_PY.exists() else sys.executable
    repo_paths = [str(ROOT / d) for d in REPO_PATH_DIRS]
    extra_paths = sorted(str(p) for p in extra_dirs if p.exists()
                         and not str(p).startswith(str(ROOT)))
    prog = (
        "import importlib.util, json, sys\n"
        "repo, extra, names = (json.loads(a) for a in sys.argv[1:4])\n"
        "def missing(paths):\n"
        "    saved = list(sys.path); sys.path[:0] = paths\n"
        "    out = []\n"
        "    for name in names:\n"
        "        try:\n"
        "            if importlib.util.find_spec(name) is None: out.append(name)\n"
        "        except (ImportError, ValueError, ModuleNotFoundError): out.append(name)\n"
        "    sys.path[:] = saved\n"
        "    return out\n"
        "m_repo = missing(repo)\n"
        "m_all = missing(repo + extra)\n"
        "print(json.dumps({'everywhere': m_all, 'external_only': sorted(set(m_repo) - set(m_all))}))\n"
    )
    out = subprocess.run([py, "-c", prog, json.dumps(repo_paths),
                          json.dumps(extra_paths), json.dumps(sorted(roots))],
                         capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    if out.returncode != 0:
        raise RuntimeError(f"venv import-resolution subprocess failed: {out.stderr[:400]}")
    res = json.loads(out.stdout.strip())
    return set(res["everywhere"]), set(res["external_only"])


def median_durations() -> dict[str, float]:
    per: dict[str, list[float]] = defaultdict(list)
    if RUNS.exists():
        for line in RUNS.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec.get("dur_s"), (int, float)):
                per[rec.get("agent", "?")].append(float(rec["dur_s"]))
    return {a: statistics.median(v) for a, v in per.items() if v}


def main(argv: list[str]) -> int:
    chain = Path(argv[1]) if len(argv) > 1 else CHAIN
    if not chain.exists():
        print(f"HARD chain file missing: {chain}")
        return 1
    invocations, bash_steps, run_defs, set_flags, guards = parse_chain(chain)
    hard: list[str] = []
    warn: list[str] = []

    print(f"== chain_lint: {chain.relative_to(ROOT)} ==")
    print(f"   {len(invocations)} $RUN invocations "
          f"({sum(1 for i in invocations if i['context'] == 'daily')} daily, "
          f"{sum(1 for i in invocations if i['context'] != 'daily')} conditional), "
          f"{len(bash_steps)} bash steps")

    # -- crash-guard audit -------------------------------------------------
    print("\n-- crash-guard --")
    for lineno, d in run_defs:
        print(f"   $RUN defined line {lineno}: {d}")
    flat = " ".join(set_flags)
    if "e" in flat.replace("pipefail", ""):
        hard.append("set -e is active: any agent crash ABORTS the whole chain "
                    "(the chain's guard is the absence of -e)")
    else:
        print(f"   set flags: {set_flags or ['(none)']} -> no -e, a crashing agent "
              "cannot abort the chain (this IS the crash-guard)")
    if not any("perl -e 'alarm" in ln or "perl -e \"alarm" in ln
               for ln in chain.read_text().splitlines()):
        warn.append("no per-step timeout (perl-alarm) anywhere in the chain: one hung "
                    "agent stalls everything until the 90-min stale-lock clears")
    for g in guards:
        (hard if g.level == "HARD" else warn).append(g.msg)

    # -- per-target checks ---------------------------------------------------
    print("\n-- targets --")
    all_roots: set[str] = set()
    extra_dirs: set[Path] = set()
    per_file_hard: dict[str, set[str]] = {}
    per_file_soft: dict[str, set[str]] = {}
    trees: dict[str, ast.Module] = {}
    for inv in invocations:
        f = ROOT / inv["target"]
        if not f.exists():
            hard.append(f"line {inv['lineno']}: $RUN target MISSING: {inv['target']}")
            continue
        if f.suffix == ".py":
            try:
                trees[inv["target"]] = ast.parse(f.read_text())
            except SyntaxError as e:
                hard.append(f"line {inv['lineno']}: {inv['target']} has a SYNTAX ERROR: {e}")
                continue
            h, s = top_level_and_nested_imports(trees[inv["target"]])
            per_file_hard[inv["target"]], per_file_soft[inv["target"]] = h, s
            all_roots |= h | s
            extra_dirs |= syspath_dirs(trees[inv["target"]])
    for st in bash_steps:
        f = ROOT / st["target"]
        if not f.exists():
            hard.append(f"line {st['lineno']}: bash step MISSING: {st['target']}")
        else:
            r = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True)
            if r.returncode != 0:
                hard.append(f"line {st['lineno']}: bash -n FAILS for {st['target']}: "
                            f"{r.stderr.strip()[:200]}")
    missing_mods, external_mods = resolve_imports_in_venv(all_roots, extra_dirs)
    ext_dirs_shown = sorted(str(p) for p in extra_dirs if not str(p).startswith(str(ROOT)))
    for target in sorted(per_file_hard):
        bad = per_file_hard[target] & missing_mods
        if bad:
            hard.append(f"{target}: top-level import(s) unresolvable in .venv: {sorted(bad)}")
        ext = (per_file_hard[target] | per_file_soft.get(target, set())) & external_mods
        if ext:
            warn.append(f"{target}: import(s) {sorted(ext)} resolve ONLY via a dir "
                        f"outside this repo ({', '.join(ext_dirs_shown)}) — works today, "
                        "but a git-clone restore of second-brain alone will NOT restore it")
        lazy_bad = per_file_soft.get(target, set()) & missing_mods
        if lazy_bad:
            warn.append(f"{target}: lazy/optional import(s) unresolvable in .venv: "
                        f"{sorted(lazy_bad)} (only bites if that code path runs)")
    print(f"   {len(trees)} python targets parsed, {len(all_roots)} distinct import "
          f"roots checked in .venv, {len(missing_mods)} unresolvable, "
          f"{len(external_mods)} external-to-repo")

    # -- flag checks ---------------------------------------------------------
    for inv in invocations:
        if not inv["flags"] or inv["target"] not in trees:
            continue
        declared, literals = argparse_flags(trees[inv["target"]])
        for flag in inv["flags"]:
            base = flag.split("=")[0]
            if base in declared:
                continue
            if base in literals:
                warn.append(f"line {inv['lineno']}: {inv['target']} takes {base} via a "
                            "raw string match (sys.argv style), not argparse")
            else:
                hard.append(f"line {inv['lineno']}: {inv['target']} is called with {base} "
                            "but no add_argument / string literal accepts it")

    # -- duplicates ------------------------------------------------------------
    counts = Counter(i["target"] for i in invocations)
    for target, n in counts.items():
        if n > 1:
            lines = [str(i["lineno"]) for i in invocations if i["target"] == target]
            warn.append(f"{target} appears {n}x in the chain (lines {', '.join(lines)})")

    # -- runtime estimate --------------------------------------------------------
    print("\n-- runtime estimate (median dur_s from store/runs.jsonl; runlog is opt-in) --")
    med = median_durations()
    rows, unknown = [], []
    for inv in invocations:
        name = Path(inv["target"]).stem
        if name in med:
            rows.append((inv["context"], name, med[name]))
        else:
            unknown.append((inv["context"], name))
    daily_known = sum(d for c, _, d in rows if c == "daily")
    weekly_known = sum(d for c, _, d in rows if c != "daily")
    daily_total = sum(1 for i in invocations if i["context"] == "daily")
    daily_covered = sum(1 for c, _, _ in rows if c == "daily")
    print(f"   daily chain: {daily_known:.0f}s ({daily_known/60:.1f} min) across "
          f"{daily_covered}/{daily_total} agents with runlog data "
          f"({daily_total - daily_covered} have NO data -> true total is higher)")
    if weekly_known:
        print(f"   conditional (Sun/Mon) extra: {weekly_known:.0f}s over "
              f"{sum(1 for c, _, _ in rows if c != 'daily')} covered agents")
    print("   10 slowest (median):")
    for ctx, name, d in sorted(rows, key=lambda r: -r[2])[:10]:
        print(f"     {d:7.1f}s  {name}{'' if ctx == 'daily' else f'  [{ctx}]'}")
    if daily_known > 45 * 60:
        warn.append(f"estimated daily chain runtime {daily_known/60:.0f} min exceeds ~45 min")
    if daily_total - daily_covered:
        print(f"   no-runlog-data agents ({daily_total - daily_covered} daily): "
              + ", ".join(sorted(n for c, n in unknown if c == "daily")[:12])
              + (" ..." if daily_total - daily_covered > 12 else ""))

    # -- report ---------------------------------------------------------------
    print("\n-- findings --")
    for msg in hard:
        print(f"   HARD  {msg}")
    for msg in warn:
        print(f"   WARN  {msg}")
    if not hard and not warn:
        print("   none")
    print(f"\nchain_lint: {'FAIL' if hard else 'PASS'} "
          f"({len(hard)} hard, {len(warn)} warnings)")
    return 1 if hard else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
