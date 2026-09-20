"""isolated Claude Desktop profiles, and Claude Code history moved between them."""

from __future__ import annotations

__version__ = "0.0.3"


class Abort(Exception):
    """a condition the user has to fix. the CLI prints it and exits 1."""
