"""Low-level bridge to ChemDraw on macOS via AppleScript + RDKit.

Empirically-derived rules (ChemDraw 25.0.2, com.revvity.ChemDraw):
  1. Address the app by BUNDLE ID, never by name -- the app name carries the
     version number ("ChemDraw 25.0.2"), so name-based scripts break on upgrade.
  2. `activate` is REQUIRED before commands like selectAll become available;
     `enabled of command "selectAll"` is the readiness signal to poll.
  3. Property reads (SMILES/MW/...) FAIL on the first Apple event after a
     document opens, then succeed. Retry across SEPARATE osascript calls.
  4. Numbers come back in the user's LOCALE ("180,159" under fr_FR).
  5. `save ... as` auto-appends the file extension.
  6. Never use System Events -- it blocks on an Accessibility prompt.
"""
import subprocess, os, re, sys, time

_HERE = os.path.dirname(os.path.abspath(__file__))
# acs_style.py is canonical in skills/chemdraw-figures/ and copied in beside
# this file when the .mcpb bundle is built, so look in both places.
sys.path[:0] = [_HERE, os.path.join(_HERE, "..", "..", "skills", "chemdraw-figures")]
import acs_style

BUNDLE = "com.revvity.ChemDraw"

class ChemDrawError(RuntimeError):
    pass

def esc(value) -> str:
    """Escape a value for embedding in an AppleScript string literal.

    Every interpolation into a `tell` block MUST go through this. Without it a
    path or command name containing a double quote closes the literal early and
    the remainder is executed as AppleScript -- which reaches `do shell script`,
    i.e. arbitrary code execution. Verified, not theoretical.
    """
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def osa(script: str, timeout: int = 90) -> str:
    p = subprocess.run(["osascript", "-e", script],
                       capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise ChemDrawError(p.stderr.strip())
    return p.stdout.strip()

def tell(body: str, timeout: int = 90) -> str:
    return osa(f'tell application id "{BUNDLE}"\n{body}\nend tell', timeout)

def parse_num(s: str) -> float:
    """ChemDraw returns locale-formatted numbers; normalise to float."""
    s = s.strip()
    if not s:
        raise ValueError("empty number")
    # If both separators present, the LAST one is the decimal separator.
    if "," in s and "." in s:
        dec = max(s.rfind(","), s.rfind("."))
        s = re.sub(r"[.,]", "", s[:dec]) + "." + s[dec + 1:]
    elif "," in s:
        s = s.replace(",", ".")
    return float(s)

def safe_output_path(out_dir, name: str) -> str:
    """Resolve an output name inside out_dir, refusing anything that escapes it.

    `name` arrives from a tool argument, so it is untrusted. Two traps: pathlib
    discards the base entirely when the right-hand side is absolute
    (Path(base) / "/etc/x" == "/etc/x"), and "../" walks out of the directory.
    """
    import pathlib
    root = pathlib.Path(out_dir).resolve()
    candidate = (root / name).resolve()
    if not str(candidate).startswith(str(root) + os.sep):
        raise ChemDrawError(
            f"output name {name!r} escapes {root}; pass a plain filename")
    return str(candidate)


def open_doc(path: str) -> None:
    """Rule 2: activate first, then open."""
    tell(f'activate\ndelay 0.5\nopen POSIX file "{esc(os.path.abspath(path))}"')

def wait_ready(tries: int = 40) -> bool:
    """Rule 2: poll the canvas readiness signal."""
    return tell(f'''repeat {tries} times
  if (enabled of command "selectAll") then return "yes"
  delay 0.25
end repeat
return "no"''') == "yes"

def select_all(strict: bool = False) -> bool:
    """Best-effort select-all. Returns True if the command actually fired.

    Rule 2: needs the app active AND the canvas ready. During recovery we do
    not want a transient 727 ("command not available") to abort the caller,
    so failures are swallowed unless strict=True.
    """
    try:
        tell("activate")
        wait_ready()
        do_command("selectAll")
        return True
    except ChemDrawError:
        if strict:
            raise
        return False

PROPS = [
    ("smiles",             "SMILES",             str),
    ("formula",            "Molecular Formula",  str),
    ("molecular_weight",   "Molecular Weight",   float),
    ("exact_mass",         "Exact Mass",         float),
    ("elemental_analysis", "Elemental Analysis", str),
]

def _read_one(prop: str, retries: int = 8) -> str:
    """Read ONE property in its OWN Apple event.

    Rule 3: batching several properties into a single event reliably returns
    -10000, and the first read after a document opens fails even when batched
    correctly. One event per property + retry is what actually survives.
    """
    last = None
    for _ in range(retries):
        try:
            v = tell(f'return ({prop} of (selection of front document)) as text')
            if v.strip():
                return v.strip()
            last = ChemDrawError(f"{prop}: empty (nothing selected?)")
        except ChemDrawError as e:
            last = e
        select_all()          # tolerant; re-arms the canvas
        time.sleep(0.5)
    raise ChemDrawError(f"could not read {prop}: {last}")

def read_props() -> dict:
    """Whole-document chemistry as ChemDraw itself understands it."""
    select_all()
    out = {}
    for key, prop, cast in PROPS:
        raw = _read_one(prop)
        out[key] = parse_num(raw) if cast is float else raw
    return out

# ChemDraw command names are camelCase identifiers (selectAll, cleanUpStructure,
# chooseArrowTool_90_CW). Anything outside this charset cannot be a real command
# and is the shape an injection attempt takes, so refuse it outright.
COMMAND_RE = re.compile(r"\A[A-Za-z][A-Za-z0-9_]{0,63}\Z")


def do_command(name: str) -> None:
    if not COMMAND_RE.match(name or ""):
        raise ChemDrawError(
            f"refusing command name {name!r}: expected a camelCase identifier")
    tell(f'do command "{name}"')


SAVE_FORMATS = {
    "cdxml": "ChemDraw XML", "cdx": "ChemDraw", "pdf": "PDF", "png": "PNG",
    "tiff": "TIFF", "jpeg": "JPEG", "gif": "GIF", "bmp": "BMP",
    "eps": "Encapsulated PostScript", "mol": "MDL Molfile",
    "ct": "Connection Table", "cml": "Chemical Markup Language (CML)",
    "tgf": "Transportable Graphics (TGF)",
}

def save_as(dest_noext: str, fmt: str) -> str:
    """Rule 5: ChemDraw appends the extension itself."""
    if fmt not in SAVE_FORMATS:
        raise ChemDrawError(f"unsupported format {fmt}; have {sorted(SAVE_FORMATS)}")
    dest_noext = os.path.abspath(dest_noext)
    tell(f'save front document in POSIX file "{esc(dest_noext)}" '
         f'as "{esc(SAVE_FORMATS[fmt])}"')
    for cand in (f"{dest_noext}.{fmt}", dest_noext):
        if os.path.exists(cand):
            return cand
    raise ChemDrawError("save produced no file")

def clean() -> None:
    tell('clean front document')

def close_all() -> None:
    tell('close every document saving no')

def apply_acs() -> None:
    """Apply ACS Document 1996 house style to the front document."""
    acs_style.apply_to_document(tell)


def smiles_to_cdxml(smiles: str, path: str) -> str:
    from rdkit import Chem
    from rdkit.Chem import AllChem
    m = Chem.MolFromSmiles(smiles)
    if m is None:
        raise ChemDrawError(f"RDKit could not parse SMILES: {smiles!r}")
    AllChem.Compute2DCoords(m)
    block = Chem.MolToCDXMLBlock(m)
    # RDKit emits a bare header (BondLength=""); restyle it to ACS on the way out
    head_end = block.index(">", block.index("<CDXML"))
    body = block[head_end + 1:]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(acs_style.cdxml_header() + body)
    return path
