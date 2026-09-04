#!/usr/bin/env bash
#
# backup-profile.sh — snapshot the user data in one or more Claude Desktop
# profiles.
#
# Copies only state that cannot be regenerated. The bulk of a profile
# directory is downloadable runtime and browser cache, which is excluded:
#
#   claude-code/, claude-code-vm/            ~591 MB, re-downloaded on launch
#   local-agent-mode-sessions/skills-plugin/ ~4 MB of built-in skills, rebuilt
#   Cache/, Code Cache/, GPUCache/, Dawn*/   Chromium caches
#
# Cookies and other credential stores are also excluded. A backup of this kind
# is meant to be safe to keep around; it should not carry a live session token.
# Sign in again rather than restoring auth from a snapshot.
#
# Run this before migrate-sessions.sh, or on a schedule if you want session
# history you can roll back to.
#
set -euo pipefail

DEFAULT_PROFILE="$HOME/Library/Application Support/Claude"
PROFILES_ROOT="$HOME/Library/Application Support/Claude Profiles"

# State worth keeping, relative to a profile root.
ITEMS=(
  claude-code-sessions
  local-agent-mode-sessions
  git-shadow
  git-worktrees.json
  scratch-workspaces
  config.json
  claude_desktop_config.json
)

OUT=""
ALL=0
PROFILES=()

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage:
  backup-profile.sh [--out <dir>] [--all | <profile-dir>...]

Options:
  --out <dir>   Where to write the snapshot.
                Defaults to ~/claude-profile-backup-<timestamp>
  --all         Back up the default profile plus every profile under
                ~/Library/Application Support/Claude Profiles/
  -h, --help    This message.

With no profile arguments and no --all, backs up the default profile at
~/Library/Application Support/Claude

Excludes runtime, caches, and credential stores. See the header comment.
EOF
}

# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------

while [ $# -gt 0 ]; do
  case "$1" in
    --out)     OUT="${2:-}"; shift 2 ;;
    --all)     ALL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*)        printf 'unknown option: %s\n\n' "$1" >&2; usage >&2; exit 1 ;;
    *)         PROFILES+=("$1"); shift ;;
  esac
done

if [ "$ALL" -eq 1 ]; then
  [ "${#PROFILES[@]}" -eq 0 ] || die "--all takes no profile arguments"
  [ -d "$DEFAULT_PROFILE" ] && PROFILES+=("$DEFAULT_PROFILE")
  if [ -d "$PROFILES_ROOT" ]; then
    while IFS= read -r d; do
      [ -n "$d" ] && PROFILES+=("$d")
    done < <(find "$PROFILES_ROOT" -mindepth 1 -maxdepth 1 -type d | sort)
  fi
elif [ "${#PROFILES[@]}" -eq 0 ]; then
  PROFILES+=("$DEFAULT_PROFILE")
fi

[ "${#PROFILES[@]}" -gt 0 ] || die "no profiles found to back up"

[ -n "$OUT" ] || OUT="$HOME/claude-profile-backup-$(date +%Y%m%d-%H%M%S)"

# ---------------------------------------------------------------------------
# label a profile directory
#
# The default profile and an extension-created profile can share a basename,
# so the default gets a fixed label and the rest use their folder name.
# ---------------------------------------------------------------------------

label_for() {
  local dir="$1"
  if [ "$dir" = "$DEFAULT_PROFILE" ]; then
    printf 'default'
  else
    printf '%s' "$(basename "$dir")"
  fi
}

# ---------------------------------------------------------------------------
# copy
# ---------------------------------------------------------------------------

mkdir -p "$OUT"
MANIFEST="$OUT/MANIFEST.txt"

{
  echo "Claude Desktop profile backup"
  echo "created: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "host:    $(hostname)"
  echo
} > "$MANIFEST"

echo "Backing up to:"
echo "  $OUT"
echo

for dir in "${PROFILES[@]}"; do
  [ -d "$dir" ] || { printf '  skip %s (not a directory)\n' "$dir"; continue; }
  dir="$(cd "$dir" && pwd)"
  label="$(label_for "$dir")"
  dest="$OUT/$label"
  mkdir -p "$dest"

  copied=0
  for item in "${ITEMS[@]}"; do
    [ -e "$dir/$item" ] || continue
    cp -R "$dir/$item" "$dest/$item"
    copied=$((copied + 1))
  done

  # skills-plugin is ~4 MB of built-in skills the app rebuilds on demand.
  rm -rf "$dest/local-agent-mode-sessions/skills-plugin"

  sessions="$(find "$dest" -name 'local_*.json' -type f 2>/dev/null | wc -l | tr -d ' ')"
  size="$(du -sh "$dest" | cut -f1)"

  printf '  %-12s %-6s %s items, %s sessions\n' "$label" "$size" "$copied" "$sessions"

  {
    echo "profile: $label"
    echo "  source:   $dir"
    echo "  size:     $size"
    echo "  sessions: $sessions"
    find "$dest" -name 'local_*.json' -type f | sed "s|$dest|    |"
    echo
  } >> "$MANIFEST"
done

{
  echo "To restore one profile's session index:"
  echo "  cp -R <backup>/<label>/claude-code-sessions <profile-dir>/"
  echo
  echo "Excluded from this backup: claude-code/, claude-code-vm/,"
  echo "local-agent-mode-sessions/skills-plugin/, Chromium caches, and all"
  echo "credential stores. Sign in again rather than restoring auth."
} >> "$MANIFEST"

echo
echo "Manifest: $MANIFEST"
