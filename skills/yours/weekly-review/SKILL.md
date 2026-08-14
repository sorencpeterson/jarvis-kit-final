---
name: weekly-review
description: Run [OWNER]'s Sunday review. Pull the week's real numbers from the second-brain stores (honesty report, staged vs sent, calls made, tier win rates, funnel), answer the 5 questions in order, and produce next week's 3 commitments in his voice. The stores talk, feelings don't. [OWNER_COMPANY].
---

# weekly-review

## When to use
Sunday evening, or Monday before anything else opens. Twenty minutes. Input: the
week's numbers pulled live from the stores below (never from memory of how the week
felt). Output: one review doc, the 5 answers, and exactly 3 commitments. The honesty
agent (`second-brain/agents/honesty_agent.py`) runs Sundays and writes the spine;
this ritual is the human half it reports to.

## The pull (exact sources, in this order)
| Number | Source |
|---|---|
| $ closed this week + month-to-date | `second-brain/store/ledger.jsonl` (kind=won) |
| Target and need-per-day | `store/money-this-month.md` (if two docs disagree, $5K, said out loud) |
| Built vs closed (the blunt sentences) | `second-brain/store/honesty_report.json` |
| Staged vs sent, $ sitting unsent, oldest age | `second-brain/store/proposals.jsonl` |
| Calls made | `second-brain/store/warm_dispo.jsonl` (0 bytes IS the answer: zero) |
| Tier win rate | `second-brain/store/quotes.jsonl` staged-to-won by tier. Small n means say "n too small," never a fake percentage |
| Funnel + rot | proposals staged, sent, opened, closed; replies waiting; day-3 follow-ups missed; price holds expiring inside 14 days |

An empty or missing store is a finding, not a blocker. Report it as the number zero
and move on. Never smooth a gap with an estimate.

## The 5 questions, in order (money first, always)
1. **What hit the ledger?** The number, against target, with need-per-day for the
   days left. No commentary until the number is on the page.
2. **What did I send versus what did I build?** Staged dollars vs sent dollars,
   commits vs sends. This is the send-finger question. Building is not shipping.
3. **Who did I actually talk to?** Dials, live conversations, interviews. The phone
   is the bottleneck until the dispo log says otherwise.
4. **What's rotting?** Oldest unsent proposal, day-3 follow-ups missed, replies
   sitting unanswered, price holds about to expire. Rot has a dollar figure, name it.
5. **What earned money or a reply, and what got hours but earned neither?** The
   keep-doing list and the stop-doing list, one line each.

## The 3 commitments (hard rules)
- Exactly 3. Each one is verb + number + day. "15 dials Monday 9am," never "focus on
  outreach."
- At least 2 are sends or calls. At most 1 is building anything.
- Last week's missed commitment goes first this week or gets killed out loud. It
  never silently rolls.
- The pricing of an hour, from the cold read: workshop hour ~$0, phone hour ~[CARE_PREMIUM].
  Commitments get priced accordingly.

## Voice (hard rules)
This doc is for [OWNER] only, so it's the bluntest voice in the system. No em-dashes or
en-dashes, ever. Two or three sentences of read, then numbers. No consolation
paragraph, no "all in all a productive week." The honesty agent's register is the
bar: "You built 224 commits and closed [STANDARD_SITE]. Zero warm calls. The phone is the
bottleneck." Full spec: `business-library/VOICE-SPEC.md`.

---

## WORKED EXAMPLE: week ending Sunday, July 6, 2026
*Input, pulled live: ledger [STANDARD_SITE] (Acme Co Soft, July 4) against a $5K month. 15
proposals staged July 3 worth $46,800, zero sent, read_secs 0 on all, 1 still has a
blank company name after the fixer ran. warm_dispo.jsonl is 0 bytes against a
58-name booked-call list. Honesty report: 224 commits. Jobs lane: 167 applied, 1
live interview (CacheFly). Tier win rate: n too small everywhere except white-label,
1 for 1.*

> **WEEKLY REVIEW | week ending Sun Jul 6**
>
> **The read.** The machine got finished and the money didn't move. $46,800 sat
> staged and unread for four days behind a 20-minute send. The one dollar that did
> land came from a lane that ran without you, which is the whole lesson.
>
> **1. What hit the ledger?** [STANDARD_SITE] of [AI_OPS_PRICE]. Acme Co Soft, white-label, cold email
> to cash in 3 days. 25 days left, need is $152 a day.
>
> **2. Sent vs built?** Sent: $0. Staged: $46,800, oldest 4 days. Built: 224 commits.
> The ratio is the problem and the ratio is a choice.
>
> **3. Who did I talk to?** Nobody. warm_dispo.jsonl is zero bytes. 58 people who
> once booked a call are still uncalled. One interview is live: CacheFly.
>
> **4. What's rotting?** The $46,800 (every staged proposal ages toward its 14-day
> requote the moment it sends, and none have sent). The 58 warm names. Three outbox
> drafts, one is Lisa, a live client thread going stale.
>
> **5. What earned?** Cold white-label email earned [STANDARD_SITE]: keep it running. The jobs
> lane earned a real interview: protect it. The workshop earned zero dollars this
> week: it's closed until August.
>
> **Commitments, week of Jul 7:**
> 1. Monday 9am: send the 14 clean-named staged proposals. 20 minutes, $43K+ in
>    motion before coffee. The 1 blank-name proposal gets eyeballed, not guessed.
> 2. Monday to Thursday, 9-10am: 15 dials a day into the 58. Every dispo logged,
>    even the voicemails. By Thursday the list is called once through.
> 3. Tonight: interview-ace on CacheFly, slot booked this week. Interview day
>    outranks everything on it.
>
> Not a commitment, a rule: nothing new gets built this week.

Why this is the ceiling: every number traces to a store, the empty file got read as
the loudest number in the doc, and the commitments are calendar entries, not
intentions. Question 5 produced a stop-doing with a date on it. The review took the
honesty agent's two sentences and turned them into Monday morning.
