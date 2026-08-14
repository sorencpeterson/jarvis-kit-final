---
name: webfix-teardown-to-quote
description: Run a live teardown of a prospect's real site, turn it into a plain fault list, and fork to the right quote. The [WEBFIX] Webfix bundle if it's salvageable, a rebuild if the teardown finds 4+ structural faults. Observed faults only, the fork rule stated in one line. [OWNER_COMPANY].
---

# webfix-teardown-to-quote

## When to use
A prospect has an existing site and [OWNER] needs to teardown, then quote. Input: the live
URL and their niche. Output: the fault list (observed, ranked, plain-English), the fork
call ([WEBFIX] Webfix vs rebuild), and the one-line reason for the fork. Skim the site like a
customer would, on a phone. Every fault must be real and observed. A teardown with
invented faults is a horoscope, and it burns the trust the teardown was supposed to earn.
The teardown plus one mockup is the only free spec work [OWNER] does.

## Step 1: the teardown pass (skim like a 9pm customer on a phone)
Walk the site the way their buyer does and log what actually fails. Check, in order:
1. **Does it load fast on mobile?** Slow load is a silent conversion killer, and it's
   fixable, so it's a Webfix candidate, not a rebuild trigger.
2. **Can they book / call / quote from the top, on a thumb?** The action above the fold
   is the whole game. Buried CTA = leaking money.
3. **Is there proof?** Faces, credentials, before/afters, reviews. A trust business with
   no trust on the page.
4. **Are prices or "from $X" anchors shown?** Hidden pricing sends buyers to whoever
   shows it.
5. **Does the structure match the business?** Right pages for their revenue lines
   (medspa: a GLP-1/membership page; men's-health: a page per program). Missing pages are
   structural.
6. **Is it a rental page?** A Fresha/Vagaro/Linktree/bare-Calendly link is not a site.
   That's not a fix, that's a build.

## Step 2: sort faults into fixable vs structural
The fork depends on this cut.
| Fixable (Webfix territory) | Structural (rebuild territory) |
|---|---|
| Slow load, unoptimized images | No real site (Fresha/Linktree-only page) |
| Broken mobile layout | Wrong or missing pages for their revenue lines |
| Buried or dead CTA button | No booking path that can be embedded at all |
| Missing meta / basic on-page SEO | Copy and brand that undersell a trust business |
| A few missing trust elements to drop in | Architecture that can't hold what they sell |

## Step 3: the fork (the one rule)
- **Under 4 structural faults, site is salvageable -> Webfix bundle, [WEBFIX].** A fix list:
  speed, mobile, the CTA, basic SEO, drop in the missing proof. One-time, fast.
- **4+ structural faults -> recommend a rebuild, and say why in ONE line.** Don't dress a
  rebuild as a fix. "This isn't a fix, it's a rebuild. Four things are broken at the
  foundation and patching them costs more than starting clean." Then route to the SKU:
  Standard [STANDARD_SITE], Booking [ECOM_PRICE] if they take appointments, White-Glove [WHITE_GLOVE] for a
  medspa/men's-health dead-or-rental page.
- **A Fresha/Vagaro/Linktree/bare-Calendly-only page is an automatic rebuild.** There's
  nothing to fix. Say it plainly and route to White-Glove (medspa/clinic) or Booking.

Webfix is confirmed at [WEBFIX]. Full routing: `business-library/playbooks/pricing-tree.md`.

## Voice (hard rules)
The fault list is a diagnosis, not a roast. Plain, specific, no gloating. No em-dashes or
en-dashes, ever. Short sentences, 9-13 words. Contractions always. No emojis. Name the
fault and what it costs, then the fork. Numbers do the talking. Banned: unlock, leverage,
seamless, elevate, excited, circle back. Full spec:
`business-library/VOICE-SPEC.md`.

## True facts you may state
Six years doing this. The teardown plus one mockup is the only free spec work, the mockup
IS the proof. Day-3 working preview on any build, approve before live, 7 days from
deposit. 50% deposit books the slot, price holds 14 days. A retained medspa tox patient
is worth about [PROJECT_EXAMPLE] a year. Never invent a fault you didn't observe, never quote a fix
for a problem the site doesn't have, never claim guaranteed rankings or results.

---

## WORKED EXAMPLE — medspa on a Fresha-only page (teardown to fork)
*Input: Nadia, medspa owner. She sends her "website," which is her Instagram bio link. It
goes to a Fresha booking page. Nothing else exists.*

**The teardown pass (skimmed on a phone, like her patient at 9pm):**
- Load: it's a Fresha page, so it loads, but it's a rental, not hers.
- Book from the top: yes, but that's ALL it does. It books and nothing else.
- Proof: none. No faces, no credentials, no before/afters, no reviews.
- Prices: none shown before booking.
- Structure: there is none. No home, no services page, no program pages. She runs a
  GLP-1 program with nowhere to send anyone.
- Rental page: yes. This is the whole finding.

**The fault list (plain, said to her):**
> Nadia, honest read. What you've got isn't a website, it's a Fresha booking page with
> your name on it. It books, and that's the only thing it does. There's no faces, no
> credentials, no before-and-afters, no prices, and no home for the weight-loss program
> you're running. Someone finds you on Instagram at 9pm, taps your link, and lands on a
> booking screen with zero reason to trust you yet. You're paying for that click and
> giving them nothing to say yes to.

**The fork (automatic rebuild, one line, then the SKU):**
> This isn't a fix, there's nothing to fix. It's a build. You need a real site with the
> GLP-1 program on its own page, your credentials and results up top, and Fresha embedded
> so booking still runs where it always has. That's White-Glove, copy, brand, and the
> build. Thirty-five hundred.

Then, because she gave no anchor yet, get it before quoting hard: "What's a patient worth
to you over the life of a program?" and anchor the [WHITE_GLOVE] to her number.

Why: a Fresha-only page is the automatic-rebuild case, so this is NOT a [WEBFIX] Webfix and
the skill says so out loud rather than forcing a fix that doesn't exist. The teardown is
specific and observed (no faces, no program page, no prices), which earns the rebuild
call. The landmine holds: Fresha gets embedded, never replaced. The White-Glove route is
the confirmed medspa dead-page steer, and the price waits for her number.
