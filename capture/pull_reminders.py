#!/usr/bin/env python3
"""Mirror Apple Reminders into todos.jsonl. No magic phrase needed.

Just say:  "Hey Siri, remind me to <thing>"  (optionally "...tomorrow at 3pm").
It lands in your default Reminders list; this mirrors it into the second brain.

Per run:
  1. Read INCOMPLETE reminders from the target lists (default "Reminders" +
     "Brain Inbox"), with the due date Siri parsed.
  2. New ones (dedup by reminder id) -> append a todo. If it has a due date it's
     "scheduled" with scheduled_time set; otherwise "inbox".
  3. We do NOT mark reminders complete — your native notification still fires and
     Reminders stays your live source. When you complete/delete one there, the
     next run flips the matching todo to "done".
  4. Compact the store (last-write-wins by id).

E330 (reminder dedupe vs existing todos, title fuzzy match): the ORIGINAL
dedupe here only ever checked source_ref (exact reminder id) against todos
already mirrored FROM Reminders, so nothing guarded against re-capturing a
thought [OWNER] already typed by hand (or via quick-add) with the same or
near-same wording. _fuzzy_dupe() below adds that second guard: before
mirroring a new reminder, its normalized text is compared (difflib
SequenceMatcher ratio, stdlib, no new dependency) against every OPEN todo's
normalized text; a match >= FUZZY_THRESHOLD skips the mirror (printed as
"(dup of existing)" rather than silently vanishing) instead of creating a
near-duplicate. This is intentionally conservative (checks only OPEN todos,
not done/dropped ones) so an already-completed manual todo doesn't block a
legitimately new Siri reminder that happens to reuse similar words later.

Run:  uv run python capture/pull_reminders.py   (from the second-brain folder)
"""
from __future__ import annotations

import re
import subprocess
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from store_lib import (  # noqa: E402
    LOCAL_TZ,
    append_todo,
    compact,
    load_todos,
    new_id,
    now_iso,
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
try:
    from planner import feed_add  # capture receipts → the Incoming feed
except Exception:
    def feed_add(*a, **k):
        pass
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))
from runlog import track  # noqa: E402  (E353: runlog adoption)

TARGET_LISTS = ["Reminders", "Brain Inbox"]  # default list + optional inbox
LOOKBACK_DAYS = 30  # ignore reminders created longer ago than this (skips old cruft)
FIELD_SEP = "␟"  # unit separator
REC_SEP = "␞"    # record separator
OFFSET = LOCAL_TZ.utcoffset(None)  # e.g. -7h -> "-07:00"
OFFSET_STR = f"{'-' if OFFSET.days < 0 else '+'}{abs(OFFSET).seconds // 3600:02d}:00"
FUZZY_THRESHOLD = 0.95  # SequenceMatcher ratio. Calibrated against the REAL
                        # store, not guessed: genuinely-DIFFERENT real todos
                        # sharing a template phrase (e.g. two separate
                        # "Revive stale deal: <Company> (draft ready in
                        # dashboard)" entries for different companies) peak
                        # at 0.877 ratio, so anything below ~0.9 risks a false-
                        # positive dedupe that silently swallows a distinct
                        # action item. 0.95 stays safely above that real
                        # ceiling while still catching near-verbatim repeats
                        # (casing/punctuation is already normalized away
                        # before scoring, so a true same-thought dupe already
                        # lands at 1.0, not in the 0.9-0.95 gray zone).


def _normalize_for_match(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — so casing/
    punctuation differences never cause a false NEGATIVE (missed dupe)."""
    t = (text or "").lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _fuzzy_dupe(text: str, open_texts_normalized: list[str]) -> bool:
    """True if `text` closely matches any already-open todo's text. Compares
    normalized forms so 'Call the bank.' and 'call the bank' correctly count
    as the same thought."""
    norm = _normalize_for_match(text)
    if not norm:
        return False
    return any(SequenceMatcher(None, norm, existing).ratio() >= FUZZY_THRESHOLD
               for existing in open_texts_normalized)

# AppleScript: emit  id ␟ name ␟ isoDueOrEmpty ␞  for each incomplete reminder
# across the target lists. Handlers convert the due date to YYYY-MM-DDTHH:MM:SS.
_LISTS_AS = ", ".join(f'"{n}"' for n in TARGET_LISTS)
READ_SCRIPT = f'''
on pad(n, w)
  set t to (n as integer) as string
  repeat while (length of t) < w
    set t to "0" & t
  end repeat
  return t
end pad
on isoOf(d)
  if d is missing value then return ""
  return my pad(year of d, 4) & "-" & my pad((month of d) as integer, 2) & "-" & my pad(day of d, 2) & "T" & my pad(hours of d, 2) & ":" & my pad(minutes of d, 2) & ":" & my pad(seconds of d, 2)
end isoOf
with timeout of 600 seconds
  tell application "Reminders"
    set out to ""
    set cutoff to (current date) - ({LOOKBACK_DAYS} * days)
    repeat with lname in {{{_LISTS_AS}}}
      if exists list (lname as string) then
        set ids to (id of every reminder in list (lname as string) whose completed is false and creation date > cutoff)
        set nms to (name of every reminder in list (lname as string) whose completed is false and creation date > cutoff)
        set dds to (due date of every reminder in list (lname as string) whose completed is false and creation date > cutoff)
        repeat with i from 1 to (count of ids)
          set out to out & (item i of ids) & "{FIELD_SEP}" & (item i of nms) & "{FIELD_SEP}" & (my isoOf(item i of dds)) & "{REC_SEP}"
        end repeat
      end if
    end repeat
    return out
  end tell
end timeout
'''


def osascript(script: str) -> str:
    try:
        res = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"osascript failed: {e}", file=sys.stderr)
        return ""
    if res.returncode != 0:
        print(f"osascript error: {res.stderr.strip()}", file=sys.stderr)
        return ""
    return res.stdout


def read_incomplete() -> list[tuple[str, str, str]]:
    """Return (reminder_id, text, due_iso_or_empty)."""
    raw = osascript(READ_SCRIPT)
    items: list[tuple[str, str, str]] = []
    for rec in raw.split(REC_SEP):
        rec = rec.strip("\n")
        if not rec or FIELD_SEP not in rec:
            continue
        parts = rec.split(FIELD_SEP)
        if len(parts) < 3:
            continue
        rid, name, due = parts[0].strip(), parts[1].strip(), parts[2].strip()
        items.append((rid, name, due))
    return items


def _run() -> int:
    items = read_incomplete()
    if not items and not osascript('tell application "Reminders" to return "ok"').strip():
        print("Could not read Reminders (permission not granted yet?).")
        return 0

    incomplete_ids = {rid for rid, _, _ in items}
    store = load_todos()
    by_ref = {t["source_ref"]: t for t in store if t.get("source_ref")}
    open_texts_normalized = [_normalize_for_match(t["text"])
                              for t in store if t.get("status") in ("inbox", "scheduled", "doing")]

    added, fuzzy_skipped = 0, 0
    for rid, text, due in items:
        if rid in by_ref:
            continue  # already mirrored
        if _fuzzy_dupe(text, open_texts_normalized):
            fuzzy_skipped += 1
            print(f"  ~ skipped (dup of existing): {text}")
            continue
        sched = f"{due}{OFFSET_STR}" if due else None
        append_todo({
            "id": new_id(rid),
            "text": text,
            "status": "scheduled" if sched else "inbox",
            "created": now_iso(),
            "source": "siri",
            "source_ref": rid,
            "project": None,
            "priority": None,
            "scheduled_time": sched,
            "duration_min": 30 if sched else None,
            "gcal_event_id": None,
            "notes": None,
        })
        feed_add("capture", f"✓ caught: {text}", "from Siri/Reminders")
        open_texts_normalized.append(_normalize_for_match(text))  # guard within this same run too
        added += 1
        print(f"  + {text}{f'  (due {due})' if due else ''}")

    # Completion sync: siri todos no longer incomplete in Reminders -> done.
    closed = 0
    for t in store:
        if (t.get("source") == "siri"
                and t.get("status") in ("inbox", "scheduled", "doing")
                and t.get("source_ref")
                and t["source_ref"] not in incomplete_ids):
            done = dict(t)
            done["status"] = "done"
            append_todo(done)
            closed += 1

    kept = compact()
    dup_note = f", {fuzzy_skipped} skipped as duplicates of an existing todo" if fuzzy_skipped else ""
    print(f"Imported {added} new, closed {closed}{dup_note}. Store holds {kept} todo(s).")
    return 0


def main() -> int:
    with track("pull_reminders"):  # E353: runlog adoption
        return _run()


if __name__ == "__main__":
    raise SystemExit(main())
