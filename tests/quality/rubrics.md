# Quality rubrics — what "good" means, defined at peak intelligence
_2026-07-03. Golden tests check output SHAPE; these grade output QUALITY. run_quality.py
grades real recent outputs against these weekly (Mondays); any dimension averaging
under 3.5/5 raises a flag in the needs queue. The baseline cohort was graded on
Fable-era outputs — that baseline is the bar, not a hope._

## How to grade (instructions to the grading model)
Score each dimension 1-5 against the anchors. Be harsh: 3 means "publishable but
forgettable." 5 is rare. Quote the specific line that earned or lost points. Never
grade up for effort; grade what a recipient would experience.

## R1: Proposal copy (headline, personal_line, faults, scope)
1. **Evidence density** — 5: every fault quotes or references something verifiably
   theirs (their headline, their award, their byte count). 3: plausible but generic
   ("your site is slow"). 1: could be mail-merged to any business.
2. **Loss framing** — 5: each fault lands as customers/money walking away, with a
   concrete scene ("9pm, broken AC, blank screen"). 3: abstract cost ("hurts
   conversions"). 1: feature-speak with no stakes.
3. **Specific-beats-clever** — 5: zero rhetorical flourishes; numbers and nouns do
   the work. 3: one metaphor too many. 1: sounds like a marketing deck.
4. **Voice compliance** — 5: passes every VOICE-SPEC hard rule (run the lint; any
   em-dash or banned word CAPS this dimension at 1) AND reads like the litmus
   (contractor on the phone). 3: rule-clean but stiff.
5. **Ask clarity** — 5: one tier, one price, one next step, stated once without
   hedging. 3: clear but padded. 1: multiple options mumbled together.

## R2: Reply/DM drafts (concierge, conveyor, timers)
1. **Answers their actual message** — 5: first sentence responds to the specific
   thing THEY said; a stranger could reconstruct their message from the reply.
   3: acknowledges then pivots to agenda. 1: template that ignores them.
2. **One move per message** — 5: exactly one question OR one CTA, never both, never
   two. 1: interrogation or link-salad.
3. **Playbook fidelity** — 5: when their message matches a known objection, the
   counter is the playbook's move (adapted, not parroted). 1: invents a new (worse)
   counter when a proven one exists.
4. **Length discipline** — 5: SMS<=3 sentences, email<=90 words, and shorter than
   their message unless adding requested substance.
5. **Human temperature** — 5: reads like a person with mild time pressure who
   respects them; can disagree, can say no. 3: pleasant but beige. 1: assistant-brain
   ("I hope this finds you well").

## R3: LinkedIn drafts (connects, comments, DMs)
1. **Their-content specificity** — 5: references a real detail from their post/
   profile that proves a human looked. 1: "love your content!"
2. **Peer register** — 5: operator-to-operator, zero supplication ("huge fan",
   "would be honored"). 3: neutral-professional.
3. **No-ask patience** — 5: first touches give or observe; the ask arrives only
   after a reply (gate: any link or pitch in touch #1 caps at 1).
4. **Comment additivity** — 5: the comment adds an insight or sharp question the
   post lacked; the author would want to reply. 1: restates the post approvingly.

## R4: Job covers (personalized layer on default_cover)
1. **Role mirroring** — 5: the two generated lines use the job's own key noun-phrases
   naturally. 1: could precede any application.
2. **Claim honesty** — 5: every claim traceable to the profile/resume facts (6 yrs,
   $400K->$1M, 35+ builds). ANY invented credential = 1 and a flag.
3. **The story lead** — 5: $400K->$1M appears in the first two sentences with the
   role-relevant angle.

## R5: Morning brief / owner report / standup lines
1. **Next-action first** — 5: the first line is what Alex should DO, not what the
   system did. 1: system diary.
2. **Number honesty** — 5: every number matches its source store; "0" said plainly
   when it's 0. Any unverifiable number = 1 + flag.
3. **Word economy** — 5: nothing cuttable without losing information; under budget
   (brief<=300 words). 3: 20% flab.
4. **Verdict bluntness** — 5: the closing line takes a position a coward wouldn't
   ("the 10-block is the whole game"). 1: "keep up the great work!"

## R6: Mockup copy (hero, services, CTAs)
1. **Niche truth** — 5: services/copy match what this business actually sells (from
   evidence); a real owner would nod. 1: wrong services or invented offerings.
2. **Claim safety** — 5: zero invented stats/reviews/credentials; placeholders
   honestly labeled. Fabricated testimonial-looking content = 1 + flag.
3. **CTA physics** — 5: primary action matches the niche's real buying motion
   (booking for medspa, call for emergency trades).

## Grading protocol
- Sample: last 5 real outputs per category (skip categories with <2 real outputs —
  never grade fixtures as real).
- Model: grade with the strongest available routed model (feature "quality_grade" —
  falls back per models map).
- Output: store/quality_scores.json {category: {dim: avg, n, flags[], worst_quote,
  best_quote}, graded_at, model}.
- Alarm: any dim avg < 3.5 OR any honesty flag -> needs-queue line + feed entry.
- Drift rule: two consecutive weeks below baseline-0.5 on any dimension = the
  generating prompt gets reviewed before more output ships.
