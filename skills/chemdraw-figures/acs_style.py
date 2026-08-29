"""ACS Document 1996 house style.

These values are not remembered or guessed -- they were read directly out of
ChemDraw's own shipped stationery file:
    .../SpecialPurpose/Stationery/ACS Document 1996.cds
via the AppleScript document properties, then cross-checked against the CDXML
that ChemDraw writes for that same document. Two unit systems are involved:

  * AppleScript document properties use 1440ths of an inch (and 20ths of a
    point for font sizes)      -> ACS_APPLESCRIPT
  * CDXML root attributes use points                     -> ACS_CDXML
"""

# --- AppleScript document properties (1440ths inch; font sizes 20ths pt) ---
ACS_APPLESCRIPT = {
    "fixed length":  288,      # 14.4 pt = 0.2 in  -- ACS bond length
    "bold width":     40,      #  2.0 pt
    "line width":     12,      #  0.6 pt
    "margin width":   32,      #  1.6 pt
    "hash spacing":   50,      #  2.5 pt
    "bond spacing":   18,      #  18 % of bond length
    "chain angle":   120,      #  degrees
    "label font":   "Arial",
    "label size":    200,      # 10 pt
    "caption font": "Arial",
    "caption size":  200,      # 10 pt
}

# --- CDXML root attributes (points) ---
ACS_CDXML = {
    "LabelFont": "58", "LabelSize": "10", "LabelFace": "96",
    "CaptionFont": "58", "CaptionSize": "10",
    "HashSpacing": "2.50", "MarginWidth": "1.60", "LineWidth": "0.60",
    "BoldWidth": "2", "BondLength": "14.40", "BondSpacing": "18",
    "ChainAngle": "120",
    "LabelJustification": "Auto", "CaptionJustification": "Left",
    "FractionalWidths": "yes", "InterpretChemically": "yes",
    "HideImplicitHydrogens": "no",
}

ARIAL = "58"          # font id in the table below
FONT_TABLE = '<fonttable><font id="58" charset="x-mac-roman" name="Arial"/></fonttable>'

# Entries 0-7 are ChemDraw's stock colours; entry 8 is the grey used for
# secondary text (reagents beside each arrow).
COLOR_TABLE = (
    '<colortable>'
    '<color r="1" g="1" b="1"/><color r="0" g="0" b="0"/>'   # 0 white, 1 black
    '<color r="1" g="0" b="0"/><color r="1" g="1" b="0"/>'   # 2 red,   3 yellow
    '<color r="0" g="1" b="0"/><color r="0" g="1" b="1"/>'   # 4 green, 5 cyan
    '<color r="0" g="0" b="1"/><color r="1" g="0" b="1"/>'   # 6 blue,  7 magenta
    '<color r="0.35" g="0.35" b="0.35"/>'                    # 8 grey
    '</colortable>'
)

# A `color="N"` ATTRIBUTE IS NOT THE TABLE INDEX. Measured by rendering a ramp:
# colour attribute N resolves to colortable entry N-2, so black (entry 1) is
# "3" and the grey (entry 8) is "10". Attributes "1" and "2" both resolve to
# white -- i.e. invisible on the page, which is why nothing may default to them.
BLACK = "3"
GREY = "10"

# CDX font-face bit flags: 1 bold, 2 italic, 4 underline,
# 32 subscript, 64 superscript.  96 = 32|64 = "formula" auto-format, which is
# right for ATOM LABELS (it is what LabelFace uses) but wrong for captions --
# it silently turns "TS1-2" into "TS(1-2)" with subscripts. Captions use 0.
PLAIN, BOLD, ITALIC = "0", "1", "2"
SUB, SUPER, BOLD_ITALIC = "32", "64", "3"
FORMULA = "96"


def cdxml_header(**overrides) -> str:
    attrs = dict(ACS_CDXML); attrs.update(overrides)
    a = "\n ".join(f'{k}="{v}"' for k, v in attrs.items())
    return ('<?xml version="1.0" encoding="UTF-8" ?>\n'
            '<!DOCTYPE CDXML SYSTEM '
            '"https://static.chemistry.revvitycloud.com/cdxml/CDXML.dtd" >\n'
            f'<CDXML\n {a}\n>\n{FONT_TABLE}\n{COLOR_TABLE}\n')


def apply_to_document(tell) -> None:
    """Force ACS Document 1996 settings onto the front ChemDraw document."""
    for key, val in ACS_APPLESCRIPT.items():
        v = f'"{val}"' if isinstance(val, str) else val
        tell(f'set {key} of front document to {v}')
