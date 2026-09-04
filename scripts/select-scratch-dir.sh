#!/usr/bin/env bash
#
# select-scratch-dir.sh — choose a real folder for throwaway Claude chats,
# instead of the app-internal scratch workspace.
#
# WHY THIS EXISTS
#
# A desktop chat started without attaching a folder runs in a scratch
# workspace. The app computes that root itself:
#
#   join(app.getPath("userData"), "scratch-workspaces")
#   then <account>/<org>/scratch-YYYY-MM-DD-xxxxxx
#
# read out of the app bundle. No setting feeds it, so it cannot be redirected
# or turned off. The directory is app-internal, lives inside the profile, and
# is emptied when the session ends, so a migrated session that names one
# points at a folder that no longer exists.
#
# The only way to never land there is to attach a folder every time. This
# script creates one to attach.
#
# It also reports the OS temp root, which is a separate mechanism: the app
# calls mkdtemp(os.tmpdir(), "claude-scratch-") for short-lived file access.
# os.tmpdir() honours TMPDIR.
#
set -euo pipefail

DEFAULT_DIR="$HOME/4_temp/claude/scratch"
TARGET="${1:-}"

if [ -z "$TARGET" ]; then
  printf 'Scratch folder [%s]: ' "$DEFAULT_DIR"
  read -r TARGET </dev/tty || TARGET=""
  TARGET="${TARGET:-$DEFAULT_DIR}"
fi
TARGET="${TARGET/#\~/$HOME}"

mkdir -p "$TARGET"
echo "scratch folder: $TARGET"

if [ ! -f "$TARGET/README.md" ]; then
  cat > "$TARGET/README.md" <<'EOF'
# Claude scratch

Attach this folder when starting a Claude Desktop chat that has no project of
its own. Without an attached folder the app creates a scratch workspace inside
its own profile directory, which is emptied when the session ends and leaves
migrated sessions pointing at a path that no longer exists.
EOF
  echo "  wrote README.md"
fi

echo
echo "app scratch workspaces on disk:"
found=0
for base in "$HOME/Library/Application Support/Claude" \
            "$HOME/Library/Application Support/Claude Profiles"/*; do
  d="$base/scratch-workspaces"
  [ -d "$d" ] || continue
  found=1
  n=$(find "$d" -type f 2>/dev/null | wc -l | tr -d ' ')
  echo "  $n files  $d"
  if [ "$n" -gt 0 ]; then
    echo "     move them with:"
    echo "     rsync -a \"$d/\" \"$TARGET/\" && find \"$d\" -type f -delete"
  fi
done
[ "$found" -eq 1 ] || echo "  none"

echo
echo "OS temp root (used for claude-scratch-* short-lived files):"
echo "  TMPDIR = ${TMPDIR:-<unset, so /tmp>}"
case "${TMPDIR:-/tmp}" in
  /tmp|/tmp/) echo "  to move it off /tmp: export TMPDIR=\"\$HOME/.tmp\" (and mkdir -p it)" ;;
  *)          echo "  already off /tmp, nothing to change" ;;
esac

echo
echo "next:"
echo "  attach $TARGET when starting a folderless desktop chat"
echo "  migrate existing scratch sessions onto it with:"
echo "    scripts/import-cli-session.py --to \"\$PROFILE\" \\"
echo "      --scratch-sessions relocate --scratch-dir \"$TARGET\""
