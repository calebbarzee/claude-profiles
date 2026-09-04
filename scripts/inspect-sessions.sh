#!/usr/bin/env bash
#
# inspect-sessions.sh — print the shape of Claude session files.
#
# Two file formats hold session state, and they are not the same thing:
#
#   local_*.json   Claude Desktop's session INDEX. One object per session,
#                  living in a profile's claude-code-sessions/<acct>/<org>/.
#                  Points at a transcript via cliSessionId.
#
#   *.jsonl        Claude Code CLI's TRANSCRIPT. One JSON object per line,
#                  living in ~/.claude/projects/<slug>/. Holds the actual
#                  conversation.
#
# This reports on either. Use it to check field names before writing a tool
# that generates or rewrites these files, since the schemas are undocumented
# and do change between app versions.
#
# Long strings and arrays are elided, so output is safe to paste into an
# issue. It does not print message bodies.
#
set -euo pipefail

DEFAULT_PROFILE="$HOME/Library/Application Support/Claude"

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage:
  inspect-sessions.sh [path]

  path may be:
    a local_*.json file   report that desktop index entry's fields
    a *.jsonl file        report a CLI transcript's line types and cwd values
    a profile directory   report the first index entry found in it
    omitted               use the default profile
                          (~/Library/Application Support/Claude)
EOF
}

TARGET="${1:-$DEFAULT_PROFILE}"
case "$TARGET" in
  -h|--help) usage; exit 0 ;;
esac

# A directory means "find me an index file to look at".
if [ -d "$TARGET" ]; then
  found="$(find "$TARGET/claude-code-sessions" -name 'local_*.json' -type f 2>/dev/null | sort | head -1)"
  [ -n "$found" ] || die "no local_*.json under $TARGET/claude-code-sessions"
  TARGET="$found"
fi

[ -f "$TARGET" ] || die "not a file: $TARGET"

# ---------------------------------------------------------------------------
# desktop session index
# ---------------------------------------------------------------------------

report_index() {
  echo "desktop session index: $(basename "$1")"
  echo
  python3 - "$1" <<'PY'
import json, sys

def describe(v, depth=0):
    if isinstance(v, str):
        return f'str({len(v)}) {v[:60]!r}...' if len(v) > 60 else f'str {v!r}'
    if isinstance(v, bool):
        return f'bool {v}'
    if isinstance(v, (int, float)):
        return f'num {v}'
    if isinstance(v, list):
        if not v:
            return 'list[0]'
        return f'list[{len(v)}] of {describe(v[0], depth+1)[:50]}'
    if isinstance(v, dict):
        return f'dict keys={list(v.keys())[:8]}'
    if v is None:
        return 'null'
    return type(v).__name__

d = json.load(open(sys.argv[1]))
for k, v in d.items():
    print(f'  {k:26} {describe(v)}')
PY
}

# ---------------------------------------------------------------------------
# CLI transcript
# ---------------------------------------------------------------------------

report_transcript() {
  echo "CLI transcript: $(basename "$1")"
  echo "lines: $(wc -l < "$1" | tr -d ' ')"
  echo
  python3 - "$1" <<'PY'
import json, sys
from collections import Counter

keys, cwds, types, versions = Counter(), Counter(), Counter(), Counter()
bad = 0
for line in open(sys.argv[1], errors='replace'):
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
    except Exception:
        bad += 1
        continue
    if not isinstance(d, dict):
        continue
    keys.update(d.keys())
    types[d.get('type', '<none>')] += 1
    if isinstance(d.get('cwd'), str):
        cwds[d['cwd']] += 1
    if isinstance(d.get('version'), str):
        versions[d['version']] += 1

print('line types:')
for k, n in types.most_common():
    print(f'  {k:24} {n}')

print('\ncwd values (these are what a path migration must rewrite):')
for k, n in cwds.most_common():
    print(f'  x{n:<6} {k}')

if versions:
    print('\nCLI versions that wrote this session:')
    for k, n in versions.most_common():
        print(f'  {k:24} {n}')

print(f'\ntop-level keys ({len(keys)} distinct):')
for k, n in keys.most_common():
    print(f'  {k:26} {n}')

if bad:
    print(f'\nunparseable lines: {bad}')
PY
}

case "$TARGET" in
  *.jsonl) report_transcript "$TARGET" ;;
  *.json)  report_index "$TARGET" ;;
  *)       die "unrecognized file type: $TARGET (expected .json or .jsonl)" ;;
esac
