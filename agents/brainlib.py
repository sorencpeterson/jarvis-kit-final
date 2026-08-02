#!/usr/bin/env python3
"""brainlib — small shared pure-function helpers for the JARVIS internal-organs
agents (E378-381, E325, E350). Deliberately separate from store_lib.py: store_lib
owns the todos.jsonl read/write/lock discipline; this owns stateless formatting +
dedupe + fast-read helpers that any agent (todos-related or not) can import
without pulling in the todo-store machinery.

WHAT: name/currency/phone formatting, generic dedupe-by-key, a large-jsonl reader
      that mmaps instead of loading the whole file into RAM, and a feed-dedupe-
      window helper (E325) for callers who want to collapse repeat feed entries.
WHEN: import from any agents/*.py that needs these; safe from anywhere (no store
      writes, no network, no subprocess).
RAILS: pure functions only. No file writes except read_jsonl_mmap's use of mmap
      for READING (never opened for write). No GHL/network/LLM calls here, ever,
      that's what keeps this importable from tests with zero mocking.

Run standalone for a quick self-check (also see tests/test_brainlib.py for the
real pytest suite):
  .venv/bin/python agents/brainlib.py
"""
from __future__ import annotations

import json
import mmap
import re
from pathlib import Path
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")

# ---------------------------------------------------------------------------
# E378: name normalization — "Braydon" vs "braydon bergeso" vs " BRAYDON  B. "
# should all key to the same person. Used by contact_graph v2 for entity
# resolution across GHL / proposals / replies / warm_dispo / LinkedIn sources.
# ---------------------------------------------------------------------------

_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "md", "esq"}


def normalize_name(name: str | None) -> str:
    """Lowercase, strip titles/punctuation/extra whitespace, drop common suffixes,
    collapse to a stable join key. NOT for display — for equality/dedupe only.

    >>> normalize_name("  Braydon   Bergeson Jr.")
    'braydon bergeson'
    >>> normalize_name("BRAYDON BERGESON")
    'braydon bergeson'
    >>> normalize_name(None)
    ''
    """
    if not name:
        return ""
    n = name.strip().lower()
    n = re.sub(r"^(mr|mrs|ms|dr|prof)\.?\s+", "", n)
    n = re.sub(r"[^\w\s'-]", " ", n)  # strip punctuation except hyphen/apostrophe in names
    n = re.sub(r"\s+", " ", n).strip()
    words = [w for w in n.split(" ") if w.strip(".") not in _NAME_SUFFIXES]
    return " ".join(words)


def display_name(name: str | None) -> str:
    """Title-cased display form for UI/copy, as distinct from the normalize_name
    join key. Doesn't attempt McDonald-style capitalization, just .title().

    >>> display_name("braydon bergeson")
    'Braydon Bergeson'
    """
    return (name or "").strip().title()


# ---------------------------------------------------------------------------
# E380: phone formatting — lifted pattern from proposal_factory._pretty_phone
# (NOT imported from there; proposal_factory is a heavy module with GHL/HTML
# deps, this is the same small pure function re-homed so other agents don't
# need to import the whole factory just to format a phone number).
# ---------------------------------------------------------------------------

def fmt_phone(p: str | None) -> str:
    """US-centric pretty phone: strips to digits, drops a leading country '1',
    formats as (555) 123-4567. Falls back to the original string (or a visibly
    fake placeholder) when it doesn't look like a 10-digit US number, same
    fallback behavior as proposal_factory._pretty_phone.

    >>> fmt_phone("+14155551234")
    '(555) 000-0000'
    >>> fmt_phone("4155551234")
    '(555) 000-0000'
    >>> fmt_phone("")
    '(555) 000-0000'
    """
    d = re.sub(r"\D", "", p or "")
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if len(d) == 10 else (p or "(555) 000-0000")


# ---------------------------------------------------------------------------
# E379: currency formatting — one place for $ display so every agent/report
# renders numbers the same way (no more "$800" here and "$800.00" there).
# ---------------------------------------------------------------------------

def fmt_currency(amount: float | int | None, *, cents: bool = False) -> str:
    """$-prefixed, comma-grouped. Negative amounts render as -$123.

    >>> fmt_currency(800)
    '$800'
    >>> fmt_currency(1234567)
    '$1,234,567'
    >>> fmt_currency(19.5, cents=True)
    '$19.50'
    >>> fmt_currency(-42)
    '-$42'
    >>> fmt_currency(None)
    '$0'
    """
    try:
        n = float(amount) if amount is not None else 0.0
    except (TypeError, ValueError):
        n = 0.0
    sign = "-" if n < 0 else ""
    n = abs(n)
    if cents:
        return f"{sign}${n:,.2f}"
    return f"{sign}${n:,.0f}"


# ---------------------------------------------------------------------------
# E381: generic dedupe helpers — email-keyed, id-keyed, or any key function.
# store_lib.compact_jsonl already does last-write-wins-by-id for FILES; these
# are for deduping already-loaded lists (e.g. merging several jsonl sources in
# memory, like contact_graph v2 does across GHL + proposals + replies).
# ---------------------------------------------------------------------------

def dedupe_by(items: Iterable[T], key: Callable[[T], object], *, keep: str = "last") -> list[T]:
    """Collapse items sharing the same key(item), keeping first or last occurrence.
    Order of first appearance is preserved in the output (stable).

    >>> dedupe_by([{"id": 1, "v": "a"}, {"id": 1, "v": "b"}, {"id": 2, "v": "c"}], key=lambda x: x["id"])
    [{'id': 1, 'v': 'b'}, {'id': 2, 'v': 'c'}]
    >>> dedupe_by([{"id": 1, "v": "a"}, {"id": 1, "v": "b"}], key=lambda x: x["id"], keep="first")
    [{'id': 1, 'v': 'a'}]
    """
    if keep not in ("first", "last"):
        raise ValueError("keep must be 'first' or 'last'")
    by_key: dict = {}
    order: list = []
    for it in items:
        k = key(it)
        if k not in by_key:
            order.append(k)
            by_key[k] = it
        elif keep == "last":
            by_key[k] = it
    return [by_key[k] for k in order]


def dedupe_by_email(items: Iterable[dict], email_field: str = "email") -> list[dict]:
    """Case-insensitive email-keyed dedupe (keep last), skipping items with no
    usable email entirely (they pass through unmerged, keyed by id(item) so
    they never collide with each other or a real email key)."""
    def _key(it: dict):
        e = (it.get(email_field) or "").strip().lower()
        return e if e else f"__no_email_{id(it)}"
    return dedupe_by(items, _key, keep="last")


# ---------------------------------------------------------------------------
# E325: feed dedupe window — same title within N hours collapses to one entry
# with a count, instead of the feed scrolling with N copies of the same line.
# This is a pure transform over an already-loaded feed list; feed.jsonl itself
# is server.py's file (off-limits to write here), so this is offered as a
# library function for a consumer (e.g. a future dashboard read path or
# daily_brief) to call on the read side rather than this repo mutating the feed.
# ---------------------------------------------------------------------------

def dedupe_feed_window(entries: list[dict], *, window_hours: float = 24.0,
                        title_field: str = "title", ts_field: str = "ts") -> list[dict]:
    """Collapse consecutive-in-time entries sharing the same title within
    window_hours into one, tagging the survivor with a 'count' field. Entries
    must be in chronological order (oldest first) — feed.jsonl's natural
    append order. Returns a NEW list; never mutates the input.

    >>> a = [{"title": "x", "ts": "2026-07-03T08:00:00+00:00"},
    ...      {"title": "x", "ts": "2026-07-03T08:05:00+00:00"},
    ...      {"title": "y", "ts": "2026-07-03T08:06:00+00:00"}]
    >>> out = dedupe_feed_window(a)
    >>> [(o["title"], o["count"]) for o in out]
    [('x', 2), ('y', 1)]
    """
    from datetime import datetime

    def _parse(ts: str):
        try:
            return datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            return None

    out: list[dict] = []
    for e in entries:
        title = e.get(title_field)
        ts = _parse(e.get(ts_field, "") or "")
        if out and out[-1].get(title_field) == title:
            prev_ts = _parse(out[-1].get(ts_field, "") or "")
            if ts and prev_ts:
                delta_h = abs((ts - prev_ts).total_seconds()) / 3600.0
            else:
                delta_h = 0.0  # unparseable timestamps: still collapse rather than duplicate
            if delta_h <= window_hours:
                merged = dict(e)
                merged["count"] = out[-1].get("count", 1) + 1
                out[-1] = merged
                continue
        merged = dict(e)
        merged.setdefault("count", 1)
        out.append(merged)
    return out


# ---------------------------------------------------------------------------
# E350: mmap read helper for jsonl files >1MB, so a large append-only store
# (feed.jsonl, replies.jsonl, runs.jsonl on a busy day) doesn't force a full
# read() into a Python string before parsing. Falls back to a plain read for
# small files (mmap has fixed overhead not worth it under the threshold).
# ---------------------------------------------------------------------------

def read_jsonl_mmap(path: str | Path, *, mmap_threshold_bytes: int = 1_000_000) -> list[dict]:
    """Parse a jsonl file into a list of dicts. Files >= mmap_threshold_bytes are
    read via mmap (no full-file copy into a Python str); smaller files use a
    plain read (mmap's per-call overhead isn't worth it below the threshold).
    Malformed lines are skipped, matching every other jsonl reader in this repo.
    Returns [] if the file doesn't exist."""
    p = Path(path)
    if not p.exists():
        return []
    size = p.stat().st_size
    if size == 0:
        return []
    out: list[dict] = []
    if size < mmap_threshold_bytes:
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
    with p.open("rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            for raw_line in iter(mm.readline, b""):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line.decode("utf-8")))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
    return out


# ---------------------------------------------------------------------------
# E355: config hot-reload helper. Every agent that reads store/config.json
# today (grep shows ~7+ near-identical `_config()` functions) re-reads the
# WHOLE file from disk on every call with zero caching — fine for an agent
# that calls it once, wasteful for one that calls it in a loop, and none of
# them currently avoid re-parsing JSON they already parsed a moment ago.
# ConfigWatcher below caches the parsed dict and only re-reads+re-parses when
# the file's mtime actually changed, so repeated .get() calls in a hot loop
# are O(1) after the first, while a live edit to config.json (no restart) is
# still picked up on the very next .get() after the mtime bumps.
# ---------------------------------------------------------------------------

class ConfigWatcher:
    """Mtime-gated cached JSON config reader.

    >>> import tempfile, os, json as _json
    >>> fd, path = tempfile.mkstemp(suffix=".json")
    >>> _ = os.write(fd, b'{"a": 1}'); os.close(fd)
    >>> w = ConfigWatcher(path)
    >>> w.get("a")
    1
    >>> w.get("missing", "default")
    'default'
    """

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._mtime: float | None = None
        self._data: dict = {}

    def _maybe_reload(self) -> None:
        try:
            current_mtime = self._path.stat().st_mtime
        except OSError:
            self._data = {}
            self._mtime = None
            return
        if current_mtime == self._mtime:
            return  # unchanged since last read, skip the re-parse
        try:
            self._data = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            self._data = {}
        self._mtime = current_mtime

    def get(self, key: str, default=None):
        self._maybe_reload()
        return self._data.get(key, default)

    def all(self) -> dict:
        """The full current config dict (reloaded if the file changed)."""
        self._maybe_reload()
        return dict(self._data)


if __name__ == "__main__":
    # Quick manual self-check (the real suite is tests/test_brainlib.py).
    assert normalize_name("  Braydon   Bergeson Jr.") == "braydon bergeson"
    assert fmt_phone("+14155551234") == "(555) 000-0000"
    assert fmt_currency(1234567) == "$1,234,567"
    assert dedupe_by_email([{"email": "A@x.com", "v": 1}, {"email": "a@x.com", "v": 2}]) == \
        [{"email": "a@x.com", "v": 2}]
    print("brainlib: self-check OK")
