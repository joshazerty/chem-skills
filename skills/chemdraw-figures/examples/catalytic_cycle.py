"""Worked example -- Re-catalysed deoxydehydration (DODH) catalytic cycle.

Seven rhenium species around one cycle, ACS Document 1996 house style.
Run:  python3 examples/catalytic_cycle.py <out.cdxml>
"""
import sys, os, math, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cdxml_build import Page, Frag, BL, pol, parse_formula
import acs_style as A

BLACK, GREY = A.BLACK, A.GREY

# ---------------------------------------------------------------- geometry --
# R is the single knob for overall size: the layout scales cleanly because the
# label ring is expressed as a fraction of it, while bond length stays fixed at
# the ACS 14.4 pt. R=132 fits the artwork into ~5.7 in, comfortably inside ACS's
# 7.00 in double-column limit; R=167 would fill that column exactly.
CX, CY, R = 306.0, 400.0, 132.0
N = 7
LABEL_R = 0.58                          # TS-label radius, as a fraction of R
ANG = [90.0 - i * 360.0 / N for i in range(N)]   # clockwise from 12 o'clock


MINUS = "⊖"          # CIRCLED MINUS -- far easier to spot than a bare "-"


def charge_label(deg):
    """"O(-)" with the circled minus on the side facing away from the bond."""
    return MINUS + "O" if 90 < deg % 360 < 270 else "O" + MINUS


def re_with(subs):
    """A rhenium centre carrying (angle, order, charge, nH) substituents."""
    f = Frag()
    re = f.atom(0, 0, "Re", nH=0)
    for deg, order, ch, nH in subs:
        lab = charge_label(deg) if ch == -1 else None
        o = f.at(re, BL, deg, "O", charge=ch, nH=nH, label=lab)
        f.bond(re, o, order)
    return f, re


def diolate_ring(f, re):
    """Fuse the 3-butene-1,2-diolate ring onto an existing Re centre.

    Five-membered Re-O-CH2-CH(vinyl)-O ring, drawn as a regular pentagon with
    Re on the right-hand vertex, vinyl pointing away to the lower left.
    """
    rr = BL / (2 * math.sin(math.radians(36)))       # pentagon circumradius
    rx, ry = f.atoms[re][0] - rr, f.atoms[re][1]     # ring centre
    def v(deg):
        dx, dy = pol(rr, deg)
        return rx + dx, ry + dy
    oa = f.atom(*v(72),  "O")      # O bonded to CH2
    ca = f.atom(*v(144), "C")      # CH2
    cb = f.atom(*v(216), "C")      # CH bearing the vinyl
    ob = f.atom(*v(288), "O")      # O bonded to CH
    f.bond(re, oa); f.bond(oa, ca); f.bond(ca, cb); f.bond(cb, ob); f.bond(ob, re)
    v1 = f.at(cb, BL, 190); f.bond(cb, v1)
    v2 = f.at(v1, BL, 250); f.bond(v1, v2, 2)
    return f


# ------------------------------------------------------------- structures --
def s1():                                   # ReO4(-)  perrhenate
    f, _ = re_with([(135, 2, 0, None), (45, 2, 0, None),
                    (315, 2, 0, None), (225, 1, -1, 0)])
    return f

def s2():                                   # iPrO-ReO2(OH)O(-)
    f, re = re_with([(50, 1, 0, 1), (350, 2, 0, None),
                     (295, 2, 0, None), (235, 1, -1, 0)])
    ol = f.at(re, BL, 145, "O"); f.bond(re, ol)
    ch = f.at(ol, BL, 205, "C"); f.bond(ol, ch)
    m1 = f.at(ch, BL, 145, "C"); f.bond(ch, m1)
    m2 = f.at(ch, BL, 265, "C"); f.bond(ch, m2)
    return f

def s3():                                   # HReO2(OH)O(-)
    # Two oxo, not one: 2 - acetone is H2ReO4(-), and it is H2ReO4(-) - H2O
    # that gives ReO3(-) (species 4). With a single oxo the cycle loses an
    # oxygen at TS2-3 and gains it back at TS3-4.
    f, re = re_with([(180, 1, 0, 1), (90, 2, 0, None), (30, 2, 0, None),
                     (330, 1, -1, 0)])
    h = f.at(re, BL, 250, "H"); f.bond(re, h)
    return f

def s4():                                   # ReO3(-)
    f, _ = re_with([(135, 2, 0, None), (45, 2, 0, None), (270, 1, -1, 0)])
    return f

def s5():                                   # HOCH2-CH(vinyl)-O-ReO(OH)O(-)
    f, re = re_with([(60, 1, 0, 1), (0, 2, 0, None), (300, 1, -1, 0)])
    ol = f.at(re, BL, 180, "O"); f.bond(re, ol)
    c2 = f.at(ol, BL, 120, "C"); f.bond(ol, c2)      # carbinol CH
    c1 = f.at(c2, BL, 180, "C"); f.bond(c2, c1)      # CH2
    oh = f.at(c1, BL, 120, "O", nH=1); f.bond(c1, oh)
    v1 = f.at(c2, BL, 240, "C"); f.bond(c2, v1)      # vinyl, swung outward:
    v2 = f.at(v1, BL, 300, "C"); f.bond(v1, v2, 2)   # keeps it off the arrow
    return f

def s6():                                   # diolate + Re(OH)2 O(-)
    f, re = re_with([(55, 1, 0, 1), (0, 1, 0, 1), (305, 1, -1, 0)])
    return diolate_ring(f, re)

def s7():                                   # diolate + ReO O(-)
    # One oxo, not two. 6 -> 7 is a condensation: two Re-OH lose ONE water and
    # leave ONE oxo (two oxo would be a loss of H2, not of H2O). It is also the
    # Re(V) diolate that does the retro-[3+2]: extruding butadiene turns the
    # two diolate oxygens into oxo ligands, giving back Re(VII) ReO4(-).
    f, re = re_with([(55, 2, 0, None), (305, 1, -1, 0)])
    return diolate_ring(f, re)


# ------------------------------------------------------------------ layout --
# (builder, label, dx, dy, label_dx, label_dy) -- dx/dy nudge the rhenium off
# its anchor; label_* place the bold numeral clear of that structure's own
# extent, which differs a lot between a bare ReO4- and a diolate with a vinyl.
SPECIES = [
    (s1, "1",   0,   0,    0, -42),
    (s2, "2",  16,   0,   48, -28),
    (s3, "3",  12,   0,   40,   8),
    (s4, "4",   0,   6,   14,  50),
    (s5, "5", -12,  10,    4,  54),
    (s6, "6", -16,   0,  -60,   6),
    (s7, "7", -16,  -6,  -44, -38),
]

# steps: (TS label, reagent line, gained, lost)
# `gained`/`lost` are the formulas that make each step balance. They are not
# decoration: check_balance() below asserts that species[i] + gained - lost is
# exactly species[i+1], and driver.py selftest runs it. A cycle that silently
# drops an oxygen is the one drawing error a reader will always catch.
H2O = [("– H", A.ITALIC), ("2", A.SUB), ("O", A.ITALIC)]

STEPS = [
    ("TS1–2",  "+ iPrOH",   "C3H8O",  None),
    ("TS2–3",  "– acetone", None,     "C3H6O"),
    ("TS3–4",  H2O,         None,     "H2O"),
    ("TS4–5",  "+ diol",    "C4H8O2", None),
    ("TS5–6",  None,        None,     None),
    ("TS6–7",  H2O,         None,     "H2O"),
    ("TS7–1",  "– diene",   None,     "C4H6"),
]


def check_balance():
    """[(step label, imbalance)] for every step that does not balance; [] if all do."""
    counts, charges = [], []
    for fn, *_ in SPECIES:
        n, q = fn().formula()
        counts.append(n)
        charges.append(q)
    bad = []
    for i, (ts, _reagent, gained, lost) in enumerate(STEPS):
        lhs = collections.Counter(counts[i])
        lhs.update(parse_formula(gained))
        lhs.subtract(parse_formula(lost))
        rhs = counts[(i + 1) % len(SPECIES)]
        diff = collections.Counter(lhs)
        diff.subtract(rhs)
        diff = {k: v for k, v in diff.items() if v}
        if charges[i] != charges[(i + 1) % len(SPECIES)]:
            diff["charge"] = charges[i] - charges[(i + 1) % len(SPECIES)]
        if diff:
            bad.append((ts, diff))
    return bad


def build():
    p = Page(612, 792)
    for i, (fn, lab, dx, dy, lx, ly) in enumerate(SPECIES):
        ax, ay = pol(R, ANG[i]); ax += CX + dx; ay += CY + dy
        p.add(fn(), ax, ay)
        p.text(ax + lx, ay + ly, lab, face=A.BOLD, size=11, color=BLACK)

    gap = 13.0
    for i, (ts, reagent, _gained, _lost) in enumerate(STEPS):
        a1 = ANG[i] - gap
        a2 = ANG[i] - 360.0 / N + gap
        p.curved_arrow(CX, CY, R - 4, a1, a2, color=BLACK)
        mid = (a1 + a2) / 2.0
        tx, ty = pol(R * LABEL_R, mid)
        p.text(CX + tx, CY + ty, ts, size=10, color=BLACK)
        if isinstance(reagent, list):
            p.runs(CX + tx, CY + ty + 14, reagent, size=9.5, color=GREY)
        elif reagent:
            p.text(CX + tx, CY + ty + 14, reagent, face=A.ITALIC,
                   size=9.5, color=GREY)
    return p.fit(margin=11)


if __name__ == "__main__":
    bad = check_balance()
    if bad:
        for ts, diff in bad:
            print(f"UNBALANCED {ts}: {diff}", file=sys.stderr)
        sys.exit(1)
    out = sys.argv[1] if len(sys.argv) > 1 else "cycle_a.cdxml"
    print(build().write(out))
