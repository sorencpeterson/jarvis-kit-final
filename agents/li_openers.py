#!/usr/bin/env python3
"""Connection-note A/B bank — A11.

[OWNER]'s connect notes are currently NOTELESS by policy (see networking.py's
queue_connections() docstring: "connect items (noteless, per [OWNER])" and
browser-agent/skills/linkedin-networking-execute.md: "Connections are noteless
— send without a note ([OWNER]'s preference)"). A11 asks for "4 openers rotated,
acceptance rate per opener tracked" — this module builds that machinery so it's
ready the moment [OWNER] decides to test connect NOTES (a real product decision,
not this lane's call to make unilaterally), and in the meantime applies the
identical rotation+tracking pattern to something already live: which VARIANT
of comment/dm opening line style gets used, so opener performance data starts
accumulating now rather than waiting for a policy change.

An "opener" here is a STYLE key (not literal boilerplate text — every draft is
still LLM-generated fresh, per [OWNER]'s real voice, never templated copy-paste),
recorded alongside each queued item so downstream acceptance-rate analysis can
group by which stylistic approach was used. See OPENER_BANK below for the 4
styles and record_opener_use()/opener_stats() for the tracking half.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import networking  # noqa: E402

OPENER_LOG = ROOT / "store" / "li_opener_log.jsonl"

# 4 distinct opening MOVES (not literal text — a prompt-steering instruction
# per use, so output stays fresh/on-voice per networking.py's existing
# COMMENT_PROMPT/REPLY_PROMPT machinery, this just picks which one to steer
# toward for THIS draft).
OPENER_BANK = [
    {
        "key": "diagnosis_flip",
        "label": "The diagnosis flip",
        "instruction": ("Open by naming the REAL underlying issue behind what they said, "
                         "the way VOICE-SPEC.md's diagnosis-flip move works: state the "
                         "surface thing, then the actual thing, plainly."),
    },
    {
        "key": "shared_experience",
        "label": "Shared lived experience",
        "instruction": ("Open with a specific, concrete moment from [OWNER]'s own agency-ops "
                         "experience that directly mirrors what they described ('Ran into "
                         "this exact thing when...')."),
    },
    {
        "key": "receipt",
        "label": "The receipt",
        "instruction": ("Open with a concrete number or timeline as proof, matching "
                         "VOICE-SPEC.md's 'the receipt' move (days of the week, dollar "
                         "figures, or percentages make it real)."),
    },
    {
        "key": "plain_agreement_plus",
        "label": "Plain agreement plus a new angle",
        "instruction": ("Open by agreeing with the specific point they made in ONE clause, "
                         "then immediately add a angle they didn't cover, no compliment "
                         "filler."),
    },
]

_KEYS = [o["key"] for o in OPENER_BANK]


def opener_by_key(key: str) -> dict | None:
    return next((o for o in OPENER_BANK if o["key"] == key), None)


def _read_log() -> list[dict]:
    if not OPENER_LOG.exists():
        return []
    out = []
    for line in OPENER_LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def next_opener(recent_n: int = 20) -> dict:
    """Pick the LEAST-recently-used opener among the last `recent_n` logged
    uses, so the rotation self-balances even if callers don't track state
    themselves (A11's 'rotated' requirement) — ties broken by bank order."""
    log = _read_log()[-recent_n:]
    used_counts = Counter(r.get("opener_key") for r in log if r.get("opener_key") in _KEYS)
    least_used_key = min(_KEYS, key=lambda k: used_counts.get(k, 0))
    return opener_by_key(least_used_key)


def record_opener_use(item_id: str, opener_key: str, kind: str = "comment"):
    """Log which opener was steered toward for a given queue item id, so
    opener_stats() can later join this against networking's queue outcomes
    (approved/done vs skipped, and once acceptance-tracking exists for connects
    specifically, accept-rate)."""
    OPENER_LOG.parent.mkdir(parents=True, exist_ok=True)
    with OPENER_LOG.open("a") as f:
        f.write(json.dumps({"ts": now_iso(), "item_id": item_id,
                            "opener_key": opener_key, "kind": kind}, ensure_ascii=False) + "\n")


def opener_stats() -> dict:
    """Per-opener: times used, and outcome breakdown by joining against the
    CURRENT status of each logged item_id in networking's queue (done/skipped/
    pending/approved/expired). This is the acceptance-rate tracking A11 asks
    for, expressed generically across whatever outcome states the queue
    actually has (connects don't have an explicit 'declined' status today —
    see li_history.py's identical note — so 'done' here means 'the action
    fired,' not 'they said yes'; once/if a real accept-vs-decline signal
    exists this joins against it with zero changes needed here, just richer
    status values flowing through networking.load_queue())."""
    log = _read_log()
    by_id_status = {r.get("id"): r.get("status") for r in networking.load_queue()}
    stats: dict[str, dict] = {k: {"used": 0, "by_status": Counter()} for k in _KEYS}
    for r in log:
        key = r.get("opener_key")
        if key not in stats:
            continue
        stats[key]["used"] += 1
        status = by_id_status.get(r.get("item_id"), "unknown")
        stats[key]["by_status"][status] += 1
    return {k: {"used": v["used"], "by_status": dict(v["by_status"])} for k, v in stats.items()}


if __name__ == "__main__":
    print("Opener bank:")
    for o in OPENER_BANK:
        print(f"  {o['key']}: {o['label']}")
    print(f"\nnext_opener(): {next_opener()['key']}")
    stats = opener_stats()
    print(f"\nopener_stats(): {json.dumps(stats, indent=2)}")
