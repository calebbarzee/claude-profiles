#!/usr/bin/env sh
# Linux-only check: a created profile writes a launcher, a badge and a registry
# entry under the XDG directories, and the CLI finds it again by id.
set -eu

export HOME=/tmp/smoke
rm -rf "$HOME"
mkdir -p "$HOME"

claude-profiles profile add "Work Laptop" --no-open
claude-profiles profile list

test -f "$HOME/.config/claude-profiles/profiles.json"
test -d "$HOME/.config/claude-profiles/work-laptop"
test -f "$HOME/.local/share/applications/claude-profile-work-laptop.desktop"
test -f "$HOME/.config/claude-profiles/icons/work-laptop.svg"
grep -q 'Exec=claude-desktop' "$HOME/.local/share/applications/claude-profile-work-laptop.desktop"

claude-profiles profile rm work-laptop --purge
test ! -e "$HOME/.local/share/applications/claude-profile-work-laptop.desktop"
test ! -e "$HOME/.config/claude-profiles/work-laptop"

echo "linux smoke ok"
