# chemdraw — MCP connector

Lets Claude drive **ChemDraw** on macOS: draw a molecule from SMILES, read the
chemistry of an open structure, clean it up, and export to any format ChemDraw
supports. This is what `skills/chemdraw-figures/` uses to render.

macOS only — it works by sending Apple events to ChemDraw.app.

## How structures get in and out

ChemDraw cannot accept a SMILES string. But **RDKit writes CDXML natively**
(`Chem.MolToCDXMLBlock`), and CDXML *is* ChemDraw's document format:

```
SMILES → RDKit 2D coords → CDXML → ChemDraw → clean → export
                                        ↓
                       SMILES / formula / MW / exact mass / analysis
```

No clipboard, no GUI scripting, no screen coordinates — only ChemDraw's own
AppleScript dictionary, so no Accessibility grant is needed.

## Tools

| Tool | Purpose |
|---|---|
| `draw` | SMILES → drawn, cleaned structure + properties + image |
| `read_front_document` | Chemistry of whatever is open in ChemDraw |
| `open_file` | Open `.cdx` `.cdxml` `.mol` `.rxn` `.cml` `.tgf` and report it |
| `export` | Front document → any supported format |
| `convert` | File → file, using ChemDraw's own converters |
| `clean_structure` | ChemDraw's Clean Up Structure |
| `run_command` | Escape hatch to any of ChemDraw's ~1477 menu commands |
| `list_commands` | Discover command names |
| `close_all` | Close documents without saving |

Export formats: `cdxml` `cdx` `pdf` `png` `tiff` `jpeg` `gif` `bmp` `eps` `mol`
`ct` `cml` `tgf`. **SMILES is not a save-as format** in ChemDraw — it comes from
the selection property instead.

Outputs default to `~/Documents/Claude/Inbox` (override with `CHEMDRAW_OUT_DIR`).

## Install

**Claude Code** — register the stdio server:

```bash
claude mcp add --scope user chemdraw -- \
  /opt/homebrew/bin/uv run --quiet "$PWD/chemdraw_server.py"
claude mcp list        # chemdraw: … ✔ Connected
```

**Claude Desktop** — this version does *not* read `claude_desktop_config.json`
for MCP servers; local servers are installed as **extensions**. Build a bundle
and install it through the UI (installations are integrity-hashed, so hand-
editing the config will not work):

```bash
./build.sh                     # → chemdraw.mcpb
```

Then Claude Desktop → Settings → Extensions → Advanced settings → Install
extension, or drag `chemdraw.mcpb` onto the window.

**Claude Science** is not supported: it declares no
`com.apple.security.automation.apple-events` entitlement and sandboxes MCP
servers, so it cannot send Apple events to ChemDraw at all.

## Prerequisites

`uv` (launches the server and resolves its PEP-723 dependency header), ChemDraw,
and **Automation permission** for whichever app hosts Claude — System Settings →
Privacy & Security → Automation → enable ChemDraw. A missing grant surfaces as
AppleEvent error `-1743`.

Check everything at once:

```bash
python3 ../../skills/chemdraw-figures/driver.py doctor
```

## Known limitation

**Structure→Name / Name→Structure** are unavailable: the commands exist in the
dictionary but report `enabled: false` — they belong to a licensed ChemDraw
Professional add-on. No tool is shipped for them; `run_command` explains this if
you try.

The AppleScript quirks this connector absorbs — and why each matters — are
documented in `../../skills/chemdraw-figures/LEARNINGS.md`.
