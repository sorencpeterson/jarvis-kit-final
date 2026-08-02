# Approve-Queue Morning Routine

## Trigger
Every morning (or "anytime" per `second-brain/SYSTEM.md`'s day-to-day
section), or whenever the ⚡ **needs you** header pill shows a non-zero count.

## Steps
1. **Glance at the phone push first** (per `SYSTEM.md` day-to-day: "glance at
   the phone push (brief + 'N need you'). Dial a few warm calls from bed.").
   The morning brief lands via `morning.sh`'s 6:30 daily chain.
2. **Open the dashboard** (localhost:8765, or the Tailscale URL on phone) and
   click the **⚡ needs you** pill. This calls `/api/needs` and opens a panel
   listing every item waiting on [OWNER], each one deep-linking to its drawer
   (`openNeed(drawer)` in `app/static/index.html`).
3. **Work the queue in this order** (matches how `openNeed()` maps drawers):
   - **💬 Replies**: `loadReplies()` pulls `/api/replies`. Each card shows
     the contact name, classified intent, channel, their message, and a
     pre-filled editable draft textarea. Read the draft, edit if needed,
     click **✓ Approve & Send** (`replyApprove(id)`: this PATCHes the edited
     text then POSTs `/api/replies/<id>/approve`, with a confirm() dialog
     showing exactly what will send before it sends) or **Skip**
     (`replySkip(id)`).
   - **Proposals** (same drawer, loads via `loadProps()`): staged proposals
     show tier, price, and open status (👁 opened ×N or "unopened"). Review
     the draft cover email, **👁 View proposal** to check the actual page,
     **✓ Send** when ready (see `run-a-proposal.md` for the full proposal
     lifecycle).
   - **💼 Jobs**: job application chain items needing CAPTCHA completion or
     review.
   - **✍ Content**: drafted posts awaiting approval (auto-approves at the
     configured score bar; anything below that bar needs a manual look).
   - **🤝 Network**: on-voice LinkedIn outreach drafts awaiting approval.
   - **Retro** (Sundays): `showRetro()` shows the weekly retro's one
     proposed config change; **✓ Apply this change** or **Skip**.
4. **Every send is gated behind an explicit click.** Nothing in this queue
   auto-sends. The `replyApprove` flow's `confirm()` dialog is the last
   checkpoint before anything goes out.
5. **Clear target**: per `SYSTEM.md`, "open the ⚡ queue, clear it in ~10
   min." If it's taking much longer than that regularly, that's a signal
   volume has outgrown the routine. Worth a look at whether auto-approve
   thresholds need tuning (a retro-managed config change, not a manual
   workaround).

## What NOT to do
Don't approve a reply or proposal without reading the actual draft text.
The whole point of the gate is a real human check, not a rubber stamp. Per
this workspace's CLAUDE.md safety rails: confirm before anything outward-
facing.

## Owner
[OWNER], daily. This is the one routine in the whole system that structurally
cannot be delegated to automation. It IS the human-in-the-loop gate.

## Last-verified
2026-07-03 (read directly from `app/static/index.html`'s `openNeed`,
`loadReplies`, `loadProps`, `replyApprove` functions and `SYSTEM.md`'s
day-to-day section).
