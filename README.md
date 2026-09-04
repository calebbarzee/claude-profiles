# Claude Profiles

Run several isolated Claude Desktop accounts side by side, and move Claude
Code session history between them.

Two parts:

- A **Raycast extension** that creates and launches isolated profiles.
- A set of **scripts** that move session history into a profile, since a new
  profile starts with an empty Claude Code session list.

Requires macOS for the Raycast extension. The scripts run on macOS and Linux;
a standalone Linux launcher lives in `claude-profiles-linux/`.

## Quick start

### Set up a second profile

```bash
npm install
npm run dev        # registers the extension in Raycast
```

Then in Raycast run **Claude Create Profile**, name it, and sign in when the
new Claude window opens. Signing in matters: it creates the account directory
that every migration writes into.

To reopen a profile later, run **Claude Swap Profile** and pick it.

### Back up before touching anything

```bash
scripts/backup-profile.sh --all
```

Snapshots session state for every profile, excluding runtime, caches, and
credential stores. Output is around 200 KB, not the 600 MB a profile weighs
on disk.

### Move desktop sessions between profiles

Copies the Claude Code session list from one profile to another.

```bash
scripts/migrate-sessions.sh --to "$HOME/Library/Application Support/Claude Profiles/personal" --dry-run
scripts/migrate-sessions.sh --to "$HOME/Library/Application Support/Claude Profiles/personal"
```

### Import CLI sessions into a profile

Surfaces sessions you ran in the `claude` CLI inside the desktop app.

```bash
scripts/import-cli-session.py --to "$PROFILE" --list          # browse, * marks imported
scripts/import-cli-session.py --to "$PROFILE" --session <id>  # one session
scripts/import-cli-session.py --to "$PROFILE" --limit 5       # five most recent
scripts/import-cli-session.py --undo <staging>                # reverse a run
scripts/import-cli-session.py --undo <staging> --only <id>    # reverse one
```

Quit the destination profile first. Every run refuses to write into a profile
with a live Claude instance.

## How session storage works

Claude Code state is split across two locations, and only one of them is
isolated by `--user-data-dir`:

| What | Where | Per profile? |
| --- | --- | --- |
| Session transcripts | `~/.claude/projects/<slug>/<id>.jsonl` | No, shared |
| Desktop session index | `<profile>/claude-code-sessions/<acct>/<org>/` | Yes |

A new profile therefore shows no sessions even though every transcript is
still on disk. Both migration tools work on the index, not the transcripts.

Subagent work nests one level deeper, as
`<slug>/<id>/subagents/agent-<hash>.jsonl`. Those sidechains carry the
parent's session id, are joined to a parent turn by a shared `promptId`, and
the app renders them inline inside the parent. They never get an index entry
of their own, so the importer refuses to treat one as a session.

## Safety model

Both tools take a rollback copy before writing, and neither modifies a source
transcript. `import-cli-session.py` additionally:

- stages every transcript twice, to `original/` and `work/`, and edits only
  the `work/` copy
- records a SHA-256 of each source before and after, and fails if it changed
- writes `manifest.json` describing everything it created, which `--undo`
  reads back to reverse a run, whole or one session at a time
- publishes a rewritten transcript under a **new** id when `--remap` is used,
  leaving the original addressable

Without `--remap`, nothing is written into `~/.claude` at all.

### Permissions on generated sessions

A generated session takes your own application default where one exists, and
the most conservative attested value where none does.

| Field | Value | Source |
| --- | --- | --- |
| `permissionMode` | your setting | `~/.claude/settings.json` → `permissions.defaultMode` |
| `chromePermissionMode` | `always_ask` | conservative; no global setting exists |

Per-session grants (`alwaysAllowedReasons`, `sessionPermissionUpdates`,
`bridgeSessionIds`, and similar) are always reset to empty, so approvals given
to one session never ride along to another. Every other field is copied from
an index entry the app itself wrote, so fields added by future app versions
carry through with the app's own value.

## Scripts

| Script | Purpose |
| --- | --- |
| `backup-profile.sh` | Snapshot session state for one or more profiles |
| `migrate-sessions.sh` | Copy the session index between two profiles |
| `import-cli-session.py` | Generate index entries for CLI sessions |
| `inspect-sessions.sh` | Print the shape of an index entry or a transcript |
| `make-icon.sh` | Rebuild `assets/icon.png` from `swap_icon.svg` |

`scripts/debug/` holds forensic tools that document undocumented formats.
Each records what it established in its header, so re-running after a Claude
Desktop update tells you whether those findings still hold.

| Script | Establishes |
| --- | --- |
| `trace-subagents.sh` | The sidechain layout and the `promptId` join |
| `permission-audit.sh` | Where permission settings live, and their valid enums |
| `sample-brand-color.sh` | The brand hex, sampled from an app icon |

## Icon

`assets/icon.png` is generated by `scripts/make-icon.sh` from
`swap_icon.svg`, tinted `#D97757` — the most common opaque non-white pixel in
Claude Desktop's own app icon. Requires `rsvg-convert`
(`brew install librsvg`, or `apt install librsvg2-bin`). The source artwork is
"swap" by Evan Shuster, from the Noun Project.

## Limitations

Each profile is a fully separate Claude account. There is no shared memory,
chat history, or context between them. This makes switching faster; it does
not merge accounts.

Claude Desktop checks whether its `userData` path is the default location.
Any profile launched by this extension fails that check, which disables local
pairing in that instance. Your default profile, launched from the Dock, is
unaffected.

The index schema is undocumented and changes between app versions. Re-run the
tools in `scripts/debug/` after an update before trusting a large migration.
