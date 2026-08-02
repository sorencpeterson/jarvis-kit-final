# Run a Proposal

## Trigger
A prospect (warm reply, discovery call, cold reply) is ready for a priced
proposal. Usually the moment `reply_watch.py` classifies an inbound message
as `intent=interested` (which already auto-builds one inline), or any time
[OWNER] decides a contact deserves one manually.

## Steps

**Automatic path (most common):** `second-brain/agents/reply_watch.py`
classifies an inbound reply using the objection-playbook digest + voice spec.
If intent is `interested`, it builds the proposal INLINE and drops the link
straight into the drafted reply. No manual trigger needed. Check the 💬
Replies drawer; if a proposal link is already in the draft, review and
approve per `approve-queue-morning-routine.md`.

**Manual path (dashboard, ⌘K or the + New button):**
1. Open the command palette (⌘K) and run **"Draft a proposal,"** or click
   **+ New** in the Proposals section of the dashboard (`makeProposal()` in
   `app/static/index.html`).
2. Answer the three prompts exactly as asked:
   - **Who for?**: email, name, or GHL contact id
   - **Niche?**: hvac, plumbing, restaurant, agency, webfix, or whatever fits
   - **Their website?**: leave blank to use GHL data / no site on file
3. This fires `POST /api/proposal/make`, which runs
   `second-brain/agents/proposal_factory.py` in the background (1-3 minutes).
   A confirmation toast says "Factory building (1-3 min). It lands here when
   staged."
4. The dashboard auto-refreshes the Proposals list at 95s and 190s after
   trigger, or manually reload the drawer.

**Manual CLI path (for testing or bulk work):**
```
python3 second-brain/agents/proposal_factory.py --email <email> --niche "<niche>"
python3 second-brain/agents/proposal_factory.py --name "<Business Name>" --url <their-site-url>
python3 second-brain/agents/proposal_factory.py --contact-id <ghl-id> --tier standard --dry
```
`--dry` skips GHL lookups (use provided args only), good for a smoke test.
`--no-llm` does a template-fill-only run with no generation call.

**What the factory actually builds:**
- A rendered proposal page: `store/proposals/<pid>.html`, served at
  `/prop/<pid>?sig=<HMAC>` (404s on a bad signature)
- A mockup preview embedded (rebuilt homepage, framed live iframe) at
  `/mock/<pid>?sig=`
- A one-page agreement at `/agree/<pid>?sig=` with typed-name acceptance
- A queue record in `store/proposals.jsonl`, status `staged`
- A drafted cover email. NOTHING sends automatically; it's staged for
  [OWNER]'s one-click send

**Sending it:**
5. Review the drafted cover email and the rendered proposal (👁 View
   proposal button opens the live link).
6. Click **Send** in the Proposals drawer (`propSend()`). This is the
   explicit outward-facing gate. Pricing logic comes straight from
   `business-library/playbooks/pricing-tree.md` (`PRICING` dict in
   `proposal_factory.py` mirrors that file exactly).

**After it's sent (automatic, no action needed):**
- `proposal_timers.py` (rides `reply_watch`) handles follow-up: opened-3-day
  loop-close, unopened-4-day resend, once each.
- The proposal page tracks opens and a beacon (time on page, max scroll,
  time per section) per `EXECUTION-PACK/legal/privacy-policy-tos-public-
  pages.md`. This is disclosed honestly in that policy.

## Owner
[OWNER] (the send click is never automated; drafting can run unattended).

## Last-verified
2026-07-03 (read directly from `proposal_factory.py`, `app/static/index.html`
`makeProposal()`/`startCoach`-adjacent code, and `STATE.md`'s Fable Window
buildout notes).
