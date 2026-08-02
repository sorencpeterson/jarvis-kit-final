#!/usr/bin/env python3
"""E340: store schema docs, auto-generated from REAL shapes — not hand-
maintained (and therefore not stale the way a hand-written doc always
drifts). Walks every store/*.jsonl and store/*.json file, infers each
field's observed type(s) and presence rate from actual records on disk, and
writes one markdown doc, so "what fields does replies.jsonl actually have
right now" is a generated fact, not something to grep six agent files to
reconstruct (this session's own research needed exactly that grep, repeatedly,
across proposals/replies/warm_dispo/todos — this generator is exactly the
tool that would have saved that time next time).

WHAT: for a .jsonl file, samples up to SAMPLE_LIMIT records (last-N, since
      recent records reflect the CURRENT shape better than a possibly-stale
      first record from months ago) and reports, per field: types observed,
      what fraction of sampled records had it (non-null), and one example
      value (truncated, so an accidental secret/long blob doesn't dump raw
      into the doc). For a .json file (not jsonl), reports the same but for
      its top-level keys treating the single file as one record.
      SKIPS files matched by SKIP_PATTERNS (locks, caches, binary-ish
      dumps like recall.db) since those aren't meant to be read as records.
WHEN: run any time; cheap, pure local file reads, no LLM call, no network.
RAILS: 100% read-only against store/. Only write is store/SCHEMA.md (full
      overwrite each run).

Run:  .venv/bin/python agents/schema_docs.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT,):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
sys.path.insert(0, str(ROOT / "agents"))
from runlog import track  # noqa: E402  (E353: runlog adoption)

STORE_DIR = ROOT / "store"
OUT = ROOT / "store" / "SCHEMA.md"
SAMPLE_LIMIT = 50  # last-N records sampled per jsonl file
EXAMPLE_MAX_CHARS = 60  # truncate example values so nothing long/sensitive dumps raw
SKIP_SUFFIXES = {".lock", ".db", ".pdf", ".mp3", ".txt"}
SKIP_NAMES = {"todos.schema.json"}  # already a hand-written schema, nothing to infer


def _type_name(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "dict"
    return type(v).__name__


def _example(v) -> str:
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    s = s.replace("\n", " ")
    return s[:EXAMPLE_MAX_CHARS] + ("..." if len(s) > EXAMPLE_MAX_CHARS else "")


def _read_jsonl_tail(path: Path, n: int) -> list[dict]:
    """Last n valid JSON records in a jsonl file (recent records reflect the
    CURRENT shape best; a schema doc built from the file's first line could
    be describing a shape nothing has written in months)."""
    if not path.exists():
        return []
    lines = path.read_text(errors="ignore").splitlines()
    out = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
        if len(out) >= n:
            break
    return list(reversed(out))


def infer_schema(records: list[dict]) -> dict:
    """{field: {"types": {type_name: count}, "present": n, "example": str}}"""
    total = len(records)
    fields: dict[str, dict] = {}
    for rec in records:
        for k, v in rec.items():
            f = fields.setdefault(k, {"types": {}, "present": 0, "example": None})
            f["present"] += 1
            t = _type_name(v)
            f["types"][t] = f["types"].get(t, 0) + 1
            if f["example"] is None and v not in (None, "", [], {}):
                f["example"] = _example(v)
    return {"total_sampled": total, "fields": fields}


def _should_skip(path: Path) -> bool:
    if path.suffix in SKIP_SUFFIXES:
        return True
    if path.name in SKIP_NAMES:
        return True
    return False


def build_docs() -> dict[str, dict]:
    """Returns {relative_filename: schema_dict} for every documentable
    store/*.jsonl and store/*.json file, sorted by filename."""
    out: dict[str, dict] = {}
    for path in sorted(STORE_DIR.glob("*.jsonl")):
        if _should_skip(path):
            continue
        records = _read_jsonl_tail(path, SAMPLE_LIMIT)
        if not records:
            out[path.name] = {"total_sampled": 0, "fields": {}, "note": "empty or unreadable"}
            continue
        out[path.name] = infer_schema(records)
    for path in sorted(STORE_DIR.glob("*.json")):
        if _should_skip(path):
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            out[path.name] = {"total_sampled": 0, "fields": {}, "note": "unreadable/invalid JSON"}
            continue
        if isinstance(data, dict):
            out[path.name] = infer_schema([data])
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            out[path.name] = infer_schema(data[:SAMPLE_LIMIT])
        else:
            out[path.name] = {"total_sampled": 0, "fields": {},
                              "note": f"top-level shape is {_type_name(data)}, not a dict/list-of-dicts"}
    return out


def render_markdown(docs: dict[str, dict]) -> str:
    lines = ["# Store schema — auto-generated from real shapes", "",
            f"_generated {now_iso()}, sampled up to {SAMPLE_LIMIT} recent records per file_", ""]
    for fname in sorted(docs.keys()):
        d = docs[fname]
        lines.append(f"## {fname}")
        if d.get("note"):
            lines.append(f"_{d['note']}_")
            lines.append("")
            continue
        total = d["total_sampled"]
        if total == 0:
            lines.append("_no records to sample_")
            lines.append("")
            continue
        lines.append(f"Sampled {total} record(s).")
        lines.append("")
        lines.append("| field | types | present | example |")
        lines.append("|---|---|---|---|")
        for field in sorted(d["fields"].keys()):
            f = d["fields"][field]
            types_str = ", ".join(f"{t}({c})" for t, c in sorted(f["types"].items(), key=lambda x: -x[1]))
            pct = round(100 * f["present"] / total)
            example = (f["example"] or "").replace("|", "\\|")
            lines.append(f"| {field} | {types_str} | {f['present']}/{total} ({pct}%) | `{example}` |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    with track("schema_docs"):  # E353: runlog adoption
        docs = build_docs()
        md = render_markdown(docs)
        OUT.write_text(md)
    n_fields = sum(len(d.get("fields", {})) for d in docs.values())
    print(f"schema_docs: documented {len(docs)} store file(s), {n_fields} total field(s) -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
