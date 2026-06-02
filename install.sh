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
