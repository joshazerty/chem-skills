#!/usr/bin/env bash
# Package the connector as a Claude Desktop extension (.mcpb).
#
# acs_style.py is canonical in skills/chemdraw-figures/ and copied in here, so
# there is exactly one source of the ACS house style in the repo.
#
# The manifest's uv path is resolved HERE rather than hardcoded: clients spawn
# MCP servers without your shell's PATH, so it must be absolute, but which
# absolute path depends on the machine (Homebrew on Apple silicon vs Intel, or
# uv's own installer under ~/.local/bin).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"

out="${1:-$here/chemdraw.mcpb}"
case "$out" in /*) ;; *) out="$PWD/$out" ;; esac      # zip runs from $stage

uv="${UV_BIN:-$(command -v uv || true)}"
for cand in /opt/homebrew/bin/uv /usr/local/bin/uv "$HOME/.local/bin/uv"; do
  [ -n "$uv" ] && break
  [ -x "$cand" ] && uv="$cand"
done
if [ -z "$uv" ]; then
  echo "error: uv not found. Install it (brew install uv) or set UV_BIN." >&2
  exit 1
fi
uv="$(cd "$(dirname "$uv")" && pwd)/$(basename "$uv")"
echo "uv: $uv"

stage="$(mktemp -d)"; trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/server"
cp "$here/chemdraw_server.py" "$here/chemdraw_bridge.py" "$stage/server/"
cp "$repo/skills/chemdraw-figures/acs_style.py" "$stage/server/"

UV="$uv" python3 - "$here/manifest.json" "$stage/manifest.json" <<'PY'
import json, os, sys
m = json.load(open(sys.argv[1]))
m["server"]["mcp_config"]["command"] = os.environ["UV"]
json.dump(m, open(sys.argv[2], "w"), indent=2)
PY

mkdir -p "$(dirname "$out")"
rm -f "$out"
(cd "$stage" && zip -rq "$out" . -x ".DS_Store" "*__pycache__*")
echo "built $out"
echo "install: Claude Desktop → Settings → Extensions → Advanced settings →"
echo "         Install extension   (or drag the file onto the window)"
