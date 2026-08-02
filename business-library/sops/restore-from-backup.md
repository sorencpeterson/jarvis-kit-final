# Restore From Backup

_Honest scope up front: as of this writing, the second-brain repo has **no
git remote configured.** `backup_verify.py` confirms this directly
(`status: no_remote`). "Restore from backup" today means local git rollback
only. There is currently NO off-machine backup of this system. This SOP
documents both what works today (local rollback) and what's missing
(off-machine safety net), rather than overstating either._

## Trigger
A bad edit broke something (a code change, a config change, an accidental
delete) and you need to get back to a known-good state.

## Steps: local rollback (works today)

1. **Check what changed:**
   ```
   cd ~/Claude/second-brain
   git log --oneline -20
   git status
   ```
2. **Every hour, `autocommit.sh` sweeps the working tree** and commits a
   snapshot automatically. So even an unstaged, uncommitted mess from
   earlier today likely has an hourly checkpoint to roll back to. Look for
   commits with the autocommit pattern in the log.
3. **To see what a specific commit changed:**
   ```
   git show <commit-hash>
   ```
4. **To undo a bad commit without losing history** (safer, preferred):
   ```
   git revert <bad-commit-hash>
   ```
5. **To hard-reset to a known-good commit** (destructive, only if certain,
   and only on this local rollback, never against a remote):
   ```
   git reset --hard <good-commit-hash>
   ```
   Per this workspace's general git safety rules: prefer `revert` over
   `reset --hard` unless you're certain and have already confirmed there's
   nothing uncommitted worth keeping.
6. **After any rollback affecting the server or dashboard**: restart the
   service and confirm health:
   ```
   curl -s localhost:8765/api/health
   ```
   (`watchdog.sh` polls this same endpoint every 5 minutes and auto-restarts
   a dead server, but confirm manually right after a rollback rather than
   waiting for the watchdog cycle.)

## Steps: verifying backup health (run this periodically, not just when
## something's broken)
```
python3 second-brain/agents/backup_verify.py
```
This checks for a configured `origin` remote. **Today it will report**
`{"status": "no_remote", "reminder": "No git remote configured for
~/Claude/second-brain, there is currently NO off-machine backup of this
repo. Add one (e.g. a private GitHub repo) and re-run backup_verify.py."}`.
That reminder is accurate and current as of 2026-07-03. If/when a remote
is added, this same script shallow-clones it into a temp directory and
confirms key files (`app/server.py`, `store_lib.py`) actually exist in the
fresh clone, proving the remote is genuinely restorable, not just that
`git push` succeeded once at some point.

## What to do about the missing off-machine backup
This is a real, currently-open gap, tracked here rather than hidden:
1. Create a private GitHub (or equivalent) repository.
2. `git remote add origin <url>` inside `~/Claude/second-brain`.
3. `git push -u origin main` (or the current branch name).
4. Re-run `backup_verify.py` to confirm the clone-restore check passes.
5. Consider adding `backup_verify.py` to a scheduled cadence (it isn't
   currently in the `launchd` agent table in `SYSTEM.md`) once a remote
   exists, so backup health gets checked automatically rather than only when
   someone remembers to run it.

## What's NOT covered by any of this
- Client site backups (hosting-level, not this repo). Those are covered
  under each client's care plan per `business-library/playbooks/
  pricing-tree.md` ("Care Basic: hosting monitored 24/7... backups"), a
  separate system from the second-brain repo entirely.
- Anything outside git's tracking (large binaries, `.env` secrets: those
  are `chmod 600` and gitignored by design, so they are explicitly NOT part
  of any git-based restore and need their own separate safekeeping, e.g. a
  password manager).

## Owner
[OWNER]. `backup_verify.py` exists as a script today but isn't yet wired into
a scheduled agent. Running it is currently a manual, periodic task.

## Last-verified
2026-07-03 (read directly from `second-brain/agents/backup_verify.py` in
full and `second-brain/SYSTEM.md`'s Troubleshooting section).
