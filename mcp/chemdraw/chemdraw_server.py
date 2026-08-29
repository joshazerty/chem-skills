# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=2", "rdkit>=2024.3"]
# ///
"""ChemDraw MCP connector -- drives ChemDraw 25 on macOS via AppleScript.

Design notes live in chemdraw_bridge.py; the empirically-derived quirks it
works around (bundle-id addressing, activate-before-command, one-Apple-event-
per-property, locale-formatted numbers) are the whole reason this layer exists.
"""
import os, sys, json, tempfile, pathlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chemdraw_bridge as cb

try:                                    # mcp >= 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:                     # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

mcp = _Server("chemdraw")

OUT_DIR = pathlib.Path(os.environ.get(
    "CHEMDRAW_OUT_DIR", os.path.expanduser("~/Documents/Claude/Inbox")))
OUT_DIR.mkdir(parents=True, exist_ok=True)

def _out(name: str) -> str:
    return str(OUT_DIR / name)


@mcp.tool()
def draw(smiles: str, name: str = "structure", clean: bool = True,
         export: str = "png") -> str:
    """Draw a molecule in ChemDraw from a SMILES string.

    Generates 2D coordinates with RDKit, hands ChemDraw a native CDXML file,
    optionally runs ChemDraw's Clean Up Structure, and exports an image.
    Returns ChemDraw's own reading of the structure plus the saved file paths.
    """
    cdxml = _out(f"{name}.cdxml")
    cb.smiles_to_cdxml(smiles, cdxml)
    cb.open_doc(cdxml)
    if clean:
        cb.clean()
    props = cb.read_props()
    result = {"opened": cdxml, "chemdraw": props}
    if export:
        result["export"] = cb.save_as(_out(f"{name}_render"), export)
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def read_front_document() -> str:
    """Read the structure currently open in ChemDraw.

    Returns SMILES, molecular formula, molecular weight, exact mass and
    elemental analysis as computed by ChemDraw itself.
    """
    return json.dumps(cb.read_props(), indent=2, ensure_ascii=False)


@mcp.tool()
def open_file(path: str) -> str:
    """Open a chemistry file in ChemDraw and report its structure.

    Accepts anything ChemDraw reads: .cdx, .cdxml, .mol, .rxn, .cml, .tgf, ...
    """
    cb.open_doc(path)
    return json.dumps({"opened": path, "chemdraw": cb.read_props()},
                      indent=2, ensure_ascii=False)


@mcp.tool()
def export(fmt: str, name: str = "export") -> str:
    """Export the front ChemDraw document to another format.

    fmt is one of: cdxml, cdx, pdf, png, tiff, jpeg, gif, bmp, eps, mol,
    ct, cml, tgf. Use pdf or eps for publication figures, mol for handing
    the structure to other cheminformatics tools.
    """
    return cb.save_as(_out(name), fmt)


@mcp.tool()
def convert(path: str, fmt: str, name: str = "converted") -> str:
    """Convert a chemistry file to another format using ChemDraw's converters."""
    cb.open_doc(path)
    return cb.save_as(_out(name), fmt)


@mcp.tool()
def clean_structure() -> str:
    """Run ChemDraw's Clean Up Structure on the front document.

    Regularises bond lengths and angles to ChemDraw's drawing conventions.
    """
    cb.clean()
    return "cleaned"


@mcp.tool()
def run_command(command: str) -> str:
    """Run any ChemDraw menu command by its internal name.

    Escape hatch to ChemDraw's ~1477 commands (e.g. convertStructureToName,
    convertNameToStructure, invertSelection). Use list_commands to discover
    names. The command must be enabled in the current context.
    """
    cb.select_all()
    try:
        cb.tell(f'do command "{command}"')
        return f"ran {command}"
    except cb.ChemDrawError as e:
        try:
            en = cb.tell(f'return (enabled of command "{command}") as text')
        except cb.ChemDrawError:
            return f"no such command {command!r}; use list_commands to find it"
        if en == "false":
            return (f"{command!r} is disabled in ChemDraw right now. Either it "
                    f"needs a different selection, or it belongs to a licensed "
                    f"add-on this install lacks (e.g. Structure->Name needs "
                    f"ChemDraw Professional). Raw error: {e}")
        raise


@mcp.tool()
def list_commands(search: str = "") -> str:
    """List ChemDraw command names, optionally filtered by substring."""
    names = cb.tell('''set out to ""
repeat with c in commands
  set out to out & (name of c) & "\\n"
end repeat
return out''', timeout=120).splitlines()
    if search:
        names = [n for n in names if search.lower() in n.lower()]
    return json.dumps(sorted(set(filter(None, names))), indent=2)


@mcp.tool()
def close_all() -> str:
    """Close every open ChemDraw document without saving."""
    cb.close_all()
    return "closed"


if __name__ == "__main__":
    mcp.run()
