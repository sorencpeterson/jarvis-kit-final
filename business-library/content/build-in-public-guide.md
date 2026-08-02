# Build in Public Guide: C42

Source: 250-IDEAS-BUSINESS.md C42. The premise: post the actual dashboards and
systems, sanitized. The operation itself is the content. Agencies and owners follow
operators who show receipts, not people who talk about showing receipts.

**Status: publish is human-gated.** Every post, especially anything with a
screenshot, gets [OWNER]'s review before it goes up. Screenshots get a specific
sanitization pass (see below) every single time, no exceptions, no "just this once."

---

## What's shareable vs never

### Always shareable (with sanitization pass)
- System structure and workflow logic (how a pipeline stage works, what triggers
  what), the mechanism, not the data inside it.
- Aggregate numbers (build count, average turnaround, retention rate), pulled from
  build-log.csv, never a single client's specific figures without their OK.
- Process screenshots with all client-identifying fields blanked or replaced with
  placeholder text before the screenshot is taken, not blurred after.
- Tools and stack decisions (what's used, why, what got replaced and why).
- Mistakes and what got fixed (see failure-post-template.md), trust compounds
  faster than wins.

### Never shareable, full stop
- Client names, business names, or anything that identifies a specific client
  without their explicit written OK (portfolio permission is a separate, tracked
  yes, see G116 in the proof kits).
- Dollar amounts tied to a specific named or identifiable client.
- API keys, tokens, credentials, internal URLs, anything from a .env file or
  account settings screen. Check every screenshot for a URL bar, an account email,
  a tab title before it goes anywhere.
- Anything under an NDA (white-label client work is invisible by design, it never
  appears in build-in-public content, ever).
- Unreleased pricing changes, unannounced offers, anything [OWNER] hasn't actually
  confirmed as final.
- Internal notes about specific people (partners, contractors, prospects) even
  anonymized, if the detail is specific enough to identify them to anyone who knows
  the business.

### The sanitization pass (every screenshot, every time)
1. Blank or replace every name field with a placeholder before screenshotting, not
   after.
2. Check the browser tab, URL bar, and any visible account email.
3. Check for dollar figures tied to anything identifiable.
4. Ask: could someone who knows this business recognize it from this image alone?
   If yes, it doesn't go out as-is.

---

## SAMPLE POST 1: The pipeline board
Every client, every build, every deadline lives in one board. No status update
meetings. No "hey, where's this at" emails. If you want to know where a build
stands, you look at the board, because the board is always current.

That single habit is the difference between a business that runs and a business
that's run BY someone, all day, reactively.

## SAMPLE POST 2: What the daily brief actually says
Every morning I get one message: what shipped yesterday, what's due today, what
needs my attention. Not a dashboard I have to check. It comes to me.

Building the thing that tells you what to do today is more valuable than doing more
things today. Most people skip that step forever.

## SAMPLE POST 3: The QA gate, sanitized
Every site gets the same automated check before it ships: broken links, missing
alt text, slow images, mobile rendering, meta tags, the works. Same bar, every
build, no exceptions for a rush job.

The check takes under two minutes to run and it's caught things I would have missed
by hand every single time I've used it. Consistency isn't a personality trait. It's
a system you build once.

## SAMPLE POST 4: A stack decision, explained
Swapped a manual step for an automated one this month: instead of me writing a
"here's your report" email by hand every time, the system drafts it from the actual
data and I approve before it sends.

Nothing sends without my eyes on it first. But I went from writing that email to
editing that email, and the difference in a busy week is real.

## SAMPLE POST 5: The mistake, told straight
Had a workflow trigger twice on the same contact a few months back, same email,
two hours apart. Caught it because the client mentioned it, not because I caught it
first. Fixed it that day: added a dedupe check before any send fires.

Systems fail quietly until someone tells you. The fix isn't "be more careful." It's
building the thing that catches it before a person has to.

## SAMPLE POST 6: The number, not the adjective
"Fast" doesn't mean anything on its own. Here's the actual number: average
turnaround on a standard build, deposit to delivery, is 48 to 72 hours.

That's not a tagline. That's what build-log.csv says when I pull it. If a claim
isn't backed by a number I can point to, I don't post it.
