# Failure Post Template: C59

Source: 250-IDEAS-BUSINESS.md C59. Quarterly: one thing that went wrong and the fix.
Trust compounds faster than wins. No client-identifying detail, ever, even when the
mistake was entirely [OWNER]'s own process failure and not a client's fault.

**Status: publish is human-gated.** This is the one content type where the honesty
bar matters more than the polish. [OWNER] picks the actual failure each quarter, this
is a skeleton, not a fill-in-the-blank generator.

---

## The skeleton
1. **State the failure plainly.** No softening language, no "opportunity for
   growth." What broke, in one or two sentences.
2. **What it actually cost.** Time, a client's trust, a missed deadline, whatever
   the real impact was. Honest, not dramatized.
3. **How it got caught.** Usually not "I caught it." Most failures get caught by
   someone else first. Say so.
4. **The fix.** Not "be more careful." A specific system change that makes the same
   failure structurally harder to repeat.
5. **No pitch at the end.** This post type earns trust by not asking for anything.

## SAMPLE (illustrative shape, not a specific real incident)
Had a workflow trigger twice on the same contact a few months back. Same automated
email, sent twice, two hours apart.

Cost: one client got a duplicate message that looked sloppy, and I found out about
it from them, not from any internal alert. That's the part that actually stung, not
the duplicate email itself.

Fix wasn't "double-check every send by hand." That doesn't scale and it doesn't
actually prevent the next version of the same bug. The real fix: added a dedupe
check before any automated send fires, so the same contact can't get hit twice by
the same trigger inside a set window, full stop, no exceptions.

Systems fail quietly until someone tells you. The job isn't avoiding every failure.
It's making sure the same one can't happen twice.
