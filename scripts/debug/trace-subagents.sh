#!/usr/bin/env bash
#
# trace-subagents.sh — show how a CLI transcript links to its subagent
# sidechains, and prove that sidechains never get their own desktop index
# entry.
#
# WHAT THIS ESTABLISHED
#
# The CLI transcript store nests subagent work one level down:
#
#   <slug>/<sessionId>.jsonl                         a real session
#   <slug>/<sessionId>/subagents/agent-<hash>.jsonl  a subagent sidechain
#
# A sidechain carries the PARENT's sessionId on every line, plus its own
# agentId matching its filename, isSidechain true throughout, and
# sessionKind "bg". Parent and sidechain are joined by a shared promptId:
# the parent's user turn and the whole sidechain share one promptId value.
#
# The desktop app therefore renders subagent work INLINE inside the parent
# turn, and gives sidechains no index entry of their own. That is why
# import-cli-session.py refuses to import one as a standalone session and,
# when --remap republishes a parent, republishes the subagents directory
# alongside it.
#
# Re-run this after a Claude Desktop update to check the layout still holds.
#
set -euo pipefail

CLI_PROJECTS="$HOME/.claude/projects"
APP_SUPPORT="$HOME/Library/Application Support"

usage() {
  cat <<'EOF'
Usage:
  trace-subagents.sh [transcript.jsonl]

With no argument, picks the transcript that has the most sidechains.
EOF
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
esac

PARENT="${1:-}"

if [ -z "$PARENT" ]; then
  PARENT="$(
    for f in "$CLI_PROJECTS"/*/*.jsonl; do
      [ -f "$f" ] || continue
      d="${f%.jsonl}/subagents"
      [ -d "$d" ] || continue
      n="$(find "$d" -name '*.jsonl' | wc -l | tr -d ' ')"
      printf '%s\t%s\n' "$n" "$f"
    done | sort -rn | head -1 | cut -f2
  )"
fi

[ -n "$PARENT" ] && [ -f "$PARENT" ] || { echo "no transcript with sidechains found" >&2; exit 1; }

AGENTS="${PARENT%.jsonl}/subagents"

echo "parent    : $PARENT"
echo "sidechains: $(find "$AGENTS" -name '*.jsonl' 2>/dev/null | wc -l | tr -d ' ')"
echo

echo "=== do any desktop index entries point at a sidechain? ==="
found=0
for f in "$APP_SUPPORT"/Claude*/claude-code-sessions/*/*/local_*.json \
         "$APP_SUPPORT"/Claude\ Profiles/*/claude-code-sessions/*/*/local_*.json; do
  [ -f "$f" ] || continue
  found=1
  python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
print("  %s  %s" % (d.get("cliSessionId", "?"), d.get("title", "")[:44]))' "$f"
done
[ "$found" -eq 1 ] || echo "  (no index entries found)"
echo "  Compare these ids against the sidechain filenames below: no overlap"
echo "  means the app never indexes a sidechain."
echo

echo "=== sidechain shape ==="
CHILD="$(find "$AGENTS" -name '*.jsonl' 2>/dev/null | head -1)"
if [ -n "$CHILD" ]; then
  echo "  file: $(basename "$CHILD")"
  head -1 "$CHILD" | python3 -c '
import json, sys
d = json.load(sys.stdin)
for k in ("agentId","sessionId","isSidechain","sessionKind","promptId","parentUuid","cwd"):
    if k in d:
        print(f"    {k:14} {d[k]!r}")'
fi
echo

echo "=== the promptId that joins parent to sidechain ==="
if [ -n "$CHILD" ]; then
  PID="$(head -1 "$CHILD" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("promptId",""))')"
  if [ -n "$PID" ]; then
    echo "  promptId $PID"
    echo "  lines carrying it in the parent  : $(grep -c "$PID" "$PARENT" || echo 0)"
    echo "  lines carrying it in the sidechain: $(grep -c "$PID" "$CHILD" || echo 0)"
    echo
    echo "  A shared promptId across both files is the join the app uses to"
    echo "  nest the agent's work under the parent turn that spawned it."
  fi
fi
