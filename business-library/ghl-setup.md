# GoHighLevel Setup

> Specifics about the GHL account so I can build in the right place, the right way.

- **App / login URL:** app.gohighlevel.com (agency: **[OWNER_COMPANY]**, whitelabel "NOVA Profits")
- **Account / sub-account:** **The [SECOND_BRAND] Company** — this is the live DBR
  sub-account. Location ID: `oU8zwsSsM64PpqEV6wT9`.
  Direct URL base: `app.gohighlevel.com/v2/location/oU8zwsSsM64PpqEV6wT9/`
  NOTE (2026-06-23): this is an **established** account, NOT fresh/clean — it already
  holds many workflows (Clients, Patients, Contact Management folders) and prior DBR
  builds (**AOA Holiday DBR** — draft, 8k+ enrolled; **AOA Websites** — published).
  Legacy GHL Campaigns also exist here (A) YOUR OFFER Claim Nurture, B) No Show
  Nurture, C) Not Yet Ready, D) Booking Requested Reply, E) Appointment Reminders,
  Example - Negative/Positive Response). Build carefully around existing assets.
- **Where automations live:** **Workflows.**
- **Existing tags / pipelines to reuse:** TODO — audit what AOA Holiday DBR / AOA
  Websites already use before building, so naming/tags stay consistent.

## GHL "Import from a campaign" — verified 2026-06-23
- The workflow **Create workflow → Import from a campaign** option ONLY imports
  **legacy GHL Campaigns that already exist inside this sub-account** (a "Pick a
  campaign" dropdown). There is **no file upload / paste / external import** — GHL
  will not ingest an externally generated artifact.
- Viable "Pattern B" = author the sequence in a **legacy Campaign** (linear, non-canvas
  editor that Claude-in-Chrome can drive) → **Import** converts it to a Workflow,
  skipping the drag-drop canvas. Pending verification that new legacy Campaigns can
  still be created in this account.
- Reliable fallback = **duplicate the existing AOA Holiday DBR workflow** (structure
  already built) and swap in new copy.

## ★ GHL WORKFLOW BUILD ENGINE — gohighlevel-cli (internal API) — WORKS (2026-06-23)
**This is now the primary way to build GHL workflows.** It uses GHL's *internal* API
(`backend.leadconnectorhq.com`) — the only programmatic path that can create workflows
(public API can't). Productized CLI by Lead Gen Jay, installed + security-reviewed clean
(GHL token only ever goes to GHL + Firebase token-refresh; bundled blotato/nextcloud
modules are unrelated and unused).
- **Location:** `playwright-project/automations/ghl/gohighlevel-cli/`
- **Build engine:** Python builders in `builders/`; `email_step`/`wait_step`/`tag_step`/
  `link_steps` → `CampaignBuilder` POSTs/PUTs the node graph. Creates **DRAFT** only.
- **Auto-creates contact-tag enrollment triggers** (adding `wf_def["tag"]`). This is the
  piece the Playwright executor could never save.
- **Runtime:** Python 3.12 via **uv** (`~/.local/bin/uv`; system python is 3.9, too old).
  venv at `gohighlevel-cli/.venv`.
- **Auth:** `.env` (perms 600, gitignored) holds `GHL_LOCATION_ID` + a Firebase refresh
  token [OWNER] grabs via the DevTools snippet in `docs/get-firebase-token.md`. Token =
  full account access; never commit, never put in chat. Refresh tokens expire — re-grab
  if a build 401s. **Claude never handles the token** (clipboard → .env directly).
- **DBR builder:** `builders/dbr-white-label-builder.py` reads our build-plan JSON.
  `--dry-run` validates with NO token/network. Live run via `./build-dbr.sh`.
  ⚠️ **[OWNER] must run `./build-dbr.sh` himself** — Claude Code's safety classifier blocks
  the assistant from running a scraped-token internal-API call against a live account.
- **Caveat:** internal/undocumented API — can break on GHL changes; marked EXPERIMENTAL.
  Keep the Playwright executor as the UI fallback.
- **First success:** DBR White-label 8-email built as DRAFT in ~4s, 0 errors —
  trigger + 8 emails + 7 waits. Workflow `433259b9-de60-4f16-b98e-440769f35ad7`,
  folder `[OWNER] - DBR Campaigns`. Verified in UI: trigger wired, copy + merge tags
  correct, draft.
- **Generalized for reuse (2026-06-23):** the tool now works for ANY workflow, any project:
  - `builders/build_from_plan.py` + `./build.sh <plan.json>` — build from a generic JSON
    plan (email/sms/wait/tag, auto tag-trigger, reply-exit goal). No per-campaign Python.
  - `builders/md_to_plan.py` — markdown email-sequence → plan JSON (parses the
    `business-library/campaigns/*.md` format; day-gaps → waits, first email = wait 0).
  - `./build.sh <plan> --update <id>` — EDIT an existing workflow (no duplicates).
  - `./verify.sh <plan> <id>` — fetch live workflow, diff vs plan (count/types/subjects).
  - `WORKFLOW-BUILDER.md` — the agent guide (schema + build loop) for any Claude project.
  - **Global skill** `~/.claude/skills/ghl-workflow-builder/` — auto-discovered in ALL of
    [OWNER]'s Claude Code projects. Division of labor: Claude authors plan + runs `--dry-run`
    (no token); **[OWNER] runs the live build/update/verify** (classifier blocks the assistant
    from the scraped-token internal-API call).

## ★ PUBLIC API TOOLKIT — `api.sh` / `tools/ghl_api.py` — LIVE (2026-06-23)
Generic caller for the **entire GHL public API** (`services.leadconnectorhq.com`).
`./api.sh GET|POST|PUT|DELETE <path> [--loc] [--query k=v] [--json '{...}']`; `{loc}` →
location id. **Runs hands-off — no permission gate** (standard PIT auth, not the gated
internal API). Verified live: contacts read, custom-value create+delete (full CRUD).
- **Auth:** `GHL_API_KEY` in `.env` must be a **sub-account-level PIT with CRM scopes**
  (contacts/calendars/customFields/customValues/opportunities/conversations/tags/etc.).
  An AGENCY-level PIT lacks CRM scopes → 401 "not authorized for this scope". Create the
  PIT *inside* the [SECOND_BRAND] Company sub-account.
- **Covers (read+write):** contacts (+tags → enroll into workflows), custom fields/values,
  calendars + appointments, opportunities, conversations (⚠️ sends — confirm first), tags,
  invoices. Read-only: pipelines, forms, campaigns. Recipes in `API-TOOLKIT.md`.
- **Can't (public API)**: create pipelines, build forms/funnels, build workflows
  (use `build.sh` / internal API for workflows; others would need internal-API work).
- The existing vendored `./ghl` CLI also wraps much of this (contacts/opps/calendars CRUD).

## Verified workflow-list capabilities (2026-06-23)
- Legacy `/campaigns` route is **blank/dead** and there's no Campaigns tab under
  Marketing → confirms **new legacy Campaigns can't be created** in this account.
  So the "author a fresh campaign → import" route is NOT viable for net-new builds.
- Workflow row "⋮" menu offers: Edit, Rename, Open in new tab, Publish,
  Move to folder, **Duplicate workflow**, **Copy to sub-account**, Delete.
  → Cloning a built workflow and replicating it to other sub-accounts is one click each.
- The workflow **builder canvas is genuinely finicky to drive live** (even opening a
  workflow from the list took repeated clicks). Treat canvas work as rung-2
  (committed Playwright executor) territory, not live ad-hoc DOM — matches CLAUDE.md
  flag that the GHL workflow builder is the known hard outlier.
- **Sending domain / phone numbers:** Email **sending domain verified** ✓. SMS
  **number provisioned** ✓.
- **Email footer address:** use GHL location merge tag `{{location.full_address}}`
  (pulls the sub-account's address dynamically).
- **Naming convention for new workflows:** TODO — confirm.

## Standard workflow structure (for sequences like the DBR campaign)
- **Trigger:** new lead / list entry (contact added to the campaign list).
- **Steps:** alternating **email + SMS** touches with **wait timers** between them.
- **Reply-detection:** a condition that removes a contact from the sequence the
  moment they reply, so engaged people stop getting blasted.
- **Send hygiene:** SMS within business hours by timezone; daily volume caps;
  staggered sends (see `automation-preferences.md`).

## Notes / gotchas
- **Build everything paused/unpublished first.** Show [OWNER] the full copy + send
  schedule, get explicit go-ahead, then activate. Nothing sends as part of "building."
