"""Low-level bridge to ChemDraw on macOS via AppleScript + RDKit.

Empirically-derived rules (ChemDraw 25.0.2, com.revvity.ChemDraw):
  1. Address the app by BUNDLE ID, never by name -- the app name carries the
     version number ("ChemDraw 25.0.2"), so name-based scripts break on upgrade.
  2. `activate` is REQUIRED before commands like selectAll become available;
     `enabled of command "selectAll"` is the readiness signal to poll.
  3. Property reads (SMILES/MW/...) FAIL on the first Apple event after a
     document opens, then succeed. Retry across SEPARATE osascript calls.
  4. Numbers come back in the user's LOCALE ("180,159" under fr_FR). The
     separators are PROBED through the same channel rather than guessed --
     see probe_separators().
  5. `save ... as` auto-appends the file extension.
  6. Never use System Events -- it blocks on an Accessibility prompt.

Every value interpolated into AppleScript goes through `_as()`. An unescaped
quote in a path or command name closes the string literal and the rest of the
argument is executed as AppleScript -- including `do shell script`.
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


def _as(value: str) -> str:
    """Render a Python string as an AppleScript string LITERAL, quotes included.

    AppleScript has no escape for a raw newline inside a literal, so control
    characters are rejected outright rather than silently mangled.
    """
    s = str(value)
    if any(ord(c) < 32 for c in s):
        raise ChemDrawError(f"control character in AppleScript value {s!r}")
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def osa(script: str, timeout: int = 90) -> str:
    try:
        p = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ChemDrawError(f"osascript timed out after {timeout}s")
    except FileNotFoundError:
        raise ChemDrawError("osascript not found -- this connector is macOS only")
    if p.returncode != 0:
        raise ChemDrawError(p.stderr.strip())
    return p.stdout.strip()

def tell(body: str, timeout: int = 90) -> str:
    return osa(f'tell application id "{BUNDLE}"\n{body}\nend tell', timeout)


# Unambiguous digit-grouping separators: never a decimal point in any locale.
# space, NBSP, NARROW NBSP, THIN SPACE, apostrophe, right single quote
_GROUPERS = "    '’"

def probe_separators() -> tuple:
    """Ask AppleScript to render a KNOWN number, and read the separators off it.

    Rule 4: the properties come back locale-formatted, and "1,234" is 1234 in
    en_US but 1.234 in fr_FR -- syntax alone cannot tell them apart. Rendering
    1234.5 through the same channel does: whatever precedes the final digit run
    is the decimal separator, and any other non-digit is the grouping one.
    """
    txt = osa("return (1234.5) as text").strip()
    m = re.search(r"(\D)(\d+)$", txt)
    dec = m.group(1) if m else "."
    thou = next((c for c in txt if not c.isdigit() and c != dec), "")
    return dec, thou

_SEPS = None

def separators(force: bool = False) -> tuple:
    """Cached probe_separators(); falls back to ambiguous parsing if it fails."""
    global _SEPS
    if _SEPS is None or force:
        try:
            _SEPS = probe_separators()
        except ChemDrawError:
            _SEPS = (None, None)
    return _SEPS


def parse_num(s: str, dec: str = None, thou: str = None) -> float:
    """ChemDraw returns locale-formatted numbers; normalise to float.

    Pass the separators from separators() when they are known -- that is the
    only way "1,234" is read as 1234 rather than 1.234. Without them the
    grouping-vs-decimal case is genuinely ambiguous and a lone separator is
    read as a decimal point, which is right for fr_FR "180,159" and wrong for
    en_US "1,234"; a mass over 1000 is exactly where it matters.
    """
    s = s.strip()
    if not s:
        raise ValueError("empty number")
    sign = "-" if s[0] == "-" else ""
    s = s.lstrip("+-")

    if dec:
        for c in set(_GROUPERS) | ({thou} if thou else set()):
            if c and c != dec:
                s = s.replace(c, "")
        s = s.replace(dec, ".")
        return float(sign + s)

    # --- no probe available: disambiguate as far as syntax allows -----------
    for c in _GROUPERS:
        s = s.replace(c, "")
    if "," in s and "." in s:                      # last separator is decimal
        d = max(s.rfind(","), s.rfind("."))
        s = re.sub(r"[.,]", "", s[:d]) + "." + s[d + 1:]
    else:
        for c in (",", "."):
            if s.count(c) > 1:                     # repeated => grouping
                s = s.replace(c, "")
        s = s.replace(",", ".")
    return float(sign + s)


def open_doc(path: str) -> None:
    """Rule 2: activate first, then open."""
    tell(f'activate\ndelay 0.5\nopen POSIX file {_as(os.path.abspath(path))}')

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
        tell('do command "selectAll"')
        return True
    except ChemDrawError:
        if strict:
            raise
        return False


# ChemDraw's own command names; the charset is deliberately narrow so a name
# can never carry AppleScript syntax even before _as() escapes it.
_COMMAND_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}$")

def do_command(name: str) -> None:
    if not _COMMAND_RE.match(name or ""):
        raise ChemDrawError(f"not a ChemDraw command name: {name!r}")
    tell(f'do command {_as(name)}')

def command_enabled(name: str) -> str:
    if not _COMMAND_RE.match(name or ""):
        raise ChemDrawError(f"not a ChemDraw command name: {name!r}")
    return tell(f'return (enabled of command {_as(name)}) as text')


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
    dec, thou = separators()
    out = {}
    for key, prop, cast in PROPS:
        raw = _read_one(prop)
        out[key] = parse_num(raw, dec, thou) if cast is float else raw
    return out

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
    tell(f'save front document in POSIX file {_as(dest_noext)} '
         f'as {_as(SAVE_FORMATS[fmt])}')
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
    acs_style.apply_to_document(tell, _as)


def smiles_to_cdxml(smiles: str, path: str) -> str:
    from rdkit import Chem
    from rdkit.Chem import AllChem
    m = Chem.MolFromSmiles(smiles)
    if m is None:
        raise ChemDrawError(f"RDKit could not parse SMILES: {smiles!r}")
    AllChem.Compute2DCoords(m)
    block = Chem.MolToCDXMLBlock(m)
    # RDKit emits a bare header (BondLength="") and draws at its own scale --
    # measured at 28.8 pt, exactly twice the ACS bond length. Restyle the
    # header AND rescale the body, or the file claims a house style it is not
    # drawn in (masked, until now, only by Clean Up Structure running after).
    head_end = block.index(">", block.index("<CDXML"))
    body = acs_style.rescale_to_bond_length(block[head_end + 1:])
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(acs_style.cdxml_header() + body)
    return path
