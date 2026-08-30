# chemdraw-figures — learnings

Newest first. Append a dated entry when you discover a fix, a rendering gotcha,
or a validated extension. Keep it concrete, and add the matching assertion to
`driver.py selftest` in the same commit — an entry with no test is a claim, not
a guarantee.

Everything below was found by probing ChemDraw 25.0.2 and reading its own
shipped template files. None of it is documented, and every one of these fails
*silently* — a blank page, an invisible caption, a mangled label. Each has a
matching assertion in `driver.py selftest`.

### 2026-08-30 — `fit()` has to move arrows, which hide their geometry
`<arrow>` keeps its endpoints in `Tail3D`/`Head3D` (`"x y z"`) and its extent in
`BoundingBox`, never in `p=""`. A page-fitting pass that rewrites only `p=""`
and `CurvePoints` therefore moves every structure and caption while leaving
straight arrows exactly where they were — displaced from the artwork by the fit
offset and usually off the shrunken page. Curved arrows were unaffected (they
are `<curve>` elements, so they ride on `CurvePoints`), which is why the worked
example never showed it. **Fix:** `fit()` measures and shifts `Tail3D`,
`Head3D` and `BoundingBox` as well; `selftest` asserts the arrow-to-atom gap is
unchanged across a fit and the head still lands inside the page.

### 2026-08-30 — Locale numbers: probe the separators, never guess them
`parse_num` originally read a lone separator as a decimal point. That is right
for fr_FR `180,159` and wrong for en_US `1,234`, which is 1234 — so every mass
over 1000 came back 1000x too small, silently, and fr_FR's actual grouping
separator (U+00A0, not a plain space) raised instead. Syntax cannot settle it:
`1,234` is genuinely ambiguous. **Fix:** render a *known* number through the
same channel — `osascript -e 'return (1234.5) as text'` — and read the decimal
and grouping separators straight off the result. `probe_separators()` does this
once per session; `parse_num(s, dec, thou)` is then deterministic and unit-
testable offline with the separators passed in.

### 2026-08-30 — RDKit does not draw at the ACS bond length
`Chem.MolToCDXMLBlock` emits coordinates at its own scale — measured at
**28.8 pt**, exactly twice ACS — with negative values around the origin and no
page `BoundingBox`. Splicing that body under the ACS header gives a file that
*claims* `BondLength="14.40"` and is drawn at 28.8. It looked fine only because
`draw()` runs Clean Up Structure afterwards, which rescales to the document's
fixed length; with `clean=False` the structure came out double size. **Fix:**
`acs_style.rescale_to_bond_length()` measures the median drawn bond length and
scales the body to the target, then shifts it positive and adds the page box.
Measuring beats assuming a factor of 2 — RDKit's scale is not a promise.

### 2026-08-30 — Everything reaching AppleScript must be escaped
An AppleScript string literal ends at the first unescaped `"`; the remainder of
the value is parsed as script, and `do shell script` is one statement away. The
connector interpolated paths, format names and `run_command` arguments straight
into `tell` blocks, and MCP tool arguments are model-controlled — a filename or
command name read out of a paper is enough. **Fix:** one `_as()` helper escapes
`\` and `"` and refuses control characters (AppleScript has no escape for a raw
newline in a literal), every interpolation goes through it, and command names
are additionally checked against `^[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}$`. Tool
`name` arguments get the same treatment on the filesystem side: they become
filenames in `CHEMDRAW_OUT_DIR`, so `../` must not survive.

### 2026-08-30 — A drawn cycle needs a mass-balance check, not an eye
The seven-species DODH example shipped with species 3 an oxo short and species
7 an oxo long, so four of its seven steps did not balance — the kind of error
that survives any amount of looking at the picture. **Fix:** `Frag.formula()`
returns element counts and total charge with implicit hydrogens filled in, the
example's `STEPS` table carries the gained/lost formula for each step, and
`check_balance()` asserts species[i] + gained - lost == species[i+1] for every
step. It runs in `selftest` and the example refuses to build if it fails.
Chemistry: 3 is HReO2(OH)O(-) (2 - acetone is H2ReO4(-)); 7 is the Re(V)
mono-oxo diolate (6 - H2O), and its retro-[3+2] turns the two diolate oxygens
into oxo ligands, returning Re(VII) ReO4(-).

### 2026-08-29 — Circled charges: override the label, keep the charge
ChemDraw has no document setting for drawing charges in circles — the only
built-in route is the manual CirclePlus/CircleMinus **symbol tool**, and nothing
in the AppleScript document properties or the CDXML root controls it.
**Fix:** give the node an explicit `<t>` child whose text is "O" plus **U+2296**
(CIRCLED MINUS). The drawn label is overridden while the node keeps its real
`Element` and `Charge`, so the file stays chemically correct — RDKit still reads
the fragment back as an anion. `charge_label()` puts the glyph on the side
facing away from the bond, matching ChemDraw's own auto-placement.

### 2026-08-29 — `color="1"` is white, not black
The `color` attribute is **not** the colortable index: measured by rendering a
ramp, attribute N resolves to colortable entry **N-2**. (This matches the CDX
spec, where indices 0 and 1 are the reserved background and foreground, so a
document's own `<colortable>` starts at 2 — the ramp only confirmed it.) So black (entry 1) is
`"3"` and the grey used for reagent captions is `"10"`; attributes `"1"` and
`"2"` both resolve to white and vanish against the page. Cost an hour of
"why is my caption missing". Use `acs_style.BLACK` / `.GREY`, never literals.

### 2026-08-29 — `face="96"` is formula mode, not plain text
CDX font-face is a bit field: 1 bold, 2 italic, 32 subscript, 64 superscript.
**96 = 32|64 = auto-format**, which is correct for *atom labels* (it is what
`LabelFace` uses) but silently turns a caption like "TS1-2" into "TS" with a
subscripted "1-2", and "TS7–1" into a superscripted mess. Captions must use
`face="0"`. Mixed runs (`Page.runs`) give a real H₂O subscript.

### 2026-08-29 — Curved arrows must be closed, filled curves
Three dead ends before the working answer:
1. `<arrow>` with `ArcAngle` — the importer **discards all arc geometry**. Even
   ChemDraw's *own* template arrows, extracted verbatim from `Shapes.ctp` and
   re-imported, come back as straight chords. `<arrow>` is only ever a straight
   line (still useful — that is `straight_arrow`, and it does need
   `ArrowheadHead="Full"` or no head is drawn).
2. Open `<curve>` — refuses to stroke at all, under every combination of
   `CurveType`, `LineType`, `LineWidth` and `FillType` tried. Only the
   arrowheads render.
3. **What works:** a **closed, filled** curve — `CurveType="129"`
   (128 filled | 1 closed) with `Closed="yes" FillType="Solid"`. Each arrow is
   one filled outline: an arc-shaped shaft tapering into a head. Bit 128 was
   found by counting `CurveType` values in `Advanced BioDraw.ctp` (129 appears
   442 times).

### 2026-08-29 — ChemDraw smooths *through* CurvePoints
`CurvePoints` are not Bezier control triples — the renderer smooths through
them. Proper cubic-Bezier control points for an arc bulge the fill into a lens
shape. **Fix:** sample the true outline densely (~2°) and let the smoothing
follow it. Corollary: a **sharp corner must be forced by repeating its point**
about three times, which is exactly what ChemDraw's own Shapes template does
(it triples the first point of every curve). Without it the arrowhead barbs
round off into a blob.

### 2026-08-29 — PDF export ignores the document bounding box
ChemDraw always exports PDF at the **print page size** (A4/Letter), regardless of
the CDXML `<page BoundingBox>`. EPS is fine — it carries a correct tight
`%%BoundingBox`. **Fix:** `crop_pdf.py` re-places the artwork on a page of
exactly its own size using PyMuPDF. Setting only `/CropBox` is not enough; some
renderers honour just `/MediaBox`, so the page is rebuilt rather than annotated.
Filter out the full-page background rectangle or the "tight" box is the whole
page again.

### 2026-08-29 — Driving ChemDraw: six AppleScript rules
Collected in `mcp/chemdraw/chemdraw_bridge.py`; the two that waste the most time:
- **Property reads fail on the first Apple event after a document opens**, then
  succeed. And batching `SMILES` + `Molecular Weight` into one `tell` reliably
  returns `-10000`. One event per property, wrapped in a retry.
- **Numbers come back locale-formatted** — under a French locale the molecular
  weight of aspirin is `180,159`. Guessing which separator is the decimal one
  is not good enough; see the 2026-08-30 entry on probing them.

Also: address the app by **bundle ID** (`com.revvity.ChemDraw`), because the app
*name* carries its version and breaks at the next upgrade; and never reach for
**System Events**, which blocks on an Accessibility prompt and hangs the call.

### 2026-08-29 — ACS numbers come from ChemDraw, not from memory
`ACS Document 1996.cds` ships inside the app
(`Contents/Resources/SpecialPurpose/Stationery/`). Opening it and reading the
document properties gives the authoritative house style — 14.4 pt bonds, 0.6 pt
lines, 2.0 pt bold, 1.6 pt margin, 2.5 pt hash, 18 % bond spacing, Arial 10 pt.
Two unit systems: AppleScript properties are in **1440ths of an inch** (font
sizes in 20ths of a point), CDXML root attributes are in **points**.
`acs_style.py` carries both.

### 2026-08-29 — Structure→Name needs a licence tier
`convertStructureToName` / `convertNameToStructure` exist in the AppleScript
dictionary but report `enabled: false` even with a structure selected and the
canvas ready — they belong to a ChemDraw Professional add-on. Not a scripting
bug; no tool was shipped for them. `run_command` explains this if called.
