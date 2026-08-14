---
name: price-increase-letter
description: Write the rate-increase letter for an existing or care-plan client. Date-certain, a grandfather window, a value recap with real numbers only, a plain exit ramp, zero apology. The best increases are re-rates to what the client is actually using. [OWNER_COMPANY].
---

# price-increase-letter

## When to use
An existing client's rate is going up: a care plan moving tiers, a legacy rate
catching up to the current ladder, a monthly SKU repricing. Input: the client, what
they pay now, what they'll pay, the effective date, and the REAL usage or delivery
facts from logs (edit counts, updates shipped, uptime). Output: the letter, ready to
send after [OWNER]'s read. Never for prospects, prospects get a quote, not a letter.

## When NOT to increase (check these first, or don't send)
Hold the letter if any is true. A mistimed increase costs more than the raise.
- **An open ticket or a recent miss.** Never raise a rate the same month something
  broke or a request sat too long. Fix it, let a clean month pass, then raise.
- **The usage doesn't back the number.** The best increases are re-rates to what
  they're actually using. If the logs don't show they've outgrown the tier, there's no
  case, only a grab. Wait for the usage or don't raise.
- **Under a year on the current rate**, unless the ladder itself moved. Rate hikes on
  a client who just signed read as bait-and-switch.
- **Right before their busy season** or a known cash-tight stretch. Time it to a calm
  window, not their worst week.
If it's genuinely a legacy rate far below the ladder, that IS the reason, and the case
is the gap itself. Say that plainly instead of manufacturing a usage story.

## Existing-client vs new-client (never confuse the two)
This letter is for an EXISTING client only. It grandfathers, recaps delivered work, and
offers a downshift and an exit. A NEW prospect never gets a "your rate is going up"
letter, they get a quote at the current ladder from `money-proposal`, full stop. Two
existing-client variants:
- **Care-plan tier move** (Basic to Growth, Growth to Growth+): anchor on documented
  usage, no onboarding fee (already onboarded), offer the downshift lane.
- **Legacy re-rate** (an old flat rate catching up to today's ladder): anchor on the
  gap to current pricing, a longer grandfather window (60 days), and the exit ramp up
  front. No usage story needed when the rate is simply years behind.

## The five moves, always all five
1. **Date-certain, first line.** "Effective September 1, your plan moves to X." The
   date is the subject of the letter, not the apology buried in paragraph four.
2. **Value recap with real numbers.** What they actually got at the old rate: edits
   turned around, updates shipped, backups run, downtime avoided. Numbers come from
   logs or from [OWNER]. No logs means categories only, never invented counts. Keep the
   log's qualifiers: "typically within 48 hours" never tightens to "all inside 48
   hours." The strongest letters show the client was already getting the new tier's
   work.
3. **The grandfather window.** Current rate holds until the date (30-60 days is the
   default). That's the whole accommodation. No open-ended "locked forever" promises.
4. **The lanes, plainly.** New rate with what it includes, the on-ladder downshift if
   one exists (Growth back to Basic, with the scope drop said out loud), and the exit
   ramp: a clean handoff offer, full backup, credentials, a walkthrough for whoever
   takes over. Offering the exit costs nothing and reads as confidence, because it is.
5. **One decision ask with a date.** "Tell me which lane by August 15." Never "let me
   know your thoughts."

## Hard lines
- **Zero apology.** No "I'm sorry to announce," no inflation paragraph, no justifying
  beyond one plain reason line. The work justifies the rate; the letter shows the work.
- **Every price on the ladder.** [CARE_BASIC] Basic, [CARE_GROWTH] Growth, [CARE_PREMIUM] Growth+ (medspa lane),
  [ECOM_PRICE] Ops Partner Lite. A number not on the tree doesn't go in a letter.
- **No onboarding fee on upgrades.** Existing clients are already onboarded. Say it,
  it's a real concession that costs nothing.
- **Never discount to soften the increase.** The grandfather window IS the softener.
  The 15% trade rule exists for testimonials and referrals, not for guilt.

## Voice (hard rules)
No em-dashes or en-dashes, ever. Short sentences, 9-13 words. Contractions always. No
emojis. Under 160 words, every line load-bearing. Confident, warm, unapologetic. Sign
"[OWNER]" alone. Full spec: `business-library/VOICE-SPEC.md`.

---

## WORKED EXAMPLE: Care Basic [CARE_BASIC] to Care Growth [CARE_GROWTH]
*Input: Dale, Ridgeline Plumbing, on Care Basic [CARE_BASIC]/mo since January. Care log shows
14 edit requests in six months, all handled, usually same week. Basic covers updates,
backups, and hosting watch, not edits. Effective date September 1, letter going out
early July.*

> Subject: Your care plan, effective September 1
>
> Dale,
>
> Change to your plan, effective September 1.
>
> You're on Care Basic at [CARE_BASIC] a month: updates, backups, uptime watch. Since January
> you've also sent 14 edit requests, and I've turned every one around, usually inside
> the week. That's Growth-level work, and it's been running at the Basic rate.
>
> On September 1 your plan becomes Care Growth at [CARE_GROWTH] a month: everything Basic
> does, plus your edits handled with priority and a monthly report. No onboarding
> fee, you're already set up. Your [CARE_BASIC] rate holds through August.
>
> If you'd rather stay at [CARE_BASIC], that's Basic: updates, backups, uptime, with edits
> quoted separately when they come up. And if neither fits, tell me before September
> and I'll hand everything off clean: full backup, all logins, a walkthrough for
> whoever takes over.
>
> Tell me which lane by August 15.
>
> [OWNER]

Why it works: the increase is a re-rate to documented usage, so the value recap isn't
a brag, it's the case. The 14 is real (from the care log; if the log didn't exist,
the line would be "you've been sending regular edits" and nothing more). Three lanes,
all honest: up at [CARE_GROWTH], hold at [CARE_BASIC] with the scope drop named, or a clean exit. No
apology anywhere, one reason line, one decision, one date. Dale keeps his dignity and
[OWNER] keeps his margin either way.
