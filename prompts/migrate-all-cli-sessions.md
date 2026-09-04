# Task: import every Claude Code CLI session into the personal Desktop profile

You are working in `~/1_projects/dev/claude_switch_profiles`. The tools you
need already exist and are tested. **Do not write new migration logic.** Your
job is to run the existing tools carefully, in batches, verifying as you go.

## Goal

Every Claude Code CLI session in `~/.claude/projects/` should appear in the
Claude Desktop profile at:

```
~/Library/Application Support/Claude Profiles/personal
```

At the time this prompt was written there were 35 sessions, of which 3 were
already imported, leaving about 32 to do. Confirm the real numbers yourself
before starting; do not trust these.

## Before you touch anything

1. **Read the README** in the repo root. It explains how session storage is
   split between a shared transcript store and a per-profile index. You need
   that model to interpret what you are doing.

2. **Quit the destination profile.** The tools refuse to write into a profile
   with a live Claude instance, and that guard is correct. Ask the user to
   quit it rather than killing the process yourself, since they may have work
   in progress.

3. **Take a backup and record where it went:**

   ```bash
   scripts/backup-profile.sh --all
   ```

4. **Re-verify the format assumptions.** The index schema is undocumented and
   changes between app versions. Run both and confirm the findings in each
   script's header still hold:

   ```bash
   scripts/debug/permission-audit.sh
   scripts/debug/trace-subagents.sh
   ```

   If `chromePermissionMode` no longer admits `always_ask`, or if the
   subagent directory layout has changed, **stop and report** rather than
   proceeding. The importer's conservative fallbacks depend on both.

## The work

Survey first:

```bash
scripts/import-cli-session.py --to "$PROFILE" --list --limit 100
```

Rows marked `*` are already indexed and will be skipped automatically. Note
the total, and note which sessions carry subagents.

Then import **in batches of 5**, not all at once:

```bash
scripts/import-cli-session.py --to "$PROFILE" --limit 5
```

Each run picks the most recently active sessions that are not yet indexed,
prints a summary table of what it migrated, and writes a `manifest.json` to a
fresh staging directory.

**After every batch:**

- Confirm the run's own validation block reported no failures.
- Record the staging directory path. You will need it to undo.
- Ask the user to open the profile and confirm the new conversations render,
  including at least one that has subagents. Wait for their confirmation
  before the next batch.

Repeat until `--list` shows every session marked `*`.

## If something goes wrong

Each batch is independently reversible:

```bash
scripts/import-cli-session.py --undo <staging>              # whole batch
scripts/import-cli-session.py --undo <staging> --only <id>  # one session
```

`--only` accepts either the `local_*` id or the CLI session id, and is
repeatable. Undo refuses ids that are not in that manifest, only deletes
files the tool created, and verifies the original transcript still matches
its recorded hash.

If a session renders wrong in the app, undo just that one, report what you
saw, and keep going with the rest.

## Rules

- **Do not modify the scripts.** If a tool seems wrong, stop and report it.
  These were verified against real data this session; a change you make in
  isolation is more likely to be the bug.
- **Do not use `--remap` or `--rewrite-content`.** Both are for cross-machine
  moves. Everything here is one machine, so no path rewriting is needed, and
  those flags would republish transcripts unnecessarily.
- **Do not use `--allow-sidechain`.** Subagent transcripts are meant to render
  inline inside their parent. Importing one as a standalone session would
  surface an agent's internal working transcript as a conversation.
- **Never delete a staging directory** until the user confirms that batch.
  The manifest inside it is the only way to undo.
- **Do not commit.** The repo is git-initialized but has no commits yet, and
  that is the user's call.

## Report at the end

- How many sessions were imported, and how many were already present.
- The staging directory for each batch, in order.
- Any session that failed, was skipped, or rendered incorrectly, with the
  reason.
- Whether the format assumptions in the debug scripts still held.
