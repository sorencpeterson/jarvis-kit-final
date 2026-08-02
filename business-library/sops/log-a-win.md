# Log a Win

## Trigger
A deal closes and money is confirmed (deposit, full payment, or any real
`won`/`payment`/`closed` event). Log it the same day it happens, not in a
batch later.

## Steps
1. **Command palette (⌘K) → "Log a WIN ($ closed)."** This is the fastest
   path and the one built for exactly this moment.
2. Two prompts appear, in order:
   - **"Amount closed ($)?"**: a plain number, no `$` or commas needed
   - **"Who / what? (goes in the ledger)"**: a short note, e.g. "Legacy
     Plumbing, standard site" or "Acme Co deposit"
3. This fires `POST /api/ledger` with `{kind: "won", amount: <a>, note: <n>}`.
4. Confirmation: a toast shows "💰 $<amount> on the board," a gold burst
   animation fires, and if the current dashboard scene is **war**, the War
   Room view re-renders immediately to reflect the new total.

## What this feeds, downstream
- **`store/ledger.jsonl`** is the single source of truth every money view
  reads from: the War Room plan bar (`/api/plan`, target vs. MTD closed vs.
  p50 forecast vs. need/day), and `owner_report.py`'s weekly MONEY section
  (see `monthly-close.md`) both read this same file directly, not a
  duplicate store.
- **Tax set-aside** (`250-IDEAS-BUSINESS.md` K170, if/when built): every
  logged win is the input a 30% set-aside calculation would run against.
  Logging accurately here is what makes that number honest later.
- **Per-SKU/per-build margin tracking** (K179, M200): a `won` entry with a
  clear note ("Example Plumbing, standard site") is what lets a future margin
  rollup match revenue back to `build-log.csv` hours. A vague note ("misc
  income") breaks that traceability. Always name the client/deal in the
  note field, not just the amount.

## What NOT to log here
- A deposit that hasn't cleared yet. Log when money is actually confirmed,
  not when a proposal is accepted (acceptance is tracked separately in
  `store/agreements.jsonl` via the `/agree/{pid}` flow, see
  `run-a-proposal.md`).
- Expenses or tool spend. This action's `kind` is hardcoded to `won`; a
  full ledger-categories system (`won`/`expense`/`tool`/`tax` buckets per
  K169) would need a separate entry path, not this one.

## Owner
[OWNER], same-day as the close. Ten seconds, no reason to batch it.

## Last-verified
2026-07-03 (read directly from `app/static/index.html`'s `PAL.push(['Log a
WIN...'])` handler and cross-checked against `owner_report.py`'s `_money()`
function, which reads the exact same `ledger.jsonl` `kind in ("won",
"payment", "closed")` filter).
