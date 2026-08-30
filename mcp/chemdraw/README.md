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
| `apply_acs_style` | Force ACS Document 1996 house style on the front document |
| `run_command` | Escape hatch to any of ChemDraw's ~1477 menu commands |
| `list_commands` | Discover command names |
| `close_all` | Close documents without saving |

Export formats: `cdxml` `cdx` `pdf` `png` `tiff` `jpeg` `gif` `bmp` `eps` `mol`
`ct` `cml` `tgf`. **SMILES is not a save-as format** in ChemDraw — it comes from
the selection property instead.

Outputs default to `~/Documents/Claude/Inbox` (override with `CHEMDRAW_OUT_DIR`).
A `name` argument becomes a filename inside that directory, so it is restricted
to letters, digits, space, `.`, `_` and `-` — it cannot carry a path.

Every value that reaches AppleScript is escaped, and `run_command` additionally
validates the command name: an unescaped quote in a name or path would close the
string literal and run the remainder as AppleScript, `do shell script` included.

## Install

**Claude Code** — register the stdio server:

```bash
claude mcp add --scope user chemdraw -- \
  "$(command -v uv)" run --quiet "$PWD/chemdraw_server.py"
claude mcp list        # chemdraw: … ✔ Connected
```

Use an absolute `uv` (as `command -v` gives): clients spawn MCP servers without
your shell's `PATH`.

**Claude Desktop** — install it as an **extension**. On the version tested
(ChemDraw 25.0.2 era, 2026-08), edits to `claude_desktop_config.json` did not
bring this server up, and `extensions-installations.json` is integrity-hashed,
so hand-editing that file is discarded on next launch. Check your own version's
behaviour before concluding the config route is unavailable to you. Build a
bundle and install it through the UI:

```bash
./build.sh                     # → chemdraw.mcpb
```

`build.sh` resolves `uv` on this machine and bakes that absolute path into the
bundled manifest, so the bundle works on Apple silicon, Intel and a `~/.local`
install alike. Override with `UV_BIN=/path/to/uv ./build.sh`.

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
