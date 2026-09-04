# claude-profiles (Linux)

Isolated Claude Desktop profiles for Ubuntu / Pop!_OS / any GNOME- or
COSMIC-based desktop — the same `--user-data-dir` mechanism as the macOS and
Raycast versions, adapted to how Linux app grids and docks actually work.

## Prerequisite

This targets Anthropic's **official** Claude Desktop for Linux (beta,
released June 30, 2026), not a browser wrapper. Install it first if you
haven't:

```bash
sudo curl -fsSLo /usr/share/keyrings/claude-desktop-archive-keyring.asc \
  https://downloads.claude.ai/claude-desktop/key.asc
echo "deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/claude-desktop-archive-keyring.asc] https://downloads.claude.ai/claude-desktop/apt/stable stable main" \
  | sudo tee /etc/apt/sources.list.d/claude-desktop.list
sudo apt update && sudo apt install claude-desktop
```

Officially supported: Ubuntu 22.04+ / Debian 12+. Pop!_OS is Ubuntu-based and
not on Anthropic's tested list, but nothing here is Ubuntu-specific — it only
needs the `claude-desktop` binary and a standard XDG desktop (see the COSMIC
note below). Full docs: https://code.claude.com/docs/en/desktop-linux

## Install this tool

```bash
mkdir -p ~/.local/bin
cp claude-profiles ~/.local/bin/
chmod +x ~/.local/bin/claude-profiles
# make sure ~/.local/bin is on PATH (it already is on most distros)
```

Optional: install ImageMagick (`sudo apt install imagemagick`) so each
profile gets a distinct colored badge icon. Without it, profiles just reuse
Claude's normal icon.

## Usage

```bash
claude-profiles add "Work"       # creates a profile + app-grid launcher, offers to open it
claude-profiles add "Personal"
claude-profiles list             # slug / name / data dir
claude-profiles open work        # launch a profile directly by slug
claude-profiles status           # sanity-check that data dirs & launchers still exist
claude-profiles remove work            # remove the launcher, keep the login/chats on disk
claude-profiles remove work --purge    # remove the launcher AND delete its data
```

After `add`, open your app grid (GNOME Activities / COSMIC Launcher) and
you'll see **"Claude — Work"** as its own entry with its own icon. Drag it to
the Dock to pin it, exactly like any other app.

## How it works

Same mechanism as the macOS version: Claude Desktop for Linux is the same
Electron app, and Electron's `--user-data-dir` flag works identically
cross-platform. Each profile gets:

- a data directory at `~/.config/claude-profiles-data/<slug>/`
- a standard XDG launcher at `~/.local/share/applications/claude-profile-<slug>.desktop`,
  whose `Exec` line is just `claude-desktop --user-data-dir=<that folder>`

That `.desktop` file is the direct Linux equivalent of the separate `.app`
bundle the macOS tool creates — it's what makes the profile show up as its
own icon in the app grid and be pinnable to the Dock, with no daemon, no
background process, and nothing running until you launch it.

## The one real platform difference — read this before you rely on it

Pinned icons are always distinct per profile — that part works exactly like
macOS. What's different is what happens to the **running window** once a
profile is open.

Electron derives a window's WM_CLASS (the identity GNOME/KDE use to group
taskbar and Dock entries) from the app's own internal name, and it **ignores**
Chromium's `--class` override switch — this is a confirmed, documented
Electron behavior, not specific to Claude, encountered by other Claude Desktop
Linux packagers debugging this exact question. So every profile's window
reports the same WM_CLASS ("Claude") no matter which `--user-data-dir` it was
launched with, and GNOME Shell / Ubuntu Dock may group multiple running
profiles under a single running-app indicator rather than showing one per
profile — even though the pinned launcher icons above it stay distinct.

This is the same trade-off the macOS version has (its own README: *"the Dock
tile of the running window shows Claude's standard icon... this is
structural"*) — not a regression, just the same underlying constraint showing
up on a different desktop shell. Alt-Tab and your actual windows are
unaffected; it's purely a Dock/taskbar grouping cosmetic.

**On Pop!_OS/COSMIC specifically:** COSMIC still reads standard XDG
`.desktop` files for its Application Library, so pinning should work the same
way. I don't have a confirmed source on whether COSMIC's native Wayland
compositor exhibits the identical running-window grouping quirk — Claude
Desktop for Linux and COSMIC's stable release are both only a few months old
as of this writing, and I couldn't find anyone who's specifically tested this
combination. Worth trying and seeing, rather than assuming either way.

## What this does not do

Same caveat as every version of this idea: each profile is a fully separate
Claude account. No shared memory, chat history, or context between them —
this only makes switching faster.
