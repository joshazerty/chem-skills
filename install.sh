#!/usr/bin/env bash
# Symlink every skill in skills/ into ~/.claude/skills/ so Claude Code discovers them.
# The repo stays the single source of truth; edits here update the live skills.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dest="${HOME}/.claude/skills"
mkdir -p "$dest"

for d in "$repo"/skills/*/; do
  name="$(basename "$d")"
  ln -sfn "$d" "$dest/$name"
  echo "linked $name  ->  $dest/$name"
done

echo "done. Skills available as /<name> in new Claude Code sessions."

# Connectors are not symlinks — an MCP server has to be registered with the
# client — so they are reported here rather than installed silently.
shopt -s nullglob
for c in "$repo"/mcp/*/; do
  name="$(basename "$c")"
  if command -v claude >/dev/null 2>&1 &&
     claude mcp list 2>/dev/null | grep -q "^${name}:"; then
    echo "connector $name: registered with Claude Code"
  else
    echo "connector $name: NOT registered — see mcp/$name/README.md"
  fi
done
