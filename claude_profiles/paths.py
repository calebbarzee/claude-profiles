"""filesystem locations, and the profile registry that lives in one of them.

every location is a function, not a module constant, so tests can redirect them by setting HOME.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from . import Abort

REGISTRY_NAME = "profiles.json"
REGISTRY_VERSION = 1


def is_macos() -> bool:
    return sys.platform == "darwin"


def default_profile() -> Path:
    """where Claude Desktop keeps state when launched normally."""
    if is_macos():
        return Path.home() / "Library" / "Application Support" / "Claude"
    return _xdg("XDG_CONFIG_HOME", ".config") / "Claude"


def profiles_root() -> Path:
    """parent directory of every profile this tool creates."""
    if is_macos():
        return Path.home() / "Library" / "Application Support" / "Claude Profiles"
    return _xdg("XDG_CONFIG_HOME", ".config") / "claude-profiles"


def state_dir() -> Path:
    """undo manifests and staged copies. not user-facing, safe to delete."""
    return _xdg("XDG_STATE_HOME", ".local/state") / "claude-profiles"


def cli_projects() -> Path:
    """shared transcript store, written by the Claude Code CLI."""
    return Path.home() / ".claude" / "projects"


def cli_settings() -> Path:
    return Path.home() / ".claude" / "settings.json"


def desktop_entries_dir() -> Path:
    """Linux app-grid launchers."""
    return _xdg("XDG_DATA_HOME", ".local/share") / "applications"


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var)
    if value:
        return Path(value)
    return Path.home().joinpath(*fallback.split("/"))


def profile_roots() -> list[Path]:
    """the default profile plus every registered one, whichever exist."""
    roots = [default_profile()] if default_profile().is_dir() else []
    root = profiles_root()
    if root.is_dir():
        roots.extend(sorted(p for p in root.iterdir() if p.is_dir()))
    return roots


def profile_is_running(profile: Path) -> bool:
    """true when a Claude instance holds this profile's data directory open."""
    if not shutil.which("pgrep"):
        return False
    done = subprocess.run(
        ["pgrep", "-f", f"--user-data-dir={profile}"],
        capture_output=True,
        check=False,
    )
    return done.returncode == 0


@dataclass(frozen=True)
class Profile:
    # camelCase because the Raycast extension reads and writes the same file.
    id: str
    name: str
    dataDir: str
    createdAt: int

    @property
    def path(self) -> Path:
        return Path(self.dataDir)


def registry_path() -> Path:
    return profiles_root() / REGISTRY_NAME


def load_registry() -> list[Profile]:
    """a missing registry is empty; one that cannot be trusted aborts before anything writes."""
    path = registry_path()
    intact = "profile folders are intact; fix or remove the file"
    try:
        text = path.read_text()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise Abort(f"cannot read {path}: {exc}. {intact}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise Abort(f"{path} is not valid JSON ({exc}). {intact}") from exc
    rows = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise Abort(f'{path} has no "profiles" list. {intact}')
    out = []
    for n, row in enumerate(rows, 1):
        try:
            out.append(
                Profile(
                    id=str(row["id"]),
                    name=str(row["name"]),
                    dataDir=str(row["dataDir"]),
                    createdAt=int(row.get("createdAt", 0)),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise Abort(f"{path} row {n} lacks an id, name or dataDir. {intact}") from exc
    return out


def save_registry(profiles: list[Profile]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": REGISTRY_VERSION, "profiles": [asdict(p) for p in profiles]}
    path.write_text(json.dumps(payload, indent=2) + "\n")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "profile"


def unique_slug(name: str, existing: list[Profile]) -> str:
    base = slug = slugify(name)
    taken = {p.id for p in existing}
    n = 2
    while slug in taken:
        slug = f"{base}-{n}"
        n += 1
    return slug


def resolve_profile(name: str) -> Path:
    """accept a registered profile id, a profile name, or a path."""
    for profile in load_registry():
        if name in (profile.id, profile.name):
            return profile.path
    return Path(name).expanduser().resolve()
