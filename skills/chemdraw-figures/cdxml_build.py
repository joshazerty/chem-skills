"""Minimal CDXML authoring toolkit -- structures, arrows and text on a page.

Coordinates are in POINTS, with y increasing DOWNWARD (ChemDraw screen space).
Helpers here take standard maths angles (y up) and flip internally, so callers
can think in ordinary degrees.
"""
import math, re, sys, os

# acs_style sits beside this file when bundled into the skill, and one level up
# in the connector checkout -- add both so either layout imports cleanly.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import acs_style as A

BL = 14.4                      # ACS bond length, points

def pol(r, deg):
    """Polar -> (dx, dy) in screen space (y down)."""
    a = math.radians(deg)
    return r * math.cos(a), -r * math.sin(a)


class Frag:
    """One connected structure, built in local coordinates around (0, 0)."""

    def __init__(self):
        self.atoms = []          # (x, y, element, charge, nH, label)
        self.bonds = []          # (i, j, order, display)

    def atom(self, x, y, el="C", charge=0, nH=None, label=None):
        self.atoms.append((x, y, el, charge, nH, label))
        return len(self.atoms) - 1

    def at(self, origin, r, deg, el="C", charge=0, nH=None, label=None):
        """Add an atom r/deg away from an existing atom index."""
        ox, oy = self.atoms[origin][0], self.atoms[origin][1]
        dx, dy = pol(r, deg)
        return self.atom(ox + dx, oy + dy, el, charge, nH, label)

    def bond(self, i, j, order=1, display=None):
        self.bonds.append((i, j, order, display))

    # atomic numbers for the elements this toolkit draws; extend as needed
    Z = {"H": 1, "B": 5, "C": 6, "N": 7, "O": 8, "F": 9, "Si": 14, "P": 15,
         "S": 16, "Cl": 17, "Br": 35, "I": 53, "Ru": 44, "Re": 75, "Ir": 77,
         "Pt": 78, "Au": 79, "Mo": 42, "W": 74, "V": 23, "Mn": 25, "Fe": 26}

    def xml(self, ox, oy, ids):
        Z = self.Z
        out = [f'<fragment id="{ids()}">']
        idx = {}
        for k, (x, y, el, ch, nH, label) in enumerate(self.atoms):
            if el not in Z:
                raise KeyError(f"{el!r} is not in Frag.Z; add its atomic number")
            i = ids(); idx[k] = i
            a = [f'id="{i}"', f'p="{ox + x:.2f} {oy + y:.2f}"',
                 f'Element="{Z[el]}"']
            if ch:
                a.append(f'Charge="{ch}"')
            if nH is not None:
                a.append(f'NumHydrogens="{nH}"')
            if label is None:
                out.append("<n " + " ".join(a) + "/>")
            else:
                # An explicit <t> child overrides the drawn label while the
                # node keeps its real Element/Charge, so the file stays
                # chemically correct. This is how the circled charge glyph
                # (U+2296) gets drawn -- ChemDraw has no document setting for
                # circled charges, only a manual symbol tool.
                al = "Right" if label.startswith("\u2296") else "Left"
                out.append(
                    "<n " + " ".join(a) + ">"
                    f'<t p="{ox + x:.2f} {oy + y:.2f}" LabelAlignment="{al}">'
                    f'<s font="{A.ARIAL}" size="10" face="{A.PLAIN}">{label}</s>'
                    "</t></n>")
        for (i, j, order, disp) in self.bonds:
            a = [f'id="{ids()}"', f'B="{idx[i]}"', f'E="{idx[j]}"']
            if order != 1:
                a.append(f'Order="{order}"')
            if disp:
                a.append(f'Display="{disp}"')
            out.append("<b " + " ".join(a) + "/>")
        out.append("</fragment>")
        return "\n".join(out)


class Page:
    def __init__(self, w=540, h=720):
        self.w, self.h, self.items, self._n = w, h, [], 10

    def ids(self):
        self._n += 1
        return self._n

    def add(self, frag, x, y):
        self.items.append(frag.xml(x, y, self.ids))

    def text(self, x, y, s, face=A.PLAIN, size=10, color=A.BLACK,
             just="Center", font=A.ARIAL):
        esc = (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        self.items.append(
            f'<t id="{self.ids()}" p="{x:.2f} {y:.2f}" '
            f'Justification="{just}" LineHeight="auto">'
            f'<s font="{font}" size="{size}" face="{face}" color="{color}">'
            f'{esc}</s></t>')

    def runs(self, x, y, parts, just="Center", font=A.ARIAL, size=10,
             color=A.BLACK):
        """Text made of (string, face) runs -- e.g. H<sub>2</sub>O."""
        out = []
        for txt, face in parts:
            esc = (txt.replace("&", "&amp;").replace("<", "&lt;")
                      .replace(">", "&gt;"))
            out.append(f'<s font="{font}" size="{size}" face="{face}" '
                       f'color="{color}">{esc}</s>')
        self.items.append(
            f'<t id="{self.ids()}" p="{x:.2f} {y:.2f}" '
            f'Justification="{just}" LineHeight="auto">' + "".join(out) + '</t>')

    def straight_arrow(self, x1, y1, x2, y2, head=2000, color=A.BLACK):
        """A plain straight reaction arrow.

        Only the attributes verified to actually render are emitted.
        `ArcAngle` is deliberately absent: ChemDraw's CDXML importer ignores it
        (and every other arc hint on <arrow>), so an <arrow> is always a
        straight chord -- see curved_arrow for how curves are really done.
        """
        self.items.append(
            f'<arrow id="{self.ids()}" '
            f'BoundingBox="{min(x1,x2):.2f} {min(y1,y2):.2f} '
            f'{max(x1,x2):.2f} {max(y1,y2):.2f}" '
            f'Z="5" color="{color}" FillType="None" '
            f'ArrowheadType="Solid" ArrowheadHead="Full" '
            f'HeadSize="{head}" ArrowheadCenterSize="{int(head*0.875)}" '
            f'ArrowheadWidth="{int(head*0.25)}" '
            f'Tail3D="{x1:.2f} {y1:.2f} 0" Head3D="{x2:.2f} {y2:.2f} 0"/>')

    # ---- curved arrows -------------------------------------------------
    # ChemDraw's CDXML import ignores arc geometry on <arrow> (verified: even
    # ChemDraw's own template arrows re-import as straight chords). Open
    # <curve> elements refuse to stroke at all. What DOES render reliably is a
    # CLOSED, FILLED curve: CurveType=129 (128 filled | 1 closed). So a curved
    # arrow is drawn here as one filled outline -- shaft plus head.

    def curved_arrow(self, cx, cy, r, a1, a2, shaft=1.1, head_len=14.0,
                     head_w=9.0, color="3", step=2.0):
        """A tapered curved arrow following arc `r`, from angle a1 to a2.

        The outline is sampled densely rather than expressed as Bezier control
        triples: ChemDraw smooths *through* CurvePoints, so dense samples of the
        true outline reproduce the intended shape, while sparse control points
        bulge the fill.
        """
        sign = 1.0 if a2 > a1 else -1.0
        head_deg = math.degrees(head_len / r) * sign
        a_base = a2 - head_deg
        pt = lambda rr, deg: (cx + rr * math.cos(math.radians(deg)),
                              cy - rr * math.sin(math.radians(deg)))

        def sweep(rr, f, t):
            n = max(2, int(abs(t - f) / step) + 1)
            return [pt(rr, f + (t - f) * i / n) for i in range(n + 1)]

        # ChemDraw smooths THROUGH CurvePoints, so a corner has to be forced by
        # repeating its point -- the same trick ChemDraw's own Shapes template
        # uses (it triples the first point of every curve). Without this the
        # arrowhead barbs round off into a blob.
        C = lambda q: [q, q, q]
        pts  = C(pt(r + shaft / 2, a1))                  # square tail, outer
        pts += sweep(r + shaft / 2, a1, a_base)          # outer edge of shaft
        pts += C(pt(r + head_w / 2, a_base))             # barb
        pts += C(pt(r, a2))                              # tip
        pts += C(pt(r - head_w / 2, a_base))             # barb
        pts += sweep(r - shaft / 2, a_base, a1)          # inner edge, back
        pts += C(pt(r - shaft / 2, a1))                  # square tail, inner
        coords = " ".join(f"{x:.2f} {y:.2f}" for x, y in pts)
        self.items.append(
            f'<curve id="{self.ids()}" Z="4" color="{color}" '
            f'CurveType="129" Closed="yes" FillType="Solid" '
            f'CurvePoints="{coords}"/>')

    def fit(self, margin=18.0):
        """Shrink the page to the artwork and shift the artwork onto it.

        ChemDraw exports PDF/EPS at full page size, so a figure destined for a
        journal has to carry its own tight page box. Text is anchored at its
        centre, so its drawn width is estimated rather than known exactly --
        hence the margin.
        """
        xs, ys = [], []

        def note(x, y, half_w=0.0, half_h=0.0):
            xs.extend((x - half_w, x + half_w))
            ys.extend((y - half_h, y + half_h))

        for it in self.items:
            for mx, my in re.findall(r'p="(-?[\d.]+) (-?[\d.]+)"', it):
                if it.startswith("<t"):
                    txt = re.sub(r"<[^>]+>", "", it)
                    size = float((re.search(r'size="([\d.]+)"', it)
                                  or [None, "10"])[1])
                    note(float(mx), float(my),
                         0.30 * size * max(len(txt), 1), size)
                else:
                    note(float(mx), float(my), 7.0, 6.0)
            cp = re.search(r'CurvePoints="([^"]+)"', it)
            if cp:
                v = [float(t) for t in cp.group(1).split()]
                for i in range(0, len(v) - 1, 2):
                    note(v[i], v[i + 1])

        dx, dy = margin - min(xs), margin - min(ys)
        self.w = (max(xs) - min(xs)) + 2 * margin
        self.h = (max(ys) - min(ys)) + 2 * margin

        def shift_p(m):
            return f'p="{float(m.group(1)) + dx:.2f} {float(m.group(2)) + dy:.2f}"'

        def shift_curve(m):
            v = [float(t) for t in m.group(1).split()]
            out = " ".join(f"{v[i] + dx:.2f} {v[i+1] + dy:.2f}"
                           for i in range(0, len(v) - 1, 2))
            return f'CurvePoints="{out}"'

        self.items = [
            re.sub(r'CurvePoints="([^"]+)"', shift_curve,
                   re.sub(r'p="(-?[\d.]+) (-?[\d.]+)"', shift_p, it))
            for it in self.items]
        return self

    def xml(self):
        body = "\n".join(self.items)
        return (A.cdxml_header()
                + f'<page id="2" BoundingBox="0 0 {self.w} {self.h}" '
                  f'HeightPages="1" WidthPages="1">\n{body}\n</page>\n</CDXML>\n')

    def write(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.xml())
        return path
