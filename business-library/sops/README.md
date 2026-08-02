# SOP Library (the convention)

_Seeded 2026-07-03 per the SOP-debt rule (`EXECUTION-PACK/hiring/sop-debt-
rule.md`): any task done 3+ times gets an SOP before it's done a 4th. This
folder is where those SOPs live._

## The format, one page each
Every SOP in this folder follows the same five-field structure, in this
order:

1. **Task title**: what this is, as a short imperative ("Run a proposal,"
   not "Proposal running procedures").
2. **Trigger**: the specific event or condition that means this SOP applies.
   Concrete, checkable. "A warm contact replies expressing interest," not
   "when it seems like a good time."
3. **Steps**: the actual sequence, numbered, referencing real file paths,
   real commands, and real UI locations. No invented steps. Every step in
   every SOP in this folder is read off the actual runbook, code, or dashboard
   it describes.
4. **Owner**: who executes this today (almost always [OWNER] right now; will
   shift as `EXECUTION-PACK/hiring/delegation-ladder.md` roles come online).
5. **Last-verified date**: the date someone actually walked through these
   steps and confirmed they still match reality. Not the date it was
   written. The date it was last CHECKED. Update this date every time an SOP
   is re-verified, even if nothing changed.

## Why last-verified matters more than the write date
Documentation rot is the actual risk here, not documentation absence. A step
that was accurate in July and silently drifted by October is worse than no
SOP at all, because it creates false confidence. The
`EXECUTION-PACK/hiring/second-[OWNER_HANDLE]-drill-script.md` quarterly drill exists
specifically to catch this. Every SOP a drill-tester touches should get its
last-verified date bumped (or its steps corrected) as part of that drill.

## What's in this library today
- `run-a-proposal.md`: the Proposal Factory flow, dashboard-triggered
- `approve-queue-morning-routine.md`: clearing the ⚡ needs-you queue
- `deliver-a-site.md`: distilled from `elementor-recoder/SITE-FACTORY.md`
- `start-the-call-coach.md`: the one-button coach launch
- `flip-cold-live.md`: the two commands + knobs that turn cold outreach on
- `log-a-win.md`: logging a closed deal to the ledger
- `monthly-close.md`: the ledger/plan review (actually a weekly cadence,
  see that file for why)
- `restore-from-backup.md`: git-based rollback + what backup actually covers
  today (honestly: local-only, no off-machine remote yet)

Plus:
- `decision-log.md`: scaffold for logging pricing/strategy calls >$500
  impact (N210)
- `niche-books/hvac.md`, `niche-books/salon.md`: per-niche sales-prep
  one-pagers (N214), seeded from real pricing-tree and playbook content

## What's explicitly out of scope for this folder
Pure [OWNER]-behavior rituals (energy-matched scheduling, the shutdown ritual,
the weekly systems-review slot, the reading pipeline, the §B/§O/N218/N219
style items in `250-IDEAS-BUSINESS.md`) are personal operating habits, not
repeatable business procedures with a machine or contractor on the other end.
They don't belong here. See the relevant sections of `250-IDEAS-BUSINESS.md`
directly for those.

## How new SOPs get added
Per the SOP-debt rule: when a task hits its 3rd repetition, write it up here
using this exact format, and link it from wherever the task naturally
triggers (a runbook reference, an onboarding doc, a checklist item) so the
next person (human or Claude) finds it without searching.
