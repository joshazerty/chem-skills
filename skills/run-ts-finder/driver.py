#!/usr/bin/env python3
"""
run-ts-finder driver — orchestrate and *verify* transition-state location.

Pipeline:  endpoints --(g-xTB + GSM)--> tsq.xyz --(Gaussian|ORCA)--> saddle
           --> verify cascade (NImag=1, imaginary mode looks right, IRC,
               endpoint opts land on the intended reactant/product).

This is agent tooling, not product surface. It reuses the gautools package
(parsers, route, template) and the GSM infra under ~/dodh/ts/_gsm_infra.

Subcommands (run `driver.py <cmd> -h`):
  setup-gxtb   verify the active g-xTB build gives ANALYTIC gradients
  gsm          build a GSM workdir from two endpoints, run it, parse + plot
  plot-profile re-plot the V_profile PNG from an existing gsm.out
  refine       turn tsq.xyz into a Gaussian/ORCA TS-opt input (+ optional submit)
  verify       NImag check + imaginary-mode analysis on a QM freq log
  irc          generate an IRC (Both) input from a converged TS log (gautools)
  endpoints    generate endpoint Opt+Freq inputs from an IRC log (gautools)
  status       progress table for a directory of logs (gautools + GSM state)
  record       append a discovered workaround to SKILL.md AND LEARNINGS.md
  escalate     emit a GSM-coupled-to-QM PBS job (scaffolded; queue-only)
"""
from __future__ import annotations
import argparse, os, re, shutil, subprocess, sys, tempfile, datetime
from pathlib import Path

# ----- locations (override via config.json / env) ----------------------------
SKILL_DIR   = Path(__file__).resolve().parent
GSM_INFRA   = Path(os.environ.get("GSM_INFRA",  "/home/joshua/dodh/ts/_gsm_infra"))
GXTB        = Path(os.environ.get("GXTB",        "/home/joshua/bins/gxtb/xtb-6.7.1/bin/xtb"))
MKL_LIB     = "/opt/intel/oneapi/intelpython/python3.12/lib"
SUBGAU16    = "subgau16"                       # on PATH in login shells
SUBORC6     = "/home/janko/Scripts/suborc6"

# DODH defaults — every one of these is overridable on the CLI / via config.
DEF_CHARGE, DEF_MULT = -1, 1
DEF_GAU_ROUTE = ("#p B3LYP/gen pseudo=read EmpiricalDispersion=GD3BJ Int=UltraFine "
                 "SCF=(MaxCycle=512,XQC) SCRF=(PCM,Solvent=1-Pentanol) "
                 "Opt=(TS,CalcFC,NoEigen,MaxCycles=200) Freq")

C = dict(g="\033[32m", r="\033[31m", y="\033[33m", b="\033[1m", x="\033[0m")
def ok(m):   print(f"{C['g']}PASS{C['x']} {m}")
def bad(m):  print(f"{C['r']}FAIL{C['x']} {m}")
def warn(m): print(f"{C['y']}WARN{C['x']} {m}")
def info(m): print(f"  {m}")

# gautools is installed editable; import its real parsers rather than reinvent.
import gautools.parsers.log as glog
import gautools.parsers.xyz as gxyz
import gautools.parsers.inp as ginp
import gautools.route as groute

GSM_ENV = {**os.environ,
           "LD_LIBRARY_PATH": MKL_LIB + ":" + os.environ.get("LD_LIBRARY_PATH", ""),
           "PATH": str(GSM_INFRA) + ":" + os.environ.get("PATH", "")}

# =============================================================================
# setup-gxtb : prove the active build computes ANALYTIC gradients.
# The v2.0.1 asset is still labelled "xtb 6.7.1", so --version cannot confirm
# the update. We test behaviorally: a --gxtb --grad on a tiny molecule runs in
# ~1 SCF and prints analytic gradient *components* (no numerical displacement
# loop). A v1.x numerical build would do 6N+1 SCFs and show none of these.
# =============================================================================
def cmd_setup_gxtb(a):
    print(f"{C['b']}g-xTB analytic-gradient check{C['x']}  ({GXTB})")
    if not GXTB.exists():
        bad(f"g-xTB binary not found at {GXTB}"); return 1
    ver = run([str(GXTB), "--version"], env=GSM_ENV).stdout
    m = re.search(r"version\s+([\d.]+)\s+\(([0-9a-f]+)\)", ver)
    if m: info(f"reports version {m.group(1)} commit {m.group(2)} "
               f"(NB: v2.0.1 keeps the 6.7.1 label — commit/date are the real tell)")
    with tempfile.TemporaryDirectory(prefix="gxtb-grad-") as d:
        x = Path(d) / "h2o.xyz"
        x.write_text("3\nwater\nO 0 0 0.11779\nH 0 0.75716 -0.47116\nH 0 -0.75716 -0.47116\n")
        t0 = _now()
        p = run([str(GXTB), x.name, "--grad", "--gxtb"], cwd=d, env=GSM_ENV)
        dt = _now() - t0
        comps = [ln.strip() for ln in p.stdout.splitlines()
                 if re.search(r"(coulomb|hamiltonian|repulsion|exchange|dispersion|acp)\s+gradient", ln)]
        numeric = re.search(r"numerical\s+gradient", p.stdout, re.I)
        has_grad = (Path(d) / "gradient").exists()
        info(f"--gxtb --grad on H2O: {dt:.2f}s, gradient file written = {has_grad}")
        if comps and not numeric and dt < 5:
            ok("ANALYTIC gradients confirmed (analytic component breakdown, single SCF):")
            for ln in comps[:4]: info("   " + ln)
            return 0
        bad("could NOT confirm analytic gradients — looks numerical or failed. "
            "Update to g-xtb v2.0.1 (see SKILL.md Setup).")
        return 1

# =============================================================================
# gsm : build workdir from two endpoints, run GSM, parse convergence, plot.
# =============================================================================
def cmd_gsm(a):
    react = gxyz.read_xyz(Path(a.reactant))
    prod  = gxyz.read_xyz(Path(a.product))
    if len(react) != len(prod):
        bad(f"atom count mismatch: reactant {len(react)} vs product {len(prod)}"); return 1
    if [x.symbol for x in react] != [x.symbol for x in prod]:
        bad("atom ORDER/identity differs between endpoints — GSM requires identical order"); return 1
    react, prod = _delinearize(react), _delinearize(prod)

    wd = Path(a.workdir).resolve(); (wd / "scratch").mkdir(parents=True, exist_ok=True)
    with open(wd / "scratch" / "initial0000.xyz", "w") as f:
        for label, geom in (("reactant", react), ("product", prod)):
            f.write(f"{len(geom)}\n{label}\n")
            for at in geom: f.write(f"{at.symbol:2s} {at.x:14.8f} {at.y:14.8f} {at.z:14.8f}\n")
    # inpfileq from the shared template, NNODES overridable
    tpl = (GSM_INFRA / "inpfileq_template").read_text()
    tpl = re.sub(r"^NNODES\s+\d+", f"NNODES                  {a.nnodes}", tpl, flags=re.M)
    (wd / "inpfileq").write_text(tpl)
    (wd / "ograd").write_text(_ograd(a.charge, a.mult)); os.chmod(wd / "ograd", 0o755)
    info(f"workdir {wd}  ({len(react)} atoms, charge {a.charge} mult {a.mult}, NNODES {a.nnodes})")

    if a.dry_run:
        warn("--dry-run: workdir built, GSM not launched"); return 0
    print("  running gsm.orca …")
    with open(wd / "gsm.out", "w") as out:
        rc = subprocess.run([str(GSM_INFRA / "gsm.orca")], cwd=wd, env=GSM_ENV,
                            stdout=out, stderr=subprocess.STDOUT,
                            timeout=a.timeout).returncode
    return _report_gsm(wd, rc)

def _report_gsm(wd: Path, rc: int) -> int:
    out = (wd / "gsm.out").read_text()
    tsq = wd / "scratch" / "tsq0000.xyz"
    conv = re.search(r"about to write tsq\.xyz, tscontinue:\s*([01])", out)
    prof = _last_v_profile(out)
    racey = "cannot stat 'scratch/gradient'" in out
    print(f"{C['b']}GSM result{C['x']}  (exit {rc})")
    if racey: bad("ograd RACE detected (mv: cannot stat scratch/gradient) — gradients corrupted")
    if prof:
        peak = max(range(len(prof)), key=lambda i: prof[i])
        info(f"V_profile (kcal/mol): {' '.join(f'{v:.1f}' for v in prof)}")
        info(f"barrier {prof[peak]:.1f} at node {peak}  (Erxn {prof[-1]:.1f})")
        _plot_profile(prof, peak, wd / "string_profile.png")
        info(f"wrote {wd/'string_profile.png'}")
        moving = sum(1 for i in range(1, len(prof)) if abs(prof[i]-prof[i-1]) > 1.0)
        (ok if moving >= 2 else warn)(f"per-node energies vary ({moving} sizable steps) "
                                      "→ gradients are flowing (not the gRMS:0.0000 artifact)")
    if conv and tsq.exists():
        ok(f"exact-TS wrote tsq.xyz (tscontinue {conv.group(1)}) → {tsq}")
        return 0
    bad("no converged tsq.xyz — exact-TS search did not finish. "
        "Inspect gsm.out (SCF failure / bad Hessian / race), or `escalate`.")
    return 1

# =============================================================================
# refine : tsq.xyz -> Gaussian or ORCA TS-opt input, optional submit.
# =============================================================================
def cmd_refine(a):
    tsq = Path(a.tsq).resolve()
    # name the input after the run directory (the workdir, not the 'scratch' subdir)
    base = tsq.parent.parent.name if tsq.parent.name == "scratch" else (tsq.parent.name or "ts")
    stem = tsq.stem.replace("tsq0000", base) if "tsq0000" in tsq.stem else f"{base}_{tsq.stem}"
    if a.backend == "gaussian":
        route = a.route or DEF_GAU_ROUTE
        inp = tsq.parent / f"{stem}_tsq.inp"
        rc = run(["gautools", "xyz2inp", str(tsq), "--route", route,
                  "--charge", str(a.charge), "--mult", str(a.mult)], check=False)
        # gautools writes <stem>.inp next to the xyz; rename to our convention
        produced = tsq.with_suffix(".inp")
        if produced.exists() and produced != inp: shutil.move(produced, inp)
        if not inp.exists(): bad(f"xyz2inp did not produce {inp}\n{rc.stdout}\n{rc.stderr}"); return 1
        ok(f"Gaussian TS input → {inp}")
        info(f"submit:  cd {inp.parent} && {SUBGAU16} --memory 32 --cpus 8 --queue m0311 --input {inp.name}")
        if a.submit: return _submit_gaussian(inp)
    else:  # orca
        inp = tsq.parent / f"{stem}_tsq.inp"
        _write_orca_ts(tsq, inp, a.charge, a.mult, a.route)
        ok(f"ORCA TS input → {inp}")
        info(f"submit:  {SUBORC6} --input {inp.name} --memory 32 --cpus 8 --queue m0311")
        if a.submit: return _submit_orca(inp)
    return 0

# =============================================================================
# verify : NImag check + imaginary-mode "looks right" analysis.
# =============================================================================
def cmd_verify(a):
    log = Path(a.log)
    st = glog.get_log_status(log)
    print(f"{C['b']}verify{C['x']}  {log}")
    if not st.normal_termination: warn("log did not terminate normally")
    nimag = len(st.imaginary_frequencies)
    if nimag == 1:
        nu = st.imaginary_frequencies[0]
        ok(f"NImag = 1  (ν = {nu:.1f} cm⁻¹)")
        if abs(nu) < 100:
            warn(f"|ν| = {abs(nu):.0f} cm⁻¹ is very small — likely a floppy/spurious mode, "
                 "NOT a genuine reaction coordinate. Tighten Opt (Opt=Tight) or re-examine the geometry.")
    elif nimag == 0:
        bad("NImag = 0 — this is a minimum, not a TS"); return 1
    else:
        warn(f"NImag = {nimag}: {', '.join(f'{v:.1f}' for v in st.imaginary_frequencies)} cm⁻¹ "
             "— extra small imaginaries are often floppy modes; inspect the lowest one")
    # imaginary-mode displacement: which atoms move, which bonds form/break
    syms, mode = _parse_imag_mode(log)
    if mode is None:
        warn("could not parse the normal-mode block (need standard Freq output)"); return 0
    geom = glog.parse_geometry(log, frame="last")
    coords = [(at.x, at.y, at.z) for at in geom]
    movers = sorted(range(len(syms)), key=lambda i: -sum(c*c for c in mode[i]))[:4]
    info("largest imaginary-mode displacements:")
    for i in movers:
        info(f"   atom {i+1:>2} {syms[i]:2s}  |d| = {sum(c*c for c in mode[i])**.5:.3f}")
    changing = _bonds_changing_along_mode(syms, coords, mode)
    info("bonds changing most along the imaginary mode (|Δ| only; eigenvector sign is arbitrary):")
    for (i, j, dd) in changing[:5]:
        info(f"   {syms[i]}{i+1}-{syms[j]}{j+1}  |Δ| = {abs(dd):.3f} Å/δ")
    if a.reactant and a.product:
        cosv = _mode_reaction_overlap(a.reactant, a.product, mode)
        (ok if abs(cosv) > 0.5 else warn)(f"|cos(imag-mode, product−reactant)| = {abs(cosv):.2f} "
                                          f"({'matches' if abs(cosv)>0.5 else 'weak overlap with'} the reaction coordinate)")
    print(f"{C['b']}→ next: driver.py irc {log}{C['x']}")
    return 0

# =============================================================================
# irc / endpoints : thin wrappers over gautools (Both directions, endpoint opts)
# =============================================================================
def cmd_irc(a):
    rc = run(["gautools", "ts2irc", a.log, "--irc-opts", "CalcFC,MaxPoints=30,StepSize=10,Both"], check=False)
    print(rc.stdout or rc.stderr)
    return rc.returncode

def cmd_endpoints(a):
    rc = run(["gautools", "irc2opt", a.log], check=False)
    print(rc.stdout or rc.stderr)
    return rc.returncode

def cmd_status(a):
    run(["gautools", "gau-status", a.target], check=False, capture=False)
    print()
    logs = sorted(str(p) for p in Path(a.target).glob("*.log")) if Path(a.target).is_dir() else [a.target]
    if logs: run(["gautools", "gau-energy", *logs], check=False, capture=False)
    return 0

# =============================================================================
# record : the self-improvement hook. Append to SKILL.md + LEARNINGS.md.
# =============================================================================
def cmd_record(a):
    stamp = datetime.date.today().isoformat()
    entry_skill = f"\n### {stamp} — {a.problem}\n**Fix:** {a.fix}\n"
    learn_line  = f"- {stamp}  {a.problem}  →  {a.fix}\n"
    skill = SKILL_DIR / "SKILL.md"
    txt = skill.read_text()
    # anchor on the real heading LINE (not inline mentions), then skip an italic note line
    m = re.search(r"(?m)^## Workarounds \(self-recorded\)\s*\n(?:_.*\n)?", txt)
    if m:
        txt = txt[:m.end()] + entry_skill.lstrip("\n") + "\n" + txt[m.end():]
    else:
        txt += f"\n\n## Workarounds (self-recorded)\n{entry_skill}"
    skill.write_text(txt)
    with open(SKILL_DIR / "LEARNINGS.md", "a") as f: f.write(learn_line)
    ok(f"recorded workaround to SKILL.md and LEARNINGS.md  [{stamp}]")
    info("for DODH runs, also add an entry to ~/dodh/ts/TRACKER.md 'Escalation history'")
    return 0

# =============================================================================
# escalate : GSM-coupled-to-QM PBS job. SCAFFOLDED — must run on a queue node.
# =============================================================================
def cmd_escalate(a):
    wd = Path(a.workdir).resolve()
    job = wd / "gsm.job"
    job.write_text(f"""#!/bin/bash
#PBS -N gsm-qm
#PBS -q {a.queue}
#PBS -l nodes=1:{a.queue}:ppn={a.cpus},mem={a.mem}gb
#PBS -l walltime=24:00:00
#PBS -j oe
#PBS -o gsm.pbs.out
cd $PBS_O_WORKDIR
export LD_LIBRARY_PATH="{MKL_LIB}:$LD_LIBRARY_PATH"
export PATH="{GSM_INFRA}:$PATH"
# NB: point ./ograd at Gaussian/ORCA (not g-xTB) for a QM-level string.
{GSM_INFRA / 'gsm.orca'} > gsm.out 2>&1
""")
    ok(f"wrote {job}  (SCAFFOLDED — submit with: qsub {job.name})")
    warn("edit ./ograd to call subgau16/suborc6 per node before submitting; "
         "this path is queue-only and was not run end-to-end in-container")
    info("after escalating, run: driver.py record \"<why GSM-xTB failed>\" \"<what worked>\"")
    return 0

# ----- helpers ---------------------------------------------------------------
def _now():
    import time; return time.monotonic()

def run(cmd, cwd=None, env=None, check=False, capture=True):
    return subprocess.run(cmd, cwd=cwd, env=env, check=check,
                          text=True, capture_output=capture, timeout=600)

def _ograd(charge: int, mult: int) -> str:
    return f"""#!/bin/bash
set -e
[ -z "$2" ] && {{ echo "ograd: need id and ncpu" >&2; exit 1; }}
CHARGE={charge}
MULTIPLICITY={mult}
GXTB="{GXTB}"
XTB_OPTS="--gxtb --chrg ${{CHARGE}} --uhf $((MULTIPLICITY-1))"
id="$1"; ncpu="$2"
base="scratch/orcain${{id}}"
xyzfile="orcain${{id}}.xyz"
natoms=$(wc -l < "scratch/structure${{id}}")
{{ echo "${{natoms}}"; echo "GSM node ${{id}}"; cat "scratch/structure${{id}}"; }} > "scratch/${{xyzfile}}"
wrk="scratch/wrk_${{id}}"
rm -rf "${{wrk}}"; mkdir -p "${{wrk}}"
cp "scratch/${{xyzfile}}" "${{wrk}}/${{xyzfile}}"
export OMP_NUM_THREADS=${{ncpu}}
export LD_LIBRARY_PATH={MKL_LIB}
( cd "${{wrk}}" && "${{GXTB}}" "${{xyzfile}}" --grad ${{XTB_OPTS}} > "../orcain${{id}}.xtbout" 2>&1 )
mv "${{wrk}}/gradient" "${{base}}.gradient"
tm2orca.py "${{base}}"
rm -rf "${{wrk}}"
"""

def _delinearize(atoms):
    """Perturb a strictly-linear geometry ~0.05 Å off-axis (GSM internal-coord gotcha)."""
    import math
    if len(atoms) < 3: return atoms
    pts = [(a.x, a.y, a.z) for a in atoms]
    def cross(u, v): return (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
    a0 = pts[0]; axis = None; linear = True
    for p in pts[1:]:
        v = (p[0]-a0[0], p[1]-a0[1], p[2]-a0[2])
        if axis is None and any(abs(c) > 1e-6 for c in v): axis = v
        elif axis is not None and sum(c*c for c in cross(axis, v)) > 1e-6: linear = False; break
    if linear:
        atoms[1].y += 0.05
        warn("strictly-linear endpoint detected → perturbed atom 2 by 0.05 Å (avoids GSM NaNs)")
    return atoms

def _last_v_profile(out: str):
    vals = re.findall(r"^\s*V_profile:\s*([-\d.\s]+)$", out, flags=re.M)
    if not vals: return None
    return [float(x) for x in vals[-1].split()]

def _plot_profile(prof, peak, path):
    import warnings; warnings.filterwarnings("ignore")
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6, 4))
    xs = list(range(len(prof)))
    ax.plot(xs, prof, "-o", color="#2c6fbb")
    ax.plot(peak, prof[peak], "*", ms=18, color="#d6471f", label=f"TS node {peak}  ({prof[peak]:.0f} kcal/mol)")
    ax.set_xlabel("GSM node"); ax.set_ylabel("relative E (kcal/mol, g-xTB)")
    ax.set_title("GSM string energy profile"); ax.legend(); fig.tight_layout()
    fig.savefig(path, dpi=130); plt.close(fig)

def _parse_imag_mode(log: Path):
    """Parse the first (most-negative) normal mode from a Gaussian Freq log.
    Handles the standard low-precision 'Atom AN X Y Z' block (always present)."""
    lines = log.read_text(errors="ignore").splitlines()
    for n, ln in enumerate(lines):
        if ln.strip().startswith("Frequencies --"):
            freqs = ln.split("--")[1].split()
            if float(freqs[0]) >= 0: return None, None   # first block already positive
            # find the 'Atom AN ...' header below this block
            for m in range(n, min(n + 12, len(lines))):
                if re.search(r"Atom\s+AN", lines[m]):
                    syms, mode = [], []
                    for row in lines[m+1:]:
                        t = row.split()
                        if len(t) < 5 or not t[0].isdigit(): break
                        syms.append(_Z.get(int(t[1]), str(t[1])))
                        mode.append((float(t[2]), float(t[3]), float(t[4])))  # mode 1 = imaginary
                    return syms, mode
    return None, None

def _bonds_changing_along_mode(syms, coords, mode, delta=0.5):
    import math
    def d(p, q): return math.dist(p, q)
    plus  = [(coords[i][0]+delta*mode[i][0], coords[i][1]+delta*mode[i][1], coords[i][2]+delta*mode[i][2]) for i in range(len(syms))]
    minus = [(coords[i][0]-delta*mode[i][0], coords[i][1]-delta*mode[i][1], coords[i][2]-delta*mode[i][2]) for i in range(len(syms))]
    res = []
    for i in range(len(syms)):
        for j in range(i+1, len(syms)):
            if d(coords[i], coords[j]) < 2.6:                      # only near-bonded pairs
                res.append((i, j, d(plus[i], plus[j]) - d(minus[i], minus[j])))
    return sorted(res, key=lambda t: -abs(t[2]))

def _mode_reaction_overlap(react_xyz, prod_xyz, mode):
    r = gxyz.read_xyz(Path(react_xyz)); p = gxyz.read_xyz(Path(prod_xyz))
    diff = [(p[i].x-r[i].x, p[i].y-r[i].y, p[i].z-r[i].z) for i in range(len(r))]
    import math
    md = [c for v in mode for c in v]; dd = [c for v in diff for c in v]
    nm = math.sqrt(sum(c*c for c in md)) or 1; nd = math.sqrt(sum(c*c for c in dd)) or 1
    return sum(a*b for a, b in zip(md, dd)) / (nm*nd)

def _write_orca_ts(tsq: Path, inp: Path, charge: int, mult: int, route: str | None):
    geom = gxyz.read_xyz(tsq)
    kw = route or "! B3LYP D3BJ def2-SVP def2/J OptTS NumFreq CPCM(Pentanol)"
    body = [kw, f"%geom Calc_Hess true end", "",
            f"* xyz {charge} {mult}"]
    for at in geom: body.append(f" {at.symbol:2s} {at.x:14.8f} {at.y:14.8f} {at.z:14.8f}")
    body.append("*")
    inp.write_text("\n".join(body) + "\n")

def _submit_gaussian(inp: Path, mem=32, cpus=8, queue="m0311"):
    # subgau16 is on PATH only in login shells; cd in + pass basename (path-doubling gotcha).
    r = subprocess.run(["bash", "-lc",
                        f'cd "{inp.parent}" && subgau16 --memory {mem} --cpus {cpus} --queue {queue} --input {inp.name}'],
                       text=True, capture_output=True, timeout=120)
    print(r.stdout, r.stderr)
    jid = re.search(r"\b(\d+\.[\w.-]+|\d{4,})\b", r.stdout or "")
    (ok if jid else warn)(f"subgau16 job id: {jid.group(1) if jid else '(none returned)'}")
    return r.returncode

def _submit_orca(inp: Path, mem=32, cpus=8, queue="m0311"):
    r = subprocess.run(["bash", "-lc",
                        f'cd "{inp.parent}" && {SUBORC6} --input {inp.name} --memory {mem} --cpus {cpus} --queue {queue}'],
                       text=True, capture_output=True, timeout=120)
    print(r.stdout, r.stderr)
    jid = re.search(r"\b(\d+\.[\w.-]+|\d{4,})\b", r.stdout or "")
    (ok if jid else warn)(f"suborc6 job id: {jid.group(1) if jid else '(none returned)'}")
    return r.returncode

_Z = {1:"H",5:"B",6:"C",7:"N",8:"O",9:"F",15:"P",16:"S",17:"Cl",35:"Br",53:"I",75:"Re"}

# ----- CLI -------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(prog="driver.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("setup-gxtb", help="verify analytic g-xTB gradients").set_defaults(fn=cmd_setup_gxtb)

    g = sub.add_parser("gsm", help="build + run a GSM string from two endpoints")
    g.add_argument("reactant"); g.add_argument("product")
    g.add_argument("--workdir", default="./gsm_run")
    g.add_argument("--charge", type=int, default=DEF_CHARGE)
    g.add_argument("--mult", type=int, default=DEF_MULT)
    g.add_argument("--nnodes", type=int, default=9)
    g.add_argument("--timeout", type=int, default=1800)
    g.add_argument("--dry-run", action="store_true")
    g.set_defaults(fn=cmd_gsm)

    pp = sub.add_parser("plot-profile", help="re-plot V_profile PNG from a gsm.out dir")
    pp.add_argument("workdir"); pp.set_defaults(fn=lambda a: _report_gsm(Path(a.workdir), 0))

    r = sub.add_parser("refine", help="tsq.xyz -> Gaussian/ORCA TS-opt input")
    r.add_argument("tsq"); r.add_argument("--backend", choices=["gaussian", "orca"], default="gaussian")
    r.add_argument("--route", default=None)
    r.add_argument("--charge", type=int, default=DEF_CHARGE); r.add_argument("--mult", type=int, default=DEF_MULT)
    r.add_argument("--submit", action="store_true"); r.set_defaults(fn=cmd_refine)

    v = sub.add_parser("verify", help="NImag + imaginary-mode check on a freq log")
    v.add_argument("log"); v.add_argument("--reactant", default=None); v.add_argument("--product", default=None)
    v.set_defaults(fn=cmd_verify)

    i = sub.add_parser("irc", help="generate IRC(Both) input from a TS log"); i.add_argument("log"); i.set_defaults(fn=cmd_irc)
    e = sub.add_parser("endpoints", help="generate endpoint Opt+Freq from an IRC log"); e.add_argument("log"); e.set_defaults(fn=cmd_endpoints)
    s = sub.add_parser("status", help="progress table for a dir/log"); s.add_argument("target", nargs="?", default="."); s.set_defaults(fn=cmd_status)

    rec = sub.add_parser("record", help="append a workaround to SKILL.md + LEARNINGS.md")
    rec.add_argument("problem"); rec.add_argument("fix"); rec.set_defaults(fn=cmd_record)

    esc = sub.add_parser("escalate", help="emit GSM-coupled-to-QM PBS job (scaffolded)")
    esc.add_argument("workdir"); esc.add_argument("--queue", default="m0311")
    esc.add_argument("--cpus", type=int, default=8); esc.add_argument("--mem", type=int, default=32)
    esc.set_defaults(fn=cmd_escalate)

    a = p.parse_args()
    sys.exit(a.fn(a) or 0)

if __name__ == "__main__":
    main()
