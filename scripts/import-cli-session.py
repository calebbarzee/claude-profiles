#!/usr/bin/env python3
"""
import-cli-session.py — surface Claude Code CLI sessions in Claude Desktop.

THE PROBLEM

A CLI session is a transcript at ~/.claude/projects/<slug>/<uuid>.jsonl.
Claude Desktop lists sessions from a per-profile INDEX at
<profile>/claude-code-sessions/<account>/<org>/local_<uuid>.json.

The transcript store is shared by every profile; the index is not. So a CLI
session exists on disk but stays invisible in the app until an index entry
points at it. This generates those entries.

SAFETY MODEL

The original transcript is never opened for writing. Every run:

  1. copies each transcript to <staging>/original/  (pristine reference)
  2. copies it again to <staging>/work/             (the only file edited)
  3. records a SHA-256 before and after the whole run and fails loudly if
     the two ever differ
  4. writes <staging>/manifest.json describing everything it created, which
     is what --undo reads back

Without --remap nothing is written into ~/.claude at all: the index points at
the transcript where it already lives. With --remap the rewritten copy is
published under a NEW session id, so the original stays untouched.

SUBAGENTS

The store nests subagent work as <slug>/<sessionId>/subagents/agent-*.jsonl.
Those sidechains carry the PARENT's sessionId on every line and are joined to
a parent turn by promptId. The app renders them inline inside the parent
session and never gives them their own index entry, so this script refuses to
import one as a session. When --remap republishes a parent, the whole
subagents directory is republished alongside it, or those inline blocks would
render empty.

FIELD POLICY

The index schema is undocumented and gains fields between app versions, so
fields resolve by rule rather than by an exhaustive list:

  DERIVED     read from the transcript (identity, timestamps, title, model)
  PERMISSION  resolved from the user's own application defaults, falling back
              to the most conservative attested value
  NEUTRAL     forced empty/false — per-session grants and stale run state
  APP_TRUTH   everything else, copied from a template entry the app wrote

APP_TRUTH is the default for any unrecognised field, so a field added by a
future app version is carried through with the app's own value rather than
being dropped or guessed at.

WORKING DIRECTORY

A session's folder appears in the entry more than once. Alongside top-level
cwd and originCwd, promptAppendSnapshot embeds its own cwd, and that field is
APP_TRUTH, so copying it verbatim gave every generated session the TEMPLATE
session's folder. Any key named cwd or originCwd is therefore retargeted at
any depth, and the run verifies afterwards that no working-directory value in
the written entry disagrees with the session's own.

The folder itself is the launch directory, the first cwd the transcript
records. Later values are subdirectories the run stepped into, so the most
common value picks a subdirectory whenever the run worked mostly below the
root. Entries the app writes always set cwd == originCwd.

DUPLICATES

An index entry claims a CLI session through cliSessionId, and also through
priorCliSessionIds, which is how the app records a session resumed or
compacted into a new CLI id. Both count as already present. Nothing already
present is re-imported or overwritten by default; those sessions are listed
at the end of a run with the --overwrite command for each.

--overwrite replaces the existing entry when that entry is the same session.
When a later session merely absorbed this one as a prior id, it adds a second
entry instead and leaves the newer conversation alone, since removing it
would delete a session that is not the one being imported.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
from collections import Counter
from pathlib import Path

CLI_HOME = Path.home() / ".claude"
CLI_PROJECTS = CLI_HOME / "projects"
CLI_SETTINGS = CLI_HOME / "settings.json"
SUBAGENT_DIR = "subagents"
MANIFEST_NAME = "manifest.json"

# --------------------------------------------------------------------------
# field policy
# --------------------------------------------------------------------------

DERIVED = {
    "sessionId",
    "cliSessionId",
    "cwd",
    "originCwd",
    "createdAt",
    "lastActivityAt",
    "lastFocusedAt",
    "title",
    "model",
    "effort",
    "completedTurns",
}

# Per-session grants and run state. Inheriting these from a template would
# hand a newly created session approvals that no human granted it.
NEUTRAL: dict[str, object] = {
    "alwaysAllowedReasons": [],
    "sessionPermissionUpdates": [],
    "priorCliSessionIds": [],
    "bridgeSessionIds": [],
    "scratchPromptRecents": [],
    "spawnSeed": {},
    "isArchived": False,
    "lastSpawnRootDetected": False,
}

# Most conservative values attested in the app bundle. Recovered by grepping
# app.asar rather than invented, so they are guaranteed to be valid enum
# members: chromePermissionMode admits skip_all_permission_checks and
# always_ask; permissionMode is assigned ask, default and acceptEdits.
CONSERVATIVE = {
    "permissionMode": "ask",
    "chromePermissionMode": "always_ask",
}

PERMISSION_FIELDS = tuple(CONSERVATIVE)


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def slug_for(path: str) -> str:
    """Encode a path the way the CLI names project folders.

    Separators, underscores and dots all collapse to a hyphen, so the encoding
    is lossy and cannot be reversed. That is why a real cwd is always read from
    inside a transcript and never parsed back out of a folder name.
    """
    return re.sub(r"[/_.]", "-", path)


def to_millis(stamp: str) -> int | None:
    try:
        return int(
            dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000
        )
    except Exception:
        return None


def top_level_transcripts() -> list[Path]:
    """Every real session transcript, excluding subagent sidechains."""
    return sorted(p for p in CLI_PROJECTS.glob("*/*.jsonl") if p.is_file())


def subagent_dir_for(transcript: Path) -> Path:
    return transcript.parent / transcript.stem / SUBAGENT_DIR


# --------------------------------------------------------------------------
# permission resolution
# --------------------------------------------------------------------------


def resolve_permissions() -> tuple[dict, dict]:
    """Base on the user's own application defaults, fall back to conservative.

    permissionMode has a real global home in ~/.claude/settings.json.
    chromePermissionMode has no global setting anywhere on disk, so it always
    takes the conservative value rather than inheriting whatever the most
    recent session happened to be left on.
    """
    values: dict[str, object] = {}
    why: dict[str, str] = {}

    default_mode = None
    try:
        settings = json.loads(CLI_SETTINGS.read_text())
        default_mode = (settings.get("permissions") or {}).get("defaultMode")
    except Exception:
        pass

    if isinstance(default_mode, str) and default_mode:
        values["permissionMode"] = default_mode
        why["permissionMode"] = f"user default ({CLI_SETTINGS.name}: permissions.defaultMode)"
    else:
        values["permissionMode"] = CONSERVATIVE["permissionMode"]
        why["permissionMode"] = "conservative fallback (no user default found)"

    values["chromePermissionMode"] = CONSERVATIVE["chromePermissionMode"]
    why["chromePermissionMode"] = "conservative fallback (no global setting exists)"

    return values, why


# --------------------------------------------------------------------------
# reading transcripts
# --------------------------------------------------------------------------


def read_lines(path: Path) -> list[dict]:
    out = []
    with path.open(errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


def summarize(transcript: Path) -> dict | None:
    """Cheap metadata for listing, without JSON-parsing every message body.

    A 16 MB transcript is mostly assistant content. Listing needs only
    timestamps, a title and a turn count, so raw lines are substring-filtered
    and only the few that can matter are parsed.
    """
    try:
        raw = [ln for ln in transcript.read_text(errors="replace").splitlines() if ln.strip()]
    except Exception:
        return None
    if not raw:
        return None

    def parse(line: str) -> dict:
        try:
            obj = json.loads(line)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    stamps = []
    for line in (raw[0], raw[-1]):
        stamp = parse(line).get("timestamp")
        if isinstance(stamp, str):
            got = to_millis(stamp)
            if got:
                stamps.append(got)

    title = None
    for marker, key in (('"custom-title"', "customTitle"), ('"ai-title"', "aiTitle")):
        for line in reversed(raw):
            if marker in line:
                title = parse(line).get(key)
                if title:
                    break
        if title:
            break

    cwd = None
    for line in raw:
        if '"cwd"' in line:
            cwd = parse(line).get("cwd")
            if cwd:
                break

    return {
        "cliSessionId": transcript.stem,
        "path": transcript,
        "title": title or (Path(cwd).name if cwd else transcript.stem[:8]),
        "cwd": cwd,
        "turns": sum(1 for ln in raw if '"type":"user"' in ln),
        "lastActivityAt": max(stamps) if stamps else 0,
        "lines": len(raw),
        "subagents": len(list(subagent_dir_for(transcript).glob("*.jsonl"))),
    }


def derive(lines: list[dict], transcript: Path) -> dict:
    """Pull everything the index needs out of the transcript itself."""
    stamps = sorted(
        s for s in (to_millis(d["timestamp"]) for d in lines if isinstance(d.get("timestamp"), str)) if s
    )
    cwds = Counter(d["cwd"] for d in lines if isinstance(d.get("cwd"), str))
    origin = next((d["cwd"] for d in lines if isinstance(d.get("cwd"), str)), None)

    custom = [d["customTitle"] for d in lines if d.get("type") == "custom-title"]
    ai = [d["aiTitle"] for d in lines if d.get("type") == "ai-title"]
    title = (custom or ai or [None])[-1]

    models = Counter(
        d["message"]["model"]
        for d in lines
        if isinstance(d.get("message"), dict) and isinstance(d["message"].get("model"), str)
    )
    efforts = Counter(d["effort"] for d in lines if isinstance(d.get("effort"), str))
    owners = [
        (d.get("ownerAccountUuid"), d.get("ownerOrganizationUuid"))
        for d in lines
        if d.get("type") == "bridge-session"
    ]

    # The launch directory is the session's folder. Later cwd values are
    # subdirectories the run stepped into: 19 of 36 transcripts on the machine
    # this was written against record more than one cwd, and every extra value
    # was a deeper path under the first. Taking the most common one therefore
    # picks a subdirectory whenever the run spent most of its turns below the
    # root. Every index entry the app itself wrote sets cwd == originCwd, so
    # both take the launch directory and the most common value is only a
    # fallback for a transcript that records no cwd on its first lines.
    launch = origin or (cwds.most_common(1)[0][0] if cwds else str(Path.home()))

    # The filename is authoritative. A sidechain carries its PARENT's
    # sessionId on every line, so trusting the in-file value would let a
    # subagent transcript masquerade as the session that spawned it.
    return {
        "cliSessionId": transcript.stem,
        "_isSidechain": SUBAGENT_DIR in transcript.parts
        and all(d.get("isSidechain") for d in lines if "isSidechain" in d),
        "cwd": launch,
        "originCwd": launch,
        "createdAt": stamps[0] if stamps else 0,
        "lastActivityAt": stamps[-1] if stamps else 0,
        "lastFocusedAt": stamps[-1] if stamps else 0,
        "title": title or Path(origin or "session").name,
        "model": models.most_common(1)[0][0] if models else None,
        "effort": efforts.most_common(1)[0][0] if efforts else None,
        "completedTurns": sum(1 for d in lines if d.get("type") == "user"),
        "_owner": owners[-1] if owners else (None, None),
        "_cwds": dict(cwds),
    }


# --------------------------------------------------------------------------
# remapping
# --------------------------------------------------------------------------

STRUCTURAL_KEYS = ("cwd", "originCwd")


def remap_line(obj: dict, old: str, new: str, content_too: bool) -> tuple[dict, int]:
    """Rewrite paths in one transcript line.

    Structural mode touches only fields that name a working directory. Content
    mode re-serialises the whole line, which also reaches paths quoted inside
    message bodies and tool output.
    """
    if content_too:
        raw = json.dumps(obj)
        hits = raw.count(old)
        return (json.loads(raw.replace(old, new)) if hits else obj), hits

    hits = 0
    for key in STRUCTURAL_KEYS:
        val = obj.get(key)
        if isinstance(val, str) and old in val:
            obj[key] = val.replace(old, new)
            hits += 1
    return obj, hits


def rewrite_file(src: Path, dst: Path, old: str, new: str, content_too: bool, new_id: str) -> int:
    hits = 0
    out = []
    for obj in read_lines(src):
        obj, n = remap_line(obj, old, new, content_too)
        obj["sessionId"] = new_id
        hits += n
        out.append(json.dumps(obj))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(out) + "\n")
    return hits


# --------------------------------------------------------------------------
# target profile
# --------------------------------------------------------------------------


def session_scope(profile: Path) -> Path:
    base = profile / "claude-code-sessions"
    if not base.is_dir():
        die(f"{profile} has no claude-code-sessions/ — sign in to that profile first")
    dirs = [p for p in base.glob("*/*") if p.is_dir()]
    if not dirs:
        die(f"{profile} has no <account>/<org> directory — sign in to that profile first")
    if len(dirs) > 1:
        die(f"{profile} holds {len(dirs)} accounts; migrate manually to avoid guessing")
    return dirs[0]


def load_template(scope: Path, explicit: Path | None) -> tuple[dict, Path]:
    if explicit:
        return json.loads(explicit.read_text()), explicit
    entries = sorted(scope.glob("local_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not entries:
        die(
            f"no existing local_*.json in {scope} to use as a template.\n"
            "  Open one Claude Code session in that profile first, or pass --template."
        )
    return json.loads(entries[0].read_text()), entries[0]


def indexed_cli_map(scope: Path) -> dict[str, dict]:
    """Every CLI session id this profile already accounts for.

    An index entry claims a CLI session two ways:

      cliSessionId        the entry IS that session
      priorCliSessionIds  the entry is a LATER segment that absorbed it, which
                          is how the app records a session that was resumed or
                          compacted into a new CLI id

    Both count as present. Reading only cliSessionId is what let an already
    imported session be migrated a second time as a separate conversation.

    Maps id -> {entry, sessionId, title, via}. A direct claim wins over an
    absorbed one when an id somehow appears as both.
    """
    out: dict[str, dict] = {}
    for f in sorted(scope.glob("local_*.json")):
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue
        claims: list[tuple[str, str]] = []
        direct = data.get("cliSessionId")
        if isinstance(direct, str):
            claims.append((direct, "cliSessionId"))
        priors = data.get("priorCliSessionIds")
        if isinstance(priors, list):
            claims.extend((p, "priorCliSessionIds") for p in priors if isinstance(p, str))
        for cli_id, via in claims:
            if cli_id in out and out[cli_id]["via"] == "cliSessionId":
                continue
            out[cli_id] = {
                "entry": f,
                "sessionId": data.get("sessionId", f.stem),
                "title": data.get("title") or "(untitled)",
                "via": via,
            }
    return out


def duplicate_report(dupes: dict[str, dict], profile: Path) -> None:
    """List CLI sessions the profile already has, and how to force each one."""
    if not dupes:
        return
    print()
    print(f"  {len(dupes)} CLI session{'s' if len(dupes) != 1 else ''} already in this profile — not re-imported:")
    print()
    for cli_id in sorted(dupes, key=lambda i: dupes[i]["title"]):
        d = dupes[cli_id]
        print(f"    {cli_id}  {d['title'][:44]}")
        if d["via"] == "cliSessionId":
            print(f"      is        {d['sessionId']}")
            print("      overwrite replaces that entry:")
        else:
            print(f"      absorbed into {d['sessionId']}  ({d['title'][:34]})")
            print("      overwrite adds a SECOND entry; the newer one is left alone:")
        print(f'        {Path(__file__).name} --to "{profile}" --overwrite {cli_id}')
        print()


def profile_is_running(profile: Path) -> bool:
    try:
        subprocess.run(
            ["pgrep", "-f", f"--user-data-dir={profile}"], check=True, capture_output=True
        )
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# index entry assembly
# --------------------------------------------------------------------------


def retarget_cwd(value, cwd: str, hits: Counter):
    """Point every nested working-directory field at this session's folder.

    APP_TRUTH fields are copied wholesale from a template entry the app wrote,
    and some of them embed that template session's own folder. The observed
    case is promptAppendSnapshot, a dict of
    {append, cliVersion, cwd, settingsKey} whose cwd tracked the entry's own
    cwd in every app-written entry inspected. Copied verbatim it hands every
    generated session the template's folder, which is what made migrated
    sessions open against the wrong directory.

    Only keys named in STRUCTURAL_KEYS are touched, at any depth, so a field
    added by a future app version is still carried through untouched unless it
    names a working directory.
    """
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in STRUCTURAL_KEYS and isinstance(v, str):
                out[k] = cwd
                hits.update([k])
            else:
                out[k] = retarget_cwd(v, cwd, hits)
        return out
    if isinstance(value, list):
        return [retarget_cwd(v, cwd, hits) for v in value]
    return value


def walk_cwds(value, path: str = "") -> list[tuple[str, str]]:
    """Every working-directory value in an entry, with where it was found.

    Used to verify after writing that no field still names the template's
    folder instead of this session's.
    """
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            here = f"{path}.{k}" if path else k
            if k in STRUCTURAL_KEYS and isinstance(v, str):
                found.append((here, v))
            else:
                found.extend(walk_cwds(v, here))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            found.extend(walk_cwds(v, f"{path}[{i}]"))
    return found


def build_entry(template: dict, facts: dict, cli_session_id: str, perms: dict) -> tuple[dict, Counter]:
    entry: dict = {}
    tally: Counter = Counter()
    nested: Counter = Counter()

    def clone(value):
        return json.loads(json.dumps(value))

    for key, value in template.items():
        if key in NEUTRAL:
            entry[key], _ = clone(NEUTRAL[key]), tally.update(["NEUTRAL"])
        elif key in perms:
            entry[key], _ = perms[key], tally.update(["PERMISSION"])
        elif key in DERIVED:
            entry[key] = None
            tally.update(["DERIVED"])
        else:
            entry[key] = retarget_cwd(clone(value), facts["cwd"], nested)
            tally.update(["APP_TRUTH"])

    for key, value in NEUTRAL.items():
        if key not in entry:
            entry[key], _ = clone(value), tally.update(["NEUTRAL"])
    for key, value in perms.items():
        if key not in entry:
            entry[key], _ = value, tally.update(["PERMISSION"])

    entry["sessionId"] = f"local_{uuid.uuid4()}"
    entry["cliSessionId"] = cli_session_id
    for key in (
        "cwd",
        "originCwd",
        "createdAt",
        "lastActivityAt",
        "lastFocusedAt",
        "title",
        "completedTurns",
    ):
        entry[key] = facts[key]
    for key in ("model", "effort"):
        entry[key] = facts[key] if facts[key] is not None else template.get(key)
    entry.setdefault("titleSource", template.get("titleSource", "auto"))

    if nested:
        tally.update({"RETARGETED": sum(nested.values())})

    return entry, tally


# --------------------------------------------------------------------------
# import one session
# --------------------------------------------------------------------------


def import_one(
    transcript: Path,
    scope: Path,
    template: dict,
    staging: Path,
    perms: dict,
    remap: tuple[str, str] | None,
    rewrite_content: bool,
    allow_sidechain: bool,
    replaces: dict | None = None,
) -> dict:
    digest = sha256(transcript)
    lines = read_lines(transcript)
    if not lines:
        die(f"transcript has no parseable lines: {transcript}")
    facts = derive(lines, transcript)

    if facts["_isSidechain"] and not allow_sidechain:
        die(
            f"{transcript.name} is a subagent sidechain, not a session.\n"
            "  It carries its parent's session id and the app renders it inline\n"
            "  inside the parent. Pass --allow-sidechain to force."
        )

    pristine = staging / "original" / transcript.name
    work = staging / "work" / transcript.name
    pristine.parent.mkdir(parents=True, exist_ok=True)
    work.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(transcript, pristine)
    shutil.copy2(transcript, work)

    # A session that is still running keeps appending to its own transcript.
    # Staging a moving file would import half a conversation, and failing hard
    # here would abandon the entries already written by earlier sessions in
    # this batch, before manifest.json exists for --undo to read. Skip it and
    # let the caller report it instead.
    if sha256(transcript) != digest:
        return {
            "skipped": "transcript changed while being staged — that session is still running",
            "title": facts["title"],
            "sourceCliSessionId": facts["cliSessionId"],
            "originalTranscript": str(transcript),
        }

    published_id = facts["cliSessionId"]
    published: Path | None = None
    published_agents: Path | None = None
    hits = 0

    if remap:
        old, new = remap
        new_id = str(uuid.uuid4())
        hits = rewrite_file(work, work, old, new, rewrite_content, new_id)

        new_facts = derive(read_lines(work), work)
        target_dir = CLI_PROJECTS / slug_for(new_facts["cwd"])
        published = target_dir / f"{new_id}.jsonl"
        if published.exists():
            die(f"refusing to overwrite {published}")
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(work, published)

        # Republish sidechains alongside the parent, or the inline subagent
        # blocks in the migrated session render empty.
        src_agents = subagent_dir_for(transcript)
        if src_agents.is_dir():
            published_agents = target_dir / new_id / SUBAGENT_DIR
            published_agents.mkdir(parents=True, exist_ok=True)
            for side in sorted(src_agents.glob("*.jsonl")):
                staged_side = staging / "work" / f"{new_id}-{side.name}"
                hits += rewrite_file(side, staged_side, old, new, rewrite_content, new_id)
                shutil.copy2(staged_side, published_agents / side.name)

        published_id = new_id
        facts.update({k: new_facts[k] for k in ("cwd", "originCwd", "_cwds")})

    entry, tally = build_entry(template, facts, published_id, perms)
    dest = scope / f"{entry['sessionId']}.json"
    if dest.exists():
        die(f"refusing to overwrite {dest}")

    # Replacing an existing entry only ever happens under --overwrite, and only
    # for an entry whose own cliSessionId is this session. An entry that merely
    # absorbed this session as a prior id belongs to a later session and is
    # never removed, or that newer conversation would disappear.
    replaced_backup = None
    replaced_path = None
    if replaces and replaces["via"] == "cliSessionId":
        old_entry = Path(replaces["entry"])
        if old_entry.is_file():
            backup_dir = staging / "replaced"
            backup_dir.mkdir(parents=True, exist_ok=True)
            replaced_backup = backup_dir / old_entry.name
            shutil.copy2(old_entry, replaced_backup)
            replaced_path = old_entry
            old_entry.unlink()

    dest.write_text(json.dumps(entry))
    (staging / "work" / dest.name).write_text(json.dumps(entry, indent=2))

    # Same race, caught after the write. Roll this one session back rather than
    # aborting a batch whose manifest does not exist yet.
    if sha256(transcript) != digest:
        dest.unlink(missing_ok=True)
        if replaced_backup and replaced_path:
            shutil.copy2(replaced_backup, replaced_path)
        return {
            "skipped": "transcript changed during import — that session is still running",
            "title": facts["title"],
            "sourceCliSessionId": facts["cliSessionId"],
            "originalTranscript": str(transcript),
        }

    return {
        "indexEntry": str(dest),
        "sessionId": entry["sessionId"],
        "cliSessionId": published_id,
        "sourceCliSessionId": facts["cliSessionId"],
        "title": entry["title"],
        "turns": entry["completedTurns"],
        "lines": len(lines),
        "cwd": entry["cwd"],
        "originalTranscript": str(transcript),
        "originalSha256": digest,
        "publishedTranscript": str(published) if published else None,
        "publishedSubagents": str(published_agents) if published_agents else None,
        "subagentCount": len(list(subagent_dir_for(transcript).glob("*.jsonl"))),
        "remap": f"{remap[0]}={remap[1]}" if remap else None,
        "rewroteContent": bool(remap and rewrite_content),
        "pathRewrites": hits,
        "fieldTally": dict(tally),
        "replacedEntryPath": str(replaced_path) if replaced_path else None,
        "replacedEntryBackup": str(replaced_backup) if replaced_backup else None,
        "duplicateOf": replaces["sessionId"] if replaces else None,
        "duplicateVia": replaces["via"] if replaces else None,
    }


# --------------------------------------------------------------------------
# undo
# --------------------------------------------------------------------------


def do_undo(staging: Path, only: list[str]) -> None:
    manifest_path = staging / MANIFEST_NAME
    if not manifest_path.is_file():
        die(f"no {MANIFEST_NAME} in {staging}")
    manifest = json.loads(manifest_path.read_text())
    records = manifest.get("imports", [])
    if not records:
        die(f"{MANIFEST_NAME} lists no imports")

    if only:
        wanted = set(only)
        selected = [
            r
            for r in records
            if wanted & {r["sessionId"], r["cliSessionId"], r["sourceCliSessionId"]}
        ]
        unknown = wanted - {
            v for r in records for v in (r["sessionId"], r["cliSessionId"], r["sourceCliSessionId"])
        }
        if unknown:
            die(f"not in this manifest: {', '.join(sorted(unknown))}")
    else:
        selected = records

    print(f"Undoing {len(selected)} of {len(records)} imports from {staging}")
    print()

    removed, kept = [], []
    for rec in selected:
        title = rec["title"][:44]
        entry = Path(rec["indexEntry"])
        if entry.exists():
            entry.unlink()
            print(f"  removed index  {title}")
        else:
            print(f"  already gone   {title}")

        # An --overwrite run displaced an entry the app had written. Put it
        # back before anything else, so undoing a forced import leaves the
        # profile exactly as it was.
        backup = rec.get("replacedEntryBackup")
        target = rec.get("replacedEntryPath")
        if backup and target:
            backup_path, target_path = Path(backup), Path(target)
            if not backup_path.is_file():
                print(f"  WARN backup missing, cannot restore {target_path.name}")
            elif target_path.exists():
                print(f"  kept existing  {target_path.name} (already back in place)")
            else:
                shutil.copy2(backup_path, target_path)
                print(f"  restored index {target_path.name}")

        pub = rec.get("publishedTranscript")
        if pub:
            pub_path = Path(pub)
            # Only ever delete a transcript this tool published. The original
            # is identified by path and never touched.
            if pub_path.exists() and str(pub_path) != rec["originalTranscript"]:
                pub_path.unlink()
                print(f"  removed copy   {pub_path.name}")
            agents = rec.get("publishedSubagents")
            if agents and Path(agents).is_dir():
                shutil.rmtree(Path(agents).parent)
                print(f"  removed agents {Path(agents).parent.name}/")

        original = Path(rec["originalTranscript"])
        if original.is_file() and sha256(original) == rec["originalSha256"]:
            print(f"  ok  original intact  {original.name}")
        else:
            print(f"  WARN original changed or missing: {original}")
        removed.append(rec)
        print()

    kept = [r for r in records if r not in selected]
    manifest["imports"] = kept
    manifest.setdefault("undone", []).extend(removed)
    manifest_path.write_text(json.dumps(manifest, indent=2))

    print(f"{len(removed)} undone, {len(kept)} still active in this manifest.")


# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------


def print_summary(records: list[dict]) -> None:
    print()
    print("=" * 74)
    print(f"Migrated {len(records)} conversation{'s' if len(records) != 1 else ''}")
    print("=" * 74)
    print()
    print(f"  {'TITLE':<44} {'TURNS':>5} {'AGENTS':>6}")
    print(f"  {'-' * 44} {'-' * 5} {'-' * 6}")
    for rec in records:
        print(f"  {rec['title'][:44]:<44} {rec['turns']:>5} {rec['subagentCount']:>6}")
    print()
    for rec in records:
        print(f"  {rec['title'][:60]}")
        print(f"      index    {Path(rec['indexEntry']).name}")
        print(f"      session  {rec['cliSessionId']}")
        print(f"      cwd      {rec['cwd']}")
        if rec["remap"]:
            scope = "structural + content" if rec["rewroteContent"] else "structural"
            print(f"      remap    {rec['remap']}  ({scope}, {rec['pathRewrites']} rewrites)")
            print(f"      copy     {rec['publishedTranscript']}")
        print()


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Surface Claude Code CLI sessions in a Claude Desktop profile.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--to", type=Path, help="destination profile data dir")
    ap.add_argument("--session", help="CLI session uuid, or a path to a .jsonl transcript")
    ap.add_argument(
        "--limit",
        type=int,
        help="import the N most recently active sessions not already indexed",
    )
    ap.add_argument("--list", action="store_true", help="list CLI sessions and exit")
    ap.add_argument("--template", type=Path, help="index entry to take APP_TRUTH fields from")
    ap.add_argument("--staging", type=Path, help="staging dir (default ~/claude-cli-migration-<ts>)")
    ap.add_argument("--remap", metavar="OLD=NEW", help="rewrite this path in the staged copy")
    ap.add_argument(
        "--rewrite-content",
        action="store_true",
        help="with --remap, also rewrite paths inside message bodies and tool output",
    )
    ap.add_argument(
        "--allow-sidechain",
        action="store_true",
        help="permit importing a subagent sidechain (normally refused)",
    )
    ap.add_argument(
        "--overwrite",
        action="append",
        default=[],
        metavar="ID",
        help="re-import a CLI session the profile already has (repeatable). "
        "Replaces the existing entry when that entry is the same session; adds "
        "a second entry when a later session merely absorbed it.",
    )
    ap.add_argument("--undo", type=Path, metavar="STAGING", help="reverse a previous run")
    ap.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="ID",
        help="with --undo, reverse just this session (repeatable)",
    )
    ap.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    args = ap.parse_args()

    if args.undo:
        do_undo(args.undo.expanduser().resolve(), args.only)
        return

    if args.list:
        rows = [r for r in (summarize(f) for f in top_level_transcripts()) if r]
        rows.sort(key=lambda r: r["lastActivityAt"], reverse=True)
        indexed: dict[str, dict] = {}
        if args.to:
            try:
                indexed = indexed_cli_map(session_scope(args.to.expanduser().resolve()))
            except SystemExit:
                indexed = {}
        print(f"{'':2}{'SESSION':38} {'TURNS':>5} {'AGENTS':>6}  TITLE")
        for row in rows[: args.limit or 20]:
            got = indexed.get(row["cliSessionId"])
            mark = " " if not got else ("*" if got["via"] == "cliSessionId" else "+")
            print(
                f"{mark:2}{row['cliSessionId']:38} {row['turns']:>5} "
                f"{row['subagents']:>6}  {row['title'][:44]}"
            )
        hidden = len(list(CLI_PROJECTS.glob(f"*/*/{SUBAGENT_DIR}/*.jsonl")))
        print(f"\n{len(rows)} sessions in {CLI_PROJECTS}")
        if hidden:
            print(f"{hidden} subagent sidechains hidden (rendered inline, never importable)")
        if any(v["via"] == "cliSessionId" for v in indexed.values()):
            print("* already indexed in the target profile")
        if any(v["via"] == "priorCliSessionIds" for v in indexed.values()):
            print("+ already in the profile, absorbed into a later session")
        return

    if not args.to:
        die("--to is required")
    if not args.session and not args.limit and not args.overwrite:
        die("pass --session <id>, or --limit N for a batch (or --list to browse)")

    profile = args.to.expanduser().resolve()
    if not profile.is_dir():
        die(f"target profile not found: {profile}")
    if profile_is_running(profile):
        die("a Claude instance is running on the target profile — quit it first")

    scope = session_scope(profile)
    template, template_path = load_template(scope, args.template)
    already = indexed_cli_map(scope)
    forced = set(args.overwrite)
    unknown_forced = forced - set(already)
    perms, perm_why = resolve_permissions()

    remap = None
    if args.remap:
        if "=" not in args.remap:
            die("--remap expects OLD=NEW")
        old, new = args.remap.split("=", 1)
        if not old or not new:
            die("--remap expects both sides to be non-empty, as OLD=NEW")
        remap = (old, new)
    elif args.rewrite_content:
        die("--rewrite-content only means something together with --remap")

    # ---- select ----------------------------------------------------------
    # A session the profile already has is never re-imported on its own. It
    # only enters the target list when named in --overwrite, and duplicates
    # that were skipped are reported afterwards with the command to force each.
    targets: list[Path] = []
    skipped: dict[str, dict] = {}

    if args.session:
        if args.session.endswith(".jsonl"):
            targets = [Path(args.session).expanduser()]
        else:
            matches = [p for p in top_level_transcripts() if p.stem == args.session]
            if not matches:
                die(f"no session {args.session}. Use --list to browse.")
            if args.session in already and args.session not in forced:
                skipped[args.session] = already[args.session]
            else:
                targets = matches[:1]
    elif args.limit:
        rows = [r for r in (summarize(f) for f in top_level_transcripts()) if r]
        rows.sort(key=lambda r: r["lastActivityAt"], reverse=True)
        skipped.update(
            {r["cliSessionId"]: already[r["cliSessionId"]] for r in rows if r["cliSessionId"] in already}
        )
        fresh = [r for r in rows if r["cliSessionId"] not in already]
        targets = [r["path"] for r in fresh[: args.limit]]

    for cli_id in args.overwrite:
        matches = [p for p in top_level_transcripts() if p.stem == cli_id]
        if not matches:
            die(f"no session {cli_id}. Use --list to browse.")
        if matches[0] not in targets:
            targets.append(matches[0])
        skipped.pop(cli_id, None)

    if unknown_forced:
        print("  note: --overwrite named ids the profile does not have; importing normally:")
        for cli_id in sorted(unknown_forced):
            print(f"    {cli_id}")
        print()

    if not targets:
        if skipped:
            print("Nothing to import — every session is already in this profile.")
            duplicate_report(skipped, profile)
        else:
            print("Nothing to import — every session is already indexed.")
        return

    # ---- report ----------------------------------------------------------
    acct, org = scope.parent.name, scope.name
    print("Claude Code CLI session import")
    print()
    print(f"  profile   : {profile}")
    print(f"  account   : {acct}")
    print(f"  org       : {org}")
    print(f"  template  : {template_path.name}")
    print()
    print("  permissions:")
    for key in PERMISSION_FIELDS:
        print(f"    {key:22} {perms[key]!r}")
        print(f"    {'':22} {perm_why[key]}")
    if remap:
        scope_note = "structural + content" if args.rewrite_content else "structural only"
        print()
        print(f"  remap     : {remap[0]} -> {remap[1]}  ({scope_note})")
    print()
    print(f"  {len(targets)} session{'s' if len(targets) != 1 else ''} to import:")
    for t in targets:
        got = summarize(t)
        if got:
            extra = f", {got['subagents']} subagents" if got["subagents"] else ""
            print(f"    {got['title'][:50]}  ({got['turns']} turns{extra})")
        if t.stem in already:
            existing = already[t.stem]
            if existing["via"] == "cliSessionId":
                print(f"      OVERWRITE replaces {existing['sessionId']}")
            else:
                print(f"      OVERWRITE adds a second entry; {existing['sessionId']} kept")

    duplicate_report(skipped, profile)

    if args.dry_run:
        print("\nDry run — nothing written.")
        return

    # ---- run -------------------------------------------------------------
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    staging = (args.staging or Path.home() / f"claude-cli-migration-{stamp}").expanduser()
    staging.mkdir(parents=True, exist_ok=True)

    records, unfinished = [], []
    for t in targets:
        got = import_one(
            t,
            scope,
            template,
            staging,
            perms,
            remap,
            args.rewrite_content,
            args.allow_sidechain,
            already.get(t.stem) if t.stem in forced else None,
        )
        (unfinished if got.get("skipped") else records).append(got)

    if unfinished:
        print()
        print(f"  {len(unfinished)} session{'s' if len(unfinished) != 1 else ''} skipped, nothing written for them:")
        for rec in unfinished:
            print(f"    {rec['title'][:50]}")
            print(f"      {rec['sourceCliSessionId']}")
            print(f"      {rec['skipped']}")
        print()

    if not records:
        (staging / MANIFEST_NAME).write_text(
            json.dumps({"imports": [], "skipped": unfinished}, indent=2)
        )
        print("Nothing was imported.")
        return

    manifest = {
        "createdAt": dt.datetime.now().isoformat(timespec="seconds"),
        "profile": str(profile),
        "scope": str(scope),
        "staging": str(staging),
        "permissions": perms,
        "imports": records,
        "skipped": unfinished,
    }
    (staging / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2))

    print_summary(records)

    print("Validating:")
    ok = True
    for rec in records:
        original = Path(rec["originalTranscript"])
        if sha256(original) != rec["originalSha256"]:
            print(f"  FAIL original modified: {original.name}")
            ok = False
        entry = Path(rec["indexEntry"])
        try:
            json.loads(entry.read_text())
        except Exception as exc:
            print(f"  FAIL bad index JSON {entry.name}: {exc}")
            ok = False
        pointed = Path(rec["publishedTranscript"] or rec["originalTranscript"])
        if not pointed.is_file():
            print(f"  FAIL index points at missing transcript: {pointed}")
            ok = False

        # Every working-directory field, nested ones included, must name this
        # session's folder. A mismatch means a template path was carried
        # through and the session would open against the wrong directory.
        written = json.loads(entry.read_text())
        stray = [
            f"{where}={value}"
            for where, value in walk_cwds(written)
            if value != rec["cwd"]
        ]
        if stray:
            print(f"  FAIL wrong working directory in {entry.name}: {', '.join(stray)}")
            ok = False

        backup = rec.get("replacedEntryBackup")
        if backup and not Path(backup).is_file():
            print(f"  FAIL replaced entry not backed up: {rec['replacedEntryPath']}")
            ok = False
    if ok:
        note = "originals unchanged, index entries valid, transcripts resolvable, cwd consistent"
        print(f"  ok  {len(records)} {note}")
    print()
    print(f"Manifest: {staging / MANIFEST_NAME}")
    print("Undo everything:")
    print(f"  python3 {Path(__file__).name} --undo {staging}")
    print("Undo one conversation:")
    print(f"  python3 {Path(__file__).name} --undo {staging} --only {records[0]['cliSessionId']}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
