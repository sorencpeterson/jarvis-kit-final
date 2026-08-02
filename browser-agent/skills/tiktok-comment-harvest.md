# Skill: TikTok/IG comment harvest -> GHL leads (SPEC — finalize on first live run)

**Status:** framework spec, written 2026-07-04 before any posts exist. The DOM
steps below get recorded into a rung-2 trace on the first real run, per the
browser-agent pattern. Do NOT run until [OWNER]'s account has posts with
comment-keyword CTAs live.

## Job
Sweep comments on [OWNER]'s recent posts for CTA keywords ("SITE" and future
keywords from content-plan captions). Each keyword commenter becomes a tagged
GHL contact. [OWNER] sends the actual DM/reply himself — capture is automated,
outreach stays human.

## Inputs
- `content-factory/posted.json` — which posts to sweep (id, platform, url)
- CTA keyword per video: grep the video's caption sidecar in
  `content-factory/render/out/<id>.caption.txt` ("Comment SITE" etc.)

## Flow (rung 3 first run -> record to rung 2)
1. For each posted.json entry <7 days old, open the post URL in [OWNER]'s Chrome
   (DOM tools, no screenshots).
2. Expand comments; collect commenter handle + comment text for any comment
   containing the keyword (case-insensitive).
3. Dedupe against `browser-agent/state/harvested.json` (append after insert).
4. Insert into GHL via rung 1: the gohighlevel-cli in
   `playwright-project/automations/ghl/` (contacts insert). Tag:
   `content-lead` + `kw-<keyword>` + `src-<platform>`. Do NOT enroll in any
   workflow automatically — [OWNER] decides the follow-up motion.
5. Write a one-line summary per run to `browser-agent/state/harvest-log.txt`
   (date, posts swept, new leads).
6. Close every tab this skill opened ([OWNER]'s tab rule).

## Hard rails
- Never DM, reply, like, or follow. Read + capture only.
- Never enroll harvested contacts in an automation without explicit go-ahead.
- Respect the existing "update, never re-import" GHL rule: match on handle
  if the contact exists, update tags only.
