---
name: linkedin-post-writer
description: Write a LinkedIn post in [OWNER]'s voice from a topic, win, or observation. First person, opinionated, short lines, real paragraphs, 0-2 hashtags, no engagement bait. Kills the five AI-LinkedIn patterns on sight. [OWNER_COMPANY].
---

# linkedin-post-writer

## When to use
[OWNER] has a topic, a take, a win, or a story and wants the post. Input is the raw
material: what happened, what he thinks about it, any real numbers. Output is one
post, ready to paste. He posts it himself, nothing auto-publishes. For close-day win
posts specifically, `win-announcement` owns that moment; this skill is for everything
else he'd say on LinkedIn.

## Inputs
1. **The material**: the take, the story, or the win, in his words if possible.
2. **The facts**: any numbers or events. Facts come FROM the input or the true-facts
   list. The post never adds a number, client, or outcome [OWNER] didn't supply.
3. **The job of the post** (optional): sell (ends on the offer), position (ends on a
   hard stop), or start conversations with agency owners (ends on a real question,
   never bait).

## The shape (from his live copy)
- **Opener**: the diagnosis flip ("Agencies don't have a sales problem. They have a
  website fulfillment bottleneck.") or the receipt ("handed off Monday, client-ready
  Thursday"). First line carries the claim or the number. No warmup.
- **Middle**: ONE idea, argued in 2-4 real paragraphs of 1-3 sentences each. His
  opinion stated as his opinion, first person, no "many people think."
- **Close**: a short imperative when there's a real offer ("Send me the scope. I'll
  tell you if I can help.") or a hard stop. Not every post sells. None of them beg.
- **Length**: 60-150 words. Hashtags: default 0, max 2 and only if they target a real
  audience. No tagging people for reach.

## The five banned patterns (rewrite on sight)
1. **Cliche hooks**: "Unpopular opinion:", "Hot take:", "I did X for 30 days. Here's
   what happened.", "Stop doing X.", "Nobody talks about..."
2. **Rule-of-three writing**: triad lists as a crutch ("Faster. Cheaper. Better."),
   three parallel examples where one real one would land harder. Before returning,
   scan each sentence for a comma triad of parallel examples ("x, y, z"): keep the
   strongest one, delete the other two.
3. **"Here's what I learned"**: lesson-listicle framing, numbered takeaways, "3 things
   this taught me about business."
4. **Broetry line breaks**: one sentence per line, whitespace theater, single-word
   paragraph "reveals." He writes paragraphs.
5. **Emoji bullets**: no emoji anywhere, no arrow or checkmark lists.

Also banned: "Agree?", "Thoughts?", "Who else...", "Repost if", polls-as-content,
"excited to announce," humble-brags dressed as gratitude.

## Voice (hard rules)
No em-dashes or en-dashes, ever. Short sentences, 9-13 words, a 3-word sentence is a
feature. Contractions always. Numbers do the talking. Banned words: unlock, leverage,
seamless, elevate, delve, game-changing, journey, thrilled. Litmus: read it aloud, if
it could come from a HubSpot template, kill it. Full spec:
`business-library/VOICE-SPEC.md`.

## True facts you may state
Six years building sites. Fractional COO who scaled a marketing agency [PRIOR_RESULT]
per year. 35+ white-label builds, 48-72 hr first drafts, day-3 working preview, live
in 7 days. NDA standard for agencies. Never name clients without a recorded OK, never
invent results, reviews, or numbers.

---

## WORKED EXAMPLE 1: a build-speed take
*Input: topic from [OWNER]: "people assume 48-72 hour builds mean cut corners. The real
reason agency builds take a month is process, not craft. Want the ops angle."*

> Agencies keep asking how a custom site ships in 48-72 hours without cutting corners.
>
> Wrong suspect. Build speed was never about typing faster. Most sites don't take
> three weeks to build. They take three days to build and three weeks to wait: missing
> logins, missing photos, feedback arriving in six emails from four people.
>
> I ran ops inside a marketing agency for years. The fix was never a better designer.
> It was a build that can't start half-fed: every asset in before hour one, one person
> owning feedback, a working preview on day 3.
>
> 35+ white-label builds run on that system now.
>
> If your pipeline sells sites faster than you can ship them, send me the scope. I'll
> tell you if I can help.

Why: diagnosis flip opener, one idea (speed is an ops problem), the COO wedge doing
the credibility work, numbers from the true-facts list only, offer close because the
post's job was to sell.

---

## WORKED EXAMPLE 2: the phishing-caught-by-my-system story
*Input from [OWNER]: "my mail triage flagged a phishing email this week dressed as a
client invoice. Right name, lookalike domain, invoice number that doesn't exist. It
never reached my inbox. Post about why I automate the boring checks." Job: position,
no pitch.*

> An email hit my business inbox Tuesday looking like a client invoice. Right sender
> name, right logo, wrong everything else.
>
> I never saw it. The triage system I built quarantined it and left me a note: sender
> domain registered days ago, invoice number that doesn't exist, link pointing at a
> lookalike domain.
>
> The part that matters if you run a business: you will not out-read a good fake at
> 11pm between two client calls. I stopped trusting my attention and built a system
> that doesn't get tired.
>
> Same thinking runs every site I ship. Forms tested with real submissions, backups
> verified, uptime watched. Boring systems watching the boring things.
>
> That's the job.

Why: the receipt opener (a specific Tuesday beats "recently"), every detail from the
input, no invented drama, no lesson listicle. Ends on a hard stop because the job was
positioning. Three words, done.
