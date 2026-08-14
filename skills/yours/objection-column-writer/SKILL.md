---
name: objection-column-writer
description: Turn one objection from [OWNER]'s 50-item objection bank into a standalone LinkedIn post in his voice — take the objection public, answer it the way he would to a prospect's face, and land on the offer or a hard stop. Content mined from the sales floor. [OWNER_COMPANY].
---

# objection-column-writer

## When to use
[OWNER] wants a post built from a real objection agencies and business owners actually say. Input:
one objection (from the 50-item bank, or one he just heard) and the theme he wants to make of it.
Output: one standalone post that takes the objection public and answers it in his voice, ending
on the offer or a hard stop. This is a content engine: the objection bank is a bottomless well of
posts, because every objection a prospect has, ten silent readers share. For handling an
objection live in a sales conversation, use `objection-handler`; this skill turns the objection
into public content, not a private reply.

## The core move: answer the objection in public
The best sales content answers, out loud, the thing prospects are too polite to say. When [OWNER]
posts "here's why my sites cost more than the $500 Fiverr option" and answers it straight, every
agency owner who quietly thought that feels seen and pre-sold. The post does the objection
handling before the sales call ever happens. Take the objection, state it honestly (don't
strawman it), answer it the way he'd answer a real prospect, and let the answer sell.

## The build (from bank to post)
1. **Pull the objection and its counter.** From the 50-item objection bank (the same source
   `objection-handler` uses) or from what [OWNER] just heard. Get the real counter first, that's the
   spine of the post.
2. **State the objection honestly as the opener.** The diagnosis flip or the receipt, built from
   the objection itself. "Agencies tell me they can get a site for $500. They're right. Here's what
   that $500 actually buys." Never mock the objection, the reader might hold it.
3. **Answer it once, the way he would to a face.** His real counter, in 2-3 short paragraphs, one
   idea. Confident, specific, no defensiveness. The answer that would end the objection in a sales
   call is the answer that ends it in a post.
4. **Land it.** Offer + short imperative if the post's job is to sell ("If your bench is backed up,
   send me the scope"), or a hard stop if the job is to position. Never beg, never bait.

## The repeat-objection rule (inherited from objection-handler)
If [OWNER]'s already posted this objection, don't re-run the same answer. A repeated objection in
content means either a sharper angle on it or a different objection entirely. Rotate the bank; 50
objections is weeks of posts without repeating himself. Same discipline as the sales floor: say the
counter, don't say it twice the same way.

## Hard lines
- **Never strawman the objection.** State it at full strength, the way a smart prospect means it.
  A weak "before" makes the answer look cheap. The reader who holds the objection has to feel it
  was taken seriously.
- **The answer stays true.** No invented proof to win the argument. Counters lean on real facts:
  [FIRST_BUILD] flat first build, 48-72 hr builds, 35+ delivered, the [PRIOR_BASELINE]→$1M COO wedge, NDA standard.
  Every price routes to the pricing tree, PROPOSED-v2 SKUs stay flagged.
- **One objection per post.** Don't cram three objections into one column. One objection, answered
  well, beats three answered thinly.
- **Anonymize.** If a real prospect said it, never name them. "An agency owner told me last week"
  is plenty.

## Voice (hard rules)
No em-dashes or en-dashes, ever. Short sentences, 9-13 words. Contractions always. 60-150 words,
0-2 hashtags, no engagement bait. The five banned AI-LinkedIn patterns apply (cliche hooks,
rule-of-three crutch, "here's what I learned," broetry line breaks, emoji bullets). The finished
post should read exactly like `linkedin-post-writer` output, because it goes through the same voice
bar. Full spec: `business-library/VOICE-SPEC.md`.

## Output structure (produce this)
1. **The post**, ready to paste.
2. **The source line:** which objection from the bank it answers, and the counter it's built on.
3. **A note:** whether the job was sell or position, and one angle to rotate to if he wants to hit
   this objection again later.

---

## WORKED EXAMPLE — "too expensive" into a post
*Input: the "$500 nephew / Fiverr" price objection from the bank. Counter on file (pricing tree,
objection pre-empts): confidence sells, gatekeeping doesn't; the proposal doubles as a spec sheet;
one flat [FIRST_BUILD] first build proves it. Job: sell.*

**The post:**

> Agencies tell me they can get a website for $500. They're right. They can.
>
> Here's what the $500 version skips. Nobody who understands the agency's client. No one who's
> shipped 35 of these and knows where a build stalls. No day-3 preview, no fix-until-your-client-
> approves, no NDA that keeps you invisible. You get a template and a stranger, and you carry the
> risk when the client's unhappy.
>
> My first build for a new agency is [FIRST_BUILD], flat. Not because typing costs more. Because you're
> buying an operator who ran an agency from [PRIOR_BASELINE] to over $1M, not a gig worker guessing at your
> brand.
>
> If cheap were the same thing, you'd have already bought it.
>
> Send me the scope. I'll tell you if I can help.

**The source line:** answers the "$500 / Fiverr / nephew" price objection from the 50-item bank.
Built on the pricing-tree counter (confidence over gatekeeping) plus the [FIRST_BUILD] flat first build
and the [PRIOR_BASELINE]→$1M COO wedge. Every number routes to the tree.

**Note:** job was sell (offer + scope-filter close). To hit this objection again later without
repeating, rotate to the "the proposal doubles as a spec sheet for your nephew, if he can hit it at
this price, hire him" angle, which answers the same objection from confidence instead of contrast.
Same objection, different door, no repeat.
