---
name: chemdraw-figures
description: Draws publication-quality chemical structures and full schemes — catalytic cycles, reaction schemes, mechanisms — in ACS Document 1996 house style, by writing CDXML directly and rendering it through ChemDraw. Use when asked to draw, redraw or tidy a chemical structure, a catalytic cycle, a reaction scheme or a mechanism; when a figure is wanted "in ACS style", "publication ready", "for a paper", or sized for a journal column; when given a SMILES string to draw; or when a chemistry figure must be exported to PDF/EPS/TIFF for submission. Also for editing an existing .cdx/.cdxml, converting between chemical file formats, and reading the formula, molecular weight or exact mass of a drawn structure. Rendering requires the `chemdraw` MCP connector (mcp/chemdraw/). Not for computational-chemistry input files, spectra, or non-chemical diagrams.
---

# chemdraw-figures

Produces **publication-quality chemistry figures** in ACS house style. Structures
are authored as **CDXML** — ChemDraw's own document format — and rendered by
driving ChemDraw itself, so the output is genuine ChemDraw artwork rather than an
imitation of it.

Everything is checked by one script:

```bash
python3 driver.py doctor      # prerequisites: ChemDraw, uv, RDKit, Automation, connector
python3 driver.py selftest    # 17 offline tests; needs no ChemDraw
python3 driver.py build [dir] # render the worked example
```

Run `doctor` first on a new machine. The failure that costs the most time is
**Automation permission** — a missing grant shows up as AppleEvent error
`-1743`, which looks like a broken connector but is a macOS privacy setting.

## Two routes

**One molecule → use the connector directly.** `draw` takes SMILES, generates 2D
coordinates with RDKit, hands ChemDraw native CDXML, runs Clean Up Structure and
returns ChemDraw's own reading of the molecule plus an image:

```
draw(smiles="CC(=O)Oc1ccccc1C(=O)O", name="aspirin", export="png")
```

Also available: `read_front_document`, `open_file`, `export`, `convert`,
`clean_structure`, `apply_acs_style`, `run_command`, `list_commands`,
`close_all`. `name` arguments become filenames in `~/Documents/Claude/Inbox`,
so they take letters, digits, space, `.`, `_` and `-` only — no paths.

**A full scheme → author CDXML with the toolkit.** Anything with several
structures, arrows and captions is built with `cdxml_build.py`:

```python
from cdxml_build import Page, Frag, BL, pol
import acs_style as A

p = Page()
f = Frag()                                     # one structure
re_ = f.atom(0, 0, "Re", nH=0)
o   = f.at(re_, BL, 225, "O", charge=-1, label="⊖O")
f.bond(re_, o)
p.add(f, x, y)

p.text(x, y, "TS1–2", color=A.BLACK)           # caption
p.runs(x, y, [("– H", A.ITALIC), ("2", A.SUB), ("O", A.ITALIC)])   # H₂O
p.curved_arrow(cx, cy, r, a1, a2)              # tapered arc arrow
p.straight_arrow(x1, y1, x2, y2)               # plain reaction arrow
p.fit(margin=11).write("scheme.cdxml")         # tight page box
```

`fit()` is always last: it measures the artwork, shrinks the page to it and
moves everything — fragments, captions, curved arrows and straight arrows
alike — onto the new box. Add geometry after `fit()` and it will be off-page.

Every structure carries a `formula()`: `Frag.formula()` returns element counts
and total charge with implicit hydrogens filled in. **Use it to check a scheme
balances before rendering** — see `check_balance()` in the worked example. A
step that quietly drops an oxygen is the error a referee always catches.

`examples/catalytic_cycle.py` is a complete worked example — a seven-species
DODH catalytic cycle. **Read it before building a new scheme** and copy its
shape; it is the reference for structure builders, ring fusion, arrow placement
and label positioning.

## Geometry

Coordinates are in **points**, y increasing **downward**. `pol(r, deg)` takes
ordinary maths angles (y up) and flips internally, so callers think in normal
degrees. Bond length is fixed at the ACS **14.4 pt** — scale a layout by changing
the cycle radius, never the bond length. In the worked example `R` is the single
size knob, because the label ring is expressed as a fraction of it.

## Rendering and export

Rendering needs the `chemdraw` MCP connector, which drives the desktop app:

```python
cb.close_all(); cb.tell("activate"); cb.tell(f'open POSIX file "{path}"')
cb.save_as(stem, "pdf")     # also eps, tiff, png, cdx, cdxml, mol, cml, ct, tgf
```

For a journal, export **PDF or EPS** (vector). ChemDraw always exports PDF at the
*print* page size, so run `crop_pdf.py <file.pdf>` afterwards to give it a tight
MediaBox. EPS already carries a correct BoundingBox.

ACS column widths: single **3.25 in** (234 pt), double **7.00 in** (504 pt).
Check the fitted page size and choose a radius that lands inside one.

`crop_pdf.py <file.pdf>` rewrites the file **in place**; pass `-o out.pdf` to
keep the original.

## Before writing any CDXML

**Read `LEARNINGS.md`.** ChemDraw's CDXML importer does not behave the way the
attribute names suggest, and each trap fails *silently* — a blank page, an
invisible caption, subscripted nonsense. The three that bite first:

- `<arrow>` cannot make a curved arrow — arc geometry is discarded on import
- a caption left at the default face is in *formula* mode, not plain text
- `color="1"` is **white**, not black — the attribute is offset by 2

`driver.py selftest` asserts all three — and eleven more, including that
`fit()` moves straight arrows, that atom labels are XML-escaped, and that the
worked cycle balances — so a regression is caught offline.

## House style

ACS Document 1996 is the default and stays so unless asked otherwise: 14.4 pt
bonds, 0.6 pt lines, Arial 10 pt labels. The numbers were read out of ChemDraw's
own shipped stationery, not guessed — see `acs_style.py`. Captions in black;
secondary text (reagents gained or lost at a step) in grey italic. Charges use
the circled glyph ⊖ / ⊕ rather than a bare minus, which `charge_label()` in the
worked example places on the side facing away from the bond.
