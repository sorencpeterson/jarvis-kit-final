# Second Brain — North Star

> The goal: **have everything in my life at a glance, and a proactive chief-of-staff
> that moves me toward my goals — that I can control from anywhere, including my phone.**

[OWNER]'s vision, in his words (2026-06-24), structured:

## What it is
A large, proactive, assertive **visual command center** — not a passive dashboard.
It brings things to my attention instead of waiting for me to look.

## Layout
- **Center (the main event):** a big **visual life-map** — my different areas of life,
  with project details **auto-aggregated from our Claude chats** + incoming info.
- **Right rail:** buttons, live info, and the day's proposed actions.
- **Pinnable trackers:** I pin what I care about; it stays in view.
- **Scenes:** the view rotates / surfaces things on a cadence so nothing goes stale.
- Looks **really cool**. Best-possible everything-dashboard.

## Powers
1. **3 top actions per day.** It proposes the three highest-leverage moves toward my
   goals. I hit **Accept** and it builds out whatever it judges best — across any area.
2. **Proactive planning.** It suggests plans/paths to my goals on its own.
3. **Auto-triage + daily briefs.** Cheap sub-agents (Haiku) organize and summarize.
4. **Control everything from one place** — Claude and my other programs — from anywhere,
   phone included.
5. **Always-on + stable.** Accessible all the time.

## Guardrails (how we keep it safe & sane)
- **Accept-to-act:** it proposes; I approve; then it builds. Nothing big happens unasked.
- **Second confirm for outward/irreversible actions** (sends, publishes, deletes).
- **Phone controls the brain, not a raw remote shell.** Reachable over Tailscale.
- **Cheapest rung that works** — Haiku sub-agents for triage/brief/planning; escalate only when needed.

## Roadmap (living)
- [x] Capture (Siri), store, dashboard, GHL live, agents, goals
- [x] Living app server + interactive UI + brain console (full AI on Max plan)
- [ ] **Big life-map UI** — center map, right rail, pinnable trackers, scenes
- [ ] **Planner** — 3 daily actions, Accept → build (this is the soul of the vision)
- [ ] **Project aggregation from chats** — per-area project notes built from sessions
- [ ] **Incoming feed** — what the agents did / found, surfaced
- [ ] **Auto-triage agent + daily brief** (scheduled Haiku sub-agents)
- [ ] **Always-on service** (launchd) + phone access (Tailscale)
- [ ] **Google Calendar** (scheduled for the next day)
- [ ] Life-area feeds: finance, health, relationships, mental health
