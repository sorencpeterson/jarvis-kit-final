---
name: linkedin-content-arc
description: Plan a connected week or month of LinkedIn posts around one theme — a sequenced arc with an angle per post, the job of each, and a cadence, then hand each beat to linkedin-post-writer. A planner on top of the post writer; it schedules the argument, it doesn't replace the voice engine. [OWNER_COMPANY].
---

# linkedin-content-arc

## When to use
[OWNER] wants a run of posts that build on each other, not one-off updates: a week making a case,
a month establishing a position. Input: the theme, and any real material (wins, teardowns,
takes, numbers). Output: a sequenced plan, one beat per post, each with its angle, job, and
draft prompt. Each beat gets written by `linkedin-post-writer` (or `win-announcement` for a
close-day post); this skill decides the ARC and hands off. It never overrides the voice rules,
it schedules them.

## What an arc is (and why it beats one-offs)
A theme argued across several posts compounds: each post reinforces the last, and by the end
the audience holds a position, not a stray impression. The arc has a spine, one claim the whole
week or month proves, approached from a different angle each time. Random posting teaches
nothing; a sequenced arc teaches one thing well. The planner's job is the spine and the angles;
the writer's job is the words.

## The planning frame (build the arc, then hand off)
1. **The spine.** One sentence the whole arc proves. ("Agencies overpay for slow site
   fulfillment and don't have to." / "White-label pricing should be transparent, and here's why
   hiding it hurts everyone.") If the spine isn't sharp, the arc rambles.
2. **The angles.** 3-5 distinct approaches to the spine, no repeats: a diagnosis flip, a
   receipt/proof post, a teardown, a myth he'll kill, a real question to agency owners. Each is a
   different door into the same room. Map each to the post type that fits.
3. **The jobs.** Assign each post a job from the writer's set: sell (ends on the offer),
   position (hard stop), or start conversations (real question, never bait). An arc usually
   opens on position, builds proof in the middle, and sells once near the end. Not every post
   sells; an arc that pitches every day gets muted.
4. **The cadence.** A week is 3-5 posts (not daily unless he's got the material). A month is
   ~8-12, roughly two a week. Never schedule more than he has real material for, thin posts hurt
   the arc. Sequence the beats so proof precedes the pitch.
5. **The handoff.** For each beat, produce a one-line brief the post writer runs with: the
   angle, the job, and the real material or facts it uses. Facts come from the true-facts list
   or [OWNER], never invented. Then `linkedin-post-writer` writes it.

## Hard lines
- **This skill plans, it does not write the final posts.** It can sketch an opener per beat, but
  the finished post goes through `linkedin-post-writer` so the voice and banned-pattern rules
  hold. Don't ship arc drafts as final copy.
- **One spine per arc.** If two themes want in, that's two arcs. Don't blur them.
- **Every beat needs real material.** No beat should require inventing a number, a client, or a
  result. If a beat has no fuel, cut it or ask [OWNER], don't pad it.
- **Cadence honesty.** Better a tight 4-post week than a limp 7. Match the plan to the material.
- **Anonymize by default**, same as every client-facing artifact. Names only with a recorded OK.

## Voice (hard rules)
The arc plan itself is internal, so it can be plain notes. But every finished post inherits the
full spec: no em-dashes, short sentences, contractions, 0-2 hashtags, and the five banned
AI-LinkedIn patterns (cliche hooks, rule-of-three crutch, "here's what I learned," broetry line
breaks, emoji bullets). Enforced by `linkedin-post-writer`. Full spec:
`~/Claude/business-library/VOICE-SPEC.md`.

## Output structure (produce this)
1. **The spine**, one sentence.
2. **The arc table:** for each beat, the day/slot, the angle, the post type, the job, and the
   real material it uses.
3. **The handoff briefs:** one line per beat that `linkedin-post-writer` can run with.
4. **A cadence note:** the posting rhythm and one line on what to do if a beat runs out of
   material.

---

## WORKED EXAMPLE — a week on white-label pricing transparency
*Input: [OWNER] wants a week of posts arguing that hiding white-label build pricing hurts agencies,
and that his flat [FIRST_BUILD] first build is the honest alternative. Real material: the [FIRST_BUILD] flat
first build, [STANDARD_SITE] standard rate, 48-72 hr builds, 35+ builds delivered, the COO [PRIOR_BASELINE]→$1M
wedge, NDA-standard invisibility.*

**The spine:** "Hidden white-label build pricing hurts agencies, and flat, honest pricing wins
the partnership."

**The arc table (4-post week, Mon/Tue/Thu/Fri):**

| Slot | Angle | Post type | Job | Material |
|---|---|---|---|---|
| Mon | Diagnosis flip: agencies fear the build cost because nobody quotes it straight | writer, standard | position | The opaque-pricing problem; the [FIRST_BUILD] flat first build as the counter |
| Tue | The receipt: what a flat-price build actually looks like start to finish | writer, standard | position | 48-72 hr build, day-3 preview, live in 7 days, 35+ builds |
| Thu | Teardown of the "custom quote" dance: why hiding the number costs both sides | portfolio-teardown-writeup angle / writer | start conversation | The COO wedge; a real question to agency owners about their own build quoting |
| Fri | The offer, earned by the week: flat [FIRST_BUILD] first build, NDA standard | writer, standard | sell | [FIRST_BUILD] flat first build, [STANDARD_SITE] rate, invisibility, scope-filter close |

**The handoff briefs:**
- *Mon:* "Diagnosis flip. Agencies don't have a build-cost problem, they have a nobody-quotes-it-
  straight problem. Land on flat pricing as the fix. Job: position, hard stop. Facts: [FIRST_BUILD] flat
  first build."
- *Tue:* "Receipt post. Walk a flat-price build's real timeline: handed off, day-3 preview, live
  in 7. Job: position. Facts: 48-72 hr drafts, 35+ builds."
- *Thu:* "Kill the 'custom quote' myth. Why hiding the number wastes everyone's time, from the COO
  seat. End on a real question to agency owners about how they quote builds now. Job: start
  conversation, never bait."
- *Fri:* "The sell. After a week making the case, state the offer flat: [FIRST_BUILD] first build, your
  brand, NDA standard. Scope-filter close. Job: sell."

**Cadence note:** four posts, proof before pitch, Friday sells because the week earned it. If the
Thursday teardown beat runs thin (no clean example to point at), collapse to a 3-post week
(Mon/Wed/Fri) rather than pad it. Every finished post goes through `linkedin-post-writer` so the
voice and banned-pattern rules hold. Nothing auto-publishes; [OWNER] posts each himself.

*Why this works: one spine, four distinct doors into it, proof stacked before the ask, and a
cadence matched to real material. The planner sets the argument; the post writer keeps the voice.*
