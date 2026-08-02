---
name: ghl-workflow-builder
description: Build or edit GoHighLevel (GHL) workflows — automations, drips, email/SMS sequences — from a JSON plan or a markdown sequence, via GHL's internal API. This is the ONLY programmatic way to CREATE GHL workflows; the public API and all MCPs (incl. OpenClaw) can only read them. Use whenever a task involves creating, building, editing, or deploying a GoHighLevel workflow / automation / email or SMS sequence. Do NOT try to build via the GHL canvas UI.
---

# GHL Workflow Builder

Programmatically build GoHighLevel workflows + drive the GHL API. The tool lives at:

```
/Users/[OWNER_SITE]/Claude/playwright-project/automations/ghl/gohighlevel-cli/
```

**`CAPABILITIES.md` in that folder is the master reference for everything this can do.**
For workflow schema specifically, read `WORKFLOW-BUILDER.md`. Quick version:

## Build loop

1. **Author** the sequence — either:
   - a build-plan JSON (see schema in `WORKFLOW-BUILDER.md`), or
   - markdown → plan: `./.venv/bin/python builders/md_to_plan.py --md copy.md --trigger-tag TAG --from-name NAME --folder-name "Folder" --out plan.json`
2. **Dry-run (you, the assistant, CAN run this — no token, no network):**
   ```bash
   ./build.sh plan.json --dry-run
   ```
3. **Hand the live build to the human** (the safety classifier blocks the assistant from
   running the scraped-token internal-API call on a live account):
   ```bash
   ./build.sh plan.json                 # create new DRAFT workflow
   ./build.sh plan.json --update <id>   # EDIT an existing workflow (no duplicate)
   ```
4. **Verify** (human runs — reads the live account):
   ```bash
   ./verify.sh plan.json <workflow_id>
   ```

## Data / config ops — the public API (contacts, calendars, custom fields, tags, etc.)

For anything that ISN'T workflow-building, use the public API tool — it reaches the whole
GHL public surface, runs hands-off (no permission gate), and is read+write:

```bash
./api.sh GET  /contacts/ --loc --query limit=20
./api.sh POST /contacts/<id>/tags --json '{"tags":["dbr-agencies-cold"]}'   # enroll into a workflow
./api.sh POST /locations/{loc}/customFields --json '{"name":"Lead Source","dataType":"TEXT"}'
```

Full recipes (contacts, calendars, custom fields/values, opportunities, conversations,
tags, invoices) in **`API-TOOLKIT.md`**. On error the tool prints the API's message, so a
422 tells you exactly which field to add. Needs `GHL_API_KEY` (a sub-account PIT with CRM
scopes) in `.env`. ⚠️ Sending messages / publishing — confirm with the human first.

## Rules

- Builds are always **DRAFT**. Never publishes, never sends. Human reviews + publishes.
- The token (`.env`, `GHL_FIREBASE_REFRESH_TOKEN`) = full account access. **Never** read it,
  echo it, commit it, or enter it anywhere. The human grabs/pastes it (DevTools snippet in
  `docs/get-firebase-token.md`). If a build 401s, the token expired — human re-grabs it.
- Internal/undocumented API (EXPERIMENTAL). If it breaks, the Playwright UI fallback is at
  `../executor/`.
- Enroll contacts by adding the workflow's `triggerTag` to them (public API / OpenClaw).
