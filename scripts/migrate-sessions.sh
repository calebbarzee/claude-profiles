#!/usr/bin/env bash
#
# migrate-sessions.sh — copy Claude Code session history between Claude Desktop
# profiles created by this extension.
#
# WHY THIS IS NEEDED
#
# Claude Code state is split across two locations, and only one is isolated by
# --user-data-dir:
#
#   ~/.claude/projects/<slug>/<cliSessionId>.jsonl   shared by every profile
#   <profile>/claude-code-sessions/<acct>/<org>/     per profile
#
# The transcripts are never lost when you make a new profile. What is missing
# is the per-profile index that points at them. This script copies that index.
#
# WHAT IT COPIES
#
#   claude-code-sessions/<acct>/<org>/local_*.json   the session index
#   git-shadow/                                      shadow repos used for diffs
#
# WHAT IT DELIBERATELY SKIPS
#
#   scheduled-tasks.json      target writes its own; a stale copy would point
#                             scheduled work at session ids that do not exist
#   git-worktrees.json        contains absolute paths into the SOURCE profile's
#                             scratch-workspaces, which do not exist in target
#   claude-code/, claude-code-vm/          ~591 MB of runtime, re-downloaded
#   local-agent-mode-sessions/skills-plugin/  ~4 MB of built-in skills, rebuilt
#
# ACCOUNT UUIDS
#
# Sessions live under <accountUuid>/<orgUuid>. Those UUIDs appear only in the
# directory path, never inside the session JSON, so migrating between two
# different accounts is a pure path remap. This script reads both sides and
# remaps automatically.
#
set -euo pipefail

DEFAULT_SOURCE="$HOME/Library/Application Support/Claude"

SOURCE="$DEFAULT_SOURCE"
TARGET=""
DRY_RUN=0
ASSUME_YES=0

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
note() { printf '  %s\n' "$*"; }

usage() {
  cat <<'EOF'
Usage:
  migrate-sessions.sh --to <profile-dir> [--from <profile-dir>] [--dry-run] [--yes]

Options:
  --to <dir>     Destination profile data dir. Required.
                 e.g. ~/Library/Application Support/Claude Profiles/personal
  --from <dir>   Source profile data dir.
                 Defaults to ~/Library/Application Support/Claude
  --dry-run      Print the plan and exit without writing anything.
  --yes          Skip the confirmation prompt.

The destination profile must have been signed in at least once. Signing in is
what creates the <accountUuid>/<orgUuid> directory this script writes into.
EOF
}

# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------

while [ $# -gt 0 ]; do
  case "$1" in
    --to)      TARGET="${2:-}"; shift 2 ;;
    --from)    SOURCE="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --yes)     ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *)         printf 'unknown option: %s\n\n' "$1" >&2; usage >&2; exit 1 ;;
  esac
done

[ -n "$TARGET" ] || { usage >&2; exit 1; }
[ -d "$SOURCE" ] || die "source profile not found: $SOURCE"
[ -d "$TARGET" ] || die "target profile not found: $TARGET"

SOURCE="$(cd "$SOURCE" && pwd)"
TARGET="$(cd "$TARGET" && pwd)"
[ "$SOURCE" != "$TARGET" ] || die "source and target are the same directory"

# ---------------------------------------------------------------------------
# locate the <accountUuid>/<orgUuid> directory on each side
#
# Prints the path relative to the profile root. Errors if a profile holds more
# than one account, since picking one automatically would be a guess.
# ---------------------------------------------------------------------------

session_scope() {
  local root="$1" label="$2" base="$1/claude-code-sessions" found n
  [ -d "$base" ] || die "$label has no claude-code-sessions/ — sign in to it first"

  found="$(find "$base" -mindepth 2 -maxdepth 2 -type d 2>/dev/null || true)"
  [ -n "$found" ] || die "$label has no <account>/<org> directory — sign in to it first"

  n="$(printf '%s\n' "$found" | wc -l | tr -d ' ')"
  [ "$n" -eq 1 ] || die "$label holds $n accounts; migrate manually to avoid guessing"

  printf '%s' "${found#"$root"/}"
}

SRC_SCOPE="$(session_scope "$SOURCE" "source")"
DST_SCOPE="$(session_scope "$TARGET" "target")"

SRC_SESSIONS="$SOURCE/$SRC_SCOPE"
DST_SESSIONS="$TARGET/$DST_SCOPE"

# ---------------------------------------------------------------------------
# safety: never write into a profile that is currently running
# ---------------------------------------------------------------------------

if pgrep -f -- "--user-data-dir=$TARGET" >/dev/null 2>&1; then
  die "a Claude instance is running on the target profile — quit it first"
fi

# ---------------------------------------------------------------------------
# build the plan
# ---------------------------------------------------------------------------

SESSION_FILES=()
while IFS= read -r f; do
  [ -n "$f" ] && SESSION_FILES+=("$f")
done < <(find "$SRC_SESSIONS" -maxdepth 1 -name 'local_*.json' -type f 2>/dev/null | sort)

[ "${#SESSION_FILES[@]}" -gt 0 ] || die "no local_*.json session files in $SRC_SESSIONS"

echo "Claude Code session migration"
echo
echo "  from : $SOURCE"
echo "         scope $SRC_SCOPE"
echo "  to   : $TARGET"
echo "         scope $DST_SCOPE"
[ "$SRC_SCOPE" = "$DST_SCOPE" ] \
  && echo "         (same account, no remap needed)" \
  || echo "         (different account, remapping paths)"
echo
echo "Sessions to copy: ${#SESSION_FILES[@]}"
for f in "${SESSION_FILES[@]}"; do
  title="$(sed -n 's/.*"title":"\([^"]*\)".*/\1/p' "$f" | head -1)"
  cwd="$(sed -n 's/.*"cwd":"\([^"]*\)".*/\1/p' "$f" | head -1)"
  overwrite=""
  [ -f "$DST_SESSIONS/$(basename "$f")" ] && overwrite=" [OVERWRITES EXISTING]"
  note "$(basename "$f")$overwrite"
  note "    title: ${title:-<none>}"
  note "    cwd:   ${cwd:-<none>}"
done

SHADOW_SRC="$SOURCE/git-shadow"
SHADOW_COUNT=0
if [ -d "$SHADOW_SRC" ]; then
  SHADOW_COUNT="$(find "$SHADOW_SRC" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"
fi
echo
echo "Shadow git repos to copy: $SHADOW_COUNT"

if [ "$DRY_RUN" -eq 1 ]; then
  echo
  echo "Dry run — nothing written."
  exit 0
fi

if [ "$ASSUME_YES" -eq 0 ]; then
  echo
  printf 'Proceed? [y/N] '
  read -r reply || reply=""
  case "$reply" in
    [Yy]*) ;;
    *) echo "Aborted."; exit 0 ;;
  esac
fi

# ---------------------------------------------------------------------------
# back up, then copy
# ---------------------------------------------------------------------------

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$TARGET/.migration-backup-$STAMP"
mkdir -p "$BACKUP"

echo
echo "Backing up target state to:"
echo "  $BACKUP"
for d in claude-code-sessions git-shadow; do
  if [ -e "$TARGET/$d" ]; then
    cp -R "$TARGET/$d" "$BACKUP/$d"
    note "saved $d"
  fi
done

echo
echo "Copying:"
mkdir -p "$DST_SESSIONS"
for f in "${SESSION_FILES[@]}"; do
  cp "$f" "$DST_SESSIONS/$(basename "$f")"
  note "$(basename "$f")"
done

if [ "$SHADOW_COUNT" -gt 0 ]; then
  mkdir -p "$TARGET/git-shadow"
  while IFS= read -r d; do
    [ -n "$d" ] || continue
    name="$(basename "$d")"
    if [ -e "$TARGET/git-shadow/$name" ]; then
      note "git-shadow/$name already present, left alone"
    else
      cp -R "$d" "$TARGET/git-shadow/$name"
      note "git-shadow/$name"
    fi
  done < <(find "$SHADOW_SRC" -mindepth 1 -maxdepth 1 -type d)
fi

# ---------------------------------------------------------------------------
# validate
#
# Three checks per session: the file landed, it is parseable JSON, and the
# transcript it points at exists in the shared ~/.claude/projects store.
# ---------------------------------------------------------------------------

echo
echo "Validating:"
FAILED=0

for f in "${SESSION_FILES[@]}"; do
  name="$(basename "$f")"
  dst="$DST_SESSIONS/$name"

  if [ ! -f "$dst" ]; then
    note "FAIL $name — not copied"; FAILED=1; continue
  fi

  if ! python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$dst" 2>/dev/null; then
    note "FAIL $name — target file is not valid JSON"; FAILED=1; continue
  fi

  cli_id="$(sed -n 's/.*"cliSessionId":"\([^"]*\)".*/\1/p' "$dst" | head -1)"
  if [ -z "$cli_id" ]; then
    note "WARN $name — no cliSessionId, cannot check transcript"
    continue
  fi

  if find "$HOME/.claude/projects" -name "$cli_id.jsonl" -type f 2>/dev/null | grep -q .; then
    note "ok   $name — transcript $cli_id found"
  else
    note "WARN $name — transcript $cli_id missing from ~/.claude/projects"
  fi
done

echo
if [ "$FAILED" -eq 0 ]; then
  echo "Migration complete. Open the target profile and confirm the sessions"
  echo "appear in Claude Code's session list."
else
  echo "Migration finished with failures. Restore with:"
  echo "  rm -rf \"$DST_SESSIONS\" && cp -R \"$BACKUP/claude-code-sessions\" \"$TARGET/\""
  exit 1
fi

echo
echo "Backup kept at $BACKUP — delete it once you have confirmed the result."
