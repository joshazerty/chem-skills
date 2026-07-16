#!/usr/bin/env python3
"""
run-ts-finder driver — orchestrate and *verify* transition-state location.

Pipeline:  endpoints --(g-xTB + GSM)--> tsq.xyz --(Gaussian|ORCA)--> saddle
           --> verify cascade (NImag=1, imaginary mode looks right, IRC,
               endpoint opts land on the intended reactant/product).

Self-contained: Python 3.8+ stdlib only (matplotlib optional, for the string
profile PNG). All site specifics (binaries, submit commands, level of theory)
come from config.json / environment / CLI flags — see config.example.json.

Subcommands (run `driver.py <cmd> -h`):
  doctor       check the site setup: binaries, GSM infra, submit commands
  selftest     run the built-in parser tests (no chemistry software needed)
  setup-gxtb   verify the active g-xTB build gives ANALYTIC gradients
  gsm          build a GSM workdir from two endpoints, run it, parse + plot
  plot-profile re-plot the V_profile PNG from an existing gsm.out
  refine       turn tsq.xyz into a Gaussian/ORCA TS-opt input (+ optional submit)
  verify       NImag check + imaginary-mode analysis on a Gaussian/ORCA freq output
  irc          generate an IRC input from a converged TS output
  endpoints    generate endpoint Opt+Freq inputs from an IRC output
  status       progress table for a directory of outputs
  record       append a discovered workaround to SKILL.md AND LEARNINGS.md
  escalate     emit a GSM-coupled-to-QM batch job (PBS or SLURM; scaffolded)
"""
from __future__ import annotations
import argparse, datetime, json, math, os, re, shutil, subprocess, sys, tempfile
from dataclasses import dataclass
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent

# ----- configuration ---------------------------------------------------------
# Precedence: built-in defaults < config.json < environment < CLI flags.
DEFAULTS = {
    "gxtb": "xtb",                    # g-xTB-enabled xtb binary (name on PATH or absolute path)
    "gsm_infra": "",                  # dir containing gsm.orca + tm2orca.py (molecularGSM build)
    "extra_lib_paths": [],            # prepended to LD_LIBRARY_PATH (e.g. an MKL lib dir for gsm.orca)
    "charge": 0,
    "mult": 1,
    "nnodes": 9,
    "gaussian_route": ("#p B3LYP/def2SVP EmpiricalDispersion=GD3BJ Int=UltraFine "
                       "SCF=(MaxCycle=512,XQC) Opt=(TS,CalcFC,NoEigen,MaxCycles=200) Freq"),
    "orca_route": "! B3LYP D3BJ def2-SVP def2/J OptTS NumFreq",
    "irc_opts": "CalcFC,MaxPoints=30,StepSize=10,Both",
    "endpoint_opt": "Opt=(CalcFC,MaxCycles=200) Freq",
    "queue": "",
    "mem_gb": 32,
    "cpus": 8,
    # Shell templates run from the input's directory; placeholders:
    # {input} (basename), {dir}, {mem}, {cpus}, {queue}. Empty = print-only.
    "submit": {"gaussian": "", "orca": ""},
    "scheduler": "pbs",               # for `escalate`: pbs | slurm
}

def load_config() -> dict:
    cfg = {**DEFAULTS, "submit": dict(DEFAULTS["submit"])}
    path = Path(os.environ.get("RUN_TS_FINDER_CONFIG", SKILL_DIR / "config.json"))
    if path.exists():
        try:
            user = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            sys.exit(f"FAIL config {path} is not valid JSON: {e}")
        for k, v in user.items():
            if k.startswith("_"):
                continue
            if k == "submit" and isinstance(v, dict):
                cfg["submit"].update(v)
            else:
                cfg[k] = v
        cfg["_config_path"] = str(path)
    if os.environ.get("GXTB"):
        cfg["gxtb"] = os.environ["GXTB"]
    if os.environ.get("GSM_INFRA"):
        cfg["gsm_infra"] = os.environ["GSM_INFRA"]
    return cfg

CFG = load_config()

def gxtb_path() -> Path | None:
    p = Path(os.path.expanduser(str(CFG["gxtb"])))
    if p.is_file():
        return p
    found = shutil.which(str(CFG["gxtb"]))
    return Path(found) if found else None

def gsm_env() -> dict:
    env = dict(os.environ)
    libs = ":".join(str(p) for p in CFG["extra_lib_paths"])
    if libs:
        env["LD_LIBRARY_PATH"] = libs + ":" + env.get("LD_LIBRARY_PATH", "")
    if CFG["gsm_infra"]:
        env["PATH"] = str(CFG["gsm_infra"]) + ":" + env.get("PATH", "")
    return env

C = dict(g="\033[32m", r="\033[31m", y="\033[33m", b="\033[1m", x="\033[0m")
def ok(m):   print(f"{C['g']}PASS{C['x']} {m}")
def bad(m):  print(f"{C['r']}FAIL{C['x']} {m}")
def warn(m): print(f"{C['y']}WARN{C['x']} {m}")
def info(m): print(f"  {m}")

# ----- atoms / xyz -----------------------------------------------------------
_SYMBOLS = ("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe "
            "Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In "
            "Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf "
            "Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu").split()
Z_SYM = {i + 1: s for i, s in enumerate(_SYMBOLS)}

@dataclass
class Atom:
    symbol: str
    x: float
    y: float
    z: float

def read_xyz(path: Path) -> list[Atom]:
    lines = Path(path).read_text().splitlines()
    if len(lines) < 3:
        sys.exit(f"FAIL {path}: not a valid XYZ file")
    n = int(lines[0].split()[0])
    atoms = []
    for ln in lines[2:2 + n]:
        t = ln.split()
        atoms.append(Atom(t[0].capitalize(), float(t[1]), float(t[2]), float(t[3])))
    if len(atoms) != n:
        sys.exit(f"FAIL {path}: header says {n} atoms, found {len(atoms)}")
    return atoms

def write_xyz(path: Path, atoms: list[Atom], comment: str = ""):
    with open(path, "w") as f:
        f.write(f"{len(atoms)}\n{comment}\n")
        for a in atoms:
            f.write(f"{a.symbol:2s} {a.x:14.8f} {a.y:14.8f} {a.z:14.8f}\n")

# ----- Gaussian log parsing --------------------------------------------------
def gau_normal_termination(text: str) -> bool:
    return "Normal termination of Gaussian" in text

def gau_frequencies(text: str) -> list[float]:
    freqs = []
    for m in re.finditer(r"^ Frequencies --\s+(.*)$", text, flags=re.M):
        freqs.extend(float(x) for x in m.group(1).split())
    return freqs

def gau_charge_mult(text: str):
    m = re.search(r"Charge\s*=\s*(-?\d+)\s+Multiplicity\s*=\s*(\d+)", text)
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)

def gau_last_geometry(text: str) -> list[Atom] | None:
    span = None
    for header in ("Standard orientation:", "Input orientation:"):
        hits = [m.end() for m in re.finditer(re.escape(header), text)]
        if hits:
            span = text[hits[-1]:]
            break
    if span is None:
        return None
    atoms = []
    for ln in span.splitlines()[1:]:
        if re.match(r"^\s*-{5,}\s*$", ln):
            if atoms:
                break
            continue
        t = ln.split()
        if len(t) >= 6 and t[0].isdigit() and t[1].lstrip("-").isdigit():
            atoms.append(Atom(Z_SYM.get(int(t[1]), t[1]),
                              float(t[-3]), float(t[-2]), float(t[-1])))
        elif atoms:
            break
    return atoms or None

def gau_imag_mode(text: str):
    """First (most-negative) normal mode from the standard low-precision
    'Atom AN X Y Z' block. Returns (symbols, [(dx,dy,dz)...]) or (None, None)."""
    lines = text.splitlines()
    for n, ln in enumerate(lines):
        if ln.strip().startswith("Frequencies --"):
            if float(ln.split("--")[1].split()[0]) >= 0:
                return None, None            # first block already positive
            for m in range(n, min(n + 12, len(lines))):
                if re.search(r"Atom\s+AN", lines[m]):
                    syms, mode = [], []
                    for row in lines[m + 1:]:
                        t = row.split()
                        if len(t) < 5 or not t[0].isdigit():
                            break
                        syms.append(Z_SYM.get(int(t[1]), t[1]))
                        mode.append((float(t[2]), float(t[3]), float(t[4])))
                    return syms, mode
    return None, None

def gau_energies(text: str):
    scf = [float(m.group(1)) for m in
           re.finditer(r"SCF Done:\s+E\([^)]+\)\s*=\s*(-?[\d.]+)", text)]
    g = re.search(r"Sum of electronic and thermal Free Energies=\s*(-?[\d.]+)", text)
    return (scf[-1] if scf else None), (float(g.group(1)) if g else None)

# ----- ORCA output parsing ---------------------------------------------------
def is_orca(text: str) -> bool:
    return bool(re.search(r"\*\s+O\s+R\s+C\s+A\s+\*", text)) or "ORCA TERMINATED" in text

def orca_normal_termination(text: str) -> bool:
    return "ORCA TERMINATED NORMALLY" in text

def orca_freqs(text: str):
    """[(mode_index, cm-1, is_imaginary), ...] from the last VIBRATIONAL
    FREQUENCIES block (indices include the zero translational/rotational modes)."""
    hits = [m.end() for m in re.finditer(r"VIBRATIONAL FREQUENCIES", text)]
    if not hits:
        return []
    out = []
    for ln in text[hits[-1]:].splitlines():
        m = re.match(r"\s*(\d+):\s+(-?[\d.]+)\s+cm\*\*-1(.*)", ln)
        if m:
            out.append((int(m.group(1)), float(m.group(2)), "imaginary" in m.group(3)))
        elif out:
            break
    return out

def orca_charge_mult(text: str):
    c = re.search(r"Total Charge\s+Charge\s+\.+\s+(-?\d+)", text)
    s = re.search(r"Multiplicity\s+Mult\s+\.+\s+(\d+)", text)
    return (int(c.group(1)) if c else None), (int(s.group(1)) if s else None)

def orca_last_geometry(text: str) -> list[Atom] | None:
    hits = [m.end() for m in re.finditer(r"CARTESIAN COORDINATES \(ANGSTROEM\)", text)]
    if not hits:
        return None
    atoms = []
    for ln in text[hits[-1]:].splitlines():
        s = ln.strip()
        if not atoms and (not s or set(s) == {"-"}):
            continue
        t = s.split()
        if len(t) == 4:
            try:
                atoms.append(Atom(t[0].capitalize(), float(t[1]), float(t[2]), float(t[3])))
                continue
            except ValueError:
                pass
        break
    return atoms or None

def orca_mode(text: str, mode_idx: int, natoms: int):
    """Column `mode_idx` of the last NORMAL MODES matrix, reshaped to per-atom
    (dx,dy,dz). Returns None if the block is absent."""
    hits = [m.end() for m in re.finditer(r"NORMAL MODES", text)]
    if not hits:
        return None
    vec, cols, started = [0.0] * (3 * natoms), [], False
    for ln in text[hits[-1]:].splitlines():
        t = ln.split()
        if not t:
            if started:
                break
            continue
        if all(re.fullmatch(r"\d+", x) for x in t):
            cols, started = [int(x) for x in t], True
            continue
        if started and re.fullmatch(r"\d+", t[0]) and len(t) == len(cols) + 1:
            row = int(t[0])
            if row < 3 * natoms:
                for c, val in zip(cols, t[1:]):
                    if c == mode_idx:
                        vec[row] = float(val)
            continue
        if started:
            break
    return [tuple(vec[3 * i:3 * i + 3]) for i in range(natoms)]

def orca_energies(text: str):
    e = [float(m.group(1)) for m in
         re.finditer(r"FINAL SINGLE POINT ENERGY\s+(-?[\d.]+)", text)]
    g = re.search(r"Final Gibbs free energy\s+\.+\s+(-?[\d.]+)", text)
    return (e[-1] if e else None), (float(g.group(1)) if g else None)

# ----- unified freq-output view ----------------------------------------------
@dataclass
class FreqResult:
    backend: str
    normal_termination: bool
    imaginary: list          # negative frequencies, cm-1
    syms: list | None        # atom symbols (mode order)
    coords: list | None      # (x,y,z) per atom
    mode: list | None        # imaginary-mode displacement per atom

def load_freq_result(log: Path) -> FreqResult:
    text = Path(log).read_text(errors="ignore")
    if is_orca(text):
        freqs = orca_freqs(text)
        imag = [v for (_, v, im) in freqs if im or v < 0]
        geom = orca_last_geometry(text)
        mode = None
        if geom and imag:
            idx = min((i for (i, v, im) in freqs if im or v < 0), default=None)
            if idx is not None:
                mode = orca_mode(text, idx, len(geom))
                if mode and not any(any(c) for c in mode):
                    mode = None
        return FreqResult("orca", orca_normal_termination(text), sorted(imag),
                          [a.symbol for a in geom] if geom else None,
                          [(a.x, a.y, a.z) for a in geom] if geom else None, mode)
    imag = sorted(v for v in gau_frequencies(text) if v < 0)
    syms, mode = gau_imag_mode(text)
    geom = gau_last_geometry(text)
    return FreqResult("gaussian", gau_normal_termination(text), imag,
                      syms or ([a.symbol for a in geom] if geom else None),
                      [(a.x, a.y, a.z) for a in geom] if geom else None, mode)

# ----- input writers ---------------------------------------------------------
def strip_opt_freq(route: str) -> str:
    r = re.sub(r"(?i)\b(opt|freq)\b\s*=\s*\([^)]*\)", "", route)
    r = re.sub(r"(?i)\b(opt|freq)\b(=\S+)?", "", r)
    return re.sub(r"\s+", " ", r).strip()

def orca_strip_ts(route: str) -> str:
    r = re.sub(r"(?i)\b(OptTS|NumFreq|AnFreq|Freq|Opt|IRC)\b", "", route)
    return re.sub(r"\s+", " ", r).strip()

def write_gaussian_input(path: Path, route: str, charge: int, mult: int,
                         atoms: list[Atom], title: str = "run-ts-finder"):
    lines = [route, "", title, "", f"{charge} {mult}"]
    lines += [f" {a.symbol:2s} {a.x:14.8f} {a.y:14.8f} {a.z:14.8f}" for a in atoms]
    lines += ["", ""]
    path.write_text("\n".join(lines) + "\n")
    if re.search(r"(?i)(^|[\s/])gen\b|pseudo\s*=\s*read", route):
        warn(f"route uses gen/pseudo=read — append the basis/ECP blocks to {path.name} before running")

def write_orca_input(path: Path, route: str, charge: int, mult: int,
                     atoms: list[Atom], blocks: list[str] | None = None):
    body = [route] + (blocks or []) + ["", f"* xyz {charge} {mult}"]
    body += [f" {a.symbol:2s} {a.x:14.8f} {a.y:14.8f} {a.z:14.8f}" for a in atoms]
    body.append("*")
    path.write_text("\n".join(body) + "\n")

# ----- submission ------------------------------------------------------------
def submit(inp: Path, backend: str, mem=None, cpus=None, queue=None) -> int:
    tpl = CFG["submit"].get(backend, "")
    if not tpl:
        warn(f"no submit.{backend} template in config.json — submit by hand:")
        info(f"cd {inp.parent} && <your submit command> {inp.name}")
        return 1
    if str(inp.resolve()).startswith("/tmp"):
        warn("input lives under /tmp — on most clusters /tmp is node-local, so the "
             "job is accepted but dies on the compute node. Move it to a shared "
             "filesystem ($HOME / project dir) first.")
    cmd = tpl.format(input=inp.name, dir=str(inp.parent),
                     mem=mem or CFG["mem_gb"], cpus=cpus or CFG["cpus"],
                     queue=queue or CFG["queue"])
    # login shell: site submit wrappers are often only on PATH in login shells
    r = subprocess.run(["bash", "-lc", f'cd "{inp.parent}" && {cmd}'],
                       text=True, capture_output=True, timeout=120)
    print((r.stdout or "") + (r.stderr or ""), end="")
    jid = re.search(r"\b(\d+\.[\w.-]+|\d{4,})\b", r.stdout or "")
    (ok if jid else warn)(f"submitted via {backend} template — job id: "
                          f"{jid.group(1) if jid else '(none detected in output)'}")
    return r.returncode

# =============================================================================
# doctor : verify the site setup end-to-end (no chemistry run needed).
# =============================================================================
def cmd_doctor(a):
    print(f"{C['b']}run-ts-finder doctor{C['x']}")
    fails = 0
    cfgp = CFG.get("_config_path")
    (ok if cfgp else warn)(f"config: {cfgp or 'no config.json — using built-in defaults'}")

    gx = gxtb_path()
    if gx:
        ok(f"g-xTB binary: {gx}")
        info("run `driver.py setup-gxtb` to confirm ANALYTIC gradients (required for GSM)")
    else:
        bad(f"g-xTB binary '{CFG['gxtb']}' not found — set \"gxtb\" in config.json or $GXTB")
        fails += 1

    infra = Path(CFG["gsm_infra"]) if CFG["gsm_infra"] else None
    if infra and (infra / "gsm.orca").exists():
        ok(f"GSM infra: {infra} (gsm.orca present)")
        if not (infra / "tm2orca.py").exists() and not shutil.which("tm2orca.py"):
            warn("tm2orca.py not found in gsm_infra or on PATH — ograd needs it "
                 "(ships with molecularGSM)")
    else:
        bad("GSM infra not configured or gsm.orca missing — set \"gsm_infra\" in "
            "config.json to a dir containing the molecularGSM gsm.orca build")
        fails += 1

    tpl = (infra / "inpfileq_template") if infra else None
    if tpl and tpl.exists():
        ok(f"inpfileq template: {tpl} (site override)")
    elif (SKILL_DIR / "inpfileq_template").exists():
        ok(f"inpfileq template: {SKILL_DIR / 'inpfileq_template'} (bundled default)")
    else:
        bad("no inpfileq_template found (bundled copy missing?)")
        fails += 1

    for be in ("gaussian", "orca"):
        t = CFG["submit"].get(be, "")
        if not t:
            warn(f"submit.{be}: not configured — `refine --submit` will print inputs only")
            continue
        first = t.split()[0]
        r = subprocess.run(["bash", "-lc", f"command -v {first}"],
                           text=True, capture_output=True)
        (ok if r.returncode == 0 else warn)(
            f"submit.{be}: '{first}' {'found' if r.returncode == 0 else 'NOT found in a login shell'}")

    try:
        import matplotlib  # noqa: F401
        ok("matplotlib available (string profile PNGs)")
    except ImportError:
        warn("matplotlib missing — GSM profile plots will be skipped (everything else works)")

    print()
    (ok if fails == 0 else bad)(f"doctor: {fails} blocking problem(s)")
    return 1 if fails else 0

# =============================================================================
# setup-gxtb : prove the active build computes ANALYTIC gradients.
# g-xTB v2.0.0+ has analytic gradients but still reports the base xtb version
# string, so --version cannot confirm it. Test behaviourally: --gxtb --grad on
# a tiny molecule must run in ~1 SCF and print analytic gradient *components*;
# a numerical build does 6N+1 SCFs and prints none of them.
# =============================================================================
def cmd_setup_gxtb(a):
    gx = gxtb_path()
    print(f"{C['b']}g-xTB analytic-gradient check{C['x']}  ({gx or CFG['gxtb']})")
    if not gx:
        bad(f"g-xTB binary '{CFG['gxtb']}' not found — see SKILL.md Setup")
        return 1
    ver = run([str(gx), "--version"], env=gsm_env()).stdout
    m = re.search(r"version\s+([\d.]+)\s+\(([0-9a-f]+)\)", ver)
    if m:
        info(f"reports version {m.group(1)} commit {m.group(2)} "
             f"(NB: g-xTB releases keep the base xtb version label — behaviour is the real tell)")
    with tempfile.TemporaryDirectory(prefix="gxtb-grad-") as d:
        x = Path(d) / "h2o.xyz"
        x.write_text("3\nwater\nO 0 0 0.11779\nH 0 0.75716 -0.47116\nH 0 -0.75716 -0.47116\n")
        t0 = _now()
        p = run([str(gx), x.name, "--grad", "--gxtb"], cwd=d, env=gsm_env())
        dt = _now() - t0
        comps = [ln.strip() for ln in p.stdout.splitlines()
                 if re.search(r"(coulomb|hamiltonian|repulsion|exchange|dispersion|acp)\s+gradient", ln)]
        numeric = re.search(r"numerical\s+gradient", p.stdout, re.I)
        has_grad = (Path(d) / "gradient").exists()
        info(f"--gxtb --grad on H2O: {dt:.2f}s, gradient file written = {has_grad}")
        if comps and not numeric and dt < 5:
            ok("ANALYTIC gradients confirmed (analytic component breakdown, single SCF):")
            for ln in comps[:4]:
                info("   " + ln)
            return 0
        bad("could NOT confirm analytic gradients — looks numerical or failed. "
            "Install g-xTB v2.0.1+ (see SKILL.md Setup).")
        return 1

# =============================================================================
# gsm : build workdir from two endpoints, run GSM, parse convergence, plot.
# =============================================================================
def cmd_gsm(a):
    infra = Path(CFG["gsm_infra"]) if CFG["gsm_infra"] else None
    if not (infra and (infra / "gsm.orca").exists()):
        bad("GSM infra not configured (need gsm.orca) — run `driver.py doctor`")
        return 1
    react, prod = read_xyz(Path(a.reactant)), read_xyz(Path(a.product))
    if len(react) != len(prod):
        bad(f"atom count mismatch: reactant {len(react)} vs product {len(prod)}")
        return 1
    if [x.symbol for x in react] != [x.symbol for x in prod]:
        bad("atom ORDER/identity differs between endpoints — GSM requires identical order")
        return 1
    react, prod = _delinearize(react), _delinearize(prod)

    wd = Path(a.workdir).resolve()
    (wd / "scratch").mkdir(parents=True, exist_ok=True)
    with open(wd / "scratch" / "initial0000.xyz", "w") as f:
        for label, geom in (("reactant", react), ("product", prod)):
            f.write(f"{len(geom)}\n{label}\n")
            for at in geom:
                f.write(f"{at.symbol:2s} {at.x:14.8f} {at.y:14.8f} {at.z:14.8f}\n")
    tplf = infra / "inpfileq_template"
    if not tplf.exists():
        tplf = SKILL_DIR / "inpfileq_template"
    tpl = re.sub(r"^NNODES\s+\d+", f"NNODES                  {a.nnodes}",
                 tplf.read_text(), flags=re.M)
    (wd / "inpfileq").write_text(tpl)
    (wd / "ograd").write_text(_ograd(a.charge, a.mult))
    os.chmod(wd / "ograd", 0o755)
    info(f"workdir {wd}  ({len(react)} atoms, charge {a.charge} mult {a.mult}, NNODES {a.nnodes})")

    if a.dry_run:
        warn("--dry-run: workdir built, GSM not launched")
        return 0
    print("  running gsm.orca …")
    with open(wd / "gsm.out", "w") as out:
        rc = subprocess.run([str(infra / "gsm.orca")], cwd=wd, env=gsm_env(),
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
    if racey:
        bad("ograd RACE detected (mv: cannot stat scratch/gradient) — gradients corrupted")
    if prof:
        peak = max(range(len(prof)), key=lambda i: prof[i])
        info(f"V_profile (kcal/mol): {' '.join(f'{v:.1f}' for v in prof)}")
        info(f"barrier {prof[peak]:.1f} at node {peak}  (Erxn {prof[-1]:.1f})")
        if _plot_profile(prof, peak, wd / "string_profile.png"):
            info(f"wrote {wd / 'string_profile.png'}")
        moving = sum(1 for i in range(1, len(prof)) if abs(prof[i] - prof[i - 1]) > 1.0)
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
    atoms = read_xyz(tsq)
    # per-backend name so gaussian/orca inputs for the same tsq don't clobber each other
    inp = tsq.parent / (f"{stem}_tsq.inp" if a.backend == "gaussian" else f"{stem}_tsq_orca.inp")
    if a.backend == "gaussian":
        write_gaussian_input(inp, a.route or CFG["gaussian_route"], a.charge, a.mult,
                             atoms, title=f"TS refine from {tsq.name}")
    else:
        route = a.route or CFG["orca_route"]
        blocks = ["%geom Calc_Hess true end"] if re.search(r"(?i)\bOptTS\b", route) else []
        write_orca_input(inp, route, a.charge, a.mult, atoms, blocks)
    ok(f"{a.backend} TS input → {inp}")
    if a.submit:
        return submit(inp, a.backend, a.mem, a.cpus, a.queue)
    tpl = CFG["submit"].get(a.backend, "")
    info("submit:  " + (f"cd {inp.parent} && " +
                        tpl.format(input=inp.name, dir=str(inp.parent), mem=a.mem or CFG["mem_gb"],
                                   cpus=a.cpus or CFG["cpus"], queue=a.queue or CFG["queue"])
                        if tpl else f"(no submit.{a.backend} template configured — run it your usual way)"))
    return 0

# =============================================================================
# verify : NImag check + imaginary-mode "looks right" analysis (g16 + ORCA).
# =============================================================================
def cmd_verify(a):
    log = Path(a.log)
    fr = load_freq_result(log)
    print(f"{C['b']}verify{C['x']}  {log}  [{fr.backend}]")
    if not fr.normal_termination:
        warn("output did not terminate normally")
    nimag = len(fr.imaginary)
    if nimag == 1:
        nu = fr.imaginary[0]
        ok(f"NImag = 1  (ν = {nu:.1f} cm⁻¹)")
        if abs(nu) < 100:
            warn(f"|ν| = {abs(nu):.0f} cm⁻¹ is very small — likely a floppy/spurious mode, "
                 "NOT a genuine reaction coordinate. Tighten Opt (Opt=Tight) or re-examine the geometry.")
    elif nimag == 0:
        bad("NImag = 0 — this is a minimum, not a TS")
        return 1
    else:
        warn(f"NImag = {nimag}: {', '.join(f'{v:.1f}' for v in fr.imaginary)} cm⁻¹ "
             "— extra small imaginaries are often floppy modes; inspect the lowest one")
    if fr.mode is None or fr.syms is None or fr.coords is None:
        warn("could not parse the normal-mode block — NImag verdict stands, but "
             "inspect the imaginary mode visually before trusting this TS")
        return 0
    syms, mode, coords = fr.syms, fr.mode, fr.coords
    movers = sorted(range(len(syms)), key=lambda i: -sum(c * c for c in mode[i]))[:4]
    info("largest imaginary-mode displacements:")
    for i in movers:
        info(f"   atom {i + 1:>2} {syms[i]:2s}  |d| = {sum(c * c for c in mode[i]) ** .5:.3f}")
    changing = _bonds_changing_along_mode(syms, coords, mode)
    info("bonds changing most along the imaginary mode (|Δ| only; eigenvector sign is arbitrary):")
    for (i, j, dd) in changing[:5]:
        info(f"   {syms[i]}{i + 1}-{syms[j]}{j + 1}  |Δ| = {abs(dd):.3f} Å/δ")
    if a.reactant and a.product:
        cosv = _mode_reaction_overlap(a.reactant, a.product, mode)
        (ok if abs(cosv) > 0.5 else warn)(
            f"|cos(imag-mode, product−reactant)| = {abs(cosv):.2f} "
            f"({'matches' if abs(cosv) > 0.5 else 'weak overlap with'} the reaction coordinate)")
    print(f"{C['b']}→ next: driver.py irc {log}{C['x']}")
    return 0

# =============================================================================
# irc / endpoints : IRC input from a TS output; endpoint opts from an IRC run.
# =============================================================================
def cmd_irc(a):
    log = Path(a.log)
    text = log.read_text(errors="ignore")
    if is_orca(text):
        geom = orca_last_geometry(text)
        chg, mult = orca_charge_mult(text)
        if not geom:
            bad("could not parse a geometry from the ORCA output")
            return 1
        inp = log.with_name(log.stem + "_irc.inp")
        route = orca_strip_ts(a.route or CFG["orca_route"]) + " IRC"
        hess = log.with_suffix(".hess")
        blocks = [f'%irc InitHess Read\n     Hess_Filename "{hess.name}"\nend'] if hess.exists() else []
        write_orca_input(inp, route, chg if chg is not None else CFG["charge"],
                         mult if mult is not None else CFG["mult"], geom, blocks)
        ok(f"ORCA IRC input → {inp}" + ("" if hess.exists() else
           "  (no .hess found next to the output — ORCA will compute one)"))
    else:
        geom = gau_last_geometry(text)
        chg, mult = gau_charge_mult(text)
        if not geom:
            bad("could not parse a geometry from the Gaussian log")
            return 1
        inp = log.with_name(log.stem + "_irc.inp")
        route = strip_opt_freq(a.route or CFG["gaussian_route"]) + f" IRC=({CFG['irc_opts']})"
        write_gaussian_input(inp, route, chg if chg is not None else CFG["charge"],
                             mult if mult is not None else CFG["mult"], geom,
                             title=f"IRC from {log.name}")
        ok(f"Gaussian IRC input → {inp}  (IRC=({CFG['irc_opts']}))")
    if a.submit:
        return submit(inp, "orca" if is_orca(text) else "gaussian")
    return 0

def cmd_endpoints(a):
    log = Path(a.log)
    text = log.read_text(errors="ignore")
    if is_orca(text):
        # ORCA IRC writes <base>_IRC_F.xyz / <base>_IRC_B.xyz next to the output
        chg, mult = orca_charge_mult(text)
        route = orca_strip_ts(CFG["orca_route"]) + " Opt Freq"
        made = 0
        for tag, suffix in (("fwd", "_IRC_F.xyz"), ("rev", "_IRC_B.xyz")):
            xyz = log.with_name(log.stem + suffix)
            if not xyz.exists():
                warn(f"{xyz.name} not found (ORCA IRC endpoint file)")
                continue
            inp = log.with_name(f"{log.stem}_irc_{tag}.inp")
            write_orca_input(inp, route, chg if chg is not None else CFG["charge"],
                             mult if mult is not None else CFG["mult"], read_xyz(xyz))
            ok(f"endpoint opt ({tag}) → {inp}")
            made += 1
        return 0 if made else 1
    fwd, rev = _gau_irc_endpoints(text)
    if not (fwd or rev):
        bad("could not extract IRC endpoint geometries from the log")
        return 1
    chg, mult = gau_charge_mult(text)
    route = strip_opt_freq(CFG["gaussian_route"]) + " " + CFG["endpoint_opt"]
    for tag, geom in (("fwd", fwd), ("rev", rev)):
        if not geom:
            warn(f"no {tag}-direction geometry found in the IRC log")
            continue
        inp = log.with_name(f"{log.stem}_irc_{tag}.inp")
        write_gaussian_input(inp, route, chg if chg is not None else CFG["charge"],
                             mult if mult is not None else CFG["mult"], geom,
                             title=f"IRC {tag} endpoint opt from {log.name}")
        ok(f"endpoint opt ({tag}) → {inp}")
    info("optimise both, then `driver.py verify` each: expect NImag=0 and geometries "
        "matching the intended reactant/product")
    return 0

def _gau_irc_endpoints(text: str):
    """Last geometry seen in each IRC direction. Follows the FORWARD/REVERSE
    markers Gaussian prints while walking the path; with `Both`, the final
    geometry of each direction is that side's endpoint."""
    direction, last = None, {"FORWARD": None, "REVERSE": None}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        up = lines[i].upper()
        if "FORWARD" in up and "PATH" in up:
            direction = "FORWARD"
        elif "REVERSE" in up and "PATH" in up:
            direction = "REVERSE"
        elif ("Input orientation:" in lines[i] or "Standard orientation:" in lines[i]):
            block = "\n".join(lines[i:i + 8000])
            geom = gau_last_first_geometry(block)
            if geom and direction:
                last[direction] = geom
        i += 1
    return last["FORWARD"], last["REVERSE"]

def gau_last_first_geometry(block: str):
    """First orientation block within `block` (helper for IRC walking)."""
    atoms = []
    for ln in block.splitlines()[1:]:
        if re.match(r"^\s*-{5,}\s*$", ln):
            if atoms:
                break
            continue
        t = ln.split()
        if len(t) >= 6 and t[0].isdigit() and t[1].lstrip("-").isdigit():
            atoms.append(Atom(Z_SYM.get(int(t[1]), t[1]),
                              float(t[-3]), float(t[-2]), float(t[-1])))
        elif atoms:
            break
    return atoms or None

# =============================================================================
# status : one-line summary per output file (termination, NImag, energies).
# =============================================================================
def cmd_status(a):
    target = Path(a.target)
    files = (sorted(list(target.glob("*.log")) + list(target.glob("*.out")))
             if target.is_dir() else [target])
    files = [f for f in files if f.name != "gsm.out"]
    if not files:
        warn(f"no .log/.out files under {target}")
    rows = []
    for f in files:
        text = f.read_text(errors="ignore")
        if is_orca(text):
            term = "done" if orca_normal_termination(text) else "…/died"
            imag = [v for (_, v, im) in orca_freqs(text) if im or v < 0]
            e, g = orca_energies(text)
            be = "orca"
        else:
            term = "done" if gau_normal_termination(text) else "…/died"
            imag = [v for v in gau_frequencies(text) if v < 0]
            e, g = gau_energies(text)
            be = "g16"
        rows.append((f.name, be, term, str(len(imag)) if ("Frequencies" in text or "VIBRATIONAL" in text) else "-",
                     f"{e:.6f}" if e is not None else "-",
                     f"{g:.6f}" if g is not None else "-"))
    if rows:
        wname = max(len(r[0]) for r in rows)
        print(f"{'file':<{wname}}  {'code':<5} {'state':<7} {'NImag':<5} {'E (Ha)':<15} {'G (Ha)':<15}")
        for r in rows:
            print(f"{r[0]:<{wname}}  {r[1]:<5} {r[2]:<7} {r[3]:<5} {r[4]:<15} {r[5]:<15}")
    gsm_out = target / "gsm.out" if target.is_dir() else None
    if gsm_out and gsm_out.exists():
        prof = _last_v_profile(gsm_out.read_text())
        if prof:
            peak = max(range(len(prof)), key=lambda i: prof[i])
            print(f"\nGSM string: barrier {prof[peak]:.1f} kcal/mol at node {peak} "
                  f"(Erxn {prof[-1]:.1f})")
    return 0

# =============================================================================
# record : the self-improvement hook. Append to SKILL.md + LEARNINGS.md.
# =============================================================================
def cmd_record(a):
    stamp = datetime.date.today().isoformat()
    entry_skill = f"\n### {stamp} — {a.problem}\n**Fix:** {a.fix}\n"
    learn_line = f"- {stamp}  {a.problem}  →  {a.fix}\n"
    skill = SKILL_DIR / "SKILL.md"
    txt = skill.read_text()
    # anchor on the real heading LINE (not inline mentions), then skip an italic note line
    m = re.search(r"(?m)^## Workarounds \(self-recorded\)\s*\n(?:_.*\n)?", txt)
    if m:
        txt = txt[:m.end()] + entry_skill.lstrip("\n") + "\n" + txt[m.end():]
    else:
        txt += f"\n\n## Workarounds (self-recorded)\n{entry_skill}"
    skill.write_text(txt)
    with open(SKILL_DIR / "LEARNINGS.md", "a") as f:
        f.write(learn_line)
    ok(f"recorded workaround to SKILL.md and LEARNINGS.md  [{stamp}]")
    return 0

# =============================================================================
# escalate : GSM-coupled-to-QM batch job. SCAFFOLDED — must run on a queue node.
# =============================================================================
def cmd_escalate(a):
    wd = Path(a.workdir).resolve()
    infra = CFG["gsm_infra"] or "<gsm_infra>"
    libs = ":".join(str(p) for p in CFG["extra_lib_paths"])
    lib_line = f'export LD_LIBRARY_PATH="{libs}:$LD_LIBRARY_PATH"\n' if libs else ""
    sched = a.scheduler or CFG["scheduler"]
    job = wd / "gsm.job"
    if sched == "slurm":
        header = (f"#!/bin/bash\n#SBATCH -J gsm-qm\n"
                  + (f"#SBATCH -p {a.queue}\n" if a.queue else "")
                  + f"#SBATCH -N 1\n#SBATCH -n {a.cpus}\n#SBATCH --mem={a.mem}G\n"
                  f"#SBATCH -t 24:00:00\ncd $SLURM_SUBMIT_DIR\n")
        submit_hint = f"sbatch {job.name}"
    else:
        header = (f"#!/bin/bash\n#PBS -N gsm-qm\n"
                  + (f"#PBS -q {a.queue}\n" if a.queue else "")
                  + f"#PBS -l nodes=1:ppn={a.cpus},mem={a.mem}gb\n"
                  f"#PBS -l walltime=24:00:00\n#PBS -j oe\n#PBS -o gsm.pbs.out\n"
                  f"cd $PBS_O_WORKDIR\n")
        submit_hint = f"qsub {job.name}"
    job.write_text(header + lib_line +
                   f'export PATH="{infra}:$PATH"\n'
                   "# NB: point ./ograd at Gaussian/ORCA (not g-xTB) for a QM-level string.\n"
                   f"{Path(infra) / 'gsm.orca'} > gsm.out 2>&1\n")
    ok(f"wrote {job}  (SCAFFOLDED — submit with: {submit_hint})")
    warn("edit ./ograd to call your QM backend per node before submitting; "
         "this path is queue-only by design")
    info("after escalating, run: driver.py record \"<why GSM-xTB failed>\" \"<what worked>\"")
    return 0

# ----- helpers ---------------------------------------------------------------
def _now():
    import time
    return time.monotonic()

def run(cmd, cwd=None, env=None, check=False, capture=True):
    return subprocess.run(cmd, cwd=cwd, env=env, check=check,
                          text=True, capture_output=capture, timeout=600)

def _ograd(charge: int, mult: int) -> str:
    gx = gxtb_path() or Path(str(CFG["gxtb"]))
    libs = ":".join(str(p) for p in CFG["extra_lib_paths"])
    lib_line = f"export LD_LIBRARY_PATH={libs}:$LD_LIBRARY_PATH\n" if libs else ""
    return f"""#!/bin/bash
set -e
[ -z "$2" ] && {{ echo "ograd: need id and ncpu" >&2; exit 1; }}
CHARGE={charge}
MULTIPLICITY={mult}
GXTB="{gx}"
XTB_OPTS="--gxtb --chrg ${{CHARGE}} --uhf $((MULTIPLICITY-1))"
id="$1"; ncpu="$2"
base="scratch/orcain${{id}}"
xyzfile="orcain${{id}}.xyz"
natoms=$(wc -l < "scratch/structure${{id}}")
{{ echo "${{natoms}}"; echo "GSM node ${{id}}"; cat "scratch/structure${{id}}"; }} > "scratch/${{xyzfile}}"
# per-id work dir — a shared dir races on scratch/gradient across parallel node calls
wrk="scratch/wrk_${{id}}"
rm -rf "${{wrk}}"; mkdir -p "${{wrk}}"
cp "scratch/${{xyzfile}}" "${{wrk}}/${{xyzfile}}"
export OMP_NUM_THREADS=${{ncpu}}
{lib_line}( cd "${{wrk}}" && "${{GXTB}}" "${{xyzfile}}" --grad ${{XTB_OPTS}} > "../orcain${{id}}.xtbout" 2>&1 )
mv "${{wrk}}/gradient" "${{base}}.gradient"
tm2orca.py "${{base}}"
rm -rf "${{wrk}}"
"""

def _delinearize(atoms):
    """Perturb a strictly-linear geometry ~0.05 Å off-axis (GSM internal-coord gotcha)."""
    if len(atoms) < 3:
        return atoms
    pts = [(a.x, a.y, a.z) for a in atoms]
    def cross(u, v):
        return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    a0 = pts[0]
    axis, linear = None, True
    for p in pts[1:]:
        v = (p[0] - a0[0], p[1] - a0[1], p[2] - a0[2])
        if axis is None and any(abs(c) > 1e-6 for c in v):
            axis = v
        elif axis is not None and sum(c * c for c in cross(axis, v)) > 1e-6:
            linear = False
            break
    if linear:
        atoms[1].y += 0.05
        warn("strictly-linear endpoint detected → perturbed atom 2 by 0.05 Å (avoids GSM NaNs)")
    return atoms

def _last_v_profile(out: str):
    vals = re.findall(r"^\s*V_profile:\s*([-\d.\s]+)$", out, flags=re.M)
    if not vals:
        return None
    return [float(x) for x in vals[-1].split()]

def _plot_profile(prof, peak, path) -> bool:
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        warn("matplotlib not available — skipping string_profile.png")
        return False
    fig, ax = plt.subplots(figsize=(6, 4))
    xs = list(range(len(prof)))
    ax.plot(xs, prof, "-o", color="#2c6fbb")
    ax.plot(peak, prof[peak], "*", ms=18, color="#d6471f",
            label=f"TS node {peak}  ({prof[peak]:.0f} kcal/mol)")
    ax.set_xlabel("GSM node")
    ax.set_ylabel("relative E (kcal/mol, g-xTB)")
    ax.set_title("GSM string energy profile")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return True

def _bonds_changing_along_mode(syms, coords, mode, delta=0.5):
    def d(p, q):
        return math.dist(p, q)
    plus = [(coords[i][0] + delta * mode[i][0], coords[i][1] + delta * mode[i][1],
             coords[i][2] + delta * mode[i][2]) for i in range(len(syms))]
    minus = [(coords[i][0] - delta * mode[i][0], coords[i][1] - delta * mode[i][1],
              coords[i][2] - delta * mode[i][2]) for i in range(len(syms))]
    res = []
    for i in range(len(syms)):
        for j in range(i + 1, len(syms)):
            if d(coords[i], coords[j]) < 2.6:                      # only near-bonded pairs
                res.append((i, j, d(plus[i], plus[j]) - d(minus[i], minus[j])))
    return sorted(res, key=lambda t: -abs(t[2]))

def _mode_reaction_overlap(react_xyz, prod_xyz, mode):
    r, p = read_xyz(Path(react_xyz)), read_xyz(Path(prod_xyz))
    diff = [(p[i].x - r[i].x, p[i].y - r[i].y, p[i].z - r[i].z) for i in range(len(r))]
    md = [c for v in mode for c in v]
    dd = [c for v in diff for c in v]
    nm = math.sqrt(sum(c * c for c in md)) or 1
    nd = math.sqrt(sum(c * c for c in dd)) or 1
    return sum(a * b for a, b in zip(md, dd)) / (nm * nd)

# =============================================================================
# selftest : exercise every parser on synthetic fixtures — no chemistry
# software needed, so it runs anywhere (CI, fresh installs).
# =============================================================================
_FAKE_G16 = """\
 Entering Gaussian System
 Charge = -1 Multiplicity = 1
                         Standard orientation:
 ---------------------------------------------------------------------
 Center     Atomic      Atomic             Coordinates (Angstroms)
 Number     Number       Type             X           Y           Z
 ---------------------------------------------------------------------
      1          6           0        0.000000    0.000000    0.000000
      2          7           0        0.000000    0.000000    1.160000
      3          1           0        1.000000    0.000000   -0.500000
 ---------------------------------------------------------------------
 Frequencies --  -1016.8123               2100.5000               3300.1000
 Red. masses --      1.1712                 1.2000                 1.0800
 Frc consts  --      0.7133                 3.1000                 6.9000
 IR Inten    --     33.1553                10.0000                55.0000
  Atom  AN      X      Y      Z        X      Y      Z        X      Y      Z
     1   6     0.05   0.00   0.10     0.00   0.00   0.70     0.10   0.00   0.00
     2   7     0.02   0.00  -0.08     0.00   0.00  -0.60     0.05   0.00   0.00
     3   1    -0.90   0.00   0.40     0.00   0.00   0.10    -0.95   0.00   0.20
 SCF Done:  E(RB3LYP) =  -93.4123456     A.U. after   12 cycles
 Sum of electronic and thermal Free Energies=            -93.401234
 Normal termination of Gaussian 16 at Mon Jan  1 00:00:00 2026.
"""

_FAKE_ORCA = """\
                                 *****************
                                 * O   R   C   A *
                                 *****************
----------------------------
CARTESIAN COORDINATES (ANGSTROEM)
----------------------------
  C      0.000000    0.000000    0.000000
  N      0.000000    0.000000    1.160000
  H      1.000000    0.000000   -0.500000

Total Charge           Charge          ....    -1
Multiplicity           Mult            ....    1
-----------------------
VIBRATIONAL FREQUENCIES
-----------------------
   0:         0.00 cm**-1
   1:         0.00 cm**-1
   2:         0.00 cm**-1
   3:         0.00 cm**-1
   4:         0.00 cm**-1
   5:         0.00 cm**-1
   6:      -1267.35 cm**-1 ***imaginary mode***
   7:       2100.50 cm**-1
   8:       3300.10 cm**-1
------------
NORMAL MODES
------------
                  0          1          2          3          4          5
      0       0.000000   0.000000   0.000000   0.000000   0.000000   0.000000
      1       0.000000   0.000000   0.000000   0.000000   0.000000   0.000000
      2       0.000000   0.000000   0.000000   0.000000   0.000000   0.000000
      3       0.000000   0.000000   0.000000   0.000000   0.000000   0.000000
      4       0.000000   0.000000   0.000000   0.000000   0.000000   0.000000
      5       0.000000   0.000000   0.000000   0.000000   0.000000   0.000000
      6       0.000000   0.000000   0.000000   0.000000   0.000000   0.000000
      7       0.000000   0.000000   0.000000   0.000000   0.000000   0.000000
      8       0.000000   0.000000   0.000000   0.000000   0.000000   0.000000
                  6          7          8
      0       0.050000   0.000000   0.100000
      1       0.000000   0.000000   0.000000
      2       0.100000   0.700000   0.000000
      3       0.020000   0.000000   0.050000
      4       0.000000   0.000000   0.000000
      5      -0.080000  -0.600000   0.000000
      6      -0.900000   0.000000  -0.950000
      7       0.000000   0.000000   0.000000
      8       0.400000   0.100000   0.200000

FINAL SINGLE POINT ENERGY       -93.412345678
Final Gibbs free energy         ...           -93.40123400 Eh
                             ****ORCA TERMINATED NORMALLY****
"""

def cmd_selftest(a):
    print(f"{C['b']}run-ts-finder selftest{C['x']}")
    fails = []
    def check(name, cond):
        (ok if cond else bad)(name)
        if not cond:
            fails.append(name)

    # xyz round trip
    with tempfile.TemporaryDirectory(prefix="tsf-selftest-") as d:
        p = Path(d) / "t.xyz"
        write_xyz(p, [Atom("O", 0, 0, 0.11779), Atom("H", 0, 0.75716, -0.47116),
                      Atom("H", 0, -0.75716, -0.47116)], "water")
        back = read_xyz(p)
        check("xyz write/read round trip", len(back) == 3 and back[0].symbol == "O"
              and abs(back[1].y - 0.75716) < 1e-6)

        # Gaussian parsers
        check("g16 termination", gau_normal_termination(_FAKE_G16))
        check("g16 charge/mult", gau_charge_mult(_FAKE_G16) == (-1, 1))
        geom = gau_last_geometry(_FAKE_G16)
        check("g16 geometry (3 atoms, C N H)", bool(geom) and
              [at.symbol for at in geom] == ["C", "N", "H"])
        imag = [v for v in gau_frequencies(_FAKE_G16) if v < 0]
        check("g16 NImag=1 at -1016.8", len(imag) == 1 and abs(imag[0] + 1016.8123) < 1e-3)
        syms, mode = gau_imag_mode(_FAKE_G16)
        check("g16 imaginary mode parsed", syms == ["C", "N", "H"] and
              mode is not None and abs(mode[2][0] + 0.90) < 1e-6)
        e, g = gau_energies(_FAKE_G16)
        check("g16 energies (SCF, G)", e is not None and abs(e + 93.4123456) < 1e-6
              and g is not None and abs(g + 93.401234) < 1e-6)

        # ORCA parsers
        check("orca detection", is_orca(_FAKE_ORCA) and not is_orca(_FAKE_G16))
        check("orca termination", orca_normal_termination(_FAKE_ORCA))
        check("orca charge/mult", orca_charge_mult(_FAKE_ORCA) == (-1, 1))
        og = orca_last_geometry(_FAKE_ORCA)
        check("orca geometry (3 atoms, C N H)", bool(og) and
              [at.symbol for at in og] == ["C", "N", "H"])
        of = orca_freqs(_FAKE_ORCA)
        oimag = [v for (_, v, im) in of if im]
        check("orca NImag=1 at -1267.35", len(oimag) == 1 and abs(oimag[0] + 1267.35) < 1e-3)
        omode = orca_mode(_FAKE_ORCA, 6, 3)
        check("orca imaginary-mode column", omode is not None and
              abs(omode[0][0] - 0.05) < 1e-6 and abs(omode[2][0] + 0.90) < 1e-6)

        # unified view: same fake reaction through both backends
        gl = Path(d) / "g.log"
        gl.write_text(_FAKE_G16)
        ol = Path(d) / "o.out"
        ol.write_text(_FAKE_ORCA)
        fg, fo = load_freq_result(gl), load_freq_result(ol)
        check("unified freq view (both backends, NImag=1, mode present)",
              fg.backend == "gaussian" and fo.backend == "orca" and
              len(fg.imaginary) == 1 and len(fo.imaginary) == 1 and
              fg.mode is not None and fo.mode is not None)

    # route surgery
    r = strip_opt_freq(DEFAULTS["gaussian_route"])
    check("gaussian route strip removes Opt/Freq",
          "Opt" not in r and "Freq" not in r and "B3LYP" in r)
    o = orca_strip_ts(DEFAULTS["orca_route"])
    check("orca route strip removes OptTS/NumFreq",
          "OptTS" not in o and "NumFreq" not in o and "B3LYP" in o)

    # profile parsing + delinearize
    prof = _last_v_profile("junk\n V_profile: 0.0 12.3 40.1 22.0 5.5\n")
    check("V_profile parse", prof == [0.0, 12.3, 40.1, 22.0, 5.5])
    lin = [Atom("H", 0, 0, 0), Atom("C", 0, 0, 1.06), Atom("N", 0, 0, 2.22)]
    _delinearize(lin)
    check("linear endpoint auto-perturbed", abs(lin[1].y - 0.05) < 1e-9)

    print()
    if fails:
        bad(f"selftest: {len(fails)} failure(s): {', '.join(fails)}")
        return 1
    ok("selftest: all checks passed")
    return 0

# ----- CLI -------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(prog="driver.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="check the site setup (binaries, infra, submit)").set_defaults(fn=cmd_doctor)
    sub.add_parser("selftest", help="run built-in parser tests (no chemistry software needed)").set_defaults(fn=cmd_selftest)
    sub.add_parser("setup-gxtb", help="verify analytic g-xTB gradients").set_defaults(fn=cmd_setup_gxtb)

    g = sub.add_parser("gsm", help="build + run a GSM string from two endpoints")
    g.add_argument("reactant"); g.add_argument("product")
    g.add_argument("--workdir", default="./gsm_run")
    g.add_argument("--charge", type=int, default=CFG["charge"])
    g.add_argument("--mult", type=int, default=CFG["mult"])
    g.add_argument("--nnodes", type=int, default=CFG["nnodes"])
    g.add_argument("--timeout", type=int, default=1800)
    g.add_argument("--dry-run", action="store_true")
    g.set_defaults(fn=cmd_gsm)

    pp = sub.add_parser("plot-profile", help="re-plot V_profile PNG from a gsm.out dir")
    pp.add_argument("workdir"); pp.set_defaults(fn=lambda a: _report_gsm(Path(a.workdir), 0))

    r = sub.add_parser("refine", help="tsq.xyz -> Gaussian/ORCA TS-opt input")
    r.add_argument("tsq"); r.add_argument("--backend", choices=["gaussian", "orca"], default="gaussian")
    r.add_argument("--route", default=None)
    r.add_argument("--charge", type=int, default=CFG["charge"])
    r.add_argument("--mult", type=int, default=CFG["mult"])
    r.add_argument("--mem", type=int, default=None); r.add_argument("--cpus", type=int, default=None)
    r.add_argument("--queue", default=None)
    r.add_argument("--submit", action="store_true"); r.set_defaults(fn=cmd_refine)

    v = sub.add_parser("verify", help="NImag + imaginary-mode check on a Gaussian/ORCA freq output")
    v.add_argument("log"); v.add_argument("--reactant", default=None); v.add_argument("--product", default=None)
    v.set_defaults(fn=cmd_verify)

    i = sub.add_parser("irc", help="generate an IRC input from a TS output")
    i.add_argument("log"); i.add_argument("--route", default=None)
    i.add_argument("--submit", action="store_true"); i.set_defaults(fn=cmd_irc)

    e = sub.add_parser("endpoints", help="generate endpoint Opt+Freq inputs from an IRC output")
    e.add_argument("log"); e.set_defaults(fn=cmd_endpoints)

    s = sub.add_parser("status", help="progress table for a dir of outputs")
    s.add_argument("target", nargs="?", default="."); s.set_defaults(fn=cmd_status)

    rec = sub.add_parser("record", help="append a workaround to SKILL.md + LEARNINGS.md")
    rec.add_argument("problem"); rec.add_argument("fix"); rec.set_defaults(fn=cmd_record)

    esc = sub.add_parser("escalate", help="emit GSM-coupled-to-QM batch job (scaffolded)")
    esc.add_argument("workdir")
    esc.add_argument("--scheduler", choices=["pbs", "slurm"], default=None)
    esc.add_argument("--queue", default=CFG["queue"])
    esc.add_argument("--cpus", type=int, default=CFG["cpus"])
    esc.add_argument("--mem", type=int, default=CFG["mem_gb"])
    esc.set_defaults(fn=cmd_escalate)

    a = p.parse_args()
    sys.exit(a.fn(a) or 0)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        os._exit(141)      # e.g. `driver.py doctor | head` — die quietly like a good pipe citizen
