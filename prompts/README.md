# Prompt convention

Every agent's LLM prompts live in the agent's OWN file, as a module-level constant:

```python
CLASSIFY = """You screen inbound messages sent to [OWNER]...
...
"""
```

or an f-string variant (`NAME = f"""..."""`) when the template needs an inline
substitution instead of `.format()`/`%`. The naming convention is ALL_CAPS, usually
just `PROMPT`, or a more specific name when a file has more than one (`CLASSIFY`,
`GEN`, `BUILD_PROMPT`, `RETRO_PROMPT`, `COMMENT_PROMPT` / `REPLY_PROMPT` / `TONE_PROMPT`
in `agents/networking.py`, etc).

**Prompts are NOT extracted into this directory.** They stay next to the code that
calls `planner._cli()` / `planner._cli_json()` with them, because:

- The prompt and its parsing logic (which keys it expects back, how failures are
  handled) change together. Splitting them across two files/directories would create
  two sources of truth that drift apart the first time someone edits one and forgets
  the other.
- Every prompt in this codebase already follows the same declaration shape (see the
  grep pattern in `tools/prompt_inventory.py`), so there was never a "where do I even
  look" problem this convention needed to solve — the problem was just that nobody had
  ever listed them all in one place.

**What lives here instead:**

- `INVENTORY.md` — a GENERATED catalog (constant name, file:line, char count, one-line
  preview) built by `tools/prompt_inventory.py`. Rerun that script any time you want a
  fresh list; never hand-edit `INVENTORY.md` directly, it will just get overwritten.

## Regenerating the inventory

```bash
tools/prompt_inventory.py            # rewrites prompts/INVENTORY.md
tools/prompt_inventory.py --check    # exit 1 if INVENTORY.md is stale (no write)
```

## Adding a new prompt

Just declare it the same way as every existing one — `NAME = """..."""` at module
level in the agent file that uses it — and rerun `tools/prompt_inventory.py` so it
shows up in the catalog. No new file, no registration step, no changelog to update by
hand (git history on the owning agent file already IS the changelog for that prompt).

## If this ever needs to become a real registry

If a future need shows up that this convention can't serve (e.g. prompts genuinely
shared across agents, or a require-review-before-prompt-change workflow), that's a
deliberate migration, not something to bolt on quietly: pick ONE new home for shared
prompts, move them there explicitly, and update every call site in the same change so
there's never a moment where a prompt exists in two places at once.
