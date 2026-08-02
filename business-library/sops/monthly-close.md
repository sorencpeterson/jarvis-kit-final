# Monthly Close (Ledger / Plan Review)

_Honest naming note: the underlying automation
(`second-brain/agents/owner_report.py`) actually runs **weekly, every
Monday**, and reports MONTH-TO-DATE numbers each time. There is no separate
"monthly" report distinct from the last Monday-of-the-month run. This SOP is
filed as "monthly-close" per the mission's naming, but the real cadence and
mechanics below are the weekly Monday number, which IS the monthly close by
the time the last Monday of the month lands._

## Trigger
Every Monday, automatically, inside the `morning.sh` chain. Gated to only
actually build on Mondays (a manual run on any other day is a no-op unless
forced). For an explicit end-of-month review, run it forced on or right
after the last business day of the month.

## Steps

**Automatic (every Monday, no action needed to trigger):**
1. `morning.sh`'s chain calls `owner_report.py`. It checks
   `datetime.now(LOCAL_TZ).isoweekday() != 1`. If it's not Monday, it exits
   immediately (no-op) unless `--force` is passed.
2. It reads directly from local stores (never calls GHL over the network,
   so it never depends on the server being up):
   - `store/config.json` → this month's target (`plan.<YYYY-MM>`)
   - `store/ledger.jsonl` → sums every entry where `kind` is `won`,
     `payment`, or `closed` AND the timestamp falls in the current month
     (this is the exact same filter `log-a-win.md`'s ledger entries feed)
   - `store/forecast_close.json` → the p50 forecast number
   - `store/warm_dispo.jsonl`, `store/replies.jsonl`, `store/proposals.jsonl`,
     `store/jobs.jsonl` → this week's pipeline activity
3. It computes `need_per_day = (target - closed) / days_left_in_month` and
   writes a one-line verdict (e.g. "plan needs $X/day and N days left to hit
   $Y" or, if already past target, "plan's already hit... bank it, don't
   coast").
4. Writes `store/owner_report.md` with four sections: **MONEY** (closed MTD
   vs. plan, need/day, p50 forecast), **PIPELINE MOVED THIS WEEK** (warm
   calls worked/booked, proposals staged/sent/opened, job apps/interviews),
   **WAITING ON YOU** (pending replies), **VERDICT** (the one-line blunt
   read).
5. Pushes a phone notification (first 3 lines) tagged "Monday number,"
   `moneybag`. Also feeds the first "closed MTD..." line into the daily feed
   via `planner.feed_add`.

**Manual review ([OWNER], after the automated report lands):**
6. Read `store/owner_report.md` (or the phone push). This is the same
   report structure the AI Ops Install sells to clients, pointed at [OWNER]
   Digital itself (per `owner_report.py`'s own header comment).
7. Cross-check the VERDICT line against gut sense of the week. If the
   report says "nothing moved" and that doesn't match memory, that's worth
   investigating (a logging gap, not just a slow week).
8. **On the actual last Monday of a calendar month** (the true monthly-close
   moment): additionally look at the full month's `ledger.jsonl` `won` total
   against `store/config.json`'s `plan` target for that month, decide if
   next month's plan number needs adjusting, and update `config.json`
   directly if so.
9. **Force a rebuild any day** if needed:
   ```
   python3 second-brain/agents/owner_report.py --force
   ```

## What this does NOT yet do (honest scope)
- No per-deal stuck-deal aging list. `forecast_close.json` is aggregate-only
  (p10/p50/p90 + deal count), so the report explicitly says "stuck-deal
  detail needs GHL" rather than fabricating a number it doesn't have.
- No expense/P&L categorization. This is a closed-revenue-vs-plan report,
  not the ledger-v2-with-categories system described in
  `250-IDEAS-BUSINESS.md` K169 (not yet built).
- No partner-channel line yet. See
  `EXECUTION-PACK/partners/channel-system.md`'s E94 section for the spec of
  what a future partner-performance line in this report would look like.

## Owner
The report itself: fully automated (Monday, morning chain). The review and
any resulting config change: [OWNER].

## Last-verified
2026-07-03 (read directly from `second-brain/agents/owner_report.py` in
full, including its `_money()`, `_verdict()`, and `run()` functions).
