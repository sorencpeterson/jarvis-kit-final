# What this costs, and how to run it for nothing

Short version: **Claude Pro at $20/mo is the only thing you need.** Everything
else has a free path or can stay off.

Run `setup.py` and pick the Pro profile and it configures itself to survive on
that plan. Nothing below is required reading unless you want to change something.

---

## The one real requirement

**Claude Code.** Every agent's thinking step shells out to `claude -p`. No API
key, it uses your existing subscription.

| Plan | What you get here |
|---|---|
| **Pro, $20** | Fine. Pick the Pro profile at setup: cheap model routing, a daily token budget, a trimmed morning chain, lower daily caps. |
| **Max** | Full chain, stronger models, no budget cap. |
| API key | Works, but you pay per token. The subscription is cheaper for this workload. |

### What the Pro profile actually changes

- **Models.** Haiku for every internal step (triage, planning, scoring, chat).
  Sonnet only for text a human will read (posts, DMs, replies, proposals,
  resume tailoring). **No Opus anywhere.**
- **Daily token budget: 400k.** Past it, `planner._cli` automatically drops
  internal features to the cheap model for the rest of the day. Public-facing
  writing keeps its model. You will not silently run out mid-morning.
- **Morning chain: lite.** The full chain touches 100+ agents. Lite runs the
  core and jobs lanes only. Flip `morning_profile` to `full` in
  `store/config.json` if you upgrade.
- **Lower caps.** 5 job applications a day, 8 connects, 4 comments.

Feeling it? Lower `daily_token_budget`, or set `morning_profile` to `lite` and
run the other lanes by hand when you want them.

---

## Optional integrations, and the free version of each

| Feature | Paid default | Free alternative |
|---|---|---|
| **Push notifications** | — | **[ntfy.sh](https://ntfy.sh) is already free.** No account. Pick an unguessable topic, install the app, subscribe. |
| **Images for posts** | OpenAI (~$0.04/image) | Skip it. Text posts do fine. Or generate elsewhere and drop files in `content/images/`. |
| **Voice brief** | ElevenLabs | macOS has `say` built in: `say -f store/brief.md`. Free, offline, good enough for a morning brief. |
| **Gmail + Calendar** | — | **Free.** Google's API quota is far above what this uses. Only cost is the OAuth setup in `schedule/SETUP.md`. |
| **Job boards** | — | **Free.** Public endpoints, no keys. |
| **LinkedIn** | — | **Free.** Drives your own logged-in browser. No LinkedIn API, no Sales Navigator. |
| **CRM sync** | GoHighLevel | Skip unless you already pay for GHL. Nothing depends on it. |

Everything above degrades cleanly. Missing key means that one feature is off,
with a note in the logs. Nothing crashes.

---

## Skipping setup entirely

The wizard is convenience, not a requirement.

```bash
python3 setup.py --quick            # name and email only, Pro defaults
python3 setup.py --plan=max         # preset the plan, still asks the rest
```

Or write `config/owner.json` yourself. Copy `config/owner.example.json` and fill
it in. The only field that matters is `name`; everything else has a fallback.

`connect.py` is entirely optional. Run `python3 connect.py --status` any time to
see what is wired up and what is not.

---

## Cheapest useful setup

If you want the value with none of the spend:

1. `bash install.sh`
2. `python3 setup.py --quick` (Pro profile)
3. `cp -r skills/yours/* ~/.claude/skills/`
4. Stop there.

That gives you 36 skills covering LinkedIn writing, job applications,
interviews, salary negotiation, and proposals. They cost nothing beyond your
Claude subscription and need no agents, no server, no keys, no scheduling.

Add the agent machinery later, when you know which parts you actually want.
