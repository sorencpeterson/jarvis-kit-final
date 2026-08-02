# Flip Cold Live (the two commands + knobs)

_Cold outreach is a supplement/test channel, not the main engine, per
`business-library/automation-preferences.md`'s strategic note and
`business-library/icp-and-personas.md`. Warm channels (law firm, Andrew's
referrals, own network) are where deals actually move. This SOP exists so
that WHEN cold gets flipped on, it's flipped on deliberately, not by
accident._

## Trigger
A deliberate decision to activate the cold agency (or webfix) outreach
sequence. As of `STATE.md` (2026-07-03), **cold is STILL OFF.** Both
workflows exist in GHL unpublished (blank status), `cold_daily_enroll=0`,
`webfix_daily_enroll=0`. This SOP documents how to turn it on, not a
statement that it's currently on.

## Steps

**Command 1 (build/confirm the workflow exists as a draft).**
```
cd ~/Claude/playwright-project/automations/ghl/gohighlevel-cli
./build-wl-cold.sh          # WL agency cold sequence
./build-wl-webfix.sh        # webfix lane, separate sequence
```
This uses the internal-API build engine (`gohighlevel-cli`) to create the
workflow as a **DRAFT** in GHL. Trigger + emails + waits wired, nothing
live. Copy for the WL sequence lives in
`business-library/campaigns/wl-cold-email-7.md`.

**Command 2 (manual, in GHL UI): publish the draft.** Per `SYSTEM.md`:
review the draft workflow **"[2026-07] Cold Agencies - WL Sites (email
only)"** in GHL directly and click Publish. This step is intentionally
manual. GHL workflow publishing isn't scripted, and this is the moment
[OWNER] looks at the actual built sequence before it can send anything.

**Knob: set the daily enrollment cap.** In `second-brain/store/config.json`:
```
cold_daily_enroll: 30   # (or webfix_daily_enroll for that lane): 0 = off
```
30 is the suggested sane starting number per `SYSTEM.md`. The morning chain
drips that many contacts per day into the workflow. Nothing enrolls faster
than this cap allows, regardless of list size.

## What refuses to run if these aren't both true
`second-brain/agents/cold_feeder.py` will not tag/enroll anyone unless:
1. **Deliverability preflight is green.** `cold_preflight.py` checks SPF/
   DKIM/DMARC on the sending domain and confirms the GHL from-address. The
   🧊 COLD dashboard drawer shows a red/green light; red means nothing sends,
   full stop.
2. **The workflow is actually published** in GHL (not draft).
3. **`cold_daily_enroll` (or the relevant lane's knob) is > 0.** It ships
   at 0 by design, so a fresh checkout or a config reset defaults to off,
   never on.

## What happens once live
- `agents/cold_import.py` (morning, automatic): sets Greeting/
  Personalization/Breakup Detail fields on the existing GHL contacts. Never
  dupes, never touches DND/unsub/client/booked contacts.
- `agents/cold_feeder.py` (morning, gated by the three checks above): tags
  the day's batch into the workflow.
- Replies land in 💬 REPLIES like every other channel, worked per
  `approve-queue-morning-routine.md`.
- `agents/cold_preflight.py`'s digging (SPF/DKIM/DMARC + from-address) is
  visible in the 🧊 COLD dashboard drawer at any time, not just at flip time.
  Check it before assuming the light is still green weeks later.

## Rehearsal mode (see before you flip)
`cold_feeder.py --rehearse` (and `morning_chain.py --rehearse`) print exactly
what the system WOULD do without touching anything. The honest way to
preview a day's enrollment batch before the knob goes above 0 for the first
time.

## Ramping
Per `STATE.md`'s webfix ramp note, the ramp pattern used was "10 +5/day to
knob." Start conservative, step the daily cap up gradually rather than
jumping straight to a large number, watching deliverability and reply
quality at each step.

## Turning it back off
Set the relevant `*_daily_enroll` knob back to `0` in `config.json`. Existing
enrolled contacts continue through whatever sequence steps they're already
in (waits/emails already queued) unless the workflow itself is unpublished
in GHL. The knob controls NEW enrollment, not in-flight contacts.

## Owner
[OWNER]. The publish step and the knob-setting step are both deliberate manual
actions by design. This is the highest-risk send channel in the system
(cold, not warm) and it's built to require conscious activation at every
gate.

## Last-verified
2026-07-03 (read directly from `second-brain/SYSTEM.md`'s Cold Outreach
section, `business-library/ghl-setup.md`, and `STATE.md`'s business-state
summary confirming current off status).
