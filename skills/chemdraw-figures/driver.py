#!/usr/bin/env python3
"""chemdraw-figures driver — setup check, offline tests, and figure build.

    python3 driver.py doctor      # PASS/FAIL per prerequisite
    python3 driver.py selftest    # offline tests; needs no ChemDraw
    python3 driver.py build [out] # render the worked example (needs ChemDraw)

`selftest` deliberately touches no chemistry software: every check below is a
property of the CDXML we generate, so the toolkit stays testable on a machine
with no ChemDraw licence.
"""
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import ast as ET_ast

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_fails = 0


def _report(ok, label, detail="", warn=False):
    global _fails
    tag = "WARN" if (warn and not ok) else ("PASS" if ok else "FAIL")
    if not ok and not warn:
        _fails += 1
    print(f"{tag:5} {label}" + (f"  — {detail}" if detail else ""))
    return ok


# ----------------------------------------------------------------- doctor --
def _find_uv():
    """Absolute path to uv: PATH first, then the usual install prefixes.

    Resolves the *directory* to a real path but keeps the final component as
    named, exactly as build.sh does. Following the last symlink would turn
    Homebrew's stable /opt/homebrew/bin/uv into the version-pinned
    /opt/homebrew/Cellar/uv/<ver>/bin/uv, which `brew upgrade uv` deletes --
    a path this function must never hand to a manifest.
    """
    import shutil
    found = shutil.which("uv")
    if found:
        return os.path.join(os.path.realpath(os.path.dirname(found)),
                            os.path.basename(found))
    for cand in ("/opt/homebrew/bin/uv", "/usr/local/bin/uv",
                 os.path.expanduser("~/.local/bin/uv"),
                 os.path.expanduser("~/.cargo/bin/uv")):
        if os.path.exists(cand):
            return cand
    return None


def _same_file(a, b):
    """True when both paths exist and hold identical bytes."""
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def _installed_server_commands():
    """(where, command) for every place this connector is already registered.

    doctor's job is to catch a registration the client cannot launch, so read
    the commands the clients will actually spawn -- the Claude Code entry and
    each installed Desktop extension manifest -- not the committed manifest,
    which holds a bare "uv" on purpose.
    """
    import json
    out = []
    cc = os.path.expanduser("~/.claude.json")
    if os.path.exists(cc):
        try:
            srv = (json.load(open(cc, encoding="utf-8")).get("mcpServers")
                   or {}).get("chemdraw")
            if srv and srv.get("command"):
                out.append(("Claude Code", srv["command"], srv.get("args", [])))
        except (ValueError, OSError):
            pass
    import glob
    for man in glob.glob(os.path.expanduser(
            "~/Library/Application Support/Claude/Claude Extensions/"
            "*chemdraw*/manifest.json")):
        try:
            cfg = (json.load(open(man, encoding="utf-8"))["server"]
                   ["mcp_config"])
        except (ValueError, OSError, KeyError):
            continue
        # ${__dirname} is the extension's own install directory.
        here = os.path.dirname(man)
        args = [a.replace("${__dirname}", here) for a in cfg.get("args", [])]
        out.append(("Claude Desktop", cfg.get("command"), args))
    return out


def _chemdraw_app():
    import glob
    apps = sorted(glob.glob("/Applications/ChemDraw*.app"))
    return apps[-1] if apps else None


def doctor():
    print("chemdraw-figures — prerequisites\n")

    app = _chemdraw_app()
    _report(bool(app), "ChemDraw installed", app or "no /Applications/ChemDraw*.app")

    # Clients spawn MCP servers without your shell's PATH, so the registration
    # has to name an absolute uv. Report the one build.sh will bake in.
    uv = _find_uv()
    _report(bool(uv), "uv available", uv or
            "needed to launch the MCP server (brew install uv)")

    try:
        from rdkit import Chem
        ok = Chem.HasChemDrawCDXSupport()
        _report(ok, "RDKit with CDXML support",
                "MolToCDXMLBlock is how structures reach ChemDraw")
    except ImportError:
        _report(False, "RDKit with CDXML support", "pip install rdkit")

    # The trap that costs the most time: Automation permission. A missing grant
    # surfaces as AppleEvent error -1743, not as a missing app.
    if app:
        try:
            r = subprocess.run(
                ["osascript", "-e", 'tell application id '
                 '"com.revvity.ChemDraw" to return version as text'],
                capture_output=True, text=True, timeout=90)
            ok, out = r.returncode == 0, r.stdout.strip()
        except (subprocess.TimeoutExpired, OSError) as e:
            ok, out = False, str(e)
        _report(ok, "Apple events reach ChemDraw",
                out if ok else
                "grant Automation → ChemDraw for your terminal/Claude app "
                "(System Settings → Privacy & Security → Automation)")

    # connector registration — either surface is fine
    installed = _installed_server_commands()
    _report(bool(installed), "MCP connector registered",
            ", ".join(sorted({w for w, _, _ in installed})) if installed else
            "see mcp/chemdraw/README.md — rendering needs it", warn=True)

    # A registration is only as good as the command the client will spawn, and
    # both halves rot independently: the interpreter can move (uv upgraded,
    # Homebrew prefix changed) and the server file can be a stale copy left
    # somewhere outside this repo. Name both so neither rots unnoticed.
    for where, cmd, args in installed:
        if cmd and os.path.isabs(cmd):
            _report(os.access(cmd, os.X_OK), f"{where} launches {cmd}",
                    "as registered" if os.access(cmd, os.X_OK) else
                    f"not executable — this machine has {uv}; re-run "
                    f"mcp/chemdraw/build.sh (Desktop) or re-add the server")
        served = next((a for a in args
                       if a.endswith("chemdraw_server.py")), None)
        if served:
            served = os.path.expanduser(served)
            mine = os.path.join(HERE, "..", "..", "mcp", "chemdraw",
                                "chemdraw_server.py")
            same = _same_file(served, mine)
            _report(same, f"{where} runs this repo's server",
                    served if same else
                    f"{served} differs from mcp/chemdraw/chemdraw_server.py — "
                    f"that client is running another copy", warn=True)

    print("\n" + ("doctor: FAIL" if _fails else "doctor: PASS"))
    return 1 if _fails else 0


# --------------------------------------------------------------- selftest --
def selftest():
    print("chemdraw-figures — offline tests\n")
    import re as _re
    import acs_style as A
    from cdxml_build import Page, Frag, BL
    sys.path.insert(0, os.path.join(HERE, "..", "..", "mcp", "chemdraw"))
    import chemdraw_bridge as cb

    def wellformed(xml):
        """Parse just the <page> subtree — the DOCTYPE names an external DTD."""
        return ET.fromstring(xml[xml.index("<page"):xml.rindex("</page>") + 7])

    # 1. ACS Document 1996 numbers, as read from ChemDraw's own stationery
    _report(A.ACS_CDXML["BondLength"] == "14.40" and
            A.ACS_CDXML["LineWidth"] == "0.60" and
            A.ACS_APPLESCRIPT["fixed length"] == 288,
            "ACS constants", "14.4 pt bonds, 0.6 pt lines")

    # 2. colour attribute is offset by 2 from the colortable index; "1" is white
    _report(A.BLACK == "3" and A.GREY == "10",
            "colour indices", "black=3, grey=10 (attr = entry + 2)")

    # 3. captions must not use formula face, which subscripts digits
    _report(A.PLAIN == "0" and A.FORMULA == "96",
            "caption face", "plain=0; 96 is formula mode and mangles 'TS1-2'")

    # 4. generated CDXML must be well-formed and carry the ACS header
    p = Page()
    f = Frag()
    re_ = f.atom(0, 0, "Re", nH=0)
    o = f.at(re_, BL, 225, "O", charge=-1, label="⊖O")
    f.bond(re_, o)
    p.add(f, 100, 100)
    p.text(100, 60, "TS1–2", color=A.BLACK)
    p.curved_arrow(100, 100, 60, 140, 40)
    xml = p.fit(margin=12).xml()
    try:
        wellformed(xml)
        wf = True
    except ET.ParseError as e:
        wf = False
        print("      parse error:", e)
    _report(wf, "CDXML is well-formed")

    # 5. curved arrows must be closed+filled or ChemDraw draws nothing at all
    _report('CurveType="129"' in xml and 'FillType="Solid"' in xml,
            "curved arrow encoding", "CurveType=129 (closed|filled)")

    # 6. corners are forced by repeating points; without it barbs round off
    pts = _re.search(r'CurvePoints="([^"]+)"', xml).group(1).split()
    xy = list(zip(pts[0::2], pts[1::2]))
    _report(any(xy[i] == xy[i + 1] == xy[i + 2] for i in range(len(xy) - 2)),
            "arrowhead corners", "points tripled to survive smoothing")

    # 6b. EVERY corner, both sides. The shaft-to-barb step is radial, so a
    #     lone sample at the shaft end lets the spline overshoot into the
    #     tripled barb -- the shaft swells and hooks just before the head.
    #     Invariant: wherever the outline steps in radius, both points either
    #     side of the step sit in a run of >= 3 identical points.
    import math as _m
    ap = Page(300, 300)
    ap.curved_arrow(150, 150, 90, 90, 20)
    apts = _re.search(r'CurvePoints="([^"]+)"', ap.items[-1]).group(1).split()
    axy = [(float(apts[i]), float(apts[i + 1]))
           for i in range(0, len(apts), 2)]
    runs = []                       # (index, length) of each repeated run
    i = 0
    while i < len(axy):
        j = i
        while j + 1 < len(axy) and axy[j + 1] == axy[i]:
            j += 1
        runs.append((i, j - i + 1))
        i = j + 1
    tripled = {k for start, n in runs if n >= 3 for k in range(start, start + n)}
    rad = [_m.hypot(x - 150, y - 150) for x, y in axy]
    loose = [i for i in range(len(axy) - 1)
             if abs(rad[i + 1] - rad[i]) > 1.0
             and not (i in tripled and i + 1 in tripled)]
    _report(not loose, "arrow corners pinned on both sides",
            f"{len(runs)} runs, every radial step tripled at both ends"
            if not loose else
            f"un-tripled radial step(s) at index {loose} — the shaft will "
            f"overshoot into the barb")

    # 7. nothing may be written in an invisible colour
    _report(not _re.search(r'color="[12]"', xml),
            "no invisible text", 'color="1"/"2" both render white')

    # 8. the circled charge keeps the node's real charge
    _report("⊖" in xml and 'Charge="-1"' in xml,
            "circled charge", "glyph drawn, formal charge preserved")

    # 9. fit() produces a tight page box
    box = _re.search(r'<page[^>]*BoundingBox="([^"]+)"', xml).group(1).split()
    w, h = float(box[2]) - float(box[0]), float(box[3]) - float(box[1])
    _report(0 < w < 400 and 0 < h < 400, "fit() tightens the page",
            f"{w:.0f} x {h:.0f} pt")

    # 10. fit() must move STRAIGHT arrows too. Their geometry is in Tail3D/
    #     Head3D, not p="", so a shift that only rewrites p="" leaves every
    #     reaction arrow behind — silently, and off the fitted page.
    q = Page()
    g = Frag()
    c0 = g.atom(0, 0, "C")
    g.bond(c0, g.at(c0, BL, 0, "O"), 2)
    q.add(g, 300, 300)
    q.straight_arrow(340, 300, 400, 300)
    q.fit(margin=18)
    arrow = next(i for i in q.items if i.startswith("<arrow"))
    atom = float(_re.search(r'p="(-?[\d.]+) ', q.items[0]).group(1))
    tail = float(_re.search(r'Tail3D="(-?[\d.]+) ', arrow).group(1))
    head = float(_re.search(r'Head3D="(-?[\d.]+) ', arrow).group(1))
    _report(abs((tail - atom) - 40.0) < 0.01 and head <= q.w,
            "fit() moves straight arrows",
            f"tail-atom gap {tail - atom:.1f} pt (40 before fit), head {head:.0f} "
            f"within page {q.w:.0f}")

    # 11. fit() on nothing must not raise
    try:
        Page().fit()
        empty_ok = True
    except Exception as e:                                  # noqa: BLE001
        empty_ok = False
        print("      ", type(e).__name__, e)
    _report(empty_ok, "fit() survives an empty page")

    # 12. atom labels need the same XML escaping captions get
    r = Page()
    h = Frag()
    a0 = h.atom(0, 0, "C")
    h.bond(a0, h.at(a0, BL, 0, "O", charge=-1, label="O&<test>"))
    r.add(h, 50, 50)
    try:
        wellformed(r.fit().xml())
        esc_ok = True
    except ET.ParseError:
        esc_ok = False
    _report(esc_ok, "labels are XML-escaped", "'&' in a label must not break the file")

    # 13. RDKit draws at its own scale; the body must be rescaled to ACS or the
    #     file claims a bond length it is not drawn in
    body = ('<page id="2" ><fragment id="3">'
            '<n id="4" p="0 0"/><n id="5" p="28.8 0"/><n id="6" p="28.8 28.8"/>'
            '<b id="7" B="4" E="5"/><b id="8" B="5" E="6"/>'
            '</fragment></page>')
    scaled = A.rescale_to_bond_length(body)
    _report(abs(A.median_bond_length(scaled) - 14.4) < 0.01 and
            "BoundingBox" in scaled and 'p="-' not in scaled,
            "RDKit body rescaled to ACS",
            f"{A.median_bond_length(body):.1f} -> "
            f"{A.median_bond_length(scaled):.2f} pt, page boxed, no negative coords")

    # 14. locale-formatted numbers. The separators are PROBED on macOS; given
    #     them, "1,234" is 1234 under en_US and 1.234 under fr_FR — guessing
    #     silently scales every mass over 1000 by a factor of 1000.
    cases = [
        ("180,159", ",", "\u00a0", 180.159),      # fr_FR aspirin
        ("1\u00a0234,56", ",", "\u00a0", 1234.56),  # fr_FR, NBSP grouping
        ("1,234", ".", ",", 1234.0),             # en_US grouping, NOT 1.234
        ("1,234.56", ".", ",", 1234.56),
        ("2 000,5", ",", " ", 2000.5),
        ("-3,5", ",", "", -3.5),
    ]
    bad = [(t, cb.parse_num(t, d, g), want)
           for t, d, g, want in cases if abs(cb.parse_num(t, d, g) - want) > 1e-9]
    _report(not bad, "locale numbers parsed with probed separators",
            "6 forms incl. NBSP grouping" if not bad else str(bad))

    # 15. every value reaching AppleScript must be escaped, or a quote in a
    #     path or command name closes the literal and the rest runs as script
    q_ok = cb._as(chr(34).join(("a", "b"))) == chr(34) + "a" + chr(92) + chr(34) + "b" + chr(34)
    b_ok = cb._as("a" + chr(92) + "b") == chr(34) + "a" + chr(92) * 2 + "b" + chr(34)
    inj = "x" + chr(34) + " & (do shell script " + chr(34) + "echo pwned"
    _report(q_ok and b_ok and
            _raises(cb.ChemDrawError, cb.do_command, inj) and
            _raises(cb.ChemDrawError, cb._as, "line1" + chr(10) + "line2"),
            "AppleScript values are escaped",
            "quotes and backslashes escaped, command names validated, "
            "control characters refused")

    # 16. and the worked example still builds — seven fragments, and a cycle
    #     whose every step balances
    ex = os.path.join(HERE, "examples", "catalytic_cycle.py")
    with tempfile.TemporaryDirectory() as td:
        dest = os.path.join(td, "cycle.cdxml")
        r = subprocess.run([sys.executable, ex, dest],
                           capture_output=True, text=True, timeout=120)
        n = 0
        if r.returncode == 0:
            n = open(dest, encoding="utf-8").read().count("<fragment")
        elif r.stderr:
            print("      ", r.stderr.strip().splitlines()[-1])
    _report(n == 7, "worked example builds", f"{n} fragments (expected 7)")

    sys.path.insert(0, os.path.join(HERE, "examples"))
    import catalytic_cycle
    unbalanced = catalytic_cycle.check_balance()
    _report(not unbalanced, "worked cycle balances",
            "every step conserves atoms and charge" if not unbalanced
            else "; ".join(f"{ts} off by {d}" for ts, d in unbalanced))

    # 18. the bundle manifest: exactly ONE version key (manifest_version and
    #     its deprecated alias dxt_version are alternates pinned to the SAME
    #     value by the mcpb schema, so setting both is invalid, not tolerant),
    #     and a tool list that has not drifted from the server's decorators
    import json as _json
    mf = os.path.join(HERE, "..", "..", "mcp", "chemdraw", "manifest.json")
    sv = os.path.join(HERE, "..", "..", "mcp", "chemdraw", "chemdraw_server.py")
    man = _json.load(open(mf, encoding="utf-8"))
    keys = [k for k in ("manifest_version", "dxt_version") if k in man]
    declared = [t["name"] for t in man.get("tools", [])]
    tree = ET_ast.parse(open(sv, encoding="utf-8").read())
    actual = [n.name for n in ET_ast.walk(tree)
              if isinstance(n, ET_ast.FunctionDef)
              and any(getattr(getattr(d, "func", d), "attr", "") == "tool"
                      for d in n.decorator_list)]
    _report(keys == ["manifest_version"] and declared == actual,
            "manifest matches the server",
            f"{len(actual)} tools, one version key ({', '.join(keys)})"
            if keys == ["manifest_version"] and declared == actual else
            f"version keys {keys}; declared {declared} vs actual {actual}")

    # 19. AppleScript switches to scientific notation at |x| >= 10000, and
    #     still uses the locale decimal separator inside the mantissa: on an
    #     en_BE Mac a 14 kDa molecular weight arrives as "1,4029016E+4". The
    #     exponent must survive parse_num's separator rewriting -- stripping
    #     "every non-digit but the decimal point" would read it as 1.4e7.
    sci = [(("1,4029016E+4", ",", ""), 14029.016),
           (("1.4029016E+4", ".", ","), 14029.016),
           (("1,401766571406E+4", ",", ""), 14017.66571406),
           (("-2,5E-3", ",", ""), -0.0025)]
    bad = [f"{t[0]}->{cb.parse_num(*t)} != {want}" for t, want in sci
           if abs(cb.parse_num(*t) - want) > 1e-9]
    _report(not bad, "locale scientific notation parsed",
            "mantissa separator rewritten, exponent left alone"
            if not bad else "; ".join(bad))

    # 20. _find_uv() must name uv the way build.sh does -- resolving the
    #     directory but NOT the final symlink. Following it turns Homebrew's
    #     stable /opt/homebrew/bin/uv into /opt/homebrew/Cellar/uv/<ver>/bin/uv,
    #     which the next `brew upgrade uv` deletes, and doctor would then be
    #     reporting a path no manifest should ever be given.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        real, binp = os.path.join(td, "real"), os.path.join(td, "bin")
        os.makedirs(real), os.makedirs(binp)
        target = os.path.join(real, "uv")
        open(target, "w").close()
        os.chmod(target, 0o755)
        link = os.path.join(binp, "uv")
        os.symlink(target, link)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = binp
        try:
            got = _find_uv()
        finally:
            os.environ["PATH"] = old_path
        want = os.path.join(os.path.realpath(binp), "uv")
        _report(got == want, "uv path keeps the stable symlink",
                got if got == want else
                f"{got} — followed the symlink; wanted {want}")

    print("\n" + ("selftest: FAIL" if _fails else "selftest: PASS"))
    return 1 if _fails else 0


def _raises(exc, fn, *a):
    try:
        fn(*a)
        return False
    except exc:
        return True


# ------------------------------------------------------------------ build --
def build(out=None):
    """Render the worked example through ChemDraw. Needs the app + Automation."""
    out = out or os.path.expanduser("~/Documents/Claude/Inbox")
    os.makedirs(out, exist_ok=True)
    sys.path.insert(0, os.path.join(HERE, "..", "..", "mcp", "chemdraw"))
    import chemdraw_bridge as cb

    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "cycle.cdxml")
        r = subprocess.run([sys.executable,
                            os.path.join(HERE, "examples", "catalytic_cycle.py"),
                            src], capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr.strip() or "example failed to build", file=sys.stderr)
            return 1
        stem = os.path.join(out, "DODH_catalytic_cycle")
        cb.close_all()
        # Rule 2 lives in the bridge -- open_doc activates first and wait_ready
        # polls the canvas, which is what a fixed sleep was only approximating.
        cb.open_doc(src)
        if not cb.wait_ready():
            print("ChemDraw did not become ready", file=sys.stderr)
            return 1
        for fmt in ("pdf", "eps", "tiff", "png", "cdx"):
            print(f"  {fmt:5} {cb.save_as(stem, fmt)}")
        import shutil
        shutil.copy(src, stem + ".cdxml")
    print(f"  cdxml {stem}.cdxml")
    print("\nPDF is exported at the print page size — tighten it with:")
    print(f"  uv run --with pymupdf python {HERE}/crop_pdf.py {stem}.pdf")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "doctor"
    if cmd == "doctor":
        sys.exit(doctor())
    if cmd == "selftest":
        sys.exit(selftest())
    if cmd == "build":
        sys.exit(build(sys.argv[2] if len(sys.argv) > 2 else None))
    print(__doc__)
    sys.exit(2)
