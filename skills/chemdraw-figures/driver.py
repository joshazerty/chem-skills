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
import xml.etree.ElementTree as ET

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
def _chemdraw_app():
    import glob
    apps = sorted(glob.glob("/Applications/ChemDraw*.app"))
    return apps[-1] if apps else None


def doctor():
    print("chemdraw-figures — prerequisites\n")

    app = _chemdraw_app()
    _report(bool(app), "ChemDraw installed", app or "no /Applications/ChemDraw*.app")

    uv = next((p for p in ("/opt/homebrew/bin/uv", "/usr/local/bin/uv")
               if os.path.exists(p)), None)
    _report(bool(uv), "uv available", uv or "needed to launch the MCP server")

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
        r = subprocess.run(
            ["osascript", "-e",
             'tell application id "com.revvity.ChemDraw" to return version as text'],
            capture_output=True, text=True, timeout=90)
        ok = r.returncode == 0
        _report(ok, "Apple events reach ChemDraw",
                r.stdout.strip() if ok else
                "grant Automation → ChemDraw for your terminal/Claude app "
                "(System Settings → Privacy & Security → Automation)")

    # connector registration — either surface is fine
    import json
    reg = []
    cc = os.path.expanduser("~/.claude.json")
    if os.path.exists(cc):
        try:
            if "chemdraw" in (json.load(open(cc)).get("mcpServers") or {}):
                reg.append("Claude Code")
        except (ValueError, OSError):
            pass
    ext = os.path.expanduser(
        "~/Library/Application Support/Claude/extensions-installations.json")
    if os.path.exists(ext):
        try:
            names = json.load(open(ext)).get("extensions", {})
            if any("chemdraw" in k.lower() for k in names):
                reg.append("Claude Desktop")
        except (ValueError, OSError):
            pass
    _report(bool(reg), "MCP connector registered",
            ", ".join(reg) if reg else
            "see mcp/chemdraw/README.md — rendering needs it", warn=True)

    print("\n" + ("doctor: FAIL" if _fails else "doctor: PASS"))
    return 1 if _fails else 0


# --------------------------------------------------------------- selftest --
def selftest():
    print("chemdraw-figures — offline tests\n")
    import acs_style as A
    from cdxml_build import Page, Frag, BL

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
        ET.fromstring(xml.split("]>")[-1] if "]>" in xml else
                      xml.replace(xml[xml.index("<!DOCTYPE"):xml.index(">\n<CDXML") + 1], ""))
        wf = True
    except ET.ParseError as e:
        wf = False
        print("      parse error:", e)
    _report(wf, "CDXML is well-formed")

    # 5. curved arrows must be closed+filled or ChemDraw draws nothing at all
    _report('CurveType="129"' in xml and 'FillType="Solid"' in xml,
            "curved arrow encoding", "CurveType=129 (closed|filled)")

    # 6. corners are forced by repeating points; without it barbs round off
    import re as _re
    pts = _re.search(r'CurvePoints="([^"]+)"', xml).group(1).split()
    xy = list(zip(pts[0::2], pts[1::2]))
    _report(any(xy[i] == xy[i + 1] == xy[i + 2] for i in range(len(xy) - 2)),
            "arrowhead corners", "points tripled to survive smoothing")

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

    # --- connector trust boundary (the bridge imports without the mcp package) --
    sys.path.insert(0, os.path.join(HERE, "..", "..", "mcp", "chemdraw"))
    try:
        import chemdraw_bridge as cb
    except ImportError:
        cb = None
    if cb is None:
        _report(False, "connector importable", "mcp/chemdraw not found", warn=True)
    else:
        # 10. every interpolation into AppleScript must be escaped, or a quote
        #     in a path/command closes the literal and reaches do shell script
        evil = '/tmp/a" & (do shell script "echo pwned") & "'
        _report('"' not in cb.esc(evil).replace('\\"', ""),
                "AppleScript escaping", "quotes neutralised in esc()")

        # 11. do command takes a model-supplied name; only identifiers allowed
        good = all(cb.COMMAND_RE.match(c) for c in
                   ("selectAll", "cleanUpStructure", "chooseArrowTool_90_CW"))
        bad = any(cb.COMMAND_RE.match(c) for c in
                  ('x" & (do shell script "id") & "', "a; rm -rf /", "", "a b"))
        _report(good and not bad, "command-name validation",
                "identifiers accepted, injection refused")

        # 12. output names are filenames, not paths
        contained = True
        try:
            cb.safe_output_path("/tmp", "fine.png")
        except Exception:
            contained = False
        for esc_name in ("/etc/passwd_clone", "../../escaped", "../.ssh/x"):
            try:
                cb.safe_output_path("/tmp", esc_name)
                contained = False
            except cb.ChemDrawError:
                pass
        _report(contained, "output path containment",
                "absolute and ../ names refused")

    # 13. the worked example still builds seven fragments
    ex = os.path.join(HERE, "examples", "catalytic_cycle.py")
    r = subprocess.run([sys.executable, ex, "/tmp/_chemdraw_selftest.cdxml"],
                       capture_output=True, text=True, timeout=120)
    n = 0
    if r.returncode == 0:
        n = open("/tmp/_chemdraw_selftest.cdxml", encoding="utf-8").read().count("<fragment")
    _report(n == 7, "worked example builds", f"{n} fragments (expected 7)")

    print("\n" + ("selftest: FAIL" if _fails else "selftest: PASS"))
    return 1 if _fails else 0


# ------------------------------------------------------------------ build --
def build(out=None):
    """Render the worked example through ChemDraw. Needs the app + Automation."""
    out = out or os.path.expanduser("~/Documents/Claude/Inbox")
    os.makedirs(out, exist_ok=True)
    sys.path.insert(0, os.path.join(HERE, "..", "..", "mcp", "chemdraw"))
    import time
    import chemdraw_bridge as cb

    src = "/tmp/_chemdraw_build.cdxml"
    subprocess.run([sys.executable, os.path.join(HERE, "examples",
                                                 "catalytic_cycle.py"), src],
                   check=True, capture_output=True)
    stem = os.path.join(out, "DODH_catalytic_cycle")
    cb.close_all(); time.sleep(1)
    cb.tell("activate", timeout=150); time.sleep(1)
    cb.tell(f'open POSIX file "{src}"', timeout=200); time.sleep(3)
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
