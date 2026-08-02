#!/usr/bin/env python3
"""A7 (FABLE-BUILD-QUEUE Section 5, MED): resume A/B outcome tracker.
Only one resume exists today (store/resume.pdf), so this run SCAFFOLDS the
machinery: the registry, the backfill, and the per-variant conversion math
are all live now, so the day [OWNER] adds a second resume file the comparison
starts producing signal instead of being built under deadline pressure.

WHAT: maintains store/resume_variants.json:
        {variant: {file, registered, applied: [job_ids], outcomes: {...}}}
      - registers the 'default' variant (store/resume.pdf) if missing
      - backfills 'default'.applied with every job id in store/jobs.jsonl
        whose status says an application really went out (APPLIED_STATUSES;
        'replied' is included: a human reply implies the application exists),
        minus ids already claimed by another variant
      - computes per-variant outcomes from each job's FULL status history, not just its
        current snapshot (2026-07-13 fix, CX15/R2-7 -- see _status_history/_ever_reached):
        replied = ever hit replied|interview (interview implies a reply), interviewed =
        ever hit interview, rejected = ever hit rejected -- a job that interviewed and was
        LATER rejected still counts as an interview, since jobs.jsonl's funnel statuses are
        last-write-wins and used to erase that credit; reply_rate and interview_rate over
        applied
      - prints the comparison table
      --register NAME --file PATH adds a new variant so future applications
      can be attributed to it (attribution wiring into the apply chain is a
      later step; manual claim via the registry works today).
WHEN: weekly, or ad hoc after a batch of applications. Cheap, pure local
      reads, no LLM.
RAILS: read-only against jobs.jsonl. Only write is store/resume_variants.json
      (atomic tmp+replace). No pushes, no sends, no LLM. Fresh install (no
      jobs.jsonl, no resume) still exits 0 with an honest table.

Run:  .venv/bin/python agents/resume_ab.py [--dry-run] [--register NAME --file PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))
from store_lib import now_iso  # noqa: E402
import jobs  # noqa: E402

# ---- tunables ----
REG = ROOT / "store" / "resume_variants.json"
DEFAULT_VARIANT = "default"
DEFAULT_FILE = "store/resume.pdf"
# statuses that mean an application actually went out (funnel stages are
# last-write-wins, so replied/interview/rejected all imply applied)
APPLIED_STATUSES = ("applied", "confirmed", "replied", "interview", "rejected")
REPLIED_STATUSES = ("replied", "interview")  # a human wrote back


def load_registry() -> dict:
    try:
        data = json.loads(REG.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_registry(reg: dict) -> None:
    REG.parent.mkdir(parents=True, exist_ok=True)
    tmp = REG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=2, ensure_ascii=False))
    tmp.replace(REG)


def ensure_default(reg: dict) -> dict:
    if DEFAULT_VARIANT not in reg:
        reg[DEFAULT_VARIANT] = {"file": DEFAULT_FILE, "registered": now_iso(),
                                "applied": [], "outcomes": {}}
    return reg


def _sink(reg: dict) -> str:
    """The variant unclaimed applied ids belong to: whichever variant's file IS
    the live store/resume.pdf (that's the file an uncredited apply actually
    uploaded). Falls back to 'default'. Matters since 2026-07-12: 'default' was
    re-pointed at the retired resume file after the v2 swap, so new ids that
    miss the applied-callback claim must not pollute the old variant's stats."""
    if (reg.get(DEFAULT_VARIANT) or {}).get("file") == DEFAULT_FILE:
        return DEFAULT_VARIANT
    for name in sorted(reg):
        if name != DEFAULT_VARIANT and (reg.get(name) or {}).get("file") == DEFAULT_FILE:
            return name
    return DEFAULT_VARIANT


def backfill(reg: dict, all_jobs: list[dict]) -> int:
    """Attribute every applied job id nobody claims to the sink variant (see
    _sink). Ids already claimed by another variant stay claimed (claim-first:
    the applied callback attributes in real time; this sweeps stragglers).
    Returns how many ids were newly added."""
    sink = _sink(reg)
    claimed: set[str] = set()
    for name, v in reg.items():
        if name != sink:
            claimed.update(v.get("applied") or [])
    dst = reg.get(sink) or reg[DEFAULT_VARIANT]
    have = set(dst.get("applied") or [])
    added = 0
    for j in all_jobs:
        jid = j.get("id")
        if not jid or jid in claimed or jid in have:
            continue
        if j.get("status") in APPLIED_STATUSES:
            dst.setdefault("applied", []).append(jid)
            have.add(jid)
            added += 1
    return added


def _status_history() -> dict[str, set[str]]:
    """Every status value each job id has EVER carried in the append-only queue log (jobs.jsonl
    is written one full-record line per change; jobs.load_jobs() folds that down to only the
    LATEST line per id). A/B outcomes need the whole history, not today's snapshot: a job that
    reached 'interview' and was later marked 'rejected' must keep its interview/reply credit
    (2026-07-13 fix, CX15/R2-7) -- the experiment's primary metric is whether the resume EVER
    got a reply/interview, not where the job eventually ended up. Best-effort: a missing/corrupt
    queue file just yields an empty map and callers fall back to current status (see
    _ever_reached)."""
    hist: dict[str, set[str]] = {}
    try:
        lines = jobs.QUEUE.read_text().splitlines()
    except OSError:
        return hist
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        jid, st = r.get("id"), r.get("status")
        if jid and st:
            hist.setdefault(jid, set()).add(st)
    return hist


def _ever_reached(job_id: str, statuses: tuple[str, ...],
                  hist: dict[str, set[str]], by_id: dict[str, dict]) -> bool:
    """True if job_id carried any of `statuses` at ANY point in its history, not just now."""
    seen = hist.get(job_id)
    if seen:
        return any(s in statuses for s in seen)
    # id absent from history (e.g. queue was compacted/archived) -- fall back to the current
    # snapshot rather than silently under-counting.
    return (by_id.get(job_id) or {}).get("status") in statuses


def _corrected_false_positive(job_id: str, by_id: dict[str, dict]) -> bool:
    """R1#8 (regression fix, post-17bf56c): the 'ever reached interview' credit (CX15/R2-7)
    correctly keeps a REAL interview that was later REJECTED -- but it also permanently keeps
    a FALSE-POSITIVE interview that the pipeline later CORRECTED (the 'next steps' boilerplate
    over-call the fixed classifier / job_rescan downgrade back to a bare confirmation).
    interview_rate is THE A/B comparison metric, so those false positives silently inflate it.

    The clean, reachable signal: a job whose CURRENT status is 'confirmed' but whose history
    shows a stronger interview/replied. 'confirmed' is the WEAKEST post-application signal
    (a bare ATS auto-ack), and every forward writer (job_replies' rank guard, job_rescan's
    CX11 authoritative guard) refuses to move a job DOWN from interview/replied to confirmed --
    so a current-'confirmed' job that ONCE carried interview/replied can only have gotten there
    by an explicit false-positive correction. A REAL interview that didn't pan out lands on
    'rejected' (terminal), not 'confirmed', so this never touches the CX15/R2-7 case."""
    return (by_id.get(job_id) or {}).get("status") == "confirmed"


def compute_outcomes(reg: dict, all_jobs: list[dict]) -> dict:
    """Fill each variant's outcomes block from the job's FULL status history (see
    _status_history/_ever_reached), not just its current snapshot. Job ids that no longer
    resolve (pruned queue) still count as applied. A false-positive interview later corrected
    to 'confirmed' does NOT keep interview/reply credit (R1#8 -- see _corrected_false_positive)."""
    by_id = {j.get("id"): j for j in all_jobs if j.get("id")}
    hist = _status_history()
    for v in reg.values():
        ids = v.get("applied") or []
        applied = len(ids)
        replied = sum(1 for i in ids if _ever_reached(i, REPLIED_STATUSES, hist, by_id)
                      and not _corrected_false_positive(i, by_id))
        interviewed = sum(1 for i in ids if _ever_reached(i, ("interview",), hist, by_id)
                          and not _corrected_false_positive(i, by_id))
        rejected = sum(1 for i in ids if _ever_reached(i, ("rejected",), hist, by_id))
        v["outcomes"] = {
            "applied": applied, "replied": replied,
            "interviewed": interviewed, "rejected": rejected,
            "reply_rate": round(replied / applied, 3) if applied else 0.0,
            "interview_rate": round(interviewed / applied, 3) if applied else 0.0,
        }
    return reg


def table(reg: dict) -> str:
    head = (f"{'variant':<12} {'file':<28} {'applied':>7} {'replied':>7} "
            f"{'intrvw':>6} {'reject':>6} {'reply%':>7} {'intrvw%':>8}")
    lines = [head, "-" * len(head)]
    for name in sorted(reg):
        v = reg[name]
        o = v.get("outcomes") or {}
        lines.append(
            f"{name:<12} {str(v.get('file') or '?')[:28]:<28} "
            f"{o.get('applied', 0):>7} {o.get('replied', 0):>7} "
            f"{o.get('interviewed', 0):>6} {o.get('rejected', 0):>6} "
            f"{o.get('reply_rate', 0.0) * 100:>6.1f}% {o.get('interview_rate', 0.0) * 100:>7.1f}%")
    if len(reg) < 2:
        lines.append("(one variant registered: rates are a baseline, not a comparison. "
                     "Add a second file with --register to start the A/B.)")
    return "\n".join(lines)


def claim(job_id: str, variant: str, file: str | None = None) -> bool:
    """Attribute one applied job id to a variant, auto-registering the variant
    if new (2026-07-12: the 'attribution wiring into the apply chain' the
    docstring promised). Called from app/server.py's applied callback, so
    attribution is claim-first: backfill() only sweeps ids nobody claimed.
    Also evicts the id from 'default' in case an earlier backfill grabbed it.
    Never raises (a registry hiccup must not break the applied callback)."""
    if not job_id or not variant:
        return False
    try:
        # lock the whole read-modify-write (2026-07-13 hunt): claim() fires per-application from
        # the server callback while run()/backfill() fires from cron; both load->mutate->save the
        # single JSON object, and without a lock the slower writer clobbers the other's committed
        # change (a claim lost, or an evicted id resurrected). save_registry's swap is atomic but
        # the RMW window around it was not.
        from store_lib import _flock
        with _flock(REG):
            reg = ensure_default(load_registry())
            v = reg.get(variant)
            if v is None:
                v = {"file": file or DEFAULT_FILE, "registered": now_iso(),
                     "applied": [], "outcomes": {}}
                reg[variant] = v
            ids = v.setdefault("applied", [])
            if job_id not in ids:
                ids.append(job_id)
            # evict from EVERY other variant, not just default (2026-07-13 hunt): a job must be
            # credited to exactly one variant, or a re-claim double-counts its eventual outcome.
            for name, other in reg.items():
                if name != variant and job_id in (other.get("applied") or []):
                    other["applied"].remove(job_id)
            save_registry(reg)
        return True
    except Exception:  # noqa: BLE001
        return False


def register(name: str, file: str, dry_run: bool = False) -> int:
    reg = ensure_default(load_registry())
    if name in reg:
        print(f"resume_ab: variant '{name}' already registered ({reg[name].get('file')})")
        return 1
    if not (ROOT / file).exists() and not Path(file).exists():
        print(f"resume_ab: WARNING file not found at {file}; registering anyway "
              "(drop the file in place before applying with it)")
    reg[name] = {"file": file, "registered": now_iso(), "applied": [], "outcomes": {}}
    if dry_run:
        print(f"[dry-run] would register '{name}' -> {file}, wrote nothing")
        return 0
    save_registry(reg)
    print(f"resume_ab: registered '{name}' -> {file}")
    return 0


def run(dry_run: bool = False) -> dict:
    all_jobs = jobs.load_jobs()

    def _compute() -> tuple[dict, int]:
        reg = ensure_default(load_registry())
        added = backfill(reg, all_jobs)
        reg = compute_outcomes(reg, all_jobs)
        return reg, added

    if dry_run:
        reg, added = _compute()
    else:
        # lock the load->backfill->save window so a concurrent claim() from the apply callback
        # isn't clobbered by this cron rewrite (2026-07-13 hunt; same race claim() now guards).
        from store_lib import _flock
        with _flock(REG):
            reg, added = _compute()
            save_registry(reg)
    print(table(reg))
    if added:
        print(f"resume_ab: backfilled {added} applied job id(s) onto '{_sink(reg)}'")
    if dry_run:
        print("[dry-run] registry not written")
    else:
        print(f"resume_ab: registry -> {REG}")
    return reg


def main() -> int:
    ap = argparse.ArgumentParser(description="Resume variant outcome tracker (scaffold)")
    ap.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    ap.add_argument("--register", metavar="NAME", help="register a new resume variant")
    ap.add_argument("--file", metavar="PATH", help="resume file for --register")
    args = ap.parse_args()
    if args.register:
        if not args.file:
            print("resume_ab: --register needs --file PATH")
            return 2
        return register(args.register, args.file, dry_run=args.dry_run)
    if args.dry_run:
        run(dry_run=True)
        return 0
    from runlog import track
    with track("resume_ab"):
        run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
