#!/usr/bin/env bash
# Package the connector as a Claude Desktop extension (.mcpb).
#
# acs_style.py is canonical in skills/chemdraw-figures/ and copied in here, so
# there is exactly one source of the ACS house style in the repo.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
out="${1:-$here/chemdraw.mcpb}"

stage="$(mktemp -d)"; trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/server"
cp "$here/chemdraw_server.py" "$here/chemdraw_bridge.py" "$stage/server/"
cp "$repo/skills/chemdraw-figures/acs_style.py" "$stage/server/"
cp "$here/manifest.json" "$stage/"

rm -f "$out"
(cd "$stage" && zip -rq "$out" . -x ".DS_Store" "*__pycache__*")
echo "built $out"
echo "install: Claude Desktop → Settings → Extensions → Advanced settings →"
echo "         Install extension   (or drag the file onto the window)"
