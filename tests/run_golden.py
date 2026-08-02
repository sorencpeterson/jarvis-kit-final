#!/usr/bin/env python3
"""J182: golden test set. 12 frozen prompt -> expected-JSON-SHAPE cases, run weekly to
catch model-router / prompt drift before it silently breaks a live feature (Monday morning
in agents/morning.sh).

Each case is a pair of files in tests/golden/:
  <name>.prompt.txt     the exact prompt text sent to the model (frozen, mirrors the real
                         prompt template in the owning agent, filled with a fixed scenario)
  <name>.schema.json     a JSON-schema-SUBSET describing the expected SHAPE (keys + types),
                         NOT the content. A case fails if the model's JSON response doesn't
                         match keys/types, never because the wording differs from a fixture.

Each case name is prefixed with its category, which maps to which `feature=` this repo's
planner._cli(..., feature=...) model-routing config uses for that real call site:
  reply_classify_*      -> feature="reply"     (agents/reply_watch.py CLASSIFY prompt)
  proposal_gen_*         -> feature="proposal"  (agents/proposal_factory.py GEN prompt)
  objection_counter_*    -> feature="reply"     (the objections[] shape embedded in
                                                  agents/warm_followup.py's booked-call prep)
  prep_pack_*            -> feature="reply"     (agents/warm_followup.py's full booked-call
                                                  prep JSON, same call site as objection_counter
                                                  but the FULL shape, not just the sub-array)

This makes REAL LLM calls (~12 x few seconds). If a case fails on SHAPE, the fix is almost
always to loosen/correct the schema to match reality, not to change the prompt (the prompts
here are frozen copies of the real ones; if the real prompt changes, update the matching
golden .prompt.txt too, deliberately, as part of that change).

Usage:
  tests/run_golden.py             # run all 12 cases, print PASS/FAIL, exit 0/1
  tests/run_golden.py --case reply_classify_01   # run just one case (debugging)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "golden"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "agents"))

# category prefix -> planner feature (must match the real call site's feature= argument,
# see the docstring above for which agent/prompt each maps to)
FEATURE_BY_PREFIX = {
    "reply_classify": "reply",
    "proposal_gen": "proposal",
    "objection_counter": "reply",
    "prep_pack": "reply",
}


def _feature_for(case_name: str) -> str:
    for prefix, feature in FEATURE_BY_PREFIX.items():
        if case_name.startswith(prefix):
            return feature
    return "default"


# ---- minimal JSON-schema-SUBSET validator (no external dependency) ----
# Supports exactly the constructs used in tests/golden/*.schema.json: type (incl. union
# lists like ["number","string"]), required, properties, items, enum, minItems, maxItems,
# minLength, additionalProperties. Enough for shape-checking LLM JSON without pulling in
# the `jsonschema` package (not installed in this venv, and this repo prefers zero new
# runtime deps for a test-only tool).
_TYPE_MAP = {
    "object": dict, "array": list, "string": str,
    "number": (int, float), "integer": int, "boolean": bool, "null": type(None),
}


def _check_type(value, type_spec) -> bool:
    types = type_spec if isinstance(type_spec, list) else [type_spec]
    for t in types:
        py_t = _TYPE_MAP.get(t)
        if py_t is None:
            continue
        if t == "number" and isinstance(value, bool):
            continue  # bool is technically an int subclass; don't let it pass as a number
        if isinstance(value, py_t):
            return True
    return False


def validate(value, schema: dict, path: str = "$") -> list[str]:
    """Return a list of human-readable errors. Empty = valid."""
    errors = []
    if "type" in schema:
        if not _check_type(value, schema["type"]):
            errors.append(f"{path}: expected type {schema['type']}, got {type(value).__name__}")
            return errors  # further checks would be noise once the base type is wrong

    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}: missing required key {req!r}")
        props = schema.get("properties", {})
        for k, v in value.items():
            if k in props:
                errors.extend(validate(v, props[k], f"{path}.{k}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected key {k!r} (additionalProperties: false)")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: expected >= {schema['minItems']} items, got {len(value)}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: expected <= {schema['maxItems']} items, got {len(value)}")
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(value):
                errors.extend(validate(item, item_schema, f"{path}[{i}]"))

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: expected length >= {schema['minLength']}, got {len(value)}")
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: {value!r} not in enum {schema['enum']}")

    return errors


def discover_cases() -> list[str]:
    """Case names are the filename with the trailing '.prompt.txt' stripped, e.g.
    'reply_classify_01.prompt.txt' -> 'reply_classify_01'."""
    suffix = ".prompt.txt"
    return sorted(p.name[: -len(suffix)] for p in GOLDEN.glob(f"*{suffix}"))


def run_case(name: str) -> dict:
    prompt_path = GOLDEN / f"{name}.prompt.txt"
    schema_path = GOLDEN / f"{name}.schema.json"
    if not prompt_path.is_file():
        return {"name": name, "ok": False, "error": f"missing {prompt_path}"}
    if not schema_path.is_file():
        return {"name": name, "ok": False, "error": f"missing {schema_path}"}

    prompt = prompt_path.read_text()
    schema = json.loads(schema_path.read_text())
    feature = _feature_for(name)

    import planner
    result = planner._cli_json(prompt, timeout=150, feature=feature)
    if result is None:
        return {"name": name, "ok": False, "error": "model call failed or returned no parseable JSON"}

    errors = validate(result, schema)
    return {"name": name, "ok": not errors, "error": "; ".join(errors) if errors else "",
            "feature": feature}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", default="", help="run only this case name (debugging)")
    a = ap.parse_args()

    cases = [a.case] if a.case else discover_cases()
    if not cases:
        print("run_golden: no golden cases found in tests/golden/")
        return 1

    print(f"run_golden: running {len(cases)} case(s)")
    results = []
    for name in cases:
        r = run_case(name)
        status = "PASS" if r["ok"] else "FAIL"
        print(f"{status} {name}" + (f" - {r['error']}" if r.get("error") else ""))
        results.append(r)

    passed = sum(1 for r in results if r["ok"])
    print(f"run_golden: {passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
