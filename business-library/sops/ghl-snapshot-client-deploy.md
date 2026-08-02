# GHL Snapshot → Client Deploy Runbook

> Build the automation **once** in a staging sub-account, ship it to any client's
> GoHighLevel in minutes. This is the "productize once, deploy many" agency play.
> First use case: the **AI Voice Agent + Discovery-Call Niche Blueprint** (8 stages).
>
> Confirmed with [OWNER] 2026-07-08:
> - **Clients run their OWN GHL agency + sub-account** (separate from [OWNER_COMPANY]),
>   BUT **[OWNER] has agency-level access to the client account** → he does every transfer
>   click himself; the client never touches anything.
> - **Loading a snapshot onto an existing sub-account is ADDITIVE** — it layers assets on
>   top, does not delete their existing workflows/pipelines/contacts. Only risk = name/key
>   collisions updating a matching asset → mitigate with unique **`pd-` / `PD —` prefixes**
>   on every asset so nothing matches theirs.
> - **AI voice = GHL native Voice AI, and the bot + number are ALREADY set up in the
>   client account.** No rebuild — workflow voice steps import with an empty agent ref and
>   just get **re-pointed** to their existing agent.

---

## 0. The one-paragraph version

Create a permanent **template sub-account** under [OWNER_COMPANY] that never holds
real client data. Build the full blueprint there against **custom values** (so nothing
is hard-coded). Snapshot it. For each client, generate a **share link**, have the
client's agency admin accept it once, load it into their sub-account, then do the
per-client wiring the snapshot can't carry (Voice AI agent, phone/A2P, custom values,
integrations, republish). ~15 custom values + a Voice AI rebuild = a full medspa funnel
live per client.

---

## 1. What a snapshot carries — and what it does NOT

### Travels in the snapshot (build these once)
- Workflows / automations (all 8 stages)
- Pipeline + stages
- Tags, custom fields, **custom values** (the placeholder system — see §3)
- Calendars (structure/availability rules), forms, funnels/websites
- Email & SMS templates, trigger links

### Does NOT travel — rebuild/reconnect per client (the deploy checklist, §5)
- **GHL native Voice AI agent** — persona, knowledge, actions. Per sub-account.
  ⚠️ Workflow "AI Voice Agent" call steps reference an agent that won't exist in the
  target account until you rebuild it and re-point the steps. **VERIFY LIVE** whether
  the current GHL snapshot spec includes Voice AI agents — GHL ships changes; assume NOT
  until tested (§6).
- **Phone number + A2P/10DLC + voice compliance** — legally must be the client's own
  brand/EIN. Provision inside their sub-account.
- **Contacts & conversations** — their data, never yours. (Good — you don't want it.)
- **Integrations** — Stripe, their Google/Outlook calendar, any external keys. Reconnect.
- **Sending domain / email authentication** — re-verify on the client's domain.
- **Workflows arrive UNPUBLISHED** — republish per client (matches our "build paused" rule).

---

## 2. The transfer mechanism (separate agency, but [OWNER] has agency access → solo job)

Snapshots are agency-scoped, so a snapshot in [OWNER_COMPANY] can't be pushed straight
into a different agency's sub-account. It has to be imported into the client's agency
library first. **But [OWNER] has agency access to the client account, so he does all three
steps himself — the client clicks nothing:**

1. In **[OWNER_COMPANY] agency → Settings → Snapshots**, create/refresh the snapshot
   from the template sub-account.
2. Generate a **Share Link** for that snapshot (restricted / one-time-use — see IP
   warning). Open it **while logged into the client's agency** → imports the snapshot
   into the **client agency's** snapshot library.
3. **Load the snapshot onto the client's sub-account**, choosing **add/merge onto the
   existing account** (not "create new / overwrite").

### Additive load — it adds, it does not wipe
Loading onto an **existing** sub-account **layers** the snapshot's workflows, pipelines,
tags, and fields on top of what's already there. It does **not** delete their existing
workflows/pipelines, and it never touches contacts or conversations.
- **Only risk:** an asset whose **name/key matches** something they already have can get
  updated/merged instead of added. Unrelated assets are untouched.
- **Mitigation:** prefix every template asset (`PD — Stage 1…`, tag `pd-web-form`, custom
  value `pd_booking_link`) so nothing collides with their existing setup → pure add.
- **Mandatory first pass:** do a **dry-run load into a throwaway/test sub-account** and
  confirm additive behavior with your own eyes BEFORE loading the live client account.

### ⚠️ IP warning (this is your product walking out the door)
Once a snapshot is imported into a client's *own* agency, **they own that copy** and can
duplicate it to other sub-accounts or (in theory) re-share it. Mitigations:
- Use **one-time-use / restricted share links**, regenerated per client.
- Consider deploying into a sub-account **you** are granted access to and loading it
  yourself, rather than handing the raw link to a client who runs many locations.
- Price accordingly — a reusable snapshot IS the asset; don't give unlimited redeploy
  rights away for a one-build fee. (See pricing-tree / agency-white-label SOP.)

---

## 3. The custom-values placeholder system (do this or you'll edit 8 workflows per client)

Build **every** client-specific reference in the template as a `{{custom_values.x}}`
merge field, not literal text. Then a deploy is "fill ~15 fields," not "open every step."

Minimum custom-value set for the medspa blueprint:
- `business_name`, `business_address`, `business_phone`, `office_hours`, `timezone`
- `booking_calendar_link`, `reschedule_link`, `intake_form_link`
- `voice_agent_number` (the client's provisioned Voice AI line)
- `staff_notify_email`, `staff_notify_phone` (internal alerts / task owner)
- `offer_name`, `price_or_plan_note`, `review_link`, `main_website_url`

Email footer stays `{{location.full_address}}` (pulls the sub-account address live).

---

## 4. Blueprint → GHL objects (what gets built in the template)

### Pipeline (single source of truth for reporting + triggers)
`New Lead → AI Qualifying → Discovery Call Scheduled → Call Completed → Nurturing
(Decision Pending) → Patient → Long-Term Nurture / Lost`

### Tags (drive logic without cluttering stages)
- **Source:** `web-form`, `paid-ad`, `referral`, `walk-in`, `chat`
- **Status:** `needs-human-follow-up`, `not-now`, `no-show`, `hot-lead`
- **Outcome:** `plan-presented`, `plan-accepted`, `price-objection`, `timing-objection`

### 8 workflows (one per stage, testable independently)
1. **Stage 1 — New Lead Captured** — tag by source, create/update contact, enter pipeline,
   assign owner. Fires the Stage-2 voice call within 60s; SMS if no answer; email at +2m.
2. **Stage 2 — AI Qualify & Book** — Voice AI qualifies + books; objection → `needs-human`
   + staff task; unreachable → retries at +1h/+4h/+24h with SMS nudges; not-interested →
   Stage 8.
3. **Stage 3 — Discovery Scheduled** — move stage, calendar event, staff notify,
   confirmation Email+SMS, intake form if applicable.
4. **Stage 4 — Pre-Call Reminders** — 3-day value email, 24h AI confirm call, 24h SMS
   confirm/reschedule, 2–3h final SMS, reschedule auto-rebooks + resets sequence.
5. **Stage 5 — Completed / No-Show** — completed → Stage 6 + log outcome; no-show → +15m
   AI call, +1h SMS+email "we missed you", +24–48h 2nd AI call → Stage 8 if dead.
6. **Stage 6 — Post-Call Nurture** — +2h SMS recap, +1d recap email w/ proof, +3d AI
   objection call, +7d value SMS, +14d final Email+AI → Stage 8. "Yes" at any point →
   Stage 7 instantly.
7. **Stage 7 — Onboarding** — move to Patient, welcome Email+SMS packet, 24–48h first-visit
   reminder, +1d post-visit check-in + review/referral ask.
8. **Stage 8 — Long-Term Nurture / Reactivation** — biweekly value email, monthly light SMS,
   quarterly AI re-qualify call → back to Stage 2 if interest returns.

**Voice steps** = GHL native Voice AI action nodes. In the template they point at a
template agent; on each deploy they get re-pointed to the client's rebuilt agent (§5).

---

## 5. Per-client deploy checklist (the repeatable SOP)

Run top to bottom for every new medspa client. Nothing sends until the last step.

- [ ] (One-time) **Dry-run load into a throwaway/test sub-account**; confirm additive, no
      collisions, before ever loading a live client account
- [ ] Generate a **fresh restricted share link** from the current template snapshot;
      import it into the client's agency (you have access) and **load onto their
      sub-account — add/merge, NOT overwrite**
- [ ] Confirm the load added your `PD —` assets and left their existing assets intact
- [ ] **Re-point every workflow Voice AI step** to the client's **already-configured**
      Voice AI agent (bot + number are already live in their account — no rebuild, no A2P)
- [ ] **Fill all custom values** (§3) — ~15 fields
- [ ] **Connect calendars** (their Google/Outlook), reassign team/owner on calendars +
      pipeline
- [ ] **Verify sending domain / email auth** on their domain; confirm SMS number sends
- [ ] Reconnect integrations (Stripe / payment, forms, ad lead sources)
- [ ] **Test each workflow** with a dummy contact (you, not a real lead)
- [ ] Show [OWNER]/client the copy + send schedule → **explicit go-ahead** → **publish**
- [ ] Close browser tabs; log the deploy

---

## 6. Open items to VERIFY LIVE before first real deploy

Honesty flags — GHL changes features often; don't assume, test in-account:
1. **Does the current snapshot include native Voice AI agents?** Build the template,
   snapshot it, load into a throwaway sub-account, and check if the Voice AI agent +
   workflow voice steps come through. If not (assume not), the rebuild in §5 is mandatory
   every time — bake it into the price/time estimate.
2. **Share-link restrictions on your plan** — confirm one-time-use / restricted links are
   available; confirm cross-agency import actually works end to end with a test agency.
3. **Voice AI per-minute cost** — native Voice AI bills usage on top of the sub-account.
   Confirm and disclose to the client so it's not a surprise line item.
4. **Snapshot "push updates" vs. one-time load** — with separate agencies you generally
   get a one-time import, not live push updates. Confirm whether updating the template
   later can be re-pushed, or if each update is a fresh manual load.

---

## 7. How this gets built (division of labor)

- **Claude (this session):** authors the pipeline/tags/8-workflow **build-plan JSON**
  against custom values, runs `--dry-run` (no token, no network) to validate. Same engine
  as the DBR build — see `ghl-workflow-builder` skill + `playwright-project/automations/
  ghl/gohighlevel-cli/`.
- **[OWNER]:** runs the live build into the **template sub-account** (the classifier blocks
  the assistant from the scraped-token internal-API call), then handles the UI-only pieces
  (snapshot create, share link, Voice AI agent build, A2P) — those are agency-UI actions,
  rung-2 Playwright at most, no public API path.
- Native Voice AI agent config itself is **UI-only** — build once in the template by hand,
  document the exact settings here so each client rebuild is a copy job, not a redesign.

---

## Next actions (when you're ready to move past "just the plan")
1. Create the `TEMPLATE — Medspa Voice AI` sub-account (I'll confirm before it's created).
2. I author the 8-workflow build-plan JSON + custom-value schema; you dry-run then live-build.
3. Build the native Voice AI agent once in the template; document its settings here.
4. Snapshot + test-load into a throwaway account to answer the §6 verify items.
5. First real client deploy using §5.
