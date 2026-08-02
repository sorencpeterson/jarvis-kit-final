---
name: linkedin-profile-edit
summary: Edit [OWNER]'s OWN LinkedIn headline and/or About text via his real Chrome. Owner-authorized account edit; save is allowed for these two fields ONLY. Proven 2026-07-15.
tools: mcp__Claude_in_Chrome__*
---

# LinkedIn Profile Edit — Executor

Updates [OWNER]'s own profile headline and/or About (summary). Dispatch as a **Sonnet**
browser operator on his REAL logged-in Chrome, DOM-first. This is the ONE account-edit
the operator brief's "stop before changing account settings" rule is waived for, and
ONLY for these two text fields, and ONLY when [OWNER] authorized it this session. Touch
nothing else: no connections, messaging, posts, experience, skills, settings, photo,
"Open to work".

## Non-negotiables
- NEVER click by screenshot coordinate. Ref-based clicks only (a past capture run
  misclicked Like via coordinates). Zoom to verify, don't coordinate-click.
- Capture the CURRENT headline + About verbatim BEFORE editing (restore point) and
  report them. Save the restore point to a dated file under second-brain/content/.
- Verify the field is visibly EMPTY before typing, and read the profile back live
  after saving. Do not trust that an action succeeded without visual confirmation.

## The two hard gotchas (both cost real time on the first run)
1. **`form_input` FAILS on both fields.** LinkedIn's Headline and About are
   contenteditable `DIV`s, not `<textarea>`/`<input>`; `form_input` throws
   `Element type "DIV" is not a supported form input`. Use instead, all ref-based:
   `computer left_click(ref)` -> `key("cmd+a")` -> `key("Delete")` ->
   verify empty (zoom/screenshot) -> `computer type(new text)` -> verify -> Save.
2. **Clearing can silently no-op the first time.** The click->cmd+a->Delete sequence
   sometimes leaves the original text fully intact (focus/timing race). ALWAYS verify
   the field reads empty before typing; if not, run the clear sequence again and
   re-verify. Only type into a confirmed-empty field.

## Also worth knowing
- **About lazy-loads below the fold.** It is absent from `get_page_text` and the
  top-level `read_page` tree on first load (renders as a skeleton). Scroll down to
  force it, THEN `find("About section edit pencil")`. A first-load "not found" does
  NOT mean the section is missing.
- **Fallback if the About pencil still won't `find`** (seen 2026-07-15 even after a
  scroll + 2s wait): navigate STRAIGHT to `https://www.linkedin.com/in/<slug>/edit/forms/summary/new/`.
  The modal opens pre-filled with the existing About; the "new" in the route is just
  LinkedIn's URL name, not a signal the section is empty. Beats concluding "no About
  section exists" and trying to create one.
- Headline limit 220 chars, About limit 2,600. The 2026-07 authority headline fit at
  153/220, About at 884/2,600 — no trim needed.

## Proven path (2026-07-15)
1. New tab -> `https://www.linkedin.com/in/me/` (resolves to his /in/ slug). Login
   wall -> stop, report blocked.
2. `get_page_text` -> capture + report OLD headline. Scroll down, capture OLD About.
3. About: `find("About section edit pencil")` -> click -> modal `/edit/forms/summary/new/`
   -> `read_page(filter:interactive)` for the contenteditable ref -> clear-and-type
   sequence above -> `find("Save button")` -> click.
4. Headline: navigate `.../edit/intro/` -> `find("Headline text input")` -> same
   clear-and-type sequence -> Save.
5. Verify: navigate back to `/in/me/`, `get_page_text` (headline repeats on every post
   byline too), scroll for About. Quote what's live.
6. Close ONLY the tab you opened.

## Restore point
The 2026-07-15 pre-rewrite headline + About are saved verbatim in
`second-brain/content/linkedin-profile-restore-point-2026-07-15.md`.
