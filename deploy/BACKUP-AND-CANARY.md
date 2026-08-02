# Backup + Canary — off-your-Mac dead-man's switch

Right now the second brain has **no off-machine backup** and **no external
liveness alert**. `agents/autocommit.sh` commits hourly, but only to the Mac's
own disk (`backup_verify.py` reports `no_remote`), and every watchdog runs *on*
the Mac, so if the Mac goes dark nothing tells you.

This runbook fixes both:

- **Off-machine backup** — a private GitHub repo + `tools/backup_push.sh` on the
  tail of the hourly autocommit, so every commit also lands on GitHub.
- **External canary** — `.github/workflows/uptime-canary.yml` runs on GitHub's
  infrastructure (not your Mac) and pings `/pub/health` every 15 min; if the
  brain is dark it pushes **"Brain is DARK"** to your phone via ntfy.

**Honest split — what's automated vs. your hands:**

| Step | Who |
|---|---|
| Create the private GitHub repo | **You** (web UI — `gh` isn't installed) |
| `git remote add origin` + first push | **You** (one-time, copy-paste) |
| Set the 2 Actions secrets | **You** (web UI) |
| Enable the workflow | **You** (one click) |
| Add `backup_push.sh` to autocommit's tail | **You** (one line, once) |
| Every hourly push thereafter | **Automated** (autocommit → backup_push) |
| Every 15-min health ping + phone alert | **Automated** (GitHub Actions) |
| `backup_verify.py` flips `no_remote → ok` | **Automated** (next run, once a remote exists) |

Nothing below sends anything outward on its own until *you* do steps a–e. The
canary only starts pinging after you enable it and set its secrets.

---

## a. Create a PRIVATE GitHub repo

`gh` (the GitHub CLI) is **not installed** on this Mac, so do this in the browser:

1. Go to <https://github.com/new>.
2. Name it e.g. `second-brain` (anything you like).
3. **Set visibility to Private.** This repo carries your whole operating system;
   it must not be public. (`.gitignore` already excludes `.env`, `*token.json`,
   `*.key`, logs, etc., and both the autocommit guard and `backup_push.sh` refuse
   to push a value-shaped secret — but Private is the backstop.)
4. **Do NOT** check "Add a README / .gitignore / license" — this repo already has
   history; an initialized remote would just cause a first-push conflict.
5. Create the repo. Copy the URL GitHub shows you. Prefer **SSH**
   (`git@github.com:<you>/second-brain.git`) if you have SSH keys set up —
   `backup_push.sh` runs unattended from cron and can't type an HTTPS password.
   (HTTPS works too if you've cached a credential/PAT in the macOS keychain.)

## b. Wire the remote + first push

```bash
cd ~/Claude/second-brain
git remote add origin git@github.com:<you>/second-brain.git   # your URL from step a
git remote -v                                                 # confirm it's set

# First push. backup_push.sh runs the secret-guard, then pushes the current
# branch (main) and sets upstream. It NEVER force-pushes.
bash tools/backup_push.sh
```

Expected last line: `backup_push: pushed main -> git@github.com:<you>/second-brain.git`.
If it says `no remote yet` you skipped `git remote add`; if it says `BLOCKED` a
secret is in a commit — inspect with the command it prints, fix that commit, retry.

## c. Set the 2 GitHub Actions secrets

In the new repo: **Settings → Secrets and variables → Actions → New repository
secret.** Add both:

| Name | Value |
|---|---|
| `PUBLIC_HEALTH_URL` | your public health URL, e.g. `https://proposals.[OWNER_SITE]/pub/health` (must be reachable from the internet once your Cloudflare tunnel / tailscale funnel is up; it returns `{"ok":true,"service":"proposals"}`) |
| `NTFY_TOPIC` | the **same** string as `store/config.json` `"ntfy_topic"` — currently `sb-4b644b58a1-brain`. Treat it like a password; anyone who knows an ntfy.sh topic can read and post to it. |

Secrets are write-only in the UI (you can't read them back), so double-check the
values as you paste.

## d. Enable the workflow

The file `.github/workflows/uptime-canary.yml` ships to GitHub on your first push
(step b). Then:

1. Open the repo's **Actions** tab. If Actions are off on a new private repo,
   click **"I understand my workflows, go ahead and enable them."**
2. Pick **uptime-canary** in the left sidebar → **Run workflow** (the
   `workflow_dispatch` button) to fire it once by hand.
3. Watch the run. **Green** = `/pub/health` answered 200 with `ok:true`. **Red**
   = it couldn't reach the brain (tunnel down, wrong URL, brain off) **and it
   just pushed "Brain is DARK" to your phone** — that's the alert path working.
   Confirm the push arrived in your ntfy app, then bring the tunnel up and re-run
   until it's green.

After that it runs itself every 15 min. Cost: ~2,900 runs/month, each a few
seconds. **Public** repos get unlimited free Actions minutes. **Private** repos
have a 2,000-min/month free tier; each run is well under a minute, but if you
keep it private and want extra headroom, widen the cron in the workflow to
`*/30 * * * *` (~1,450 runs/mo) or make the repo public (see the workflow's
header comment).

## e. Add backup_push.sh to autocommit's tail

`agents/autocommit.sh` is the hourly committer. Append **one line** at the very
end so every hourly commit also gets pushed off-machine (this is a separate
manual edit — the task that created `backup_push.sh` intentionally did not touch
`autocommit.sh`):

```bash
bash tools/backup_push.sh >> agents/autocommit.log 2>&1 || true
```

Put it as the **last line** of `agents/autocommit.sh`, after the `git commit`
line. The `|| true` keeps a transient push failure (network blip) from making the
scheduled job look failed; `backup_push.sh` logs why and the next hour retries.
It re-runs the secret guard itself, so it's safe even though the commit already
passed one.

## f. Verify backup_verify.py flips no_remote → ok

```bash
cd ~/Claude/second-brain
python3 agents/backup_verify.py
cat store/backup_verify.json
```

Before: `"status": "no_remote"`. After a remote exists and you've pushed:
`"status": "ok"` (it shallow-clones the remote to a temp dir and confirms
`app/server.py` + `store_lib.py` are actually in it, then deletes the clone —
proving the backup is *restorable*, not just that a push once succeeded).

If it says `clone_failed`, the remote URL isn't reachable for cloning (SSH key /
auth); if `missing_files`, the wrong branch was pushed — push `main`.

---

### The two dead-man layers, together

- **Local half** — `tools/dead_man_check.py` (read-only, one JSON line): is the
  morning chain fresh, and was the last *off-machine* push < 26h ago? Run it
  from `make doctor` or a cron. It still dies with the Mac — that's why there's
  a cloud half.
- **Cloud half** — the canary curls `/pub/health` from GitHub every 15 min and
  pushes your phone if it can't reach the brain. Its docstring also sketches a
  future `GET /pub/deadman` endpoint so the same off-machine cron could verify
  *internal* freshness (morning + backup), not just "the port is open." That
  endpoint is **not built server-side** yet — it's a designed next step.
