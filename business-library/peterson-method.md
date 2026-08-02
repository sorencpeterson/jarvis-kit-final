# The [OWNER] Method
_Authored 2026-07-07, executing R267: "The delivery system gets a name... Call it
the [OWNER] Method in proposals and the one-pager. IP that raises prices costs
nothing" (`long-game-policies.md`). This is the named write-up. The operational
detail lives in `sops/deliver-a-site.md` and the full runbook
(`~/Claude/elementor-recoder/SITE-FACTORY.md`); this doc is the IP layer: what to
call it, how to say it, and why the name earns premium pricing._

## The diagram in one line
INTAKE GATE -> BUILD -> CONVERT -> IMPORT -> QA -> DELIVER + CARE

Six stations. Three are gated, two are scripted, and the last one pays twice.
A build enters on a cleared deposit and leaves as a live site with a QA report
attached and a care plan on the table.

## Why a name at all
Buyers can't compare processes they can't see, so they compare prices. A named
method makes the invisible thing the money buys visible and comparable, and the
comparison favors us: nobody else at this price shows a gate, a script, and a QA
artifact. The name costs nothing and re-frames "why [STANDARD_SITE] when Fiverr is [REFERRAL_FEE]"
into "which delivery system do you want your name on" (R267;
`competitive-teardowns.md` §1).

## The six stations

**1. INTAKE GATE.** Everything needed to build, collected before the clock starts:
deposit, tier and page list, logo, colors, copy source, real-name testimonials,
photos, form destination, booking embed, domain access path, hosting call,
analytics, care decision. **Missing items = build does not start**
(`sops/deliver-a-site.md` Stage 0).
The rule sounds strict because it's the entire speed promise: 48-72 hour
white-label delivery is honest only because the clock starts with assets in hand.
No mid-build stall, no "waiting on the client" week (offers.md; the client-delay
rule in every SOW).
*Kills the failure mode:* the stalled build. Every agency has lived it.

**2. BUILD.** The site gets designed and written as one pass, in [OWNER]'s stack,
with every line of copy held to the voice spec. Landing roughly 1 hour, Standard
roughly 2 (`sops/deliver-a-site.md` Stage 1; internal timings, don't publish).
Client-safe framing: "AI-assisted build system, my judgment on every screen." The
honesty line already exists: "I use AI in my delivery stack, that's why I'm fast
and why the price is what it is. The judgment and the guarantee are me"
(`playbooks/objections.md` #25). Tool names stay internal.
*Kills:* the six-week design phase that produces a mood board.

**3. CONVERT.** The approved build converts to the production platform by script,
about 10 minutes (`sops/deliver-a-site.md` Stage 2). Scripted means repeatable:
build 40 runs like build 4.
*Kills:* the hand-rebuild where things quietly change between the mockup the
client approved and the site they got.

**4. IMPORT.** The converted site lands on real hosting by script, minutes not
days, then gets a known-cleanups pass: forms rebuilt and tested with a real
submission to the real destination, images moved to the client's own library,
interactive elements remapped (`sops/deliver-a-site.md` Stages 3-5). DNS, SSL,
analytics. Nothing "we'll wire that up later."
*Kills:* the site that looks done and isn't. Pretty with a dead contact form is
the most expensive kind of broken.

**5. QA.** Two layers. A scripted check that fails loudly: exit 1 means FAILs
exist, means not delivered yet, no exceptions. Then a human pass, phone in hand:
tap every CTA, submit the form, read the copy aloud, check the one thing the
client said mattered (`sops/deliver-a-site.md` Stage 6).
The QA report is a deliverable, not an internal note. It ships with delivery, "a
differentiator most $500 vendors never show," and it closes care plans (Stage 7).
*Kills:* "is it actually done?" The report answers before they ask.

**6. DELIVER + CARE.** Delivery email with the QA report attached. Care plan
pitched AT delivery, not at signing, because care converts at delivery
(pricing-tree upsell path). Then the timers run: +14 days testimonial ask, +30
days referral ask. Every build logged to the build log, which is what proves or
kills the internal 2-hour Standard target over time (`sops/deliver-a-site.md`
Stage 7).
*Kills:* the post-launch abandonment every burned client expects. Care is the
answer to "what happens after it launches" (objections #46), and the referral
timer makes each delivery feed the next one.

## The white-label variant (three changes, everything else identical)
1. Agency's brand on every artifact, including the QA report.
2. The agency owns ALL client communication. [OWNER] never contacts their client,
   written confidentiality policy behind it.
3. The delivery email goes to the agency with a forwardable, client-ready version
   inside, so passing it along takes one click
   (`sops/deliver-a-site.md`, white-label variant).

## The promises the Method backs (and which station backs them)
| Promise | Station | Source |
|---|---|---|
| 48-72 hrs, white-label, from complete assets | Intake gate | offers.md |
| 7 days deposit-to-live, direct | Gate + scripted core | objections #18 |
| Working preview day 3, approve before live | Build -> Convert | pricing-tree pre-empts |
| Unlimited revisions until your client approves (first WL build) | QA + Deliver | offers.md guarantee |
| Two revision rounds included, direct | Deliver | pricing-tree rule 4 |
| QA report with every delivery | QA | deliver-a-site Stage 7 |
| Rush +50%, said plainly | Gate (scheduling) | pricing-tree rule 2 |

## Why it justifies premium pricing (the argument, compressed)
A [REFERRAL_FEE] gig and a [STANDARD_SITE] Method build can look similar on day one. The difference
is everything the buyer got burned by last time: the stall (gate kills it), the
drift (scripts kill it), the dead form (import pass kills it), the "done?"
argument (QA report kills it), the vanishing vendor (care + timers kill it). The
Method is one failure-mode killer per station. That's what the extra [FIRST_BUILD] buys,
and it's why the comparison to cheap alternatives gets EASIER when the process is
shown, not hidden (`why-me-over-freelancer.md` reason 2).

## How to say it (ready lines, voice-checked)
- **Proposal line:** "Every build runs the [OWNER] Method: intake gate, build,
  scripted convert and import, QA with a written report, delivery with care. You
  approve before anything goes live."
- **Call moment, trust objection:** "I'll walk you through the Method in two
  minutes. The short version: your build can't start incomplete, can't drift, and
  can't ship broken. The QA report comes with delivery, you'll see everything I
  checked."
- **Agency pitch:** "Your last vendor's problem wasn't talent, it was no system.
  Mine is written down. Gate, build, convert, import, QA, deliver. Your brand on
  all of it."
- **One-pager footer:** "Built on the [OWNER] Method | intake-gated | scripted
  delivery | QA report included."

## Boundaries (what the Method is not)
- Not a scope loophole. "While you're in there" is new scope, quoted after
  delivery (objections #38; scope shield in every SOW).
- Not spec work. No three-mockup bake-offs; the day-3 working preview IS the proof
  (pricing-tree rule 5, objections #40).
- Not hourly. Fixed SKU, fixed scope, fixed timeline, always (pricing-tree rule 6).

## Internal notes
- Station timings and tool names (Lovable, lovable2elementor, import-site.sh,
  qa.py) never appear in client-facing copy. Client-safe: "scripted," "gated,"
  "AI-assisted, my judgment."
- The one-page client diagram is staged as a Lovable prompt task (R267,
  `long-game-policies.md`). Until it exists, the one-line diagram at the top of
  this doc is the visual.
- First-hire note: stations 2-4 are the first contractor's actual job
  (`sops/deliver-a-site.md` owner section). The Method is also the training
  manual, which is the point of naming it: IP survives delegation.
