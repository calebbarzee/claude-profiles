#!/usr/bin/env python3
"""
optimize-profile.py — clean up index entries a profile already has.

This is NOT the migration tool. import-cli-session.py creates entries; this
one reviews entries that already exist and offers to remove state that is
stale, oversized, or points somewhere that no longer exists. It never reads a
transcript, never creates an entry, and never deletes a conversation.

WHAT IT OFFERS

  stale error state   error, errorAt and priorErrorMark record something that
                      happened to whichever entry was used as an import
                      template, most often a rate limit. The app shows an
                      error badge for them, on sessions that never hit one.

  connector snapshot  remoteMcpServersConfig is a per-session cache of the
                      remote MCP servers available when the entry was written.
                      It does not follow a later disconnect, and one Figma
                      snapshot is around 90 KB per entry. The live connector
                      list is account-side, so this is a cache, not the
                      source of truth.

  scratch workspace   an entry whose cwd is an app-internal scratch workspace
                      names a folder that is emptied when the session ends.

SAFETY

Mirrors import-cli-session.py. Every entry it changes is copied to
<staging>/original/ first, a manifest.json records each change, and
--undo <staging> restores them. It refuses to write into a profile with a
live Claude instance.

By default it only touches entries that import-cli-session.py generated, read
from the migration manifests. --include-app-written widens that to entries the
app wrote itself, whose error state was a real event.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

MANIFEST_NAME = "manifest.json"
STALE_RUN_STATE = ("error", "errorAt", "priorErrorMark")
CONNECTOR_FIELD = "remoteMcpServersConfig"
STRUCTURAL_KEYS = ("cwd", "originCwd")
SCRATCH_WORKSPACE = re.compile(
    r"[/\\]scratch-workspaces[/\\][^/\\]+[/\\][^/\\]+[/\\]scratch-\d{4}-\d{2}-\d{2}-[0-9a-f]{6}(?:[/\\]|$)"
)
DEFAULT_SCRATCH_DIR = Path.home() / "4_temp" / "claude" / "scratch"


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def profile_is_running(profile: Path) -> bool:
    try:
        subprocess.run(
            ["pgrep", "-f", f"--user-data-dir={profile}"], check=True, capture_output=True
        )
        return True
    except Exception:
        return False


def session_scope(profile: Path) -> Path:
    base = profile / "claude-code-sessions"
    if not base.is_dir():
        die(f"{profile} has no claude-code-sessions directory")
    for acct in sorted(base.iterdir()):
        if not acct.is_dir():
            continue
        for org in sorted(acct.iterdir()):
            if org.is_dir():
                return org
    die(f"{profile} has no <account>/<org> directory — sign in to that profile first")
    raise SystemExit(1)


def generated_entries(profile: Path) -> set[str]:
    """Entry filenames that import-cli-session.py created, per its manifests."""
    out: set[str] = set()
    for pattern in ("claude-cli-migration-*", "claude-cli-work-batch*", "claude-cli-*"):
        for m in Path.home().glob(f"{pattern}/{MANIFEST_NAME}"):
            try:
                data = json.loads(m.read_text())
            except Exception:
                continue
            if Path(data.get("profile", "")) != profile:
                continue
            for rec in data.get("imports", []):
                out.add(Path(rec["indexEntry"]).name)
    return out


def retarget_cwd(value, cwd: str):
    if isinstance(value, dict):
        return {
            k: (cwd if k in STRUCTURAL_KEYS and isinstance(v, str) else retarget_cwd(v, cwd))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [retarget_cwd(v, cwd) for v in value]
    return value


def survey(scope: Path, generated: set[str], include_app: bool) -> list[dict]:
    rows = []
    for f in sorted(scope.glob("local_*.json")):
        try:
            entry = json.loads(f.read_text())
        except Exception:
            continue
        origin = "tool" if f.name in generated else "app"
        if origin == "app" and not include_app:
            continue
        cfg = entry.get(CONNECTOR_FIELD)
        cwd = entry.get("cwd")
        rows.append(
            {
                "path": f,
                "entry": entry,
                "origin": origin,
                "title": entry.get("title") or "(untitled)",
                "stale": [k for k in STALE_RUN_STATE if k in entry],
                "connectorBytes": len(json.dumps(cfg)) if cfg else 0,
                "connectorNames": sorted(
                    {
                        s["name"]
                        for s in cfg
                        if isinstance(s, dict) and isinstance(s.get("name"), str)
                    }
                )
                if isinstance(cfg, list)
                else [],
                "isScratch": bool(isinstance(cwd, str) and SCRATCH_WORKSPACE.search(cwd)),
                "cwd": cwd,
            }
        )
    return rows


def ask(prompt: str, choices: tuple[str, ...], assume: str | None) -> str:
    if assume:
        print(f"{prompt} {assume}  (--yes)")
        return assume
    if not sys.stdin.isatty():
        die("not a terminal; pass --yes to run non-interactively")
    while True:
        got = input(f"{prompt} [{'/'.join(choices)}] ").strip().lower()
        for c in choices:
            if got == c or (got and c.startswith(got)):
                return c
        print(f"  pick one of {', '.join(choices)}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Clean up stale state in a Claude Desktop profile's session index.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--to", type=Path, help="profile data dir to work on")
    ap.add_argument("--staging", type=Path, help="staging dir (default ~/claude-optimize-<ts>)")
    ap.add_argument(
        "--include-app-written",
        action="store_true",
        help="also touch entries the app wrote, not just generated ones",
    )
    ap.add_argument(
        "--scratch-dir",
        type=Path,
        default=DEFAULT_SCRATCH_DIR,
        help=f"folder to repoint scratch-workspace entries at (default {DEFAULT_SCRATCH_DIR})",
    )
    ap.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="ID",
        help="limit to these entry ids or cliSessionIds (repeatable). "
        "Useful for trying one session before committing to all of them.",
    )
    # Each group can be decided up front. Anything left unset is asked about,
    # so the default run stays interactive and nothing is decided for you.
    ap.add_argument(
        "--errors", choices=("clear", "keep"), help="stale error state: decide without asking"
    )
    ap.add_argument(
        "--connectors", choices=("clear", "keep"), help="connector snapshot: decide without asking"
    )
    ap.add_argument(
        "--scratch", choices=("repoint", "keep"), help="scratch workspace cwd: decide without asking"
    )
    ap.add_argument("--yes", action="store_true", help="take the recommended action for each group")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    ap.add_argument("--undo", type=Path, metavar="STAGING", help="restore a previous run")
    args = ap.parse_args()

    if args.undo:
        do_undo(args.undo.expanduser().resolve())
        return

    if not args.to:
        die("--to is required")
    profile = args.to.expanduser().resolve()
    if not profile.is_dir():
        die(f"profile not found: {profile}")
    if profile_is_running(profile):
        die("a Claude instance is running on that profile — quit it first")

    scope = session_scope(profile)
    rows = survey(scope, generated_entries(profile), args.include_app_written)
    if args.only:
        want = set(args.only)
        rows = [
            r
            for r in rows
            if want & {r["path"].stem, r["path"].name, str(r["entry"].get("cliSessionId"))}
        ]
        if not rows:
            die(f"no entries matched: {', '.join(sorted(want))}")

    if not rows:
        print("No entries to review.")
        return

    stale = [r for r in rows if r["stale"]]
    conn = [r for r in rows if r["connectorBytes"]]
    scratch = [r for r in rows if r["isScratch"]]

    print("Claude Desktop profile optimize")
    print()
    print(f"  profile : {profile}")
    print(f"  scope   : {scope.parent.name}/{scope.name}")
    print(f"  entries : {len(rows)} reviewed"
          f" ({sum(1 for r in rows if r['origin'] == 'tool')} generated,"
          f" {sum(1 for r in rows if r['origin'] == 'app')} app-written)")
    print()

    print("  findings:")
    if stale:
        msgs = {str(r["entry"].get("error")) for r in stale}
        print(f"    stale error state    {len(stale)} entries")
        for m in sorted(msgs)[:2]:
            print(f"                         {m[:66]!r}")
    else:
        print("    stale error state    none")

    if conn:
        total = sum(r["connectorBytes"] for r in conn)
        names = sorted({n for r in conn for n in r["connectorNames"]})
        print(f"    connector snapshot   {len(conn)} entries, {total // 1024} KB total")
        print(f"                         connectors: {', '.join(names) or '(unnamed)'}")
    else:
        print("    connector snapshot   none")

    if scratch:
        print(f"    scratch workspace    {len(scratch)} entries")
        for r in scratch[:2]:
            print(f"                         {r['title'][:40]}")
    else:
        print("    scratch workspace    none")
    print()

    plan: list[tuple[dict, list[str]]] = []

    if stale:
        assume = args.errors or ("clear" if args.yes else None)
        pick = ask("  Stale error state?", ("clear", "keep"), assume)
        if pick == "clear":
            for r in stale:
                plan.append((r, ["stale"]))

    if conn:
        print()
        print("  Connector snapshot: 'clear' empties it, 'keep' leaves it alone.")
        print("  The live connector list is account-side, so clearing removes a cache.")
        assume = args.connectors or ("keep" if args.yes else None)
        pick = ask("  Connector snapshot?", ("keep", "clear"), assume)
        if pick == "clear":
            for r in conn:
                plan.append((r, ["connectors"]))

    if scratch:
        print()
        print(f"  Repoint scratch entries at {args.scratch_dir}?")
        assume = args.scratch or ("repoint" if args.yes else None)
        pick = ask("  Scratch workspace cwd?", ("repoint", "keep"), assume)
        if pick == "repoint":
            for r in scratch:
                plan.append((r, ["scratch"]))

    if not plan:
        print()
        print("Nothing selected. No changes made.")
        return

    merged: dict[Path, tuple[dict, set[str]]] = {}
    for row, actions in plan:
        got = merged.setdefault(row["path"], (row, set()))
        got[1].update(actions)

    print()
    print(f"  {len(merged)} entries to change:")
    for row, actions in merged.values():
        print(f"    {row['title'][:46]:48} {', '.join(sorted(actions))}")

    if args.dry_run:
        print("\nDry run — nothing written.")
        return

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    staging = (args.staging or Path.home() / f"claude-optimize-{stamp}").expanduser()
    (staging / "original").mkdir(parents=True, exist_ok=True)

    records = []
    for row, actions in merged.values():
        path = row["path"]
        entry = row["entry"]
        shutil.copy2(path, staging / "original" / path.name)

        removed = []
        if "stale" in actions:
            for k in STALE_RUN_STATE:
                if entry.pop(k, None) is not None:
                    removed.append(k)
        if "connectors" in actions:
            entry[CONNECTOR_FIELD] = []
            removed.append(CONNECTOR_FIELD)
        moved_from = None
        if "scratch" in actions:
            moved_from = entry.get("cwd")
            target = str(args.scratch_dir.expanduser())
            for k, v in list(entry.items()):
                entry[k] = retarget_cwd(v, target) if isinstance(v, (dict, list)) else v
            entry["cwd"] = target
            entry["originCwd"] = target

        path.write_text(json.dumps(entry))
        records.append(
            {
                "indexEntry": str(path),
                "backup": str(staging / "original" / path.name),
                "title": row["title"],
                "actions": sorted(actions),
                "removedFields": removed,
                "movedFrom": moved_from,
            }
        )

    manifest = {
        "createdAt": dt.datetime.now().isoformat(timespec="seconds"),
        "profile": str(profile),
        "scope": str(scope),
        "staging": str(staging),
        "changes": records,
    }
    (staging / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2))

    print()
    print("Validating:")
    ok = True
    for rec in records:
        try:
            json.loads(Path(rec["indexEntry"]).read_text())
        except Exception as exc:
            print(f"  FAIL bad JSON {Path(rec['indexEntry']).name}: {exc}")
            ok = False
        if not Path(rec["backup"]).is_file():
            print(f"  FAIL no backup for {Path(rec['indexEntry']).name}")
            ok = False
    if ok:
        print(f"  ok  {len(records)} entries changed, all valid, all backed up")
    print()
    print(f"Manifest: {staging / MANIFEST_NAME}")
    print("Undo:")
    print(f"  {Path(__file__).name} --undo {staging}")


def do_undo(staging: Path) -> None:
    manifest_path = staging / MANIFEST_NAME
    if not manifest_path.is_file():
        die(f"no {MANIFEST_NAME} in {staging}")
    manifest = json.loads(manifest_path.read_text())
    records = manifest.get("changes", [])
    if not records:
        die(f"{MANIFEST_NAME} lists no changes")

    print(f"Restoring {len(records)} entries from {staging}")
    print()
    for rec in records:
        target, backup = Path(rec["indexEntry"]), Path(rec["backup"])
        if not backup.is_file():
            print(f"  WARN backup missing: {target.name}")
            continue
        shutil.copy2(backup, target)
        print(f"  restored  {rec['title'][:46]}")
    print()
    print(f"{len(records)} restored.")


if __name__ == "__main__":
    main()
