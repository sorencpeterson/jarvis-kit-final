# Time-blocking rules (the brain follows these when proposing schedule)

When [OWNER] says "schedule my day" / "block these out", the brain reads `inbox`
todos, proposes time blocks, shows them, and only on his **yes** runs
`gcal_write.py --confirm`. The brain sets `scheduled_time`, `duration_min`, and
`status: scheduled` on each todo first.

## Defaults
- **Working hours:** 9:00am–5:00pm Pacific. No blocks before 9 or after 6.
- **Deep-work mornings:** put high-priority / heavy-thinking work 9:00–12:00.
- **Batch by project** so context isn't fragmented (all `ghl-dbr` together, etc.).
- **Default block length:** 30 min; 60 min for anything that looks like real build/writing work.
- **Buffer:** leave ≥10 min between blocks; don't stack back-to-back all day.
- **Admin/comms** (emails, follow-ups, Upwork proposal review) → early afternoon.
- **Cap:** no more than ~5 hours of blocks in one day; the rest stays in inbox.
- **Respect existing due dates:** a todo already carrying a `scheduled_time` from
  Siri ("remind me tomorrow at 3pm") keeps that time — don't move it.

## Priority mapping
- `priority: 1` → schedule today/tomorrow morning.
- `priority: 2` → within 2–3 days.
- `priority: 3` → park unless the day is light.

Warm-channel work outranks cold outreach (see memory `warm-over-cold`).
