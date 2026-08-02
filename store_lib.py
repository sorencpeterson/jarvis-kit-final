"""Shared store helpers for the second-brain system.

The store is store/todos.jsonl — one JSON todo per line. Edits append a new
line with the same id; load_todos() does last-write-wins compaction by id so
callers always see the current state.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import zoneinfo
from contextlib import contextmanager
from datetime import datetime, timedelta, tzinfo
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "store" / "todos.jsonl"
STAGING = ROOT / "store" / "inbox_staging.jsonl"


def _resolve_local_zone_name() -> str | None:
    """Best-effort IANA zone key for the machine's local timezone (e.g.
    'Europe/Berlin'), tried in order: TZ env var, the /etc/localtime symlink
    target (macOS + most Linux), /etc/timezone plain-text (Debian/Ubuntu).
    Deliberately does NOT fall back to time.tzname: that only gives an
    abbreviation ('EST'/'PST'), and while a couple of those happen to resolve
    as valid (DST-observing) IANA zone keys by coincidence, most of the common
    US ones (EST/MST/PST) resolve to a FIXED-offset zone with no DST at all --
    exactly the wrong-offset-half-the-year failure mode this function exists
    to avoid. Returns None if nothing resolves to a name zoneinfo actually has
    data for; callers fall back to the frozen current-offset tzinfo."""
    candidates = []
    tz_env = os.environ.get("TZ")
    if tz_env:
        candidates.append(tz_env)
    try:
        localtime = Path("/etc/localtime")
        if localtime.is_symlink():
            resolved = os.path.realpath(localtime)
            marker = "zoneinfo/"
            idx = resolved.find(marker)
            if idx != -1:
                candidates.append(resolved[idx + len(marker):])
    except OSError:
        pass
    try:
        candidates.append(Path("/etc/timezone").read_text().strip())
    except OSError:
        pass
    for name in candidates:
        if not name:
            continue
        try:
            zoneinfo.ZoneInfo(name)  # validate it actually resolves before trusting it
            return name
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            continue
    return None


def _current_zone() -> tzinfo:
    """The machine's local zone, re-resolved fresh on every call (so a change of
    OS timezone -- [OWNER] traveling -- is picked up without a restart, same as
    the pre-fix intent), as a zoneinfo.ZoneInfo -- which, unlike a fixed offset,
    resolves the correct DST-aware offset for whatever SPECIFIC datetime it is
    asked about. Falls back to the frozen current-moment offset only if no IANA
    zone name resolves at all (e.g. no tzdata installed)."""
    name = _resolve_local_zone_name()
    if name:
        try:
            return zoneinfo.ZoneInfo(name)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            pass
    return datetime.now().astimezone().tzinfo


def _naive_for_zone_lookup(dt):
    """Strip tzinfo (if any) so the wall-clock fields can be handed to a
    zoneinfo.ZoneInfo as the instant to resolve. `dt=None` (a couple of call
    sites do `LOCAL_TZ.utcoffset(None)` for "some usable current offset") is
    treated as "now" -- the pre-fix behavior for that specific pattern."""
    if dt is None:
        return datetime.now()
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


class _LocalTZ(tzinfo):
    """Backward-compat shim so `LOCAL_TZ` stays a plain importable module
    attribute (`from store_lib import LOCAL_TZ`, ~130 call sites: `datetime.
    now(LOCAL_TZ)`, `dt.astimezone(LOCAL_TZ)`, `datetime.fromtimestamp(ts,
    LOCAL_TZ)`, `datetime(..., tzinfo=LOCAL_TZ)`, `LOCAL_TZ.utcoffset(None)`)
    instead of becoming a function every caller would need to be rewritten to
    call. Still ONE stable object bound once at import.

    2026-07-14 fix (VERIFIED wrong): the previous version of this shim called
    `datetime.now()` INSIDE utcoffset()/dst()/tzname() regardless of the `dt`
    it was asked about, so EVERY datetime -- including a January one -- got
    TODAY's offset (a Jan lookup returned +2/CEST instead of +1/CET while the
    machine's "now" was in July). This version delegates to a real
    zoneinfo.ZoneInfo (see _current_zone() above), which resolves the correct
    DST offset FOR THE SPECIFIC dt passed in, so a Jan and a Jul datetime now
    correctly get different offsets.

    Note: because LOCAL_TZ is intentionally one shared object (not a fresh
    instance per datetime), CPython's datetime subtraction/comparison takes a
    fast path when both operands carry the IDENTICAL tzinfo object and skips
    per-instant offset resolution entirely (cpython _pydatetime.py datetime.
    __sub__: `if self._tzinfo is other._tzinfo: return base`) -- this is a
    stdlib-wide characteristic of sharing one tzinfo singleton (it would
    happen with a bare zoneinfo.ZoneInfo() too), not something introduced or
    fixable here without abandoning the "one shared object" backward-compat
    requirement. `now()` / `fromtimestamp()` / `astimezone()` -- this class's
    actual job -- are all per-instant correct."""

    def utcoffset(self, dt):
        return _current_zone().utcoffset(_naive_for_zone_lookup(dt))

    def dst(self, dt):
        return _current_zone().dst(_naive_for_zone_lookup(dt))

    def tzname(self, dt):
        return _current_zone().tzname(_naive_for_zone_lookup(dt))

    def __repr__(self) -> str:
        return f"LOCAL_TZ->{_current_zone()!r}"


# Use the machine's ACTUAL local zone so "today" boundaries follow [OWNER] wherever he is,
# AND so each datetime gets the offset that was really true for ITS OWN instant (a Jan
# datetime is CET/+1, a Jul one is CEST/+2 -- not "whatever offset happens to be true
# right now" applied to every datetime, which was the 2026-07-14 regression).
LOCAL_TZ = _LocalTZ()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_id(seed: str) -> str:
    """Stable-ish unique id: tdo_YYYYMMDD_<8 hex of seed>. 8 hex (vs 4) makes different-seed
    birthday collisions ~65000x rarer; callers should still seed with a URL/text, not just author."""
    day = datetime.now().astimezone().strftime("%Y%m%d")
    h = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
    return f"tdo_{day}_{h}"


_ENV_CACHE = None


def _load_env() -> dict:
    """Parse second-brain/.env (KEY=value lines) once. No dependency on python-dotenv."""
    global _ENV_CACHE
    if _ENV_CACHE is None:
        _ENV_CACHE = {}
        try:
            for line in (ROOT / ".env").read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                _ENV_CACHE[k.strip()] = v.strip().strip('"').strip("'")
        except OSError:
            pass
    return _ENV_CACHE


def star_bank() -> str:
    """Return [OWNER]'s filled STAR bank, or '' if it's still the template (has [bracket] prompts)."""
    try:
        t = (ROOT / "store" / "star_bank.md").read_text()
        return "" if ("[what " in t or "[the " in t or "[ranked" in t or "[a time" in t) else t.strip()
    except OSError:
        return ""


def compact_jsonl(path, id_field: str = "id") -> int:
    """Rewrite an append-only jsonl store keeping only the last record per id (atomic).
    Loaders already do last-write-wins, so this just shrinks a file that grows ~1 line per edit.
    Runs under the same sibling .lock as the file's appenders: without it, an append landing
    inside the read-then-replace window is silently erased (2026-07-06 audit, janitor race)."""
    p = Path(path)
    if not p.exists():
        return 0
    with _flock(p):
        by_id, order = {}, []
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            i = r.get(id_field)
            if i is None:
                continue
            if i not in by_id:
                order.append(i)
            by_id[i] = r
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text("".join(json.dumps(by_id[i], ensure_ascii=False) + "\n" for i in order))
        tmp.replace(p)
        return len(order)


def secret(name: str, default: str = "") -> str:
    """Resolve a secret: process env -> .env file -> config.json (legacy). `name` is the lowercase key."""
    up = name.upper()
    if os.environ.get(up):
        return os.environ[up]
    if _load_env().get(up):
        return _load_env()[up]
    try:
        cfg = json.loads((ROOT / "store" / "config.json").read_text())
        if cfg.get(name):
            return str(cfg[name])
    except (OSError, json.JSONDecodeError):
        pass
    return default


def sign_secret() -> str:
    """The HMAC key for capability links (/prop /mock /agree /delivered).

    NEVER degrade to a public constant: a hardcoded fallback would make every
    signed link forgeable by anyone who reads the source. Prefer brain_token
    (so signatures stay stable on a configured install); if it is absent,
    persist a random per-install key once, so a fresh clone still works but with
    an unguessable secret. (2026-07-05: replaced the `or "no-token"` footgun.)"""
    tok = secret("brain_token")
    if tok:
        return tok
    key_file = ROOT / "store" / ".sign_secret"
    try:
        existing = key_file.read_text().strip()
        if existing:
            return existing
    except OSError:
        pass
    import secrets as _secrets
    gen = _secrets.token_hex(32)
    try:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(gen)
        try:
            key_file.chmod(0o600)
        except OSError:
            pass
    except OSError:
        pass
    return gen


def humanize(text: str) -> str:
    """Hard filter for [OWNER]'s voice rules: NO em/en dashes (the #1 AI tell).
    Models ignore the prompt instruction often, so we strip them no matter what,
    swapping to natural punctuation. Applied to every post + comment before save."""
    if not text:
        return text
    # em/en dash (with optional surrounding spaces) -> comma; keeps the flow human
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    # also catch the " -- " typed double-hyphen people use as a dash
    text = re.sub(r"\s+--+\s+", ", ", text)
    # tidy artifacts: " ," / ",," / doubled spaces
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def load_todos(path: Path = STORE) -> list[dict]:
    """Read all lines, last-write-wins by id, drop nothing else. Order = first-seen."""
    if not path.exists():
        return []
    by_id: dict[str, dict] = {}
    order: list[str] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = rec.get("id")
            if not rid:
                continue
            if rid not in by_id:
                order.append(rid)
            by_id[rid] = rec
    return [by_id[i] for i in order]


@contextmanager
def _flock(path: Path):
    """Exclusive lock on a sibling .lock file, so an append can't land inside compact's
    read-then-replace window (which would silently drop it). Cross-process (dashboard, poller,
    morning cron all write todos)."""
    lf = open(path.with_suffix(".lock"), "w")
    try:
        fcntl.flock(lf, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(lf, fcntl.LOCK_UN)
        finally:
            lf.close()


def append_todo(rec: dict, path: Path = STORE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _flock(path), path.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def compact(path: Path = STORE) -> int:
    """Rewrite the file with one current line per id. Returns count kept."""
    with _flock(path):
        todos = load_todos(path)
        tmp = path.with_suffix(".jsonl.tmp")
        with tmp.open("w") as f:
            for rec in todos:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
        return len(todos)


def existing_source_refs(path: Path = STORE) -> set[str]:
    return {t["source_ref"] for t in load_todos(path) if t.get("source_ref")}


_VOICE_CACHE = {"t": 0.0, "txt": ""}


def voice_spec(max_chars: int = 2400) -> str:
    """The hard voice spec (business-library/VOICE-SPEC.md), cached 10 min.
    Inject into any prompt that writes client-facing words."""
    import time
    from pathlib import Path as _P
    now = time.time()
    if now - _VOICE_CACHE["t"] < 600 and _VOICE_CACHE["txt"]:
        return _VOICE_CACHE["txt"][:max_chars]
    try:
        txt = (_P.home() / "Claude" / "business-library" / "VOICE-SPEC.md").read_text()
    except OSError:
        txt = ""
    _VOICE_CACHE.update(t=now, txt=txt)
    return txt[:max_chars]
